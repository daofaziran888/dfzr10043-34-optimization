"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    ClaimPayload,
    EntryPayload,
    HandoffActionPayload,
    HandoffScopePayload,
    PageResult,
)
from app.scope import ScopeError
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)


# ── 归集归属：共享回执下按执行独立核对/领用/撤回/补采/交接 ──────────
# 这些固定路径都在 /{module}/{entry_id} 之后注册不会冲突（多一段路径），
# 但统一放在动作接口旁边，便于和通用的登记/动作入口区分。


def _scope_result(callable_, *args, **kwargs) -> dict[str, Any]:
    try:
        result = callable_(*args, **kwargs)
    except ScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "message": "归属操作已完成", "data": result}


@router.get("/capture_batch/{batch_id}/claims")
def list_batch_claims(batch_id: int) -> dict[str, Any]:
    """列出同一回执下所有执行的归属，交接方核对各自接收清单。"""
    service = require_service("capture_batch")
    try:
        claims = service.list_claims(batch_id)
    except ScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "items": claims, "total": len(claims)}


@router.get("/capture_batch/{batch_id}/scope")
def get_batch_scope(batch_id: int, execution: str = Query(...)) -> dict[str, Any]:
    """查询某执行的当前范围、缺失范围、历次确认与交接记录。"""
    service = require_service("capture_batch")
    try:
        view = service.scope_view(batch_id, execution)
    except ScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "data": view}


@router.get("/capture_batch/{batch_id}/confirmations")
def list_batch_confirmations(
    batch_id: int, execution: str | None = Query(default=None),
) -> dict[str, Any]:
    """核对已确认与被收窄的确认记录（只读复盘）。"""
    service = require_service("capture_batch")
    try:
        rows = service.list_confirmations(batch_id, execution)
    except ScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "items": rows, "total": len(rows)}


@router.post("/capture_batch/{batch_id}/claims")
def claim_batch_scope(batch_id: int, payload: ClaimPayload) -> dict[str, Any]:
    service = require_service("capture_batch")
    return _scope_result(
        service.claim_scope, batch_id, payload.execution, payload.items,
        source=payload.source or "", checksum=payload.checksum or "")


@router.post("/capture_batch/{batch_id}/confirmations")
def confirm_batch_scope(batch_id: int, payload: ClaimPayload) -> dict[str, Any]:
    service = require_service("capture_batch")
    return _scope_result(
        service.confirm_scope, batch_id, payload.execution,
        payload.items or None, reason=payload.reason or "")


@router.post("/capture_batch/{batch_id}/withdrawals")
def withdraw_batch_scope(batch_id: int, payload: ClaimPayload) -> dict[str, Any]:
    """撤回某执行的部分归属：只收窄该执行，并挂起其受影响的交接。"""
    service = require_service("capture_batch")
    return _scope_result(
        service.withdraw_scope, batch_id, payload.execution, payload.items,
        reason=payload.reason or "")


@router.post("/capture_batch/{batch_id}/supplements")
def supplement_batch_scope(batch_id: int, payload: ClaimPayload) -> dict[str, Any]:
    """就某执行实际缺失的部分补采，不影响其他执行。"""
    service = require_service("capture_batch")
    return _scope_result(
        service.supplement_scope, batch_id, payload.execution,
        payload.items or None, reason=payload.reason or "")


@router.post("/handoff_pack/{batch_id}/requests")
def open_handoff_request(batch_id: int, payload: HandoffScopePayload) -> dict[str, Any]:
    """按某执行的当前确认归属发起交接；范围随确认结果收敛。"""
    service = require_service("handoff_pack")
    code = payload.handoff_code or f"HAND-{batch_id}-{payload.execution}"
    return _scope_result(
        service.open_handoff, batch_id, payload.execution, code,
        payload.target_system, items=payload.items or None,
        pack_id=payload.pack_id, reason=payload.reason or "发起交接")


@router.post("/handoff_pack/{handoff_code}/resume")
def resume_handoff_request(handoff_code: str, payload: HandoffActionPayload) -> dict[str, Any]:
    """继续被撤回挂起的原交接请求，按剩余归属收敛范围。"""
    service = require_service("handoff_pack")
    return _scope_result(service.resume_handoff, handoff_code)


@router.post("/handoff_pack/{handoff_code}/deliver")
def confirm_handoff_request(handoff_code: str,
                            payload: HandoffActionPayload) -> dict[str, Any]:
    """确认交接：已撤回数据项不能带出。"""
    service = require_service("handoff_pack")
    return _scope_result(service.confirm_handoff, handoff_code)


@router.get("/handoff_pack/{handoff_code}/scope")
def get_handoff_scope(handoff_code: str) -> dict[str, Any]:
    service = require_service("handoff_pack")
    return _scope_result(service.handoff_scope, handoff_code)
