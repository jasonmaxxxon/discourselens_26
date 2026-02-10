"""
Contract check for cluster payload tactics field.
Run: PYTHONPATH=. python3 scripts/verify_cluster_payload_contract.py
"""
import sys


def validate_tactics(tactics):
    if tactics is None:
        raise ValueError("tactics None not allowed; use []")
    if not isinstance(tactics, list):
        raise ValueError("tactics must be list")
    if not all(isinstance(t, str) for t in tactics):
        raise ValueError("tactics elements must be strings")
    return True


def run_case(name, tactics, should_pass):
    try:
        validate_tactics(tactics)
        if not should_pass:
            print(f"[FAIL] {name}: expected failure but passed")
            return False
        print(f"[PASS] {name}")
        return True
    except Exception as e:
        if should_pass:
            print(f"[FAIL] {name}: {e}")
            return False
        print(f"[PASS] {name} (failed as expected: {e})")
        return True


def main():
    cases = [
        ("empty string", "", False),
        ("string json array", "[]", False),
        ("single string", "Rage Baiting", False),
        ("None", None, False),
        ("valid empty list", [], True),
        ("valid list", ["Rage Baiting"], True),
    ]
    results = [run_case(name, val, ok) for name, val, ok in cases]
    if not all(results):
        sys.exit(1)
    print("All contract checks passed.")


if __name__ == "__main__":
    main()
