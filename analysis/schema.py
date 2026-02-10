from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, conint, confloat, validator
from typing import Literal


class Metrics(BaseModel):
    likes: conint(ge=0) = 0
    views: Optional[conint(ge=0)] = None
    replies: Optional[conint(ge=0)] = None


class ToneProfile(BaseModel):
    primary: Optional[str] = None
    cynicism: Optional[confloat(ge=0, le=1)] = None
    hope: Optional[confloat(ge=0, le=1)] = None
    outrage: Optional[confloat(ge=0, le=1)] = None
    notes: Optional[str] = None


class SegmentSample(BaseModel):
    comment_id: Optional[str] = None
    user: Optional[str] = None
    text: str = ""
    likes: Optional[int] = None


class Segment(BaseModel):
    label: str
    share: Optional[confloat(ge=0, le=1)] = None
    samples: List[SegmentSample] = Field(default_factory=list)
    linguistic_features: List[str] = Field(default_factory=list)


class NarrativeStack(BaseModel):
    l1: Optional[str] = None
    l2: Optional[str] = None
    l3: Optional[str] = None


class Phenomenon(BaseModel):
    id: Optional[str] = None
    status: Optional[str] = None  # pending | matched | minted | failed
    name: Optional[str] = None
    description: Optional[str] = None
    ai_image: Optional[str] = None


class DangerBlock(BaseModel):
    bot_homogeneity_score: Optional[confloat(ge=0, le=1)] = None
    notes: Optional[str] = None


class HardMetricShare(BaseModel):
    cluster_id: int
    share: float


class PerCapitaLike(BaseModel):
    cluster_id: int
    value: float


class MinorityDominanceIndex(BaseModel):
    top_k_clusters: int
    like_share: float
    size_share: float


class HardMetrics(BaseModel):
    n_comments: int
    n_clusters: int
    cluster_size_share: List[HardMetricShare]
    cluster_like_share: List[HardMetricShare]
    per_capita_like: List[PerCapitaLike]
    gini_like_share: float
    entropy_like_share: float
    dominance_ratio_top1: float
    minority_dominance_index: MinorityDominanceIndex


class PerClusterMetric(BaseModel):
    cluster_id: int
    size: int
    size_share: float
    like_sum: int
    like_share: float
    likes_per_comment: float


class PowerMetrics(BaseModel):
    intensity: Literal["HIGH", "MEDIUM", "LOW"]
    population: Literal["HIGH", "MEDIUM", "LOW"]
    asymmetry_score: Literal["HIGH", "MEDIUM", "LOW"]


class BattlefieldMapEntry(BaseModel):
    cluster_id: int
    role: Optional[str] = None
    tactic: Optional[str] = None
    power_metrics: PowerMetrics
    evidence_comment_ids: List[str] = Field(default_factory=list)


class StructuralInsight(BaseModel):
    keystone_cluster_id: Optional[int] = None
    counterfactual_analysis: Optional[str] = None
    evidence_comment_ids: List[str] = Field(default_factory=list)


class StrategicVerdict(BaseModel):
    verdict: Optional[str] = None
    rationale: Optional[str] = None
    evidence_comment_ids: List[str] = Field(default_factory=list)


class PostBlock(BaseModel):
    post_id: str
    author: Optional[str] = None
    text: Optional[str] = None
    link: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    timestamp: Optional[datetime] = None
    metrics: Metrics

    @validator("timestamp", pre=True)
    def _parse_ts(cls, v):
        if v is None or isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except Exception:
            return None


class SummaryCompat(BaseModel):
    one_line: Optional[str] = None
    narrative_type: Optional[str] = None


class BattlefieldCompat(BaseModel):
    factions: List[Segment] = Field(default_factory=list)


class AnalysisV4(BaseModel):
    post: PostBlock
    phenomenon: Phenomenon
    emotional_pulse: ToneProfile
    segments: List[Segment] = Field(default_factory=list)
    narrative_stack: NarrativeStack
    danger: Optional[DangerBlock] = None
    full_report: Optional[str] = None
    hard_metrics: Optional[HardMetrics] = None
    per_cluster_metrics: List[PerClusterMetric] = Field(default_factory=list)
    battlefield_map: List[BattlefieldMapEntry] = Field(default_factory=list)
    structural_insight: Optional[StructuralInsight] = None
    strategic_verdict: Optional[StrategicVerdict] = None
    # Compatibility fields for existing UI adapters
    summary: Optional[SummaryCompat] = None
    battlefield: Optional[BattlefieldCompat] = None

    class Config:
        extra = "allow"
