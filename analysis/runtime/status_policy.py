from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AttemptContext:
    attempt_count: int = 0
    reask_count: int = 0
    downgrade_count: int = 0
    last_error_type: Optional[str] = None
    last_action: Optional[str] = None
    retry_history: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Action:
    name: str
    reason: Optional[str] = None


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, window_sec: int = 300, open_sec: int = 600) -> None:
        self.threshold = threshold
        self.window_sec = window_sec
        self.open_sec = open_sec
        self._events: Dict[str, List[float]] = {}
        self._state: Dict[str, Dict[str, Any]] = {}

    def _prune(self, key: str, now: float) -> None:
        events = self._events.get(key) or []
        cutoff = now - self.window_sec
        self._events[key] = [ts for ts in events if ts >= cutoff]

    def get_state(self, key: str) -> str:
        now = time.time()
        state = (self._state.get(key) or {}).get("state") or "closed"
        if state == "open":
            opened_at = (self._state.get(key) or {}).get("opened_at") or 0
            if now - opened_at >= self.open_sec:
                self._state[key] = {"state": "half_open", "opened_at": opened_at}
                return "half_open"
            return "open"
        return state

    def record_failure(self, key: str, *, error_type: str) -> None:
        if error_type not in {"timeout", "error"}:
            return
        now = time.time()
        self._events.setdefault(key, []).append(now)
        self._prune(key, now)
        if len(self._events.get(key) or []) >= self.threshold:
            self._state[key] = {"state": "open", "opened_at": now}

    def record_success(self, key: str) -> None:
        state = (self._state.get(key) or {}).get("state")
        if state == "half_open":
            self._state[key] = {"state": "closed"}


_breaker = CircuitBreaker()


def circuit_state(mode: str, model_name: Optional[str]) -> str:
    key = f"{mode}:{model_name or 'none'}"
    return _breaker.get_state(key)


def record_llm_failure(mode: str, model_name: Optional[str], *, error_type: str) -> None:
    key = f"{mode}:{model_name or 'none'}"
    _breaker.record_failure(key, error_type=error_type)


def record_llm_success(mode: str, model_name: Optional[str]) -> None:
    key = f"{mode}:{model_name or 'none'}"
    _breaker.record_success(key)


def decide_action(
    *,
    llm_status: str,
    claims_status: str,
    attempt_ctx: AttemptContext,
) -> Action:
    if llm_status == "disabled" and claims_status == "disabled":
        return Action("NOOP")
    if llm_status == "timeout":
        if attempt_ctx.downgrade_count < 1:
            return Action("RETRY_WITH_DOWNGRADE", reason="llm_timeout")
        return Action("ABORT", reason="llm_timeout")
    if llm_status == "error":
        return Action("ABORT", reason="llm_error")
    if llm_status == "ok":
        if claims_status in {"fail_no_claims", "fail_parse"}:
            if attempt_ctx.reask_count < 1:
                return Action("REASK_JSON_ONLY", reason=claims_status)
        return Action("FINALIZE")
    return Action("FINALIZE")


def reset_circuit() -> None:
    _breaker._events.clear()
    _breaker._state.clear()
