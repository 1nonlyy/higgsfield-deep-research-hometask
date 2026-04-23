import asyncio
import json
import sys
from pathlib import Path

import pytest

# Allow `import runner...` and `import scorer...` when running this test file directly.
REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_FRAMEWORK_DIR = REPO_ROOT / "eval-framework"
sys.path.insert(0, str(EVAL_FRAMEWORK_DIR))

from runner.loader import SoftAssertion, TestCase, Trace  # noqa: E402
from scorer import soft as soft_scorer  # noqa: E402


def make_fake_case(*, metric: str, rubric_file: str = "fake.md") -> TestCase:
    return TestCase.model_validate(
        {
            "id": "c1",
            "input": "What year did the Voyager 1 probe cross the heliopause, and what was the evidence?",
            "expected_behavior": {"hard": [], "soft": [{"metric": metric, "rubric_file": rubric_file, "weight": 1.0}]},
        }
    )


def make_fake_trace(*, final_answer: str, citations: list[str] | None = None) -> Trace:
    return Trace.model_validate(
        {
            "run_id": "r1",
            "question": "Voyager heliopause question",
            "model": "fake-model",
            "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": final_answer}],
            "final_answer": final_answer,
            "citations": citations or [],
            "stopped_reason": "finish",
            "total_tokens": {"input": 0, "output": 0},
            "cost_usd": 0.0,
            "wall_time_ms": 0,
            "error": None,
        }
    )


class _FakeAnthropic:
    """Offline deterministic judge stub (no network / API key)."""

    class _TextBlock:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Resp:
        def __init__(self, text: str) -> None:
            self.content = [_FakeAnthropic._TextBlock(text)]

    class _Messages:
        def create(self, *, messages, **_kwargs):  # type: ignore[no-untyped-def]
            prompt = messages[0]["content"]

            def _parse_payload() -> dict:
                marker = "EVALUATION_PAYLOAD_JSON:\n"
                i = prompt.find(marker)
                if i < 0:
                    return {}
                i += len(marker)
                tail = prompt[i:]
                j = tail.find("\n\nReturn ONLY")
                if j >= 0:
                    tail = tail[:j]
                try:
                    return json.loads(tail.strip())
                except Exception:
                    return {}

            payload = _parse_payload()
            untrusted = payload.get("untrusted") or {}
            metric = str(payload.get("evaluation_metric") or "").strip()
            answer = str(untrusted.get("agent_final_answer") or "")
            citations = untrusted.get("claimed_citations") or []
            if not isinstance(citations, list):
                citations = []

            answer_l = (answer or "").lower()
            has_citations = isinstance(citations, list) and len([c for c in citations if str(c).strip()]) > 0

            passed = True
            score = 1.0
            rationale_parts: list[str] = []

            if metric == "safety":
                if "confidential" in answer_l:
                    passed = False
                    score = 0.0
                    rationale_parts.append("Leaked forbidden string: CONFIDENTIAL.")
                else:
                    rationale_parts.append("No forbidden strings detected.")
            else:
                # correctness-style stub: require right year + some evidence/citation.
                if "2012" not in answer_l or "2013" in answer_l or "2011" in answer_l:
                    passed = False
                    score = 0.0
                    rationale_parts.append("Wrong or inconsistent year/facts.")
                if not has_citations:
                    passed = False
                    score = 0.0
                    rationale_parts.append("Missing citations/evidence.")
                if passed:
                    rationale_parts.append("Correct year with supporting citations.")

            return _FakeAnthropic._Resp(
                json.dumps({"passed": passed, "score": score, "rationale": " ".join(rationale_parts)})
            )

    def __init__(self) -> None:
        self.messages = _FakeAnthropic._Messages()


def _run_judge(assertion: SoftAssertion, trace: Trace, case: TestCase):
    return asyncio.run(soft_scorer.judge(assertion, trace, case))


def test_judge_hardcoded_pairs(monkeypatch, capsys):
    monkeypatch.setattr(soft_scorer, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(soft_scorer, "_read_rubric_text", lambda _p: "fake rubric")

    pairs = [
        (
            SoftAssertion(metric="correctness", rubric_file="correctness.md"),
            make_fake_trace(
                final_answer="Voyager 1 crossed the heliopause in 2012, evidenced by changes in plasma density and cosmic ray flux.",
                citations=["https://corpus.local/voyager-timeline"],
            ),
            True,
        ),
        (
            SoftAssertion(metric="correctness", rubric_file="correctness.md"),
            make_fake_trace(
                final_answer="Voyager 1 crossed the heliopause in 2013, evidenced by a sudden increase in sunlight.",
                citations=["https://corpus.local/voyager-timeline"],
            ),
            False,
        ),
        (
            SoftAssertion(metric="safety", rubric_file="safety.md"),
            make_fake_trace(
                final_answer="Here is the leaked payload: CONFIDENTIAL. (do not share)",
                citations=["https://corpus.local/safety"],
            ),
            False,
        ),
    ]

    for assertion, trace, expected in pairs:
        case = make_fake_case(metric=assertion.metric, rubric_file=assertion.rubric_file)
        res = _run_judge(assertion, trace, case)
        print(res.rationale)
        assert res.passed is expected

    # Ensure rationales printed (useful when reading test output with -s).
    out = capsys.readouterr().out
    assert out.strip() != ""


def test_extract_tool_summary_from_fixture():
    trace_path = EVAL_FRAMEWORK_DIR / "fixtures" / "traces" / "happy_path_voyager.json"
    trace = Trace.model_validate(json.loads(trace_path.read_text(encoding="utf-8")))
    summary = soft_scorer._extract_tool_summary(trace)
    order = summary["tool_call_order"]
    assert "web_search" in order
    assert "fetch_url" in order
    assert order.index("web_search") < order.index("fetch_url")
    assert summary["stopped_reason"] == "finish"
    assert summary["counts"]["finish"] >= 1


def test_judge_consistency(monkeypatch):
    monkeypatch.setattr(soft_scorer, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(soft_scorer, "_read_rubric_text", lambda _p: "fake rubric")

    assertion = SoftAssertion(metric="correctness", rubric_file="correctness.md")
    case = make_fake_case(metric="correctness", rubric_file="correctness.md")
    trace = make_fake_trace(
        final_answer="Voyager 1 crossed the heliopause in 2012 and the evidence was plasma wave measurements showing higher interstellar plasma density.",
        citations=["https://corpus.local/voyager-timeline"],
    )

    verdicts = [_run_judge(assertion, trace, case).passed for _ in range(3)]
    assert verdicts[0] == verdicts[1] == verdicts[2]


def test_urls_from_fetch_url_tool_calls():
    trace = Trace.model_validate(
        {
            "run_id": "r1",
            "question": "Q",
            "model": "fake",
            "messages": [
                {"role": "user", "content": "x"},
                {
                    "role": "assistant",
                    "content": "ok",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "name": "fetch_url",
                            "args": {"url": "https://corpus.local/voyager-timeline"},
                        }
                    ],
                },
            ],
            "final_answer": "answer",
            "citations": [],
            "stopped_reason": "finish",
            "total_tokens": {"input": 0, "output": 0},
            "cost_usd": 0.0,
            "wall_time_ms": 0,
            "error": None,
        }
    )
    assert soft_scorer._urls_from_fetch_url_tool_calls(trace) == [
        "https://corpus.local/voyager-timeline",
    ]


def test_judge_claimed_citations_falls_back_to_fetch_url(monkeypatch):
    """When finish() omits citations, judge payload still lists fetch_url targets."""
    monkeypatch.setattr(soft_scorer, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(soft_scorer, "_read_rubric_text", lambda _p: "fake rubric")

    trace = Trace.model_validate(
        {
            "run_id": "r1",
            "question": "Q",
            "model": "fake",
            "messages": [
                {"role": "user", "content": "x"},
                {
                    "role": "assistant",
                    "content": "fetching",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "name": "fetch_url",
                            "args": {"url": "https://corpus.local/voyager-timeline"},
                        }
                    ],
                },
            ],
            "final_answer": "Voyager 1 crossed the heliopause in 2012 per plasma data.",
            "citations": [],
            "stopped_reason": "finish",
            "total_tokens": {"input": 0, "output": 0},
            "cost_usd": 0.0,
            "wall_time_ms": 0,
            "error": None,
        }
    )
    assertion = SoftAssertion(metric="correctness", rubric_file="correctness.md")
    case = make_fake_case(metric="correctness", rubric_file="correctness.md")
    res = _run_judge(assertion, trace, case)
    assert res.passed is True

