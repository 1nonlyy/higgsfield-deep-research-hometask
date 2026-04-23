"""Metric plugin registry.

This module provides a simple plugin mechanism so new metrics can be added
without editing runner or scorer core.

Example (external registration):

```python
from eval_framework.scorer.registry import registry


@registry.register("my_custom_metric")
def my_metric(trace, case):
    # ... compute your metric ...
    return True, 1.0, "looks good"
```
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runner.loader import HardAssertion, SoftAssertion, TestCase, Trace

from . import hard as hard_scorer
from . import soft as soft_scorer


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    passed: bool
    score: float
    reason: str


MetricFn = Callable[[Trace, TestCase], MetricResult | tuple[bool, float, str] | tuple[bool, str] | bool]


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync code."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # If we're already inside an event loop, fall back to creating a new task
        # and waiting for it. This path is mainly for notebooks or async runners.
        loop = asyncio.get_running_loop()
        fut = asyncio.ensure_future(coro, loop=loop)
        return loop.run_until_complete(fut)


def _as_metric_result(name: str, out: Any) -> MetricResult:
    if isinstance(out, MetricResult):
        return out
    if isinstance(out, tuple):
        if len(out) == 3:
            passed, score, reason = out
            return MetricResult(name=name, passed=bool(passed), score=float(score), reason=str(reason))
        if len(out) == 2:
            passed, reason = out
            return MetricResult(name=name, passed=bool(passed), score=1.0 if passed else 0.0, reason=str(reason))
    if isinstance(out, bool):
        return MetricResult(name=name, passed=out, score=1.0 if out else 0.0, reason="")
    raise TypeError(f"metric {name!r} returned unsupported type: {type(out).__name__}")


class MetricRegistry:
    def __init__(self) -> None:
        self._fns: dict[str, MetricFn] = {}

    def register(self, name: str, fn: Callable[[Trace, TestCase], Any] | None = None):
        """Decorator to register a metric implementation."""

        def _decorator(f: Callable[[Trace, TestCase], Any]) -> Callable[[Trace, TestCase], Any]:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("metric name must be a non-empty string")
            self._fns[name] = f  # type: ignore[assignment]
            return f

        return _decorator(fn) if fn is not None else _decorator

    def score(self, name: str, trace: Trace, case: TestCase) -> MetricResult:
        fn = self._fns.get(name)
        if fn is None:
            return MetricResult(name=name, passed=False, score=0.0, reason=f"unknown metric: {name!r}")
        out = fn(trace, case)
        return _as_metric_result(name, out)

    def names(self) -> list[str]:
        return sorted(self._fns.keys())


registry = MetricRegistry()


def _filter_hard(case: TestCase, types: set[str]) -> list[HardAssertion]:
    return [a for a in (case.expected_behavior.hard or []) if a.type in types]


def _soft_for_metric(case: TestCase, metric: str, default_rubric: str) -> SoftAssertion:
    for a in (case.expected_behavior.soft or []):
        if a.metric == metric:
            return a
    return SoftAssertion(metric=metric, rubric_file=default_rubric, weight=1.0)


@registry.register("correctness")
def _metric_correctness(trace: Trace, case: TestCase) -> MetricResult:
    hard_asserts = _filter_hard(case, {"answer_contains", "answer_contains_any"})
    hard_results = [hard_scorer.check_hard(a, trace) for a in hard_asserts]
    hard_failed = [r for r in hard_results if not r.passed]
    if hard_failed:
        return MetricResult(
            name="correctness",
            passed=False,
            score=0.0,
            reason=f"hard assertion failed: {hard_failed[0].reason}",
        )

    judge_assertion = _soft_for_metric(case, "correctness", default_rubric="correctness.md")
    judge_result = _run_async(soft_scorer.judge(judge_assertion, trace, case))
    passed = bool(getattr(judge_result, "passed", False))
    score = float(getattr(judge_result, "score", 0.0))
    rationale = str(getattr(judge_result, "rationale", "")).strip()
    return MetricResult(
        name="correctness",
        passed=passed,
        score=score,
        reason=rationale or "judge completed",
    )


@registry.register("tool_efficiency")
def _metric_tool_efficiency(trace: Trace, case: TestCase) -> MetricResult:
    hard_asserts = _filter_hard(case, {"tool_call_count_lte", "tool_called"})
    if not hard_asserts:
        return MetricResult(name="tool_efficiency", passed=True, score=1.0, reason="no tool-efficiency constraints")
    results = [hard_scorer.check_hard(a, trace) for a in hard_asserts]
    failed = [r for r in results if not r.passed]
    if failed:
        return MetricResult(name="tool_efficiency", passed=False, score=0.0, reason=failed[0].reason)
    reason = "; ".join(r.reason for r in results) or "passed"
    return MetricResult(name="tool_efficiency", passed=True, score=1.0, reason=reason)


@registry.register("cost_latency")
def _metric_cost_latency(trace: Trace, case: TestCase) -> MetricResult:
    max_cost = getattr(case, "max_cost_usd", None)
    max_wall = getattr(case, "max_wall_time_ms", None)

    cost_ok = True
    wall_ok = True
    parts: list[str] = []

    if max_cost is not None:
        try:
            limit = float(max_cost)
            cost_ok = float(trace.cost_usd) <= limit
            parts.append(f"cost_usd {trace.cost_usd:.6f} <= {limit:.6f}" if cost_ok else f"cost_usd {trace.cost_usd:.6f} > {limit:.6f}")
        except Exception:
            return MetricResult(name="cost_latency", passed=False, score=0.0, reason=f"invalid max_cost_usd: {max_cost!r}")

    if max_wall is not None:
        try:
            limit_ms = int(max_wall)
            wall_ok = int(trace.wall_time_ms) <= limit_ms
            parts.append(f"wall_time_ms {trace.wall_time_ms} <= {limit_ms}" if wall_ok else f"wall_time_ms {trace.wall_time_ms} > {limit_ms}")
        except Exception:
            return MetricResult(name="cost_latency", passed=False, score=0.0, reason=f"invalid max_wall_time_ms: {max_wall!r}")

    if max_cost is None and max_wall is None:
        return MetricResult(name="cost_latency", passed=True, score=1.0, reason="no thresholds provided")

    passed = cost_ok and wall_ok
    return MetricResult(name="cost_latency", passed=passed, score=1.0 if passed else 0.0, reason="; ".join(parts))


@registry.register("safety")
def _metric_safety(trace: Trace, case: TestCase) -> MetricResult:
    hard_asserts = _filter_hard(case, {"answer_not_contains"})
    hard_results = [hard_scorer.check_hard(a, trace) for a in hard_asserts]
    hard_failed = [r for r in hard_results if not r.passed]
    if hard_failed:
        return MetricResult(name="safety", passed=False, score=0.0, reason=f"hard assertion failed: {hard_failed[0].reason}")

    judge_assertion = _soft_for_metric(case, "safety", default_rubric="safety.md")
    judge_result = _run_async(soft_scorer.judge(judge_assertion, trace, case))
    passed = bool(getattr(judge_result, "passed", False))
    score = float(getattr(judge_result, "score", 0.0))
    rationale = str(getattr(judge_result, "rationale", "")).strip()
    return MetricResult(name="safety", passed=passed, score=score, reason=rationale or "judge completed")


@registry.register("honest_refusal")
def _metric_honest_refusal(trace: Trace, case: TestCase) -> MetricResult:
    judge_assertion = _soft_for_metric(case, "honest_refusal", default_rubric="honest_refusal.md")
    judge_result = _run_async(soft_scorer.judge(judge_assertion, trace, case))
    passed = bool(getattr(judge_result, "passed", False))
    score = float(getattr(judge_result, "score", 0.0))
    rationale = str(getattr(judge_result, "rationale", "")).strip()
    return MetricResult(
        name="honest_refusal",
        passed=passed,
        score=score,
        reason=rationale or "judge completed",
    )


@registry.register("ambiguity")
def _metric_ambiguity(trace: Trace, case: TestCase) -> MetricResult:
    judge_assertion = _soft_for_metric(case, "ambiguity", default_rubric="ambiguity.md")
    judge_result = _run_async(soft_scorer.judge(judge_assertion, trace, case))
    passed = bool(getattr(judge_result, "passed", False))
    score = float(getattr(judge_result, "score", 0.0))
    rationale = str(getattr(judge_result, "rationale", "")).strip()
    return MetricResult(
        name="ambiguity",
        passed=passed,
        score=score,
        reason=rationale or "judge completed",
    )

