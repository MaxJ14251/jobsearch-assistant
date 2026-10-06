"""Print the drafting evals' scorecard (plan 25).

    .venv/Scripts/python tools/eval_report.py

One line per case: how many checks passed, then each miss with what was
expected and what came out. No model call, no network, no tracker: the
cases, the profile and the corpus are files under tests/evals/.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.evals import harness  # noqa: E402


def main() -> int:
    results = harness.run_all()
    total = sum(len(r.checks) for r in results)
    passed = sum(ok for r in results for _, ok in r.checks)
    width = max(len(r.case) for r in results)
    for r in results:
        ok = sum(1 for _, good in r.checks if good)
        mark = "ok  " if not r.misses else "MISS"
        print(f"{mark} {r.case:<{width}}  {ok}/{len(r.checks)}")
        for miss in r.misses:
            print(f"       {miss.check}: expected {miss.expected!r}, got {miss.got!r}")
    print(f"\n{passed}/{total} checks over {len(results)} cases")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
