"""共享归集回执下的归属隔离测试。

场景：A、B 两个执行共享同一份来源 Y，A 另外领用了 X。
A 撤回 X 的归属时，只允许挂起/收窄 A 自己的交接；B 的合法范围
仍然可以独立交接，不需要因为 A 的撤回而重采。
"""
from __future__ import annotations

import pytest

from app.pipeline import collect_files_and_land_data
from app.scope import ScopeError
from app.services import service_for


def _batch_service():
    return service_for("capture_batch")


def _handoff_service():
    return service_for("handoff_pack")


def _shared_batch():
    """建一条 A 领 X、Y，B 领 Y 的共享回执批次。"""
    result = collect_files_and_land_data(
        3,
        [{"文件名称": "source_x.csv"}, {"文件名称": "source_y.csv"}],
        [],
        execution="EXEC-A",
        upstream_source="SRC-SHARED",
        receipt_items=["X", "Y"],
    )
    batch_svc = _batch_service()
    batch_svc.claim_scope(result["batch_id"], "EXEC-B", ["Y"])
    return result, result["batch_id"]


class TestSharedReceiptOwnership:
    def test_shared_source_reuses_one_receipt_without_losing_claims(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        claims = {row["执行编号"]: row for row in batch_svc.list_claims(batch_id)}

        assert set(claims) == {"EXEC-A", "EXEC-B"}
        assert claims["EXEC-A"]["确认范围"] == ["X", "Y"]
        assert claims["EXEC-B"]["确认范围"] == ["Y"]
        # 共享来源只有一份批次，不因 B 再次采集而新建。
        items, total = batch_svc.list_entries(size=1000)
        shared = [it for it in items if it.get("上游来源") == "SRC-SHARED"]
        assert total >= 1
        assert len(shared) == 1
        assert shared[0]["status"] == "已确认"

    def test_b_claim_survives_a_claim_of_same_source(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        view_b = batch_svc.scope_view(batch_id, "EXEC-B")
        assert view_b["当前范围"] == ["Y"]
        assert view_b["归属"]["撤回范围"] == []

    def test_withdraw_narrows_only_the_initiating_execution(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        outcome = batch_svc.withdraw_scope(
            batch_id, "EXEC-A", ["X"], reason="归属更正")

        assert outcome["remaining_scope"] == ["Y"]
        assert outcome["missing_scope"] == ["X"]
        # B 的范围与撤回记录完全不受影响。
        view_b = batch_svc.scope_view(batch_id, "EXEC-B")
        assert view_b["当前范围"] == ["Y"]
        assert view_b["缺失范围"] == []
        assert view_b["归属"]["撤回范围"] == []

    def test_withdraw_requires_item_to_be_confirmed_by_that_execution(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        # B 从未确认 X，不能替 A 撤回；A 也不能撤回不存在的 Z。
        with pytest.raises(ScopeError):
            batch_svc.withdraw_scope(batch_id, "EXEC-B", ["X"])
        with pytest.raises(ScopeError):
            batch_svc.withdraw_scope(batch_id, "EXEC-A", ["Z"])


class TestWithdrawnScopeIsRefused:
    def test_old_claim_cannot_carry_withdrawn_item_again(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        with pytest.raises(ScopeError) as excinfo:
            batch_svc.claim_scope(batch_id, "EXEC-A", ["X"])
        assert "已撤回" in str(excinfo.value)

    def test_handoff_cannot_be_opened_with_withdrawn_item(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        handoff_svc = _handoff_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        with pytest.raises(ScopeError):
            handoff_svc.open_handoff(
                batch_id, "EXEC-A", "HAND-A-BAD", "datalake",
                items=["X", "Y"])

    def test_scope_view_exposes_missing_range_and_confirmation_history(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        view = batch_svc.scope_view(batch_id, "EXEC-A")
        assert view["缺失范围"] == ["X"]
        assert view["当前范围"] == ["Y"]
        kinds = [row["确认类型"] for row in view["确认记录"]]
        assert "初次确认" in kinds
        assert "收窄确认" in kinds
        narrowed = next(row for row in view["确认记录"] if row["确认类型"] == "收窄确认")
        assert narrowed["确认前范围"] == ["X", "Y"]
        assert narrowed["确认后范围"] == ["Y"]
        assert narrowed["撤回项"] == ["X"]
        assert narrowed["收窄说明"] == "归属更正"

    def test_b_can_deliver_y_without_recollecting(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        handoff_svc = _handoff_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        handoff_svc.open_handoff(batch_id, "EXEC-B", "HAND-B", "datalake")
        delivery = handoff_svc.confirm_handoff("HAND-B")["delivery"]
        assert delivery["交接范围"] == ["Y"]
        # B 没有缺失范围，不需要补采，补采会被拒绝。
        with pytest.raises(ScopeError):
            batch_svc.supplement_scope(batch_id, "EXEC-B", ["Y"])


class TestSuspendedHandoffAndResume:
    def test_withdrawal_suspends_only_the_affected_handoff(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        handoff_svc = _handoff_service()
        handoff_svc.open_handoff(batch_id, "EXEC-A", "HAND-A", "datalake")
        handoff_svc.open_handoff(batch_id, "EXEC-B", "HAND-B", "datalake")

        outcome = batch_svc.withdraw_scope(
            batch_id, "EXEC-A", ["X"], reason="归属更正")
        suspended = [row["交接编号"] for row in outcome["suspended_handoffs"]]
        # 归集链路自动发起的 HAND-R3-EXEC-A 和显式登记的 HAND-A 都归 A；
        # B 的 HAND-B 不受影响。
        assert set(suspended) == {"HAND-A", "HAND-R3-EXEC-A"}
        for code in suspended:
            record = handoff_svc.handoff_scope(code)["record"]
            assert record["状态"] == "已挂起"
            assert "X" not in record["交接范围"]
        assert handoff_svc.handoff_scope("HAND-B")["record"]["状态"] == "打包中"

    def test_resume_then_deliver_drops_withdrawn_item(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        handoff_svc = _handoff_service()
        handoff_svc.open_handoff(batch_id, "EXEC-A", "HAND-A", "datalake")
        handoff_svc.open_handoff(batch_id, "EXEC-B", "HAND-B", "datalake")
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        # 挂起状态下不允许直接确认。
        with pytest.raises(ScopeError):
            handoff_svc.confirm_handoff("HAND-A")

        resumed = handoff_svc.resume_handoff("HAND-A")
        assert resumed["交接范围"] == ["Y"]
        assert resumed["恢复次数"] == 1

        delivery_a = handoff_svc.confirm_handoff("HAND-A")["delivery"]
        assert delivery_a["交接范围"] == ["Y"]
        delivery_b = handoff_svc.confirm_handoff("HAND-B")["delivery"]
        assert delivery_b["交接范围"] == ["Y"]

        # A 不能带出 X，B 仍收到 Y。
        assert "X" not in delivery_a["交接范围"]
        assert delivery_b["交接范围"] == ["Y"]

    def test_supplement_restores_only_a_missing_part(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")

        with pytest.raises(ScopeError):
            batch_svc.supplement_scope(batch_id, "EXEC-A", ["Y"])  # Y 未缺失

        outcome = batch_svc.supplement_scope(
            batch_id, "EXEC-A", ["X"], reason="补采上游更正数据")
        assert outcome["supplemented"] == ["X"]
        assert outcome["remaining_missing"] == []
        view = batch_svc.scope_view(batch_id, "EXEC-A")
        assert view["当前范围"] == ["Y", "X"]
        assert [row["确认类型"] for row in view["确认记录"]][-1] == "补采确认"
        # B 不受补采影响。
        assert batch_svc.scope_view(batch_id, "EXEC-B")["当前范围"] == ["Y"]

    def test_supplemented_item_can_be_handed_off_again(self):
        result, batch_id = _shared_batch()
        batch_svc = _batch_service()
        handoff_svc = _handoff_service()
        batch_svc.withdraw_scope(batch_id, "EXEC-A", ["X"], reason="归属更正")
        batch_svc.supplement_scope(batch_id, "EXEC-A", ["X"], reason="补采")

        handoff_svc.open_handoff(
            batch_id, "EXEC-A", "HAND-A-RESTORED", "datalake")
        delivery = handoff_svc.confirm_handoff("HAND-A-RESTORED")["delivery"]
        assert set(delivery["交接范围"]) == {"X", "Y"}


class TestPipelineSharedSource:
    def test_same_source_two_executions_both_land(self):
        first = collect_files_and_land_data(
            3, [{"文件名称": "shared.csv"}], [],
            execution="EXEC-A", upstream_source="SRC")
        second = collect_files_and_land_data(
            3, [{"文件名称": "shared.csv"}], [],
            execution="EXEC-B", upstream_source="SRC")

        assert first["status"] == "landed"
        assert second["status"] == "landed"
        assert first["batch_id"] == second["batch_id"]
        assert second["reused_receipt"] is True
        assert second["confirmed_scope"] == ["shared.csv"]

    def test_identical_replay_by_same_execution_is_duplicate(self):
        first = collect_files_and_land_data(
            3, [{"文件名称": "replay.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-REPLAY")
        second = collect_files_and_land_data(
            3, [{"文件名称": "replay.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-REPLAY")
        assert first["status"] == "landed"
        assert second["status"] == "duplicate"
        assert second["batch_id"] == first["batch_id"]

    def test_landed_handoff_scope_matches_confirmed_scope(self):
        result = collect_files_and_land_data(
            3, [{"文件名称": "scope_x.csv"}, {"文件名称": "scope_y.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-SCOPE",
            receipt_items=["X", "Y"])
        handoff_svc = _handoff_service()
        record = handoff_svc.handoff_scope(result["handoff_code"])["record"]
        assert record["交接范围"] == result["confirmed_scope"] == ["X", "Y"]

    def test_duplicate_source_with_different_execution_is_not_deduped(self):
        """重复判定的键含执行维度：来源相同但执行不同，不是重复批次。"""
        first = collect_files_and_land_data(
            3, [{"文件名称": "distinct.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-SAME")
        second = collect_files_and_land_data(
            3, [{"文件名称": "distinct.csv"}], [],
            execution="EXEC-B", upstream_source="SRC-SAME")
        assert first["status"] == "landed"
        assert second["status"] == "landed"
        assert first["batch_id"] == second["batch_id"]
        batch_svc = _batch_service()
        assert {row["执行编号"] for row in batch_svc.list_claims(first["batch_id"])} == {
            "EXEC-A", "EXEC-B"}

    def test_recollect_after_withdrawal_is_rejected_not_silently_dropped(self):
        first = collect_files_and_land_data(
            3, [{"文件名称": "x.csv"}, {"文件名称": "y.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-W", receipt_items=["X", "Y"])
        _batch_service().withdraw_scope(
            first["batch_id"], "EXEC-A", ["X"], reason="归属更正")

        replay = collect_files_and_land_data(
            3, [{"文件名称": "x.csv"}, {"文件名称": "y.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-W", receipt_items=["X", "Y"])
        # 旧领用不能把撤回项再带出来：给出可读拒绝，而不是当作合法归属落库。
        assert replay["status"] == "rejected"
        assert "已撤回" in replay["reason"]
        assert "X" in _batch_service().scope_view(
            first["batch_id"], "EXEC-A")["缺失范围"]

    def test_shared_receipt_accepts_new_items_from_same_source(self):
        first = collect_files_and_land_data(
            3, [{"文件名称": "y.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-GROW", receipt_items=["Y"])
        second = collect_files_and_land_data(
            3, [{"文件名称": "x.csv"}], [],
            execution="EXEC-B", upstream_source="SRC-GROW", receipt_items=["X"])

        assert second["status"] == "landed"
        assert second["batch_id"] == first["batch_id"]
        assert second["reused_receipt"] is True
        receipt = _batch_service().scope_view(
            first["batch_id"], "EXEC-B")["回执"]
        assert set(receipt["数据项"]) == {"X", "Y"}
        # A 的归属没有因为 B 领用 X 而变化。
        assert _batch_service().scope_view(
            first["batch_id"], "EXEC-A")["当前范围"] == ["Y"]

    def test_new_source_creates_new_batch_without_code_collision(self):
        first = collect_files_and_land_data(
            3, [{"文件名称": "a.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-ONE")
        second = collect_files_and_land_data(
            3, [{"文件名称": "b.csv"}], [],
            execution="EXEC-A", upstream_source="SRC-TWO")
        assert second["status"] == "landed"
        assert second["batch_code"] != first["batch_code"]
        assert second["handoff_code"] != first["handoff_code"]
