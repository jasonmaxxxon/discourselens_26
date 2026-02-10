from __future__ import annotations

from typing import Dict


CONFIDENCE_CAP_LOW_DATA = 0.4


def apply_presentation_policy(
    raw_risk_level: str,
    confidence: float,
    cap_applied: bool,
) -> Dict[str, str]:
    level = (raw_risk_level or "low").lower()
    conf = float(confidence or 0.0)

    if cap_applied or conf <= CONFIDENCE_CAP_LOW_DATA:
        if level == "high":
            return {"effective_level": "suspected_high", "ui_color": "amber"}
        if level == "med":
            return {"effective_level": "suspected_med", "ui_color": "amber"}
        return {"effective_level": "low", "ui_color": "gray"}

    if level == "high" and conf > 0.7:
        return {"effective_level": "high", "ui_color": "red"}
    if level == "med" and conf > 0.7:
        return {"effective_level": "med", "ui_color": "yellow"}
    if level == "high":
        return {"effective_level": "suspected_high", "ui_color": "amber"}
    if level == "med":
        return {"effective_level": "suspected_med", "ui_color": "amber"}
    if conf > 0.7:
        return {"effective_level": "low", "ui_color": "green"}
    return {"effective_level": "low", "ui_color": "gray"}

