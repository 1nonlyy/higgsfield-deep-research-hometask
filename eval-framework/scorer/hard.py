"""Deterministic (hard) assertions over a run Trace.

Assertion semantics (for case authors):

- ``tool_call_count_lte``: counts every assistant ``tool_calls`` entry, including
  ``finish`` (and any other tools), not "research tools only".
- ``citations_fetched``: for each URL in ``trace.citations``, requires an identical
  string as the ``url`` argument of some ``fetch_url`` tool call in the trace.
  URL matching is literal (no normalization). If ``citations`` is empty, the
  check passes vacuously. The YAML ``value`` field is ignored (conventionally
  ``true``).
- ``corpus_urls_in_answer_fetched``: every ``https://corpus.local/...`` substring
  found in ``trace.final_answer`` must appear as the ``url`` argument of some
  ``fetch_url`` call (same set as ``citations_fetched``). If the answer contains
  no such URLs, passes vacuously—pair with ``answer_contains`` when the prompt
  requires listing corpus URLs in prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from runner.loader import HardAssertion, Trace

# `https://corpus.local/...` URLs as they typically appear in answers (stop at whitespace / closers).
_CORPUS_LOCAL_URL_RE = re.compile(r"https://corpus\.local/[^\s)>\]\"',]+")


@dataclass(frozen=True, slots=True)
class AssertionResult:
    passed: bool
    reason: str


def _all_tool_calls(trace: Trace) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for msg in trace.messages:
        if not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            out.append((tc.name, dict(tc.args or {})))
    return out


def _fetched_url_set(tool_calls: list[tuple[str, dict[str, Any]]]) -> set[str]:
    return {
        str(args.get("url"))
        for name, args in tool_calls
        if name == "fetch_url" and args.get("url") is not None
    }


def _corpus_urls_in_text(text: str | None) -> list[str]:
    if not text:
        return []
    return list(dict.fromkeys(_CORPUS_LOCAL_URL_RE.findall(text)))


def check_hard(assertion: HardAssertion, trace: Trace) -> AssertionResult:
    t = assertion.type
    v = assertion.value

    tool_calls = _all_tool_calls(trace)
    tool_names = [name for name, _ in tool_calls]

    if t == "tool_called":
        name = str(v)
        passed = any(n == name for n in tool_names)
        return AssertionResult(
            passed=passed,
            reason=(
                f"tool {name!r} was called" if passed else f"tool {name!r} was not called"
            ),
        )

    if t == "tool_not_called":
        name = str(v)
        passed = not any(n == name for n in tool_names)
        return AssertionResult(
            passed=passed,
            reason=(
                f"tool {name!r} was not called"
                if passed
                else f"tool {name!r} was called"
            ),
        )

    if t == "answer_contains":
        needle = str(v).lower()
        hay = (trace.final_answer or "").lower()
        passed = needle in hay
        return AssertionResult(
            passed=passed,
            reason=(
                f"final answer contains {str(v)!r}"
                if passed
                else f"final answer does not contain {str(v)!r}"
            ),
        )

    if t == "answer_contains_any":
        hay = (trace.final_answer or "").lower()
        if isinstance(v, list):
            needles = [str(x).lower() for x in v]
        else:
            needles = [str(v).lower()]
        if not needles:
            return AssertionResult(
                passed=False,
                reason="answer_contains_any requires a non-empty list of substrings",
            )
        matched = [n for n in needles if n in hay]
        passed = len(matched) > 0
        if passed:
            idx = next(i for i, n in enumerate(needles) if n in hay)
            raw = v[idx] if isinstance(v, list) else v
            return AssertionResult(
                passed=True,
                reason=f"final answer contains at least one of {needles!r} (matched {str(raw)!r})",
            )
        return AssertionResult(
            passed=False,
            reason=f"final answer does not contain any of: {needles!r}",
        )

    if t == "answer_not_contains":
        needle = str(v).lower()
        hay = (trace.final_answer or "").lower()
        passed = needle not in hay
        return AssertionResult(
            passed=passed,
            reason=(
                f"final answer does not contain {str(v)!r}"
                if passed
                else f"final answer contains {str(v)!r}"
            ),
        )

    if t == "tool_call_count_lte":
        try:
            limit = int(v)
        except Exception:
            return AssertionResult(passed=False, reason=f"invalid int limit: {v!r}")
        count = len(tool_calls)
        passed = count <= limit
        return AssertionResult(
            passed=passed,
            reason=(
                f"tool call count {count} <= {limit}"
                if passed
                else f"tool call count {count} > {limit}"
            ),
        )

    if t == "stopped_reason":
        expected = str(v)
        actual = trace.stopped_reason
        passed = actual == expected
        return AssertionResult(
            passed=passed,
            reason=(
                f"stopped_reason == {expected!r}"
                if passed
                else f"stopped_reason {actual!r} != {expected!r}"
            ),
        )

    if t == "citations_fetched":
        # ``assertion.value`` (e.g. YAML ``true``) is ignored; checks use ``trace`` only.
        # For every URL in trace.citations, ensure it appears as the `url` argument
        # of a `fetch_url` tool call somewhere in the trace messages.
        cited = [str(u) for u in (trace.citations or [])]
        fetched = _fetched_url_set(tool_calls)
        missing = [u for u in cited if u not in fetched]
        passed = len(missing) == 0
        return AssertionResult(
            passed=passed,
            reason=(
                "all citations were fetched"
                if passed
                else f"citations not fetched: {missing!r}"
            ),
        )

    if t == "corpus_urls_in_answer_fetched":
        # ``assertion.value`` is ignored. Every https://corpus.local/... in final_answer
        # must match a fetch_url ``url`` arg (literal string match). Vacuous if none.
        fetched = _fetched_url_set(tool_calls)
        urls = _corpus_urls_in_text(trace.final_answer)
        if not urls:
            return AssertionResult(
                passed=True,
                reason="no https://corpus.local/ URLs in final_answer (vacuous pass)",
            )
        missing = [u for u in urls if u not in fetched]
        passed = len(missing) == 0
        return AssertionResult(
            passed=passed,
            reason=(
                "all corpus URLs in final answer were fetched"
                if passed
                else f"final_answer corpus URLs not fetched: {missing!r}"
            ),
        )

    return AssertionResult(passed=False, reason=f"unknown hard assertion type: {t!r}")

