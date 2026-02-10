"""
Lightweight verification for CDX-108.3 (V6.1 hard metrics + alias enforcement).
Run without pytest: `python3 scripts/verify_v61.py`
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.quant_calculator import QuantCalculator
from analysis.analyst import _reverse_map_evidence_ids, _evidence_compliance_errors


def assert_quant_determinism():
    comments = [
        {"comment_id": "a", "cluster_key": 0, "text": "alpha", "like_count": 10, "quant_x": 0.0, "quant_y": 0.0},
        {"comment_id": "b", "cluster_key": 0, "text": "beta", "like_count": 5, "quant_x": 0.1, "quant_y": 0.0},
        {"comment_id": "c", "cluster_key": 1, "text": "gamma", "like_count": 20, "quant_x": 1.0, "quant_y": 1.0},
    ]
    first = QuantCalculator.compute("post-verify", comments)
    second = QuantCalculator.compute("post-verify", comments)
    assert first == second, "QuantCalculator output is not deterministic"
    assert first["hard_metrics"]["n_clusters"] == 2, "Expected 2 clusters in hard metrics"


def assert_alias_reverse_mapping():
    payload = {"battlefield_map": [{"cluster_id": 0, "evidence_comment_ids": ["c1", "c2"]}]}
    mapped, unknown = _reverse_map_evidence_ids(payload, {"c1": "real-1", "c2": "real-2"})
    assert not unknown, f"Unexpected unknown aliases: {unknown}"
    ids = mapped["battlefield_map"][0]["evidence_comment_ids"]
    assert ids == ["real-1", "real-2"], f"Reverse mapping failed: {ids}"


def assert_evidence_compliance():
    payload = {"battlefield_map": [{"cluster_id": 0, "evidence_comment_ids": ["one"]}]}
    errors = _evidence_compliance_errors(payload)
    assert errors, "Expected evidence compliance error when <2 evidence ids"


def main():
    checks = [
        ("quant_determinism", assert_quant_determinism),
        ("alias_reverse_mapping", assert_alias_reverse_mapping),
        ("evidence_compliance", assert_evidence_compliance),
    ]
    for name, fn in checks:
        try:
            fn()
            print(f"[OK] {name}")
        except AssertionError as ae:
            print(f"[FAIL] {name}: {ae}")
            sys.exit(1)
        except Exception as exc:
            print(f"[ERROR] {name}: {exc}")
            sys.exit(1)
    print("All verification checks passed.")


if __name__ == "__main__":
    main()
