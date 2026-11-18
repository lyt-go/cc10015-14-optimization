"""数据导出接口（V014）：任务时间线 + 分片级导出文件清单。

- 保留原时间线/登记/动作/CSV 导出接口，工作台与详情页共用同一份后端数据；
- 新增清单规划、整批提交、分片完成/失败/取消、批次重试、文件下载；
- 清单处理的未清残留与原子性破坏会触发 V014 事务回滚并在留档表可查。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.data_export import (
    POLICY_KEEP,
    POLICY_RETRY_BATCH,
    DataExportService,
    ExportServiceError,
)

router = APIRouter(prefix="/api/data_export", tags=["数据导出"])
service = DataExportService()
LIST_FIELDS = ["任务编号", "数据来源", "导出格式", "字段清单", "数据范围", "压缩方式", "分片大小", "导出批次"]
STATUSES = ["草稿", "导出中", "已完成", "已取消", "部分失败", "待重试"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


def _raise_if_error(error: ExportServiceError) -> None:
    raise HTTPException(status_code=409, detail=str(error))


@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=data_export.csv"})


# ------------------------------------------------------------ 分片清单（V014）
@router.get("/manifests")
def list_manifests(
    task_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """数据文件清单：工作台与详情页读取同一个接口，保证展示相同业务数据。"""
    items = service.list_manifests(task_id=task_id, status=status)
    return {"items": items, "total": len(items)}


@router.get("/manifests/archive")
def list_v014_archive() -> dict[str, Any]:
    """V014 留档核对：回滚、残留等事务事件都在这里可查。"""
    items = service.list_archive()
    return {"items": items, "total": len(items)}


@router.post("/manifests/plan")
def plan_manifests(payload: dict[str, Any]) -> ActionResult:
    task_id = int(payload.get("task_id") or 0)
    shard_count = int(payload.get("shard_count") or 3)
    batch_no = payload.get("batch_no")
    try:
        items = service.plan_manifests(task_id, shard_count, str(batch_no) if batch_no else None)
    except ExportServiceError as error:
        _raise_if_error(error)
    return ActionResult(ok=True, message=f"已规划 {len(items)} 个分片", entry={"items": items})


@router.post("/manifests/batch")
def submit_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """整组导出：同一批次多条清单统一提交，整组结果逐条记录。"""
    try:
        return service.submit_batch(payload)
    except ExportServiceError as error:
        _raise_if_error(error)


@router.get("/manifests/{manifest_id}")
def get_manifest(manifest_id: int) -> dict[str, Any]:
    manifest = service.get_manifest(manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"分片清单 {manifest_id} 不存在")
    return manifest


@router.post("/manifests/{manifest_id}/complete")
def complete_shard(manifest_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("file_metadata") or payload
    expected_version = payload.get("expected_version")
    try:
        item = service.complete_shard(
            manifest_id, metadata, int(expected_version) if expected_version is not None else None)
    except ExportServiceError as error:
        _raise_if_error(error)
    return {"ok": True, "message": "分片完成标记与文件元数据已同时落库", "item": item}


@router.post("/manifests/{manifest_id}/fail")
def fail_shard(manifest_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    expected_version = payload.get("expected_version")
    try:
        item = service.fail_shard(
            manifest_id, str(payload.get("error_message") or "分片执行失败"),
            int(expected_version) if expected_version is not None else None)
    except ExportServiceError as error:
        _raise_if_error(error)
    return {"ok": True, "message": "失败已单独记录，不影响其余分片", "item": item}


@router.post("/manifests/{manifest_id}/cancel")
def cancel_shard(manifest_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    policy = str(payload.get("policy") or POLICY_KEEP)
    if policy not in {POLICY_KEEP, POLICY_RETRY_BATCH}:
        raise HTTPException(status_code=400, detail="policy 只能是 keep_success 或 retry_batch")
    expected_version = payload.get("expected_version")
    try:
        result = service.cancel_shard(
            manifest_id, policy, int(expected_version) if expected_version is not None else None)
    except ExportServiceError as error:
        _raise_if_error(error)
    result["ok"] = True
    return result


@router.post("/tasks/{task_id}/retry")
def retry_batch(task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    batch_no = str(payload.get("batch_no") or "").strip()
    if not batch_no:
        raise HTTPException(status_code=400, detail="batch_no 不能为空")
    try:
        return service.retry_batch(task_id, batch_no)
    except ExportServiceError as error:
        _raise_if_error(error)


@router.get("/manifests/{manifest_id}/download")
def download_shard(manifest_id: int) -> StreamingResponse:
    try:
        metadata = service.download_shard(manifest_id)
    except ExportServiceError as error:
        _raise_if_error(error)
    payload = {
        "manifest_id": manifest_id,
        "file_metadata": metadata,
        "hint": "演示环境返回文件元数据清单，真实环境这里返回对象存储文件流",
    }
    buf = io.StringIO()
    json.dump(payload, buf, ensure_ascii=False, indent=2)
    buf.seek(0)
    filename = metadata["file_name"]
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.post("/tasks/{task_id}/reconcile")
def reconcile_signals(task_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """V014 跨模块联动：异常检测读取清单信号编号，逐分片核对告警范围。"""
    batch_no = (payload or {}).get("batch_no")
    try:
        return service.reconcile_with_signals(task_id, str(batch_no) if batch_no else None)
    except ExportServiceError as error:
        _raise_if_error(error)


# ------------------------------------------------------------ 任务侧（保持兼容）
@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="导出任务已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
