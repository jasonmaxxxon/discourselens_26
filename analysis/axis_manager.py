import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from analysis.axis_models import AxisLibrary


class AxisManager:
    def __init__(self, library_path: Optional[str] = None) -> None:
        if library_path is None:
            library_path = os.path.join(
                os.path.dirname(__file__), "knowledge_base", "axis_library.v2.6.json"
            )
        self.library_path = library_path
        self.library = self._load_library(library_path)
        self.library_version = (
            self.library.meta.get("library_version")
            or self.library.meta.get("version")
            or self.library.schema_version
        )

    def _load_library(self, path: str) -> AxisLibrary:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Axis library missing: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            raise ValueError(f"Axis library unreadable: {path}") from e
        if not raw.strip():
            raise ValueError("Axis library empty")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("Axis library JSON invalid") from e
        try:
            return AxisLibrary.model_validate(payload)
        except ValidationError as e:
            raise ValueError("Axis library schema invalid") from e

    def get_few_shot_context(self) -> str:
        blocks: List[str] = []
        for axis in self.library.library_axes:
            pos_examples = axis.pos_examples[:3]
            neg_examples = axis.neg_examples[:1]
            lines: List[str] = [f"[Axis] {axis.axis_name}", f"Def: {axis.definition}"]
            lines.append("Pos Examples:")
            for ex in pos_examples:
                lines.append(f"- {ex.text} (id={ex.id})")
            if neg_examples:
                lines.append("Neg Examples:")
                for ex in neg_examples:
                    lines.append(f"- {ex.text} (id={ex.id})")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _tokenize(self, text: str, *, cjk_ngram: int = 2) -> Set[str]:
        raw = (text or "").strip()
        if not raw:
            return set()
        lowered = raw.lower()
        latin_tokens = set(re.findall(r"[A-Za-z0-9_]+", lowered))

        no_space = re.sub(r"\s+", "", lowered)
        cjk_chars = [ch for ch in no_space if self._is_cjk(ch)]
        cjk_tokens: Set[str] = set()
        if cjk_chars and cjk_ngram > 1:
            for i in range(len(cjk_chars) - cjk_ngram + 1):
                cjk_tokens.add("".join(cjk_chars[i : i + cjk_ngram]))
        if cjk_chars and not cjk_tokens:
            cjk_tokens.update(cjk_chars)

        return latin_tokens | cjk_tokens

    def _is_cjk(self, ch: str) -> bool:
        code = ord(ch)
        return (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF
            or 0x2A700 <= code <= 0x2B73F
            or 0x2B740 <= code <= 0x2B81F
            or 0x2B820 <= code <= 0x2CEAF
            or 0xF900 <= code <= 0xFAFF
            or 0x2F800 <= code <= 0x2FA1F
        )

    def find_anchor_text(self, axis_name: str, anchor_id: Optional[str]) -> str:
        axis = next((a for a in self.library.library_axes if a.axis_name == axis_name), None)
        if axis is None:
            return ""
        if anchor_id:
            for ex in axis.pos_examples + axis.neg_examples:
                if ex.id == anchor_id and ex.text:
                    return ex.text
        return "\n".join([ex.text for ex in axis.pos_examples[:3] if ex.text])

    def lexical_novelty_trace(
        self,
        comment_text: str,
        axis_name: str,
        semantic_score: float,
        matched_anchor_id: Optional[str],
        *,
        semantic_gate: float = 0.75,
        containment_gate: float = 0.4,
    ) -> Dict[str, Any]:
        axis = next((a for a in self.library.library_axes if a.axis_name == axis_name), None)
        debug = {
            "semantic_score": float(semantic_score),
            "semantic_gate": semantic_gate,
            "containment_gate": containment_gate,
            "max_containment": 0.0,
            "checked_axis": axis_name,
            "status": "skipped",
        }
        if axis is None or not axis.pos_examples:
            return {"is_novel": False, "reason": None, "debug": debug}

        input_tokens = self._tokenize(comment_text)
        if not input_tokens:
            return {"is_novel": False, "reason": None, "debug": debug}

        if semantic_score < semantic_gate:
            debug["status"] = "skipped_low_score"
            return {"is_novel": False, "reason": None, "debug": debug}

        max_containment = 0.0
        for ex in axis.pos_examples:
            ex_tokens = self._tokenize(ex.text)
            if not ex_tokens:
                continue
            overlap = len(input_tokens & ex_tokens)
            containment = overlap / len(input_tokens) if input_tokens else 0.0
            if containment > max_containment:
                max_containment = containment

        debug["status"] = "evaluated"
        debug["max_containment"] = max_containment
        is_novel = max_containment < containment_gate
        reason = (
            "semantic_score={score:.2f} max_containment={containment:.2f} "
            "thresholds(score>={gate},containment<{containment_gate})"
        ).format(
            score=semantic_score,
            containment=max_containment,
            gate=semantic_gate,
            containment_gate=containment_gate,
        )
        return {"is_novel": is_novel, "reason": reason, "debug": debug}

    def lexical_novelty_heuristic(
        self,
        comment_text: str,
        axis_name: str,
        semantic_score: float,
        matched_anchor_id: Optional[str],
        *,
        semantic_gate: float = 0.75,
        jaccard_gate: float = 0.15,
    ) -> Tuple[bool, Optional[str]]:
        """
        Backward compatibility wrapper.
        NOTE: `jaccard_gate` is deprecated in V7 logic. We use `containment_gate=0.4` as the hard rule.
        """
        _ = jaccard_gate
        trace = self.lexical_novelty_trace(
            comment_text,
            axis_name,
            semantic_score,
            matched_anchor_id,
            semantic_gate=semantic_gate,
            containment_gate=0.4,
        )
        return trace["is_novel"], trace["reason"]
