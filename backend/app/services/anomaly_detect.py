"""异常检测业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "anomaly_detect"
SIGNAL_MODULE = "anomaly_signal"
MANIFEST_MODULE = "export_manifest"
SPEC_VERSION = "V014"
LIST_FIELDS = ["规则编号", "监控指标", "检测算法", "基线窗口", "波动阈值", "告警级别", "静默时段", "规则版本"]
REQUIRED_FIELDS = ["规则编号", "监控指标", "检测算法"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]


class AnomalyDetectService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检测规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于异常检测可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检测规则已{action}"

    def run_detection(self, entry_id: int, values: list[float]) -> dict[str, Any]:
        """对历史数据执行异常检测：计算移动平均和标准差，判断最新值是否异常。"""
        entry = self.get_entry(entry_id)
        if not entry:
            return {"ok": False, "message": "规则不存在"}
        if len(values) < 3:
            return {"ok": True, "message": "数据不足", "is_anomaly": False}
        try:
            window = int(entry.get("基线窗口", "7"))
        except Exception:
            window = 7
        try:
            threshold = float(entry.get("波动阈值", "0.3"))
        except Exception:
            threshold = 0.3
        # 使用最近window个点计算基线
        baseline = values[-window-1:-1] if len(values) > window else values[:-1]
        latest = values[-1]
        mean = sum(baseline) / len(baseline)
        variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
        std = variance ** 0.5
        deviation = abs(latest - mean) / std if std > 0 else 0
        is_anomaly = deviation > threshold
        return {"ok": True, "mean": round(mean, 4), "std": round(std, 4),
                "latest": latest, "deviation": round(deviation, 4),
                "threshold": threshold, "is_anomaly": is_anomaly,
                "message": f"异常检测完成，偏差={deviation:.4f}"}

    # ------------------------------------------------------------- V014 跨模块
    def list_signals(self, *, status: str | None = None, keyword: str | None = None) -> list[dict[str, Any]]:
        """读取异常检测信号表，供数据导出清单与核对页面共同使用。"""
        rows = store.rows(SIGNAL_MODULE)
        if status:
            rows = [row for row in rows if row.get("信号状态") == status]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("信号编号", "")).casefold()]
        return rows

    def reconcile_export_manifests(self, task_id: int, batch_no: str | None = None) -> dict[str, Any]:
        """V014 联动：读取导出文件清单中的信号编号，核对每个分片的告警范围。

        沿调用链找到数据导出清单的落库位置，直接复用同一份信号表数据，
        保证异常检测侧与数据导出侧核对口径完全一致。
        """
        manifests = [m for m in store.rows(MANIFEST_MODULE) if m.get("task_id") == task_id]
        if batch_no:
            manifests = [m for m in manifests if m.get("batch_no") == batch_no]
        if not manifests:
            return {"ok": False, "message": f"导出任务 {task_id} 没有可核对的清单分片"}
        signals = {str(row.get("信号编号")): row for row in store.rows(SIGNAL_MODULE)}
        items: list[dict[str, Any]] = []
        for manifest in manifests:
            signal_no = str(manifest.get("signal_no", ""))
            signal = signals.get(signal_no)
            expected = str(signal.get("告警范围", "")) if signal else ""
            actual = str(manifest.get("alert_range", ""))
            matched = bool(signal) and signal.get("信号状态") == "生效" and expected == actual
            items.append({
                "manifest_id": manifest.get("id"),
                "shard_no": manifest.get("shard_no"),
                "signal_no": signal_no,
                "expected_alert_range": expected,
                "actual_alert_range": actual,
                "shard_status": manifest.get("shard_status"),
                "matched": matched,
                "remark": "核对一致" if matched else "信号缺失/停用或告警范围不一致",
            })
        matched_count = sum(1 for item in items if item["matched"])
        return {
            "ok": True,
            "spec_version": SPEC_VERSION,
            "task_id": task_id,
            "batch_no": batch_no or "ALL",
            "total": len(items),
            "matched": matched_count,
            "mismatched": len(items) - matched_count,
            "items": items,
        }
