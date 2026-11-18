"""数据导出服务（V014）：按任务分片生成导出文件清单，分片独立完成/失败/取消。

设计要点
--------
1. 每个导出任务可规划成多个分片，分片是最小执行单元，单个分片取消或失败
   不会中断同一批次的其余分片。
2. 分片完成标记与文件元数据在同一个事务里落库；缺任一文件元数据时整批回滚，
   清单里不会出现“已完成但无法下载”的条目。
3. 同一批次的多条清单统一提交，整组结果逐条记录在 ``export_manifest`` 表。
4. 重试只补写未完成分片（失败/取消），已完成分片及其文件永不覆盖。
5. V014 下清单处理若发现未清残留（已规划但未提交的半成品），触发事务回滚，
   并写入 ``v014_transaction_archive`` 留档核对。
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "data_export"
MANIFEST_MODULE = "export_manifest"
SIGNAL_MODULE = "anomaly_signal"
ARCHIVE_MODULE = "v014_transaction_archive"
SPEC_VERSION = "V014"

REQUIRED_FIELDS = ["任务编号", "数据来源", "导出格式"]
STATUS_ORDER = ["草稿", "导出中", "已完成", "已取消", "部分失败", "待重试"]
ACTION_RULES = {"开始导出": "导出中", "确认完成": "已完成", "取消导出": "已取消"}
NEGATIVE_ACTIONS: list[str] = []

# 分片级状态：每个分片独立成功或失败
SHARD_PENDING = "待处理"
SHARD_RUNNING = "处理中"
SHARD_SUCCESS = "成功"
SHARD_FAILED = "失败"
SHARD_CANCELED = "已取消"
UNFINISHED_SHARDS = {SHARD_PENDING, SHARD_RUNNING, SHARD_FAILED, SHARD_CANCELED}
FILE_METADATA_FIELDS = ["file_name", "file_path", "file_size", "checksum", "content_type"]

# 单分片取消、其余继续时的两种处置策略
POLICY_KEEP = "keep_success"      # 保留成功分片，取消项单独记录
POLICY_RETRY_BATCH = "retry_batch"  # 整批转待重试，暂不发布文件，保留原分片边界


class ExportServiceError(Exception):
    """业务异常：触发 V014 事务回滚的统一基类。"""


class ResidueError(ExportServiceError):
    """清单处理发现未清残留，V014 要求整笔事务回滚。"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _task_brief(task: dict[str, Any]) -> str:
    keys = list(task.keys())
    for key in keys[1:]:
        value = str(task.get(key) or "").strip()
        if value and key not in {"status", "pending", "abnormal"}:
            return value
    return ""


class DataExportService:
    # ------------------------------------------------------------------ 任务侧
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            status = str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)]))
            manifests = store.filter_by_field(MANIFEST_MODULE, "task_id", int(row.get("id", i + 1)))
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": _task_brief(row),
                "start": start,
                "end": end,
                "status": status,
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if status == '已完成' else '进行中'}",
                "manifest_count": len(manifests),
                "shard_summary": self._summarize_shards(manifests),
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        manifests = store.filter_by_field(MANIFEST_MODULE, "task_id", entry_id)
        detail["manifests"] = [self._public_manifest(m) for m in manifests]
        detail["shard_summary"] = self._summarize_shards(manifests)
        return detail

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"导出任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据导出可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != "已取消"
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"导出任务已{action}"

    # -------------------------------------------------------------- 清单规划侧
    def plan_manifests(self, task_id: int, shard_count: int, batch_no: str | None = None) -> list[dict[str, Any]]:
        """为一个导出任务规划分片清单，只生成“待处理”分片，不发布任何文件。"""
        task = store.find(MODULE, task_id)
        if task is None:
            raise ExportServiceError(f"导出任务 {task_id} 不存在或已归档")
        if not isinstance(shard_count, int) or shard_count <= 0:
            raise ExportServiceError("分片数量必须是正整数")
        batch = (batch_no or f"BATCH-{task_id}-{time.strftime('%Y%m%d%H%M%S')}").strip()
        existing = store.filter_by_field(MANIFEST_MODULE, "task_id", task_id)
        if any(m.get("batch_no") == batch and m.get("plan_committed") for m in existing):
            raise ExportServiceError(f"批次 {batch} 已存在已提交清单，不能重复规划")
        with store.transaction([MANIFEST_MODULE, MODULE]) as tx:
            self._purge_residue_if_any(task_id, batch)
            manifests: list[dict[str, Any]] = []
            next_manifest_id = store.next_id(MANIFEST_MODULE)
            for index in range(shard_count):
                manifest = self._build_manifest(task, batch, index, shard_count, next_manifest_id + index)
                store.rows(MANIFEST_MODULE).append(manifest)
                manifests.append(manifest)
            task["status"] = "导出中"
            task["pending"] = True
            tx.commit()
            return [self._public_manifest(m) for m in manifests]

    def submit_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """整组导出：同一批次的多条清单统一提交，整组结果逐条记录。

        payload 结构：
            task_id: 导出任务 id
            shard_count: 分片数（可选，默认 3）
            batch_no: 批次号（可选）
            shards: [{shard_index, result: success|failed|canceled,
                      file_metadata?: {...}, error_message?: str}]
        """
        task_id = int(payload.get("task_id") or 0)
        task = store.find(MODULE, task_id)
        if task is None:
            raise ExportServiceError(f"导出任务 {task_id} 不存在或已归档")
        shard_count = int(payload.get("shard_count") or 3)
        if shard_count <= 0:
            raise ExportServiceError("分片数量必须是正整数")
        batch = str(payload.get("batch_no") or f"BATCH-{task_id}-{time.strftime('%Y%m%d%H%M%S')}").strip()
        shard_payloads = payload.get("shards") or []
        indexes = {int(item.get("shard_index")) for item in shard_payloads if item.get("shard_index") is not None}
        if indexes and (min(indexes) < 0 or max(indexes) >= shard_count or len(indexes) != len(shard_payloads)):
            raise ExportServiceError("分片下标必须落在 [0, 分片数) 且不能重复")

        with store.transaction([MANIFEST_MODULE, MODULE]) as tx:
            self._purge_residue_if_any(task_id, batch)
            next_manifest_id = store.next_id(MANIFEST_MODULE)
            manifests = [self._build_manifest(task, batch, i, shard_count, next_manifest_id + i)
                         for i in range(shard_count)]
            for manifest in manifests:
                store.rows(MANIFEST_MODULE).append(manifest)
            by_index = {int(item["shard_index"]): item for item in shard_payloads}
            for manifest in manifests:
                item = by_index.get(manifest["shard_index"])
                if item is None:
                    continue
                self._apply_shard_result(manifest, item)
            for manifest in manifests:
                manifest["plan_committed"] = True
                manifest["committed_at"] = _now()
            self._refresh_task_status(task, manifests)
            tx.commit()
            return {
                "ok": True,
                "message": f"批次 {batch} 已统一提交，共 {len(manifests)} 个分片",
                "batch_no": batch,
                "items": [self._public_manifest(m) for m in manifests],
                "summary": self._summarize_shards(manifests),
            }

    # -------------------------------------------------------------- 分片执行侧
    def complete_shard(self, manifest_id: int, file_metadata: dict[str, Any], expected_version: int | None = None) -> dict[str, Any]:
        """分片完成：完成标记与文件元数据同事务落库，缺元数据则整笔回滚。"""
        with store.transaction([MANIFEST_MODULE]) as tx:
            manifest = self._lock_manifest(manifest_id, expected_version)
            missing = [key for key in FILE_METADATA_FIELDS if not str(file_metadata.get(key) or "").strip()]
            if missing:
                raise ExportServiceError(
                    f"分片 {manifest_id} 缺少文件元数据 {','.join(missing)}，为避免无法下载的完成项，事务回滚")
            if manifest["shard_status"] == SHARD_SUCCESS:
                raise ExportServiceError(f"分片 {manifest_id} 已完成，文件元数据不可覆盖")
            metadata = {key: str(file_metadata.get(key)) for key in FILE_METADATA_FIELDS}
            manifest["file_metadata"] = metadata
            manifest["file_name"] = metadata["file_name"]
            manifest["shard_status"] = SHARD_SUCCESS
            manifest["error_message"] = ""
            manifest["finished_at"] = _now()
            manifest["version"] = store.bump_version(MANIFEST_MODULE)
            tx.commit()
            return self._public_manifest(manifest)

    def fail_shard(self, manifest_id: int, error_message: str, expected_version: int | None = None) -> dict[str, Any]:
        """单分片失败：只记录自身失败，不影响同批其他分片。"""
        with store.transaction([MANIFEST_MODULE, MODULE]) as tx:
            manifest = self._lock_manifest(manifest_id, expected_version)
            if manifest["shard_status"] == SHARD_SUCCESS:
                raise ExportServiceError(f"分片 {manifest_id} 已成功，不能改判失败")
            manifest["shard_status"] = SHARD_FAILED
            manifest["error_message"] = error_message or "分片执行失败"
            manifest["finished_at"] = _now()
            manifest["version"] = store.bump_version(MANIFEST_MODULE)
            self._refresh_task_by_task_id(manifest["task_id"])
            tx.commit()
            return self._public_manifest(manifest)

    def cancel_shard(self, manifest_id: int, policy: str = POLICY_KEEP, expected_version: int | None = None) -> dict[str, Any]:
        """单分片取消，其余分片继续；按策略保留成功项或整批转待重试。"""
        if policy not in {POLICY_KEEP, POLICY_RETRY_BATCH}:
            raise ExportServiceError("取消策略只能是 keep_success 或 retry_batch")
        with store.transaction([MANIFEST_MODULE, MODULE]) as tx:
            manifest = self._lock_manifest(manifest_id, expected_version)
            if manifest["shard_status"] == SHARD_SUCCESS:
                raise ExportServiceError(f"分片 {manifest_id} 已成功，不能取消")
            batch_no = manifest["batch_no"]
            siblings = [m for m in store.rows(MANIFEST_MODULE)
                        if m["task_id"] == manifest["task_id"] and m["batch_no"] == batch_no]
            if policy == POLICY_RETRY_BATCH:
                # 整批转待重试：保留原分片边界，已发布文件撤回，取消项也回到待处理
                for sibling in siblings:
                    sibling["shard_status"] = SHARD_PENDING
                    sibling["file_metadata"] = None
                    sibling["file_name"] = ""
                    sibling["error_message"] = ""
                    sibling["finished_at"] = ""
                    sibling["published"] = False
                manifest["error_message"] = "整批转待重试，暂不发布文件"
                manifest["finished_at"] = _now()
                task = store.find(MODULE, manifest["task_id"])
                if task:
                    task["status"] = "待重试"
                    task["pending"] = True
                decision = "整批转为待重试，保留原分片边界且暂不发布文件"
            else:
                manifest["shard_status"] = SHARD_CANCELED
                manifest["error_message"] = "分片被单独取消"
                manifest["finished_at"] = _now()
                decision = "保留成功分片，取消项单独记录"
            for sibling in siblings:
                sibling["version"] = store.bump_version(MANIFEST_MODULE)
            self._refresh_task_by_task_id(manifest["task_id"])
            tx.commit()
            return {"ok": True, "decision": decision, "policy": policy,
                    "items": [self._public_manifest(m) for m in siblings]}

    def retry_batch(self, task_id: int, batch_no: str) -> dict[str, Any]:
        """批次重试：只补写失败/取消/待处理分片，已完成文件绝不覆盖。"""
        task = store.find(MODULE, task_id)
        if task is None:
            raise ExportServiceError(f"导出任务 {task_id} 不存在或已归档")
        with store.transaction([MANIFEST_MODULE, MODULE]) as tx:
            manifests = [m for m in store.rows(MANIFEST_MODULE)
                         if m["task_id"] == task_id and m["batch_no"] == batch_no]
            if not manifests:
                raise ExportServiceError(f"任务 {task_id} 下不存在批次 {batch_no}")
            retried: list[dict[str, Any]] = []
            for manifest in manifests:
                if manifest["shard_status"] == SHARD_SUCCESS:
                    continue  # 已完成分片保持原文件不动
                if manifest["shard_status"] in UNFINISHED_SHARDS:
                    manifest["shard_status"] = SHARD_PENDING
                    manifest["error_message"] = ""
                    manifest["finished_at"] = ""
                    manifest["retry_count"] = int(manifest.get("retry_count", 0)) + 1
                    manifest["version"] = store.bump_version(MANIFEST_MODULE)
                    retried.append(manifest)
            task["status"] = "导出中"
            task["pending"] = True
            tx.commit()
            return {"ok": True, "message": f"批次 {batch_no} 已重置 {len(retried)} 个未完成分片，成功分片保持不动",
                    "retried_shard_ids": [m["id"] for m in retried],
                    "items": [self._public_manifest(m) for m in manifests]}

    # -------------------------------------------------------------- 读取/下载侧
    def list_manifests(self, task_id: int | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(MANIFEST_MODULE)
        if task_id is not None:
            rows = [m for m in rows if m["task_id"] == task_id]
        if status:
            rows = [m for m in rows if m["shard_status"] == status]
        return [self._public_manifest(m) for m in rows]

    def get_manifest(self, manifest_id: int) -> dict[str, Any] | None:
        manifest = store.find(MANIFEST_MODULE, manifest_id)
        return self._public_manifest(manifest) if manifest else None

    def download_shard(self, manifest_id: int) -> dict[str, Any]:
        manifest = store.find(MANIFEST_MODULE, manifest_id)
        if manifest is None:
            raise ExportServiceError(f"分片清单 {manifest_id} 不存在")
        if manifest["shard_status"] != SHARD_SUCCESS or not manifest.get("file_metadata"):
            raise ExportServiceError(f"分片 {manifest_id} 未成功完成，没有可下载文件")
        return manifest["file_metadata"]

    # -------------------------------------------------------------- V014 联动侧
    def reconcile_with_signals(self, task_id: int, batch_no: str | None = None) -> dict[str, Any]:
        """V014 跨模块：异常检测读取清单里的信号编号，逐分片核对告警范围。"""
        manifests = [m for m in store.rows(MANIFEST_MODULE) if m["task_id"] == task_id]
        if batch_no:
            manifests = [m for m in manifests if m["batch_no"] == batch_no]
        if not manifests:
            raise ExportServiceError(f"任务 {task_id} 没有可核对的导出清单")
        signals = store.rows(SIGNAL_MODULE)
        signal_by_no = {str(s.get("信号编号")): s for s in signals}
        checks: list[dict[str, Any]] = []
        for manifest in manifests:
            signal_no = manifest.get("signal_no", "")
            signal = signal_by_no.get(signal_no)
            expected = str(signal.get("告警范围", "")) if signal else ""
            actual = manifest.get("alert_range", "")
            matched = bool(signal) and expected == actual and signal.get("信号状态") == "生效"
            checks.append({
                "manifest_id": manifest["id"],
                "shard_index": manifest["shard_index"],
                "signal_no": signal_no,
                "expected_alert_range": expected,
                "actual_alert_range": actual,
                "shard_status": manifest["shard_status"],
                "matched": matched,
                "remark": "核对一致" if matched else "信号缺失/停用或告警范围不一致",
            })
        return {
            "ok": True,
            "spec_version": SPEC_VERSION,
            "task_id": task_id,
            "batch_no": batch_no or "ALL",
            "total": len(checks),
            "matched": sum(1 for c in checks if c["matched"]),
            "mismatched": sum(1 for c in checks if not c["matched"]),
            "items": checks,
            "checked_at": _now(),
        }

    def list_archive(self) -> list[dict[str, Any]]:
        return store.list_archive()

    # ------------------------------------------------------------------ 内部方法
    def _purge_residue_if_any(self, task_id: int, batch_no: str) -> None:
        """V014 残留稽查：未统一提交的半成品先留档再清除，并拒绝本次处理。

        留档写在独立的留档表（不随业务事务回滚）；随后抛出残留异常，
        让当前清单处理事务整体回滚，满足“V014 下触发事务回滚、按 V014 留档核对”。
        """
        table = store.rows(MANIFEST_MODULE)
        residue = [m for m in table
                   if m["task_id"] == task_id and m["batch_no"] == batch_no and not m.get("plan_committed")]
        if not residue:
            return
        store.archive_v014({
            "archive_no": f"V014-RES-{task_id}-{int(time.time() * 1000)}",
            "spec_version": SPEC_VERSION,
            "modules": MANIFEST_MODULE,
            "task_id": task_id,
            "batch_no": batch_no,
            "result": "residue_rollback",
            "residue_count": len(residue),
            "residue_shards": [m["shard_no"] for m in residue],
            "remark": "清单处理发现未清残留，按 V014 触发事务回滚并留档核对",
        })
        raise ResidueError(f"批次 {batch_no} 存在 {len(residue)} 条未清残留，已按 V014 回滚")

    def _build_manifest(self, task: dict[str, Any], batch_no: str, index: int,
                        shard_count: int, manifest_id: int | None = None) -> dict[str, Any]:
        task_id = int(task["id"])
        signals = store.rows(SIGNAL_MODULE)
        signal = signals[index % len(signals)] if signals else {}
        signal_no = str(signal.get("信号编号", f"SIG-V014-{task_id:04d}-{index + 1:03d}"))
        alert_range = str(signal.get("告警范围", f"ALM-{index * 100 + 1:04d}~ALM-{(index + 1) * 100:04d}"))
        return {
            "id": manifest_id if manifest_id is not None else store.next_id(MANIFEST_MODULE),
            "task_id": task_id,
            "task_no": str(task.get("任务编号", "")),
            "batch_no": batch_no,
            "shard_index": index,
            "shard_total": shard_count,
            "shard_no": f"{batch_no}-S{index + 1:02d}",
            "signal_no": signal_no,
            "alert_range": alert_range,
            "shard_status": SHARD_PENDING,
            "file_metadata": None,
            "file_name": "",
            "error_message": "",
            "published": False,
            "plan_committed": False,
            "retry_count": 0,
            "version": 1,
            "created_at": _now(),
            "finished_at": "",
        }

    def _apply_shard_result(self, manifest: dict[str, Any], item: dict[str, Any]) -> None:
        result = str(item.get("result", "")).strip().lower()
        if result == "success":
            metadata = item.get("file_metadata") or {}
            missing = [key for key in FILE_METADATA_FIELDS if not str(metadata.get(key) or "").strip()]
            if missing:
                raise ExportServiceError(
                    f"分片 {manifest['shard_no']} 缺少文件元数据 {','.join(missing)}，整批回滚")
            manifest["file_metadata"] = {key: str(metadata[key]) for key in FILE_METADATA_FIELDS}
            manifest["file_name"] = str(metadata["file_name"])
            manifest["shard_status"] = SHARD_SUCCESS
            manifest["published"] = True
            manifest["finished_at"] = _now()
        elif result == "failed":
            manifest["shard_status"] = SHARD_FAILED
            manifest["error_message"] = str(item.get("error_message") or "分片执行失败")
            manifest["finished_at"] = _now()
        elif result == "canceled":
            manifest["shard_status"] = SHARD_CANCELED
            manifest["error_message"] = str(item.get("error_message") or "分片被取消")
            manifest["finished_at"] = _now()
        else:
            raise ExportServiceError(f"分片 {manifest['shard_no']} 的结果 {result!r} 不被支持")

    def _lock_manifest(self, manifest_id: int, expected_version: int | None) -> dict[str, Any]:
        manifest = store.find(MANIFEST_MODULE, manifest_id)
        if manifest is None:
            raise ExportServiceError(f"分片清单 {manifest_id} 不存在")
        if expected_version is not None and int(manifest.get("version", 1)) != int(expected_version):
            raise ExportServiceError(
                f"分片 {manifest_id} 版本冲突：期望 {expected_version}，实际 {manifest.get('version')}")
        return manifest

    def _refresh_task_by_task_id(self, task_id: int) -> None:
        task = store.find(MODULE, task_id)
        if task is None:
            return
        manifests = store.filter_by_field(MANIFEST_MODULE, "task_id", task_id)
        self._refresh_task_status(task, manifests)

    def _refresh_task_status(self, task: dict[str, Any], manifests: list[dict[str, Any]]) -> None:
        if not manifests:
            return
        # 整批改判“待重试”后保留该结论，不被分片状态刷新覆盖
        if task.get("status") == "待重试":
            task["pending"] = True
            return
        statuses = {m["shard_status"] for m in manifests}
        if statuses <= {SHARD_SUCCESS}:
            task["status"] = "已完成"
            task["pending"] = False
            task["abnormal"] = False
        elif SHARD_FAILED in statuses and SHARD_SUCCESS in statuses:
            task["status"] = "部分失败"
            task["pending"] = True
            task["abnormal"] = True
        elif statuses <= {SHARD_CANCELED}:
            task["status"] = "已取消"
            task["pending"] = False
        elif SHARD_CANCELED in statuses and SHARD_SUCCESS in statuses and SHARD_FAILED not in statuses:
            task["status"] = "部分失败"
            task["pending"] = True
        else:
            task["status"] = "导出中"
            task["pending"] = True

    def _summarize_shards(self, manifests: list[dict[str, Any]]) -> dict[str, int]:
        summary = {SHARD_SUCCESS: 0, SHARD_FAILED: 0, SHARD_CANCELED: 0,
                   SHARD_PENDING: 0, SHARD_RUNNING: 0}
        for manifest in manifests:
            summary[manifest["shard_status"]] = summary.get(manifest["shard_status"], 0) + 1
        return summary

    def _public_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        data = dict(manifest)
        data["downloadable"] = manifest["shard_status"] == SHARD_SUCCESS and bool(manifest.get("file_metadata"))
        return data
