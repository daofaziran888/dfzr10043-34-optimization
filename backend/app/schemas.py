"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class ClaimPayload(BaseModel):
    """归属请求：执行编号必填；数据项缺省时按该执行已领取/缺失范围处理。"""

    execution: str
    items: list[str] = Field(default_factory=list)
    source: str | None = None
    checksum: str | None = None
    reason: str | None = None


class HandoffScopePayload(BaseModel):
    execution: str
    handoff_code: str | None = None
    target_system: str = "datalake_downstream"
    items: list[str] = Field(default_factory=list)
    pack_id: int | None = None
    reason: str | None = None


class HandoffActionPayload(BaseModel):
    """继续/确认交接只需要补充说明；执行与范围由交接记录自身决定。"""

    reason: str | None = None
