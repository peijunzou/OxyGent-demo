import time
from typing import Dict, List, Optional

from oxygent.schemas import OxyRequest

_DEFAULT_TTL_SECONDS = 30 * 60
_MEMORY: Dict[str, Dict[str, object]] = {}


def _now() -> float:
    return time.time()


def _cleanup() -> None:
    now = _now()
    expired = [key for key, value in _MEMORY.items() if now - value.get("ts", 0) > value.get("ttl", _DEFAULT_TTL_SECONDS)]
    for key in expired:
        _MEMORY.pop(key, None)


def get_memory_key(oxy_request: Optional[OxyRequest]) -> str:
    if not oxy_request:
        return ""
    return (
        oxy_request.group_id
        or oxy_request.from_trace_id
        or oxy_request.current_trace_id
        or oxy_request.request_id
    )


def set_candidates(key: str, candidates: List[str], ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    if not key:
        return
    entry = _MEMORY.get(key, {})
    entry["last_candidates"] = candidates
    entry["ts"] = _now()
    entry["ttl"] = ttl_seconds
    _MEMORY[key] = entry


def get_candidates(key: str) -> Optional[List[str]]:
    if not key:
        return None
    _cleanup()
    entry = _MEMORY.get(key)
    if not entry:
        return None
    return entry.get("last_candidates") or None


def clear_candidates(key: str) -> None:
    if not key:
        return
    entry = _MEMORY.get(key)
    if not entry:
        return
    entry.pop("last_candidates", None)


def set_pending_action(
    key: str, action: str, ids: List[str], ttl_seconds: int = _DEFAULT_TTL_SECONDS
) -> None:
    if not key:
        return
    entry = _MEMORY.get(key, {})
    entry["pending_action"] = {"action": action, "ids": ids}
    entry["ts"] = _now()
    entry["ttl"] = ttl_seconds
    _MEMORY[key] = entry


def get_pending_action(key: str) -> Optional[Dict[str, object]]:
    if not key:
        return None
    _cleanup()
    entry = _MEMORY.get(key)
    if not entry:
        return None
    return entry.get("pending_action")


def clear_pending_action(key: str) -> None:
    if not key:
        return
    entry = _MEMORY.get(key)
    if not entry:
        return
    entry.pop("pending_action", None)


def set_pending_tool_response(key: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
    if not key:
        return
    entry = _MEMORY.get(key, {})
    entry["pending_tool_response"] = True
    entry["ts"] = _now()
    entry["ttl"] = ttl_seconds
    _MEMORY[key] = entry


def has_pending_tool_response(key: str) -> bool:
    if not key:
        return False
    _cleanup()
    entry = _MEMORY.get(key)
    if not entry:
        return False
    return bool(entry.get("pending_tool_response"))


def clear_pending_tool_response(key: str) -> None:
    if not key:
        return
    entry = _MEMORY.get(key)
    if not entry:
        return
    entry.pop("pending_tool_response", None)
