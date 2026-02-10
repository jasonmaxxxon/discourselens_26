from typing import Any, Dict


def sanitize_analysis_json(analysis_json: Any) -> Any:
    if not isinstance(analysis_json, dict):
        return analysis_json
    sanitized: Dict[str, Any] = dict(analysis_json)
    sanitized.pop("axis_signals", None)
    meta = sanitized.get("meta")
    if isinstance(meta, dict):
        meta = dict(meta)
        meta.pop("axis_registry_version", None)
        meta.pop("missing_axes", None)
        sanitized["meta"] = meta
    return sanitized
