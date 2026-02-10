from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --- Library Loading Models ---
class AxisExample(BaseModel):
    text: str
    reason: str
    id: Optional[str] = None
    context_summary: Optional[str] = None
    context_post_id: Optional[str] = None


class AxisDefinition(BaseModel):
    axis_name: str
    definition: str
    pos_examples: List[AxisExample]
    neg_examples: List[AxisExample] = []


class AxisLibrary(BaseModel):
    schema_version: str
    meta: Dict
    library_axes: List[AxisDefinition]


# --- Analysis Output Models (LLM measurement payload) ---
class SingleAxisResult(BaseModel):
    axis_name: str
    score: float = Field(..., description="0.0 to 1.0 semantic alignment score. 1.0 = perfect match.")
    reasoning: str = Field(..., description="Why this score was given, citing specific cues.")
    matched_anchor_id: Optional[str] = Field(None, description="ID of closest library example")


class AxisAlignmentMeta(BaseModel):
    library_version: str
    is_extension_candidate: bool = Field(False, description="High semantic fit but low lexical similarity")
    extension_reason: Optional[str] = None


class AxisAlignmentBlock(BaseModel):
    meta: AxisAlignmentMeta
    axes: List[SingleAxisResult]
