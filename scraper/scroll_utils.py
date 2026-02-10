"""
Shared Playwright scrolling utilities to reduce duplicate wheel/height loops.
"""

from typing import Optional

def scroll_until_stable(
    page,
    max_loops: int = 15,
    wait_ms: int = 1200,
    wheel_px: int = 2800,
    stability_threshold: int = 3,
    on_loop=None,
    expected_comment_count: Optional[int] = None,
    min_rounds_override: Optional[int] = None,
) -> dict:
    """
    Scrolls the page downward until scrollHeight stops growing or loop limit is reached.
    - on_loop: optional callable loop_idx -> bool; return True to break early.
    """
    def _min_rounds() -> int:
        if min_rounds_override is not None:
            return min(min_rounds_override, max_loops)
        min_rounds = 6
        if expected_comment_count is not None:
            if expected_comment_count >= 300:
                min_rounds = 16
            elif expected_comment_count >= 150:
                min_rounds = 10
        return min(min_rounds, max_loops)

    min_rounds = _min_rounds()
    stable_rounds = 0
    last_height = 0
    stop_reason = None
    loops_done = 0
    notice_seen = False

    for loop_idx in range(max_loops):
        page.mouse.wheel(0, wheel_px)
        page.wait_for_timeout(wait_ms)
        loops_done = loop_idx + 1

        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_height = height

        if on_loop:
            result = on_loop(loop_idx)
            should_stop = False
            reason = None
            if isinstance(result, dict):
                should_stop = bool(result.get("stop"))
                reason = result.get("reason")
                notice_seen = notice_seen or bool(result.get("unavailable_notice_seen"))
            elif isinstance(result, (tuple, list)) and len(result) >= 1:
                should_stop = bool(result[0])
                reason = result[1] if len(result) > 1 else None
            elif isinstance(result, bool):
                should_stop = result
            if should_stop and loops_done >= min_rounds:
                stop_reason = reason or "callback_stop"
                break

        # Only treat stability as terminal when the "unavailable replies" notice is observed.
        if notice_seen and stable_rounds >= stability_threshold and loops_done >= min_rounds:
            stop_reason = "stable_rounds_with_notice"
            break
    if stop_reason is None and loops_done >= max_loops:
        stop_reason = "max_loops_reached"
    return {
        "loops": loops_done,
        "stable_rounds": stable_rounds,
        "stop_reason": stop_reason,
        "min_rounds": min_rounds,
        "notice_seen": notice_seen,
    }
