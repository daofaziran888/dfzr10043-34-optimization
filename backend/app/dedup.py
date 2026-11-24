"""领域专用数据处理工具：去重、冲突裁决、校验、水位推进、过滤与聚合。

这些函数由 pipeline.py 和 services/*.py 中的业务方法调用，实现真实的数据处理行为，
包括：脏行过滤、按采集窗口判重、冲突版本仲裁、校验值计算、水位推进等。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def fingerprint_row(row: dict[str, Any], fields: list[str]) -> str:
    """为一行采集数据计算指纹（组合字段的判定性校验值）。"""
    payload = "|".join(str(row.get(f, "")) for f in fields)
    return sha256_hex(payload)


def is_dirty_row(row: dict[str, Any], required_fields: list[str]) -> bool:
    """判定是否脏行：必填字段为空或全部为同一占位值。"""
    values = [str(row.get(f, "")).strip() for f in required_fields]
    if any(v == "" for v in values):
        return True
    if len(set(values)) == 1:
        return True
    return False


def dedup_by_window(rows: list[dict[str, Any]], key_fields: list[str],
                    window_days: int = 1) -> list[dict[str, Any]]:
    """按采集窗口去重：同一键在相邻窗口内的重复行只保留第一条。

    窗口以数据自身的时间戳为基准，而不是以“当前时间”为基准。这样历史批次
    回放、补采和跨执行共享来源时，旧数据不会被整体当作过期数据丢掉，只有
    真正落在同一个窗口内的重复行才会被折叠。
    """
    seen: dict[str, list[datetime]] = {}
    result = []
    tz = timezone(timedelta(hours=8))

    def _parse(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed

    for row in rows:
        key = "|".join(str(row.get(f, "")) for f in key_fields)
        gen_time = row.get("生成时间", "") or row.get("入队时间", "") or ""
        row_dt = _parse(gen_time)
        previous = seen.get(key, [])
        if row_dt is None:
            # 时间不可解析时按“未知窗口”处理：同键只保留第一条，避免脏数据放大。
            if previous:
                continue
        elif any(abs((row_dt - prev).days) <= window_days for prev in previous):
            continue
        result.append(row)
        seen.setdefault(key, []).append(row_dt)  # type: ignore[arg-type]
    return result


def conflict_resolve_rollback(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """冲突版本仲裁：回滚场景下旧版本胜出，返回应采用的版本。"""
    cur_version = str(current.get("版本号", "0"))
    prev_version = str(previous.get("版本号", "0"))
    cur_rollback = current.get("回滚版本", "")
    prev_rollback = previous.get("回滚版本", "")
    # 回滚场景：当前版本有回滚目标 或 上一版本标记了回滚 → 旧版本胜出
    if cur_rollback or prev_rollback:
        return previous
    try:
        if float(prev_version.replace("v", "")) > float(cur_version.replace("v", "")):
            return previous
    except (ValueError, TypeError):
        pass
    return current


def compute_checksum(payload: str) -> str:
    return sha256_hex(payload)


class Watermark:
    """落库水位：记录最后完成的写入时间戳，用于增量消费。"""

    def __init__(self) -> None:
        self._value: int = 0

    @property
    def value(self) -> int:
        return self._value

    def advance(self, ts: int) -> bool:
        if ts > self._value:
            self._value = ts
            return True
        return False

    def reset(self) -> None:
        self._value = 0


_global_watermark = Watermark()


def get_watermark() -> Watermark:
    return _global_watermark


def is_late_arrival(schedule_end_date: str, now_iso: str | None = None) -> bool:
    """判断排程数据是否为迟到数据（生效截止日期已过）。"""
    if not schedule_end_date:
        return False
    if now_iso is None:
        now_iso = datetime.now(timezone(timedelta(hours=8))).isoformat()[:10]
    return schedule_end_date < now_iso


def validate_field_value(value: str, rule: str) -> tuple[bool, str]:
    """按校验规则验证表单字段值。返回(是否通过, 原因)。"""
    if not value or not value.strip():
        return False, "字段值为空"
    if "required" in rule.lower() and not value.strip():
        return False, "必填字段缺失"
    if "numeric" in rule.lower():
        try:
            float(value)
        except ValueError:
            return False, "字段值不是有效数字"
    if "length>" in rule:
        try:
            min_len = int(re.search(r"length>(\d+)", rule).group(1))
            if len(value) < min_len:
                return False, f"字段值长度不足{min_len}"
        except (ValueError, AttributeError):
            pass
    return True, "校验通过"


def compute_confidence_score(matches: int, total_attempts: int) -> float:
    """计算OCR识别置信度得分。"""
    if total_attempts <= 0:
        return 0.0
    return round(matches / total_attempts, 4)


def exhaust_resource(current_usage: int, total_quota: int) -> bool:
    """判断资源是否耗尽。"""
    return current_usage >= total_quota


def sample_pass_rate(passed: int, sampled: int) -> float:
    if sampled <= 0:
        return 0.0
    return round(passed / sampled, 4)