"""归集归属台账：共享归集回执下，按执行拆分归属、确认、撤回与交接范围。

一条归集回执（同一份上游来源文件）可以被多个执行共享：
来源数据只有一份，但 A、B 两个执行各自领用的数据项、确认范围、撤回记录
和已交接记录必须彼此独立，互不覆盖。

台账只操作 store 里的私有表，不直接改业务模块行；业务服务
（capture_batch / handoff_pack）负责把台账结果写回模块记录。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.store import store

RECEIPTS = "capture_receipts"
CLAIMS = "capture_claims"
CONFIRMATIONS = "capture_confirmations"
SUPPLEMENTS = "capture_supplements"
HANDOFFS = "handoff_scopes"
DELIVERIES = "handoff_deliveries"


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _clean_items(items: Iterable[Any] | None) -> list[str]:
    """去掉空值并保序去重，避免同一数据项被重复领用。"""
    result: list[str] = []
    for item in items or []:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    return result


def _code(prefix: str, batch_code: str, sequence: int) -> str:
    return f"{prefix}-{batch_code}-{sequence:02d}"


def _unrestored_withdrawn(claim: dict[str, Any]) -> set[str]:
    """已撤回且尚未补采回来的数据项；这些项任何入口都不能带出。"""
    restored = set(claim.get("补采范围", []))
    return set(claim.get("撤回范围", [])) - restored


class ScopeError(ValueError):
    """归属台账的领域错误：调用方要把 reason 原样返回给值班员。"""


class ScopeLedger:
    """按 (回执, 执行) 维度维护归属生命周期。

    状态流转：
      领取 → 确认 →（可多次）收窄 →（可选）补采 → 交付
    收窄只影响发起撤回的执行；同一回执下的其他执行保持原范围。
    """

    # ── 回执 ────────────────────────────────────────────────
    def register_receipt(
        self,
        batch_code: str,
        items: Iterable[Any],
        *,
        source: str = "",
        checksum: str = "",
        receipt_code: str | None = None,
    ) -> dict[str, Any]:
        rows = store.aux_rows(RECEIPTS)
        cleaned = _clean_items(items)
        if not cleaned:
            raise ScopeError("归集回执至少需要一个数据项")
        for row in rows:
            if row.get("批次编号") == batch_code:
                # 同一回执重复登记时只补充数据项，不重写已有确认/撤回。
                row["数据项"] = _clean_items([*row.get("数据项", []), *cleaned])
                if source:
                    row["上游来源"] = source
                if checksum:
                    row["校验摘要"] = checksum
                return row
        row = {
            "id": _next_id(rows),
            "回执编号": receipt_code or f"RCPT-{batch_code}",
            "批次编号": batch_code,
            "上游来源": source,
            "数据项": cleaned,
            "校验摘要": checksum,
            "登记时间": _now_iso(),
        }
        rows.append(row)
        return row

    def get_receipt(self, batch_code: str) -> dict[str, Any] | None:
        for row in store.aux_rows(RECEIPTS):
            if row.get("批次编号") == batch_code:
                return row
        return None

    # ── 归属 ────────────────────────────────────────────────
    def get_claim(self, batch_code: str, execution: str) -> dict[str, Any] | None:
        execution = str(execution).strip()
        for row in store.aux_rows(CLAIMS):
            if row.get("批次编号") == batch_code and row.get("执行编号") == execution:
                return row
        return None

    def claim_scope(
        self,
        batch_code: str,
        execution: str,
        items: Iterable[Any],
        *,
        source: str = "",
        checksum: str = "",
        reason: str = "领用确认",
    ) -> dict[str, Any]:
        """按执行领用回执中的数据项，并把领用范围确认为该执行的归属。

        领用即确认：只有确认过的范围才能发起交接、被撤回或被补采。
        """
        execution = str(execution).strip()
        if not execution:
            raise ScopeError("领用时必须指定执行编号")
        cleaned = _clean_items(items)
        if not cleaned:
            raise ScopeError("领用范围不能为空")
        receipt = self.get_receipt(batch_code)
        if receipt is None:
            receipt = self.register_receipt(
                batch_code, cleaned, source=source, checksum=checksum)
        allowed = set(receipt.get("数据项", []))
        unknown = [item for item in cleaned if item not in allowed]
        if unknown:
            raise ScopeError(f"数据项不在归集回执范围内：{'、'.join(unknown)}")

        existing = self.get_claim(batch_code, execution)
        if existing is None:
            claim = {
                "id": _next_id(store.aux_rows(CLAIMS)),
                "回执编号": receipt["回执编号"],
                "批次编号": batch_code,
                "执行编号": execution,
                "领取范围": cleaned,
                "确认范围": [],
                "撤回范围": [],
                "补采范围": [],
                "状态": "已领取",
                "领取时间": _now_iso(),
                "最近确认": "",
                "确认编号": "",
            }
            store.aux_rows(CLAIMS).append(claim)
            before: list[str] = []
            new_items = list(cleaned)
        else:
            claim = existing
            # 已有归属：只允许扩充未撤回的数据项；已撤回项必须走补采。
            blocked = [
                item for item in cleaned
                if item in _unrestored_withdrawn(claim)]
            if blocked:
                raise ScopeError(
                    f"数据项已撤回，不能再用旧领用带出：{'、'.join(blocked)}；"
                    "请通过补采恢复缺失范围"
                )
            before = list(claim.get("确认范围", []))
            new_items = [item for item in cleaned if item not in before]
            claim["领取范围"] = _clean_items([*claim.get("领取范围", []), *cleaned])
            if not new_items:
                # 幂等重复领用：不重复写确认记录。
                return claim

        claim["确认范围"] = _clean_items([*before, *new_items])
        claim["状态"] = "已确认"
        claim["最近确认"] = _now_iso()
        confirmation = self._write_confirmation(
            batch_code, execution,
            kind="初次确认" if not before else "补充确认",
            before=before, after=claim["确认范围"], withdrawn=[],
            supplemented=[], reason=reason, handoff_code="")
        claim["确认编号"] = confirmation["确认编号"]
        return claim

    def confirm_scope(
        self,
        batch_code: str,
        execution: str,
        items: Iterable[Any] | None = None,
        *,
        kind: str = "初次确认",
        reason: str = "",
        handoff_code: str = "",
    ) -> dict[str, Any]:
        execution = str(execution).strip()
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            raise ScopeError(f"执行 {execution} 尚未领用该回执，不能确认")
        target = _clean_items(items) if items is not None else list(claim.get("领取范围", []))
        if not target:
            raise ScopeError("确认范围不能为空")
        blocked = [item for item in target if item in _unrestored_withdrawn(claim)]
        if blocked:
            raise ScopeError(f"确认范围包含已撤回数据项：{'、'.join(blocked)}")
        allowed = set(claim.get("领取范围", [])) | set(claim.get("补采范围", []))
        unknown = [item for item in target if item not in allowed]
        if unknown:
            raise ScopeError(f"确认范围超出领用范围：{'、'.join(unknown)}")

        before = list(claim.get("确认范围", []))
        claim["确认范围"] = target
        claim["状态"] = "已确认"
        claim["最近确认"] = _now_iso()
        confirmation = self._write_confirmation(
            batch_code, execution, kind=kind, before=before, after=target,
            withdrawn=[], supplemented=[], reason=reason, handoff_code=handoff_code)
        claim["确认编号"] = confirmation["确认编号"]
        return {"claim": claim, "confirmation": confirmation}

    def withdraw_scope(
        self,
        batch_code: str,
        execution: str,
        items: Iterable[Any],
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        execution = str(execution).strip()
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            raise ScopeError(f"执行 {execution} 尚未领用该回执，不能撤回")
        target = _clean_items(items)
        if not target:
            raise ScopeError("撤回范围不能为空")
        confirmed = set(claim.get("确认范围", []))
        invalid = [item for item in target if item not in confirmed]
        if invalid:
            raise ScopeError(f"撤回范围不在该执行的已确认归属内：{'、'.join(invalid)}")

        before = list(claim.get("确认范围", []))
        claim["确认范围"] = [item for item in before if item not in target]
        # 撤回记录只归本执行；同一回执下其他执行不受影响。
        claim["撤回范围"] = _clean_items([*claim.get("撤回范围", []), *target])
        claim["补采范围"] = [
            item for item in claim.get("补采范围", []) if item not in target]
        claim["状态"] = "已收窄"
        claim["最近确认"] = _now_iso()

        confirmation = self._write_confirmation(
            batch_code, execution, kind="收窄确认", before=before,
            after=claim["确认范围"], withdrawn=target, supplemented=[],
            reason=reason, handoff_code="")
        claim["确认编号"] = confirmation["确认编号"]

        return {
            "claim": claim,
            "confirmation": confirmation,
            "withdrawn": target,
            "remaining_scope": list(claim["确认范围"]),
            "missing_scope": self.missing_scope(batch_code, execution)["missing"],
        }

    def claims_for_receipt(self, batch_code: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in store.aux_rows(CLAIMS)
            if row.get("批次编号") == batch_code
        ]

    def missing_scope(self, batch_code: str, execution: str) -> dict[str, Any]:
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            return {"missing": [], "confirmed": [], "claim": None}
        missing = [
            item for item in claim.get("撤回范围", [])
            if item not in claim.get("补采范围", [])
        ]
        return {
            "missing": missing,
            "confirmed": list(claim.get("确认范围", [])),
            "claim": claim,
        }

    def supplement_scope(
        self,
        batch_code: str,
        execution: str,
        items: Iterable[Any] | None = None,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        """只对某个执行实际缺失的部分补采，其他执行的范围不受影响。"""
        execution = str(execution).strip()
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            raise ScopeError(f"执行 {execution} 尚未领用该回执，不能补采")
        missing_info = self.missing_scope(batch_code, execution)["missing"]
        if not missing_info:
            raise ScopeError("该执行没有缺失范围，无需补采")
        target = _clean_items(items) if items is not None else list(missing_info)
        if not target:
            raise ScopeError("补采范围不能为空")
        not_missing = [item for item in target if item not in missing_info]
        if not_missing:
            raise ScopeError(
                f"补采范围不是该执行的实际缺失项：{'、'.join(not_missing)}")
        receipt = self.get_receipt(batch_code)
        allowed = set(receipt.get("数据项", [])) if receipt else set(target)
        unknown = [item for item in target if item not in allowed]
        if unknown:
            raise ScopeError(f"补采数据项不在归集回执内：{'、'.join(unknown)}")

        before = list(claim.get("确认范围", []))
        claim["确认范围"] = _clean_items([*before, *target])
        claim["补采范围"] = _clean_items([*claim.get("补采范围", []), *target])
        # 补采恢复的是缺失项，撤回记录保留；状态回到可交接。
        claim["状态"] = "已确认"
        claim["最近确认"] = _now_iso()
        confirmation = self._write_confirmation(
            batch_code, execution, kind="补采确认", before=before,
            after=claim["确认范围"], withdrawn=[], supplemented=target,
            reason=reason, handoff_code="")
        claim["确认编号"] = confirmation["确认编号"]

        supplement = {
            "id": _next_id(store.aux_rows(SUPPLEMENTS)),
            "补采编号": f"SUPP-{batch_code}-{len(store.aux_rows(SUPPLEMENTS)) + 1:02d}",
            "批次编号": batch_code,
            "回执编号": claim.get("回执编号", ""),
            "执行编号": execution,
            "补采范围": target,
            "补采原因": reason,
            "补采时间": _now_iso(),
            "确认编号": confirmation["确认编号"],
        }
        store.aux_rows(SUPPLEMENTS).append(supplement)
        return {
            "claim": claim,
            "confirmation": confirmation,
            "supplement": supplement,
            "supplemented": target,
            "remaining_missing": self.missing_scope(batch_code, execution)["missing"],
        }

    def active_scope(self, batch_code: str, execution: str) -> list[str]:
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            return []
        missing = set(self.missing_scope(batch_code, execution)["missing"])
        return [item for item in claim.get("确认范围", []) if item not in missing]

    def scope_view(self, batch_code: str, execution: str) -> dict[str, Any]:
        """值班员查询入口：当前范围、缺失范围、历次确认与交接记录。"""
        receipt = self.get_receipt(batch_code)
        claim = self.get_claim(batch_code, execution)
        history = self.history(batch_code, execution)
        handoffs = [
            dict(row) for row in store.aux_rows(HANDOFFS)
            if row.get("批次编号") == batch_code
            and row.get("执行编号") == str(execution).strip()
        ]
        delivered = [
            dict(row) for row in store.aux_rows(DELIVERIES)
            if row.get("批次编号") == batch_code
            and row.get("执行编号") == str(execution).strip()
        ]
        return {
            "回执": dict(receipt) if receipt else None,
            "归属": dict(claim) if claim else None,
            "当前范围": self.active_scope(batch_code, execution),
            "缺失范围": self.missing_scope(batch_code, execution)["missing"],
            "确认记录": history,
            "交接记录": handoffs,
            "已交接": delivered,
        }

    def history(self, batch_code: str | None = None,
                execution: str | None = None) -> list[dict[str, Any]]:
        rows = store.aux_rows(CONFIRMATIONS)
        result = [
            dict(row) for row in rows
            if (batch_code is None or row.get("批次编号") == batch_code)
            and (execution is None or row.get("执行编号") == str(execution).strip())
        ]
        return result

    def _write_confirmation(
        self,
        batch_code: str,
        execution: str,
        *,
        kind: str,
        before: list[str],
        after: list[str],
        withdrawn: list[str],
        supplemented: list[str],
        reason: str,
        handoff_code: str,
    ) -> dict[str, Any]:
        rows = store.aux_rows(CONFIRMATIONS)
        sequence = sum(1 for row in rows if row.get("批次编号") == batch_code) + 1
        row = {
            "id": _next_id(rows),
            "确认编号": _code("CONF", batch_code, sequence),
            "批次编号": batch_code,
            "执行编号": str(execution).strip(),
            "确认类型": kind,
            "确认前范围": list(before),
            "确认后范围": list(after),
            "撤回项": list(withdrawn),
            "补采项": list(supplemented),
            "收窄说明": reason,
            "关联交接": handoff_code,
            "确认时间": _now_iso(),
        }
        rows.append(row)
        return row

    # ── 交接范围 ────────────────────────────────────────────
    def open_handoff(
        self,
        batch_code: str,
        execution: str,
        handoff_code: str,
        target_system: str,
        *,
        items: Iterable[Any] | None = None,
    ) -> dict[str, Any]:
        execution = str(execution).strip()
        if self.handoff_record(handoff_code) is not None:
            raise ScopeError(f"交接 {handoff_code} 已存在，请继续原交接请求")
        claim = self.get_claim(batch_code, execution)
        if claim is None:
            raise ScopeError(f"执行 {execution} 尚未领用该回执，不能发起交接")
        active = self.active_scope(batch_code, execution)
        if not active:
            raise ScopeError("该执行当前没有可交接范围")
        delivered = self.delivered_items(batch_code, execution)
        # 缺省只带出尚未交接的当前归属；已交接项不会重复进入新交接。
        default_scope = [item for item in active if item not in delivered]
        if not default_scope:
            raise ScopeError("该执行的当前归属都已交接，没有新的可交接范围")
        requested = _clean_items(items) if items is not None else default_scope
        withdrawn = set(self.missing_scope(batch_code, execution)["missing"])
        blocked = [item for item in requested if item in withdrawn]
        if blocked:
            raise ScopeError(
                f"交接范围包含已撤回数据项：{'、'.join(blocked)}，不能带出")
        outside = [item for item in requested if item not in active]
        if outside:
            raise ScopeError(f"交接范围超出当前确认归属：{'、'.join(outside)}")
        repeat = [item for item in requested if item in delivered]
        if repeat:
            raise ScopeError(f"数据项已经交接，不能重复带出：{'、'.join(repeat)}")
        row = {
            "id": _next_id(store.aux_rows(HANDOFFS)),
            "交接编号": handoff_code,
            "批次编号": batch_code,
            "执行编号": execution,
            "目标系统": target_system,
            "交接范围": requested,
            "状态": "打包中",
            "挂起原因": "",
            "挂起时间": "",
            "恢复次数": 0,
            "交接时间": "",
        }
        store.aux_rows(HANDOFFS).append(row)
        return row

    def delivered_items(self, batch_code: str, execution: str) -> list[str]:
        items: list[str] = []
        for row in store.aux_rows(DELIVERIES):
            if row.get("批次编号") == batch_code \
                    and row.get("执行编号") == str(execution).strip():
                items.extend(row.get("交接范围", []))
        return _clean_items(items)

    def handoff_record(self, handoff_code: str) -> dict[str, Any] | None:
        for row in store.aux_rows(HANDOFFS):
            if row.get("交接编号") == handoff_code:
                return row
        return None

    def suspend_handoffs_for_withdrawal(
        self,
        batch_code: str,
        execution: str,
        withdrawn: Iterable[Any],
        *,
        reason: str = "",
    ) -> list[dict[str, Any]]:
        """撤回归属时挂起本执行已领取、且包含被撤回项的交接。"""
        target = set(_clean_items(withdrawn))
        suspended: list[dict[str, Any]] = []
        for row in store.aux_rows(HANDOFFS):
            if row.get("批次编号") != batch_code:
                continue
            if row.get("执行编号") != str(execution).strip():
                continue
            if row.get("状态") not in ("待打包", "打包中"):
                continue
            if not target.intersection(row.get("交接范围", [])):
                continue
            row["状态"] = "已挂起"
            row["挂起原因"] = reason or "归属撤回，交接范围需要重新确认"
            row["挂起时间"] = _now_iso()
            # 挂起时先把被撤回项从待交接范围剔出，避免任何入口带出。
            row["挂起前范围"] = list(row.get("交接范围", []))
            row["交接范围"] = [
                item for item in row.get("交接范围", []) if item not in target]
            row["本次撤回项"] = sorted(target)
            suspended.append(dict(row))
        return suspended

    def resume_handoff(self, handoff_code: str) -> dict[str, Any]:
        record = self.handoff_record(handoff_code)
        if record is None:
            raise ScopeError(f"交接 {handoff_code} 不存在")
        if record.get("状态") != "已挂起":
            raise ScopeError(f"交接 {handoff_code} 当前状态为{record.get('状态')}，无需继续")
        active = self.active_scope(record["批次编号"], record["执行编号"])
        if not active:
            raise ScopeError("撤回后该执行已无剩余可交接范围")
        delivered = self.delivered_items(record["批次编号"], record["执行编号"])
        remaining = [item for item in active if item not in delivered]
        if not remaining:
            raise ScopeError("撤回后该执行已无可交接范围（其余项目已交接）")
        record["交接范围"] = remaining
        record["状态"] = "打包中"
        record["恢复次数"] = int(record.get("恢复次数", 0)) + 1
        record["挂起原因"] = ""
        return record

    def confirm_handoff(self, handoff_code: str) -> dict[str, Any]:
        record = self.handoff_record(handoff_code)
        if record is None:
            raise ScopeError(f"交接 {handoff_code} 不存在")
        if record.get("状态") not in ("打包中", "待打包"):
            raise ScopeError(f"交接 {handoff_code} 当前状态为{record.get('状态')}，不能确认")
        batch_code = record["批次编号"]
        execution = record["执行编号"]
        scope = list(record.get("交接范围", []))
        missing = set(self.missing_scope(batch_code, execution)["missing"])
        blocked = [item for item in scope if item in missing]
        if blocked:
            raise ScopeError(
                f"交接范围包含已撤回数据项：{'、'.join(blocked)}，"
                "请先继续原交接请求收敛范围")
        active = self.active_scope(batch_code, execution)
        if set(scope) != set(active):
            raise ScopeError("交接范围与当前确认归属不一致，请先继续原交接请求")
        delivered = self.delivered_items(batch_code, execution)
        repeat = [item for item in scope if item in delivered]
        if repeat:
            raise ScopeError(f"数据项已经交接，不能重复带出：{'、'.join(repeat)}")
        record["状态"] = "已交接"
        record["交接时间"] = _now_iso()
        delivery = {
            "id": _next_id(store.aux_rows(DELIVERIES)),
            "交接编号": handoff_code,
            "批次编号": batch_code,
            "执行编号": execution,
            "目标系统": record.get("目标系统", ""),
            "交接范围": scope,
            "核对摘要": ",".join(sorted(scope)),
            "交接时间": record["交接时间"],
        }
        store.aux_rows(DELIVERIES).append(delivery)
        return {"record": record, "delivery": delivery}

    def delivered_scope(self, execution: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in store.aux_rows(DELIVERIES)
            if row.get("执行编号") == str(execution).strip()
        ]

    def handoff_scope(self, handoff_code: str) -> dict[str, Any]:
        record = self.handoff_record(handoff_code)
        if record is None:
            raise ScopeError(f"交接 {handoff_code} 不存在")
        deliveries = [
            dict(row) for row in store.aux_rows(DELIVERIES)
            if row.get("交接编号") == handoff_code
        ]
        return {"record": dict(record), "deliveries": deliveries}


scope_ledger = ScopeLedger()
