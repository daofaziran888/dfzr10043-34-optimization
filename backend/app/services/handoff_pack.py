"""交接包服务：批次打包、清单校验与下游交接。

把已确认批次连同字段映射、校验摘要和清单打包成交接包，
供下游数仓直接消费并回溯来源。交接范围来自归属台账中该执行的
实际确认范围：撤回只收窄发起撤回的执行，其他执行仍可正常交接。
退回后批次不得再次交接。
"""
from __future__ import annotations

from typing import Any

from app.scope import ScopeError, scope_ledger
from app.services.base import BaseModuleService


class HandoffPackService(BaseModuleService):
    MODULE = "handoff_pack"
    ENTITY = "交接包"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "交接编号", "关联批次", "目标系统", "清单文件数", "记录行数",
        "校验摘要", "交接版本", "交接时间", "执行编号", "交接数据项",
        "交接状态", "挂起原因",
    ]
    REQUIRED_FIELDS = ["交接编号", "关联批次", "目标系统"]
    STATUS_ORDER = ["待打包", "打包中", "已交接", "交接退回"]
    ACTION_RULES = {"启动打包": "打包中", "确认交接": "已交接", "记录退回": "交接退回"}
    NEGATIVE_ACTIONS = []

    def reject_handoff(self, pack_id: int, reason: str) -> dict[str, Any]:
        """驳回交接包，退回后批次不得进入交接。"""
        entry = self.get_entry(pack_id)
        if entry is None:
            return {"ok": False, "reason": "交接包不存在"}
        self.run_action(pack_id, "记录退回")
        return {"ok": True, "pack_id": pack_id, "reason": reason, "action": "交接退回"}

    def get_delivered_packs(self, target_system: str | None = None) -> list[dict[str, Any]]:
        """获取已交接的包列表，可按目标系统筛选。"""
        items, _ = self.list_entries(size=10000)
        delivered = [it for it in items if it.get("status") == "已交接"]
        if target_system:
            delivered = [it for it in delivered if it.get("目标系统") == target_system]
        return delivered

    # ── 执行维度交接（共享回执下各交各的） ──────────────────
    def _batch_code(self, batch_id: int) -> str:
        from app.services import service_for
        batch_svc = service_for("capture_batch")
        entry = batch_svc.get_entry(batch_id) if batch_svc else None
        if entry is None:
            raise ScopeError(f"采集批次 {batch_id} 不存在")
        return str(entry.get("批次编号", ""))

    def open_handoff(self, batch_id: int, execution: str, handoff_code: str,
                     target_system: str, *, items: list[str] | None = None,
                     pack_id: int | None = None,
                     reason: str = "发起交接") -> dict[str, Any]:
        """登记一次执行的交接请求，范围取该执行当前确认归属。

        pack_id 传入时同步建好交接包行；不传则只登记交接范围。
        """
        code = self._batch_code(batch_id)
        record = scope_ledger.open_handoff(
            code, execution, handoff_code, target_system, items=items)
        if pack_id is not None:
            self._sync_pack(pack_id, execution, record)
        self._audit(code, execution, handoff_code, "交接发起", reason)
        return record

    def suspend_handoffs(self, batch_id: int, execution: str,
                         items: list[str], *, reason: str = "") -> list[dict[str, Any]]:
        """撤回归属时由批次服务回调：挂起本执行受影响的交接包。"""
        code = self._batch_code(batch_id)
        suspended = scope_ledger.suspend_handoffs_for_withdrawal(
            code, execution, items, reason=reason)
        for row in suspended:
            pack = self._pack_by_code(row["交接编号"])
            if pack is not None:
                self._sync_pack(int(pack["id"]), execution, row)
        return suspended

    def resume_handoff(self, handoff_code: str) -> dict[str, Any]:
        """继续被挂起的原交接请求：按撤回后的剩余归属收敛范围。"""
        record = scope_ledger.resume_handoff(handoff_code)
        pack = self._pack_by_code(handoff_code)
        if pack is not None:
            self._sync_pack(int(pack["id"]), record["执行编号"], record)
        return record

    def confirm_handoff(self, handoff_code: str) -> dict[str, Any]:
        """确认交接：只允许交出发起执行当前仍然有效的归属范围。"""
        result = scope_ledger.confirm_handoff(handoff_code)
        record = result["record"]
        pack = self._pack_by_code(handoff_code)
        if pack is not None:
            self._sync_pack(int(pack["id"]), record["执行编号"], record)
            self.run_action(int(pack["id"]), "确认交接")
        return result

    def handoff_scope(self, handoff_code: str) -> dict[str, Any]:
        return scope_ledger.handoff_scope(handoff_code)

    def delivered_scope(self, execution: str) -> list[dict[str, Any]]:
        return scope_ledger.delivered_scope(execution)

    def _pack_by_code(self, handoff_code: str) -> dict[str, Any] | None:
        items, _ = self.list_entries(size=10000)
        for it in items:
            if it.get("交接编号") == handoff_code:
                return it
        return None

    def _sync_pack(self, pack_id: int, execution: str,
                   record: dict[str, Any]) -> None:
        pack = self.get_entry(pack_id)
        if pack is None:
            return
        scope = list(record.get("交接范围", []))
        pack["执行编号"] = execution
        pack["交接数据项"] = ",".join(scope)
        pack["清单文件数"] = str(len(scope))
        pack["记录行数"] = str(len(scope))
        status = record.get("状态", "")
        pack["交接状态"] = status
        pack["挂起原因"] = record.get("挂起原因", "")
        if status == "已挂起":
            pack["status"] = "打包中"
            pack["abnormal"] = True
            pack["pending"] = True
        elif status == "已交接":
            pack["status"] = "已交接"
            pack["pending"] = False

    def _audit(self, batch_code: str, execution: str,
               handoff_code: str, action: str, reason: str) -> None:
        from app.services import service_for
        audit_svc = service_for("audit_trail")
        if audit_svc is None:
            return
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        audit_svc.create_entry({
            "条目编号": f"AUDI-{handoff_code}-{action}",
            "操作对象": f"交接包.{handoff_code}",
            "操作类型": action,
            "操作者标识": execution,
            "操作时间": now,
            "变更摘要": f"{batch_code} 执行{execution} {action}：{reason}",
            "关联执行": execution,
            "追溯标记": f"handoff-{handoff_code}",
        })
