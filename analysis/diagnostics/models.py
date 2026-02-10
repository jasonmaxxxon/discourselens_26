from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ISDLabelItem(BaseModel):
    run: int = Field(..., description="Generation index")
    raw: Optional[str] = Field(default=None, description="Raw LLM output (bounded length)")
    parsed: Dict[str, Any] = Field(default_factory=dict)
    valid: bool = False
    errors: List[str] = Field(default_factory=list)
    label: Optional[str] = None
    one_liner: Optional[str] = None
    label_confidence: Optional[float] = None
    evidence_ids: List[str] = Field(default_factory=list)

    class Config:
        extra = "allow"


class ISDReport(BaseModel):
    id: str
    post_id: int
    cluster_key: int
    run_id: str
    verdict: str
    k: int
    labels: List[ISDLabelItem] = Field(default_factory=list)
    stability_avg: Optional[float] = None
    stability_min: Optional[float] = None
    drift_avg: Optional[float] = None
    drift_max: Optional[float] = None
    context_mode: str
    prompt_hash: Optional[str] = None
    model_name: Optional[str] = None

    class Config:
        extra = "allow"
