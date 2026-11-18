"""V014 数据导出分片清单与异常检测跨模块联动测试。

覆盖：
- 整组导出按分片独立成功/失败/取消，取消一个不中断其余分片；
- 完成标记与文件元数据同事务落库，缺元数据整批回滚；
- 批次重试只补未完成分片，已完成文件不覆盖；
- 单分片取消的两种策略（保留成功 / 整批待重试）；
- 未清残留触发 V014 事务回滚并留档；
- 乐观锁版本冲突；
- 异常检测 V014 读取清单信号编号核对告警范围。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.anomaly_detect import AnomalyDetectService  # noqa: E402
from app.services.data_export import (  # noqa: E402
    POLICY_KEEP,
    POLICY_RETRY_BATCH,
    SHARD_CANCELED,
    SHARD_FAILED,
    SHARD_PENDING,
    SHARD_SUCCESS,
    DataExportService,
    ExportServiceError,
)
from app.store import store  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_runtime_tables():
    store._tables["export_manifest"] = []
    store._tables["v014_transaction_archive"] = []
    if not store.rows("data_export"):
        # 同进程内其他测试可能清空过模块表，这里补一个最小可用任务
        store.rows("data_export").append({
            "id": 1, "任务编号": "DATA-UT-0001", "数据来源": "ut", "导出格式": "csv",
            "status": "草稿", "pending": True, "abnormal": False,
        })
    if not store.rows("anomaly_signal"):
        store.rows("anomaly_signal").extend([
            {"id": 1, "信号编号": "SIG-V014-0001", "规则编号": "ANOM-UT", "信号状态": "生效",
             "告警范围": "ALM-0001~ALM-0100", "信号版本": "V014"},
            {"id": 2, "信号编号": "SIG-V014-0002", "规则编号": "ANOM-UT", "信号状态": "生效",
             "告警范围": "ALM-0101~ALM-0200", "信号版本": "V014"},
        ])
    for row in store.rows("data_export"):
        row["status"] = "草稿"
        row["pending"] = True
        row["abnormal"] = False
    yield


def _meta(name: str) -> dict[str, str]:
    return {"file_name": name, "file_path": f"/data/{name}", "file_size": "1024",
            "checksum": "md5-x", "content_type": "text/csv"}


def _task_id() -> int:
    return int(store.rows("data_export")[0]["id"])


def test_batch_records_each_shard_independently():
    svc = DataExportService()
    result = svc.submit_batch({
        "task_id": _task_id(), "shard_count": 3, "batch_no": "UT-1",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("a.csv")},
            {"shard_index": 1, "result": "failed", "error_message": "分区缺失"},
            {"shard_index": 2, "result": "canceled", "error_message": "用户取消"},
        ],
    })
    assert [i["shard_status"] for i in result["items"]] == [SHARD_SUCCESS, SHARD_FAILED, SHARD_CANCELED]
    assert result["summary"][SHARD_SUCCESS] == 1
    assert store.find("data_export", _task_id())["status"] == "部分失败"
    # 成功分片可下载，失败/取消分片不可下载
    success = next(i for i in result["items"] if i["shard_status"] == SHARD_SUCCESS)
    assert success["downloadable"] is True
    assert all(not i["downloadable"] for i in result["items"] if i["shard_status"] != SHARD_SUCCESS)


def test_missing_file_metadata_rolls_back_whole_batch():
    svc = DataExportService()
    before = len(store.rows("export_manifest"))
    with pytest.raises(ExportServiceError):
        svc.submit_batch({
            "task_id": _task_id(), "shard_count": 2, "batch_no": "UT-2",
            "shards": [
                {"shard_index": 0, "result": "success", "file_metadata": {"file_name": "bad"}},
                {"shard_index": 1, "result": "success", "file_metadata": _meta("ok.csv")},
            ],
        })
    # 事务回滚：没有任何分片落库，清单不会出现无法下载的完成项
    assert len(store.rows("export_manifest")) == before
    assert store.list_archive(), "V014 回滚必须留档"


def test_complete_shard_atomic_with_file_metadata():
    svc = DataExportService()
    planned = svc.plan_manifests(_task_id(), 1, "UT-3")
    manifest_id = planned[0]["id"]
    with pytest.raises(ExportServiceError):
        svc.complete_shard(manifest_id, {"file_name": "only-name"})
    manifest = store.find("export_manifest", manifest_id)
    assert manifest["shard_status"] == SHARD_PENDING and manifest["file_metadata"] is None
    done = svc.complete_shard(manifest_id, _meta("done.csv"))
    assert done["shard_status"] == SHARD_SUCCESS and done["file_name"] == "done.csv"
    # 已完成分片不能被二次覆盖
    with pytest.raises(ExportServiceError):
        svc.complete_shard(manifest_id, _meta("overwrite.csv"))


def test_retry_only_fills_unfinished_shards():
    svc = DataExportService()
    svc.submit_batch({
        "task_id": _task_id(), "shard_count": 3, "batch_no": "UT-4",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("keep.csv")},
            {"shard_index": 1, "result": "failed", "error_message": "超时"},
            {"shard_index": 2, "result": "canceled"},
        ],
    })
    retry = svc.retry_batch(_task_id(), "UT-4")
    kept = next(i for i in retry["items"] if i["shard_status"] == SHARD_SUCCESS)
    assert kept["file_name"] == "keep.csv" and kept["retry_count"] == 0
    reset_rows = [i for i in retry["items"] if i["shard_status"] == SHARD_PENDING]
    assert len(reset_rows) == 2
    assert all(i["retry_count"] == 1 for i in reset_rows)


def test_cancel_one_shard_keep_success_policy():
    svc = DataExportService()
    svc.submit_batch({
        "task_id": _task_id(), "shard_count": 3, "batch_no": "UT-5",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("a.csv")},
            {"shard_index": 1, "result": "success", "file_metadata": _meta("b.csv")},
            {"shard_index": 2, "result": "failed", "error_message": "处理中"},
        ],
    })
    target = [m for m in store.rows("export_manifest") if m["batch_no"] == "UT-5"][2]
    out = svc.cancel_shard(target["id"], POLICY_KEEP)
    assert out["decision"].startswith("保留成功分片")
    assert sum(1 for i in out["items"] if i["shard_status"] == SHARD_SUCCESS) == 2
    assert sum(1 for i in out["items"] if i["shard_status"] == SHARD_CANCELED) == 1


def test_cancel_one_shard_retry_batch_policy_keeps_boundaries():
    svc = DataExportService()
    svc.submit_batch({
        "task_id": _task_id(), "shard_count": 3, "batch_no": "UT-6",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("a.csv")},
            {"shard_index": 1, "result": "success", "file_metadata": _meta("b.csv")},
            {"shard_index": 2, "result": "failed", "error_message": "处理中"},
        ],
    })
    target = [m for m in store.rows("export_manifest") if m["batch_no"] == "UT-6"][2]
    out = svc.cancel_shard(target["id"], POLICY_RETRY_BATCH)
    assert len(out["items"]) == 3  # 原分片边界保留
    assert all(not i["published"] for i in out["items"])  # 暂不发布文件
    assert all(i["shard_status"] == SHARD_PENDING for i in out["items"])  # 整批回到待处理
    assert store.find("data_export", _task_id())["status"] == "待重试"


def test_residue_triggers_v014_rollback_and_archive():
    svc = DataExportService()
    svc.plan_manifests(_task_id(), 2, "UT-7")  # 规划后未统一提交 => 残留
    with pytest.raises(ExportServiceError):
        svc.submit_batch({"task_id": _task_id(), "shard_count": 2,
                          "batch_no": "UT-7", "shards": []})
    residue_archive = [a for a in store.list_archive() if a["result"] == "residue_rollback"]
    assert residue_archive and residue_archive[-1]["spec_version"] == "V014"
    # 回滚后本次拒绝的新清单不落库，仍是规划阶段的 2 条残留
    leftovers = [m for m in store.rows("export_manifest") if m["batch_no"] == "UT-7"]
    assert len(leftovers) == 2 and all(not m["plan_committed"] for m in leftovers)


def test_optimistic_lock_blocks_stale_version():
    svc = DataExportService()
    svc.submit_batch({
        "task_id": _task_id(), "shard_count": 1, "batch_no": "UT-8",
        "shards": [{"shard_index": 0, "result": "failed", "error_message": "x"}],
    })
    manifest_id = store.rows("export_manifest")[0]["id"]
    svc.fail_shard(manifest_id, "再次失败")  # version 已推进
    with pytest.raises(ExportServiceError):
        svc.complete_shard(manifest_id, _meta("late.csv"), expected_version=1)


def test_anomaly_detect_reconciles_manifest_signal_ranges():
    export_svc = DataExportService()
    detect_svc = AnomalyDetectService()
    export_svc.submit_batch({
        "task_id": _task_id(), "shard_count": 2, "batch_no": "UT-9",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("a.csv")},
            {"shard_index": 1, "result": "success", "file_metadata": _meta("b.csv")},
        ],
    })
    # 数据导出侧与异常检测侧沿同一调用链核对，口径一致
    export_view = export_svc.reconcile_with_signals(_task_id(), "UT-9")
    detect_view = detect_svc.reconcile_export_manifests(_task_id(), "UT-9")
    assert export_view["total"] == detect_view["total"] == 2
    assert export_view["matched"] == detect_view["matched"]
    for item in detect_view["items"]:
        assert item["signal_no"].startswith("SIG-V014-")
        assert item["expected_alert_range"] == item["actual_alert_range"]
        assert item["matched"] is True


def test_manifests_visible_in_task_detail_and_timeline():
    svc = DataExportService()
    svc.submit_batch({
        "task_id": _task_id(), "shard_count": 2, "batch_no": "UT-10",
        "shards": [
            {"shard_index": 0, "result": "success", "file_metadata": _meta("a.csv")},
            {"shard_index": 1, "result": "failed", "error_message": "x"},
        ],
    })
    detail = svc.get_entry(_task_id())
    assert len(detail["manifests"]) == 2  # 详情页与工作台读取同一份业务数据
    timeline = next(i for i in svc.get_timeline() if i["id"] == _task_id())
    assert timeline["manifest_count"] == 2
    assert timeline["shard_summary"][SHARD_FAILED] == 1
