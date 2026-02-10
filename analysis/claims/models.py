from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field
from typing_extensions import Literal


ClaimType = Literal["assert", "interpret", "infer", "summarize"]
ClaimScope = Literal["post", "cluster", "cross_cluster"]
ClaimSeverity = Literal["low", "med", "high"]


class Claim(BaseModel):
    claim_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_key: Optional[str] = None
    post_id: int
    cluster_key: Optional[int] = None
    cluster_keys: List[int] = Field(default_factory=list)
    primary_cluster_key: Optional[int] = None
    run_id: str
    claim_type: ClaimType
    scope: ClaimScope
    text: str
    source_agent: str = "analyst"
    evidence_ids: List[str] = Field(default_factory=list)
    evidence_aliases: List[str] = Field(default_factory=list)
    evidence_locator_keys: List[str] = Field(default_factory=list)
    evidence_refs: List[dict] = Field(default_factory=list)
    confidence: Optional[float] = None
    confidence_cap: Optional[float] = None
    tags: Optional[List[str]] = None
    severity: Optional[ClaimSeverity] = None
    status: Optional[Literal["audited", "hypothesis"]] = None
    audit_reason: Optional[str] = None
    missing_evidence_type: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        extra = "allow"


class ClaimPackMeta(BaseModel):
    prompt_hash: Optional[str] = None
    model_name: Optional[str] = None
    build_id: Optional[str] = None
    audit_verdict: Literal["pass", "fail", "partial"] = "fail"
    dropped_claims_count: int = 0
    fail_reasons: List[str] = Field(default_factory=list)

    class Config:
        extra = "allow"


class ClaimPack(BaseModel):
    post_id: int
    run_id: str
    claims: List[Claim] = Field(default_factory=list)
    meta: ClaimPackMeta = Field(default_factory=ClaimPackMeta)

    class Config:
        extra = "allow"
