from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Allow `import runner...` and `import scorer...` when running as:
#   cd eval-framework && python smoke_test.py
REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_FRAMEWORK_DIR = REPO_ROOT / "eval-framework"
sys.path.insert(0, str(EVAL_FRAMEWORK_DIR))

from runner.loader import TestCase, Trace  # noqa: E402
from runner.runner import run_case  # noqa: E402
from scorer.hard import check_hard  # noqa: E402


def _ensure_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def load_one_case(path: Path) -> TestCase:
    raw_text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw_text)
    items = _ensure_list(loaded)
    if not items:
        raise ValueError(f"{path}: no cases found")
    if not isinstance(items[0], dict):
        raise ValueError(f"{path}: expected mapping, got {type(items[0]).__name__}")
    return TestCase.model_validate(items[0])


def load_trace_json(path: Path) -> Trace:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Trace.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one happy-path case + hard assertions.")
    parser.add_argument(
        "--trace",
        type=str,
        default=None,
        help="Path to a pre-saved trace JSON file; skips agent run.",
    )
    args = parser.parse_args(argv)

    case_path = EVAL_FRAMEWORK_DIR / "cases" / "happy_path_voyager.yaml"
    if not case_path.exists():
        print(f"Missing case file: {case_path}", file=sys.stderr)
        return 1

    case = load_one_case(case_path)

    if args.trace:
        trace_path = Path(args.trace)
        if not trace_path.exists():
            alt = EVAL_FRAMEWORK_DIR / args.trace
            if alt.exists():
                trace_path = alt
            else:
                alt2 = REPO_ROOT / args.trace
                if alt2.exists():
                    trace_path = alt2
        trace = load_trace_json(trace_path)
    else:
        trace = run_case(case, agent_dir=str(REPO_ROOT))

    print(json.dumps(trace.model_dump(), indent=2, ensure_ascii=False, default=str))

    all_passed = True
    for assertion in case.expected_behavior.hard:
        res = check_hard(assertion, trace)
        prefix = "✅" if res.passed else "❌"
        print(f"{prefix} {res.reason}")
        all_passed = all_passed and res.passed

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

