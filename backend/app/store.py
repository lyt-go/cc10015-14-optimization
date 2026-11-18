"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    # V014 运行期表：种子数据里没有，但必须在首个事务快照前登记，
    # 否则懒创建的表在事务回滚时会被快照恢复成空表、自增 id 也随之重置。
    RUNTIME_TABLES = ("export_manifest", "v014_transaction_archive")

    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        for name in self.RUNTIME_TABLES:
            self._tables.setdefault(name, [])
        self._versions: dict[str, int] = {name: 1 for name in self._tables}
        self._created_at: dict[str, float] = {name: time.time() for name in self._tables}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        for row in self.rows(module):
            if row.get(field) == value:
                return row
        return None

    def filter_by_field(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        return [row for row in self.rows(module) if row.get(field) == value]

    def next_id(self, module: str) -> int:
        rows = self.rows(module)
        return max((int(r.get("id", 0)) for r in rows), default=0) + 1

    def bump_version(self, module: str) -> int:
        self._versions[module] = self._versions.get(module, 0) + 1
        return self._versions[module]

    def version(self, module: str) -> int:
        return self._versions.get(module, 1)

    @contextmanager
    def transaction(self, modules: list[str]) -> Iterator["Transaction"]:
        """V014 事务边界：对涉及的表做快照，异常时整体回滚并留档。

        内存仓库没有真正的数据库事务，这里用深拷贝快照模拟：
        - 正常退出时提交快照之后的改动；
        - 抛出异常时把表恢复成进入事务前的状态，再把失败现场写入
          ``v014_transaction_archive`` 留档表，最后原样抛回异常。
        """
        tx = Transaction(self, modules)
        try:
            yield tx
        except Exception:
            tx.rollback()
            # 回滚本身留档（残留类业务留档由业务层另行写入，二者互补）
            self.archive_v014({
                "archive_no": f"V014-RB-{int(time.time() * 1000)}",
                "spec_version": "V014",
                "modules": ",".join(modules),
                "started_at": tx.started_at,
                "result": "rolled_back",
                "snapshot_rows": {m: len(rows) for m, rows in tx.snapshots.items()},
                "remark": "事务异常，已按 V014 回滚到进入事务前快照并留档核对",
            })
            raise

    def archive_v014(self, record: dict[str, Any]) -> dict[str, Any]:
        """写入一条 V014 留档记录；留档表本身不参与业务事务回滚。"""
        table = self._tables.setdefault("v014_transaction_archive", [])
        record = dict(record)
        record.setdefault("id", self.next_id("v014_transaction_archive"))
        record.setdefault("archived_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        table.append(record)
        return record

    def list_archive(self) -> list[dict[str, Any]]:
        return self.rows("v014_transaction_archive")

    # 内部表：清单/留档是数据导出的运行期表，信号表是异常检测的数据源，
    # 都不是导航栏里的业务模块，不进运营概览统计。
    NON_BUSINESS_TABLES = ("export_manifest", "v014_transaction_archive", "anomaly_signal")

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            if name in self.NON_BUSINESS_TABLES:
                continue
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


class Transaction:
    """单事务内的表快照与版本登记，配合 Store.transaction 使用。"""

    def __init__(self, store: "Store", modules: list[str]) -> None:
        self.store = store
        self.modules = list(dict.fromkeys(modules))
        self.snapshots: dict[str, list[dict[str, Any]]] = {
            module: copy.deepcopy(store.rows(module)) for module in self.modules
        }
        self.version_snapshots = {
            module: store._versions.get(module, 1) for module in self.modules
        }
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.committed = False

    def commit(self) -> None:
        self.committed = True
        for module in self.modules:
            self.store.bump_version(module)

    def rollback(self) -> None:
        if self.committed:
            return
        for module in self.modules:
            self.store._tables[module] = copy.deepcopy(self.snapshots[module])
            self.store._versions[module] = self.version_snapshots[module]


store = Store()
