"""采集批次服务：执行产出归集、完整性校验、窗口去重和批次确认。

一次或多次流程执行的产出记录归集成批次，附带采集窗口和
完整性校验值供下游引用。去重按采集窗口+上游来源判重，但判重结果
不再等于“拒绝”：同一上游来源可以被多个执行共享，每个执行在回执下
独立领用、确认、撤回和补采自己的归属（见 app/scope.py）。
"""
from __future__ import annotations

from typing import Any

from app.scope import ScopeError, scope_ledger
from app.services.base import BaseModuleService


class CaptureBatchService(BaseModuleService):
    MODULE = "capture_batch"
    ENTITY = "采集批次"
    OPERATION_TERM = "采集"
    LIST_FIELDS = [
        "批次编号", "关联执行", "记录行数", "采集窗口", "完整性校验",
        "批次标签", "上游来源", "回执编号", "归集数据项", "归属执行",
        "最新确认编号", "生成时间",
    ]
    REQUIRED_FIELDS = ["批次编号", "关联执行", "记录行数"]
    STATUS_ORDER = ["归集中", "校验中", "已确认", "校验失败"]
    ACTION_RULES = {"启动校验": "校验中", "确认批次": "已确认", "标记失败": "校验失败"}
    NEGATIVE_ACTIONS = []

    # ── 去重与共享来源 ─────────────────────────────────────
    def find_duplicate(self, checksum: str, source: str) -> dict[str, Any] | None:
        """按完整性校验值和上游来源查找重复批次。"""
        items, _ = self.list_entries(size=10000)
        for it in items:
            if it.get("完整性校验") == checksum and it.get("上游来源") == source:
                return it
        return None

    def find_by_source(self, source: str,
                       checksum: str | None = None) -> dict[str, Any] | None:
        """按上游来源查找已有回执批次，共享来源时复用同一份回执。

        来源是共享依据：不同执行可以只领用同一来源里的不同数据项，
        所以来源相同即复用回执，数据项由 register_receipt 取并集。
        checksum 参数保留用于调用方显式区分来源修订，默认不参与匹配。
        """
        if not source:
            return None
        items, _ = self.list_entries(size=10000)
        for it in items:
            if it.get("上游来源") != source or it.get("status") == "校验失败":
                continue
            if checksum is not None and it.get("完整性校验") != checksum:
                continue
            return it
        return None

    def get_confirmed_batches(self) -> list[dict[str, Any]]:
        """获取所有已确认的采集批次，供下游交接打包。"""
        items, _ = self.list_entries(size=10000)
        return [it for it in items if it.get("status") == "已确认"]

    def verify_integrity(self, batch_id: int) -> dict[str, Any]:
        """启动校验并返回校验结果。"""
        entry = self.get_entry(batch_id)
        if entry is None:
            return {"ok": False, "reason": "批次不存在"}
        self.run_action(batch_id, "启动校验")
        row_count = int(entry.get("记录行数", "0") or "0")
        if row_count <= 0:
            self.run_action(batch_id, "标记失败")
            return {"ok": False, "reason": "批次记录行数为0，校验失败"}
        self.run_action(batch_id, "确认批次")
        return {"ok": True, "batch_id": batch_id, "row_count": row_count}

    # ── 归属台账（按执行独立） ─────────────────────────────
    def _batch_code(self, batch_id: int) -> str:
        entry = self.get_entry(batch_id)
        if entry is None:
            raise ScopeError(f"采集批次 {batch_id} 不存在")
        return str(entry.get("批次编号", ""))

    def register_receipt(self, batch_id: int, items: list[str], *,
                         source: str = "", checksum: str = "") -> dict[str, Any]:
        entry = self.get_entry(batch_id)
        if entry is None:
            raise ScopeError(f"采集批次 {batch_id} 不存在")
        receipt = scope_ledger.register_receipt(
            self._batch_code(batch_id), items,
            source=source or str(entry.get("上游来源", "")),
            checksum=checksum or str(entry.get("完整性校验", "")),
            receipt_code=f"RCPT-{entry.get('批次编号', '')}",
        )
        self._sync_entry(batch_id, entry)
        return receipt

    def claim_scope(self, batch_id: int, execution: str, items: list[str], *,
                    source: str = "", checksum: str = "",
                    reason: str = "领用确认") -> dict[str, Any]:
        code = self._batch_code(batch_id)
        entry = self.get_entry(batch_id)
        receipt = scope_ledger.get_receipt(code)
        if receipt is None:
            receipt = self.register_receipt(
                batch_id, items, source=source or str(entry.get("上游来源", "")),
                checksum=checksum or str(entry.get("完整性校验", "")))
        claim = scope_ledger.claim_scope(
            code, execution, items, source=receipt.get("上游来源", ""),
            checksum=receipt.get("校验摘要", ""), reason=reason)
        self._sync_entry(batch_id, entry)
        return claim

    def confirm_scope(self, batch_id: int, execution: str,
                      items: list[str] | None = None, *, reason: str = "",
                      kind: str = "初次确认",
                      handoff_code: str = "") -> dict[str, Any]:
        code = self._batch_code(batch_id)
        result = scope_ledger.confirm_scope(
            code, execution, items, kind=kind, reason=reason,
            handoff_code=handoff_code)
        self._sync_entry(batch_id, self.get_entry(batch_id))
        return result

    def withdraw_scope(self, batch_id: int, execution: str, items: list[str],
                       *, reason: str = "") -> dict[str, Any]:
        code = self._batch_code(batch_id)
        result = scope_ledger.withdraw_scope(code, execution, items, reason=reason)
        # 撤回归属会挂起本执行已经领取、且包含被撤回项的交接；
        # 同一回执下其他执行的交接不受影响。
        from app.services import service_for
        handoff_svc = service_for("handoff_pack")
        suspended = []
        if handoff_svc is not None:
            suspended = handoff_svc.suspend_handoffs(
                batch_id, execution, result["withdrawn"], reason=reason)
        result["suspended_handoffs"] = suspended
        self._sync_entry(batch_id, self.get_entry(batch_id))
        return result

    def supplement_scope(self, batch_id: int, execution: str,
                         items: list[str] | None = None, *,
                         reason: str = "") -> dict[str, Any]:
        code = self._batch_code(batch_id)
        result = scope_ledger.supplement_scope(
            code, execution, items, reason=reason)
        self._sync_entry(batch_id, self.get_entry(batch_id))
        return result

    def scope_view(self, batch_id: int, execution: str) -> dict[str, Any]:
        return scope_ledger.scope_view(self._batch_code(batch_id), execution)

    def list_claims(self, batch_id: int) -> list[dict[str, Any]]:
        """列出同一回执下的所有执行归属，交接方可据此核对各自接收清单。"""
        code = self._batch_code(batch_id)
        return scope_ledger.claims_for_receipt(code)

    def list_confirmations(self, batch_id: int,
                           execution: str | None = None) -> list[dict[str, Any]]:
        return scope_ledger.history(self._batch_code(batch_id), execution)

    def _sync_entry(self, batch_id: int, entry: dict[str, Any] | None) -> None:
        """把回执与归属摘要投影到批次行，列表页可直接核对。"""
        if entry is None:
            return
        code = str(entry.get("批次编号", ""))
        receipt = scope_ledger.get_receipt(code)
        if receipt is None:
            return
        claims = scope_ledger.claims_for_receipt(code)
        entry["回执编号"] = receipt.get("回执编号", "")
        entry["归集数据项"] = ",".join(receipt.get("数据项", []))
        entry["归属执行"] = ",".join(
            f"{row.get('执行编号', '')}[{row.get('状态', '')}]" for row in claims)
        entry["最新确认编号"] = next(
            (row.get("确认编号", "") for row in reversed(
                scope_ledger.history(code))), "")
