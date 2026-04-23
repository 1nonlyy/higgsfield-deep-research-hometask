"""Eval framework CLI: run cases, score cached traces, view HTML reports."""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVAL_FRAMEWORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_FRAMEWORK_DIR))

from runner.loader import TestCase, Trace  # noqa: E402
from runner.loader import load_cases_from_paths  # noqa: E402
from runner.runner import run_suite  # noqa: E402
from reporter.cli import print_report  # noqa: E402
from reporter.html import write_html_report  # noqa: E402
from scorer.hard import AssertionResult, check_hard  # noqa: E402
from scorer.registry import registry  # noqa: E402

# Soft metrics that invoke the LLM judge (see scorer/registry.py). Used for dry-run cost hints only.
_JUDGE_SOFT_METRICS = frozenset({"correctness", "safety", "honest_refusal", "ambiguity"})


@dataclass
class CaseResult:
    """One row in an eval report (possibly merged from multiple repeats)."""

    case_id: str
    passed: bool
    assertion_results: list[AssertionResult]
    trace: Trace
    cost_usd: float
    wall_time_ms: int
    tool_call_count: int
    metric_scores: dict[str, float] = field(default_factory=dict)
    repeats_summary: dict[str, Any] | None = None


def _count_tool_calls(trace: Trace) -> int:
    n = 0
    for msg in trace.messages:
        if msg.tool_calls:
            n += len(msg.tool_calls)
    return n


def _expand_timestamp(path_str: str) -> str:
    if "<timestamp>" in path_str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return path_str.replace("<timestamp>", ts)
    return path_str


def score_case(case: TestCase, trace: Trace) -> CaseResult:
    """Run hard checks + registered soft metrics; overall pass requires all checks to pass."""
    assertion_results: list[AssertionResult] = []
    metric_scores: dict[str, float] = {}

    for ha in case.expected_behavior.hard:
        assertion_results.append(check_hard(ha, trace))

    seen_metrics: set[str] = set()
    for sa in case.expected_behavior.soft:
        if sa.metric in seen_metrics:
            continue
        seen_metrics.add(sa.metric)
        mr = registry.score(sa.metric, trace, case)
        metric_scores[mr.name] = float(mr.score)
        assertion_results.append(
            AssertionResult(passed=mr.passed, reason=f"[{mr.name}] {mr.reason}")
        )

    passed = all(a.passed for a in assertion_results) if assertion_results else True

    return CaseResult(
        case_id=case.id,
        passed=passed,
        assertion_results=assertion_results,
        trace=trace,
        cost_usd=float(trace.cost_usd or 0.0),
        wall_time_ms=int(trace.wall_time_ms or 0),
        tool_call_count=_count_tool_calls(trace),
        metric_scores=metric_scores,
        repeats_summary=None,
    )


def _merge_repeats(case: TestCase, per_run: list[CaseResult]) -> CaseResult:
    repeat_count = len(per_run)
    pass_count = sum(1 for r in per_run if r.passed)
    flaky = pass_count < repeat_count
    rep_idx = next((i for i, r in enumerate(per_run) if not r.passed), repeat_count - 1)
    rep = per_run[rep_idx]
    per_repeat = [
        {
            "passed": r.passed,
            "cost_usd": r.cost_usd,
            "wall_time_ms": r.wall_time_ms,
            "tool_call_count": r.tool_call_count,
            "metric_scores": dict(r.metric_scores),
        }
        for r in per_run
    ]
    return CaseResult(
        case_id=case.id,
        passed=pass_count == repeat_count,
        assertion_results=list(rep.assertion_results),
        trace=rep.trace,
        cost_usd=rep.cost_usd,
        wall_time_ms=rep.wall_time_ms,
        tool_call_count=rep.tool_call_count,
        metric_scores=dict(rep.metric_scores),
        repeats_summary={
            "repeat_count": repeat_count,
            "pass_count": pass_count,
            "flaky": flaky,
            "per_repeat": per_repeat,
        },
    )


def _result_to_jsonable(r: CaseResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "case_id": r.case_id,
        "passed": r.passed,
        "assertion_results": [asdict(a) for a in r.assertion_results],
        "trace": r.trace.model_dump(mode="json"),
        "cost_usd": r.cost_usd,
        "wall_time_ms": r.wall_time_ms,
        "tool_call_count": r.tool_call_count,
        "metric_scores": dict(r.metric_scores),
    }
    if r.repeats_summary is not None:
        d["repeats_summary"] = json.loads(json.dumps(r.repeats_summary, default=str))
    return d


def _result_from_jsonable(d: dict[str, Any]) -> CaseResult:
    assertions_raw = d.get("assertion_results") or []
    assertion_results = [
        AssertionResult(passed=bool(a.get("passed")), reason=str(a.get("reason", "")))
        for a in assertions_raw
        if isinstance(a, dict)
    ]
    trace = Trace.model_validate(d["trace"])
    rs = d.get("repeats_summary")
    return CaseResult(
        case_id=str(d["case_id"]),
        passed=bool(d.get("passed", False)),
        assertion_results=assertion_results,
        trace=trace,
        cost_usd=float(d.get("cost_usd", 0.0) or 0.0),
        wall_time_ms=int(d.get("wall_time_ms", 0) or 0),
        tool_call_count=int(d.get("tool_call_count", 0) or 0),
        metric_scores={str(k): float(v) for k, v in (d.get("metric_scores") or {}).items()},
        repeats_summary=rs if isinstance(rs, dict) else None,
    )


def _unique_soft_metrics(case: TestCase) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sa in case.expected_behavior.soft:
        if sa.metric in seen:
            continue
        seen.add(sa.metric)
        out.append(sa.metric)
    return out


def _dry_run_judge_calls_per_repeat(case: TestCase) -> int:
    return sum(1 for m in _unique_soft_metrics(case) if m in _JUDGE_SOFT_METRICS)


def _dry_run_estimated_cost_usd(case: TestCase, repeats: int) -> float:
    """Rough budget from env-tunable defaults (no API calls)."""
    agent = float(os.environ.get("EVAL_DRY_RUN_EST_AGENT_USD", "0.05"))
    judge = float(os.environ.get("EVAL_DRY_RUN_EST_JUDGE_USD", "0.002"))
    per_repeat = agent + _dry_run_judge_calls_per_repeat(case) * judge
    return float(repeats) * per_repeat


def _validate_cases_soft_metrics(cases: list[TestCase]) -> list[str]:
    known = set(registry.names())
    errors: list[str] = []
    for c in cases:
        for sa in c.expected_behavior.soft:
            if sa.metric not in known:
                errors.append(
                    f"{c.id}: unknown soft metric {sa.metric!r} "
                    f"(registered: {', '.join(sorted(known))})"
                )
    return errors


def cmd_run_dry_run(case_paths: list[str], repeats: int) -> int:
    try:
        cases = load_cases_from_paths(case_paths)
    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        print(f"Schema / load error: {e}", file=sys.stderr)
        return 1

    metric_errors = _validate_cases_soft_metrics(cases)
    if metric_errors:
        for line in metric_errors:
            print(line, file=sys.stderr)
        return 1

    print(
        f"dry-run: {len(cases)} case(s), repeats={repeats} "
        f"(cost uses EVAL_DRY_RUN_EST_AGENT_USD / EVAL_DRY_RUN_EST_JUDGE_USD if set)\n"
    )
    for c in cases:
        n_hard = len(c.expected_behavior.hard)
        n_soft = len(c.expected_behavior.soft)
        est = _dry_run_estimated_cost_usd(c, repeats)
        print(f"  {c.id}: hard={n_hard} soft={n_soft} est_cost_usd≈{est:.4f}")
    total_est = sum(_dry_run_estimated_cost_usd(c, repeats) for c in cases)
    print(f"\n  total est_cost_usd≈{total_est:.4f}")
    return 0


def _find_case_for_trace(cases: list[TestCase], trace: Trace) -> TestCase:
    q = (trace.question or "").strip()
    matches = [c for c in cases if c.input.strip() == q]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No case in corpus with input matching trace question ({q[:80]!r}…)")
    ids = ", ".join(c.id for c in matches)
    raise SystemExit(f"Multiple cases match trace question; disambiguate cases YAML ids: {ids}")


def _cases_paths_from_args(ns: argparse.Namespace) -> list[str]:
    paths = ns.cases
    if not paths:
        return ["./cases"]
    return paths


def _cases_report_label(paths: list[str]) -> str:
    resolved = [str(Path(p).resolve()) for p in paths]
    return resolved[0] if len(resolved) == 1 else ";".join(resolved)


def cmd_run(args: argparse.Namespace) -> int:
    case_paths = _cases_paths_from_args(args)
    repeats = max(1, int(args.repeats))

    if args.dry_run:
        return cmd_run_dry_run(case_paths, repeats)

    agent_dir = str(Path(args.agent).resolve())

    cases = load_cases_from_paths(case_paths)
    expanded: list[TestCase] = []
    for c in cases:
        expanded.extend([c] * repeats)

    traces = run_suite(expanded, concurrency=int(args.concurrency), agent_dir=agent_dir)

    results: list[CaseResult] = []
    idx = 0
    for c in cases:
        chunk = traces[idx : idx + repeats]
        idx += repeats
        scored = [score_case(c, t) for t in chunk]
        if repeats > 1:
            results.append(_merge_repeats(c, scored))
        else:
            results.append(scored[0])

    out_path = Path(_expand_timestamp(args.output)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repeats": repeats,
        "cases_dir": _cases_report_label(case_paths),
        "agent_dir": agent_dir,
        "concurrency": int(args.concurrency),
        "results": [_result_to_jsonable(r) for r in results],
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote report: {out_path}")

    print_report(results, prev_report_path=args.prev)
    return 0 if all(r.passed for r in results) else 1


def cmd_score(args: argparse.Namespace) -> int:
    cases = load_cases_from_paths(_cases_paths_from_args(args))
    trace_path = Path(args.trace)
    if not trace_path.is_file():
        raise SystemExit(f"Trace file not found: {trace_path}")
    trace = Trace.model_validate(json.loads(trace_path.read_text(encoding="utf-8")))
    case = _find_case_for_trace(cases, trace)
    result = score_case(case, trace)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(_result_to_jsonable(result), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote: {args.output}")

    print_report([result], prev_report_path=None)
    return 0 if result.passed else 1


def cmd_view(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    items = raw.get("results")
    if not isinstance(items, list):
        raise SystemExit("Report JSON must contain a 'results' list")
    results = [_result_from_jsonable(x) for x in items if isinstance(x, dict)]

    html_path = report_path.with_suffix(".html")
    written = write_html_report(results, str(html_path))
    print(f"Wrote HTML: {written}")
    webbrowser.open(Path(written).resolve().as_uri())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep research eval framework")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Load YAML cases, run agent, score, write JSON report")
    p_run.add_argument(
        "--cases",
        action="append",
        metavar="PATH",
        help="YAML case file or directory of YAML files (repeatable; default ./cases)",
    )
    p_run.add_argument(
        "--agent",
        type=str,
        default="..",
        help="Path to agent package directory (directory containing agent.py)",
    )
    p_run.add_argument("--concurrency", type=int, default=4)
    p_run.add_argument("--repeats", type=int, default=1, help="Run each case N times (flaky detection)")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Load and validate cases, print summary + estimated cost; do not call agent or judge",
    )
    p_run.add_argument(
        "--output",
        type=str,
        default="./reports/run_<timestamp>.json",
        help='Report path; "<timestamp>" is replaced with UTC time',
    )
    p_run.add_argument(
        "--prev",
        type=str,
        default=None,
        help="Optional previous report JSON for pass/fail diff",
    )
    p_run.set_defaults(func=cmd_run)

    p_score = sub.add_parser("score", help="Score a cached trace JSON without calling the agent")
    p_score.add_argument("--trace", type=str, required=True, help="Path to trace JSON")
    p_score.add_argument(
        "--cases",
        action="append",
        metavar="PATH",
        help="YAML case file or directory (repeatable; default ./cases); used to match trace.question",
    )
    p_score.add_argument("--output", type=str, default=None, help="Optional path to write scored result JSON")
    p_score.set_defaults(func=cmd_score)

    p_view = sub.add_parser("view", help="Build HTML report viewer and open in browser")
    p_view.add_argument("--report", type=str, required=True, help="Path to report JSON from `run`")
    p_view.set_defaults(func=cmd_view)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
