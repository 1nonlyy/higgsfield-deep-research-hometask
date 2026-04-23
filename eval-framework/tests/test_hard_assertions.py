import sys
from pathlib import Path

import pytest

# Allow `import runner...` and `import scorer...` when running this test file directly.
REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_FRAMEWORK_DIR = REPO_ROOT / "eval-framework"
sys.path.insert(0, str(EVAL_FRAMEWORK_DIR))

from runner.loader import HardAssertion, Trace  # noqa: E402
from scorer.hard import check_hard  # noqa: E402


def make_fake_trace(**overrides) -> Trace:
    base = {
        "run_id": "r1",
        "question": "When did Voyager 1 cross the heliopause?",
        "model": "fake-model",
        "stopped_reason": "finish",
        "final_answer": "Voyager 1 crossed the heliopause in 2012.",
        "citations": ["https://corpus.local/voyager-timeline"],
        "messages": [
            {"role": "user", "content": "Tell me about Voyager 1 and the heliopause."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_web_1",
                        "name": "web_search",
                        "args": {"query": "voyager heliopause"},
                    }
                ],
            },
            {"role": "tool", "name": "web_search", "content": [{"title": "t", "url": "u"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_fetch_1",
                        "name": "fetch_url",
                        "args": {"url": "https://corpus.local/voyager-timeline"},
                    }
                ],
            },
            {"role": "tool", "name": "fetch_url", "content": "Voyager 1 crossed the heliopause in 2012."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_finish_1",
                        "name": "finish",
                        "args": {
                            "answer": "Voyager 1 crossed the heliopause in 2012.",
                            "citations": ["https://corpus.local/voyager-timeline"],
                        },
                    }
                ],
            },
        ],
        "total_tokens": {"input": 0, "output": 0},
        "cost_usd": 0.0,
        "wall_time_ms": 0,
        "error": None,
    }

    base.update(overrides)
    return Trace.model_validate(base)


def test_tool_called():
    ok = make_fake_trace()
    res_ok = check_hard(HardAssertion(type="tool_called", value="web_search"), ok)
    assert res_ok.passed is True

    bad = make_fake_trace(
        messages=[{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    )
    res_bad = check_hard(HardAssertion(type="tool_called", value="web_search"), bad)
    assert res_bad.passed is False


def test_tool_not_called():
    ok = make_fake_trace()
    res_ok = check_hard(HardAssertion(type="tool_not_called", value="some_unused_tool"), ok)
    assert res_ok.passed is True

    bad = make_fake_trace()
    res_bad = check_hard(HardAssertion(type="tool_not_called", value="web_search"), bad)
    assert res_bad.passed is False


def test_answer_contains():
    ok = make_fake_trace(final_answer="VOYAGER 1 crossed the HELIOPAUSE in 2012.")
    res_ok = check_hard(HardAssertion(type="answer_contains", value="heliopause"), ok)
    assert res_ok.passed is True

    bad = make_fake_trace(final_answer="Something else entirely.")
    res_bad = check_hard(HardAssertion(type="answer_contains", value="heliopause"), bad)
    assert res_bad.passed is False


def test_answer_contains_any():
    needles = ["cannot", "not found", "no information"]
    ok = make_fake_trace(final_answer="That detail is NOT FOUND in the corpus.")
    res_ok = check_hard(HardAssertion(type="answer_contains_any", value=needles), ok)
    assert res_ok.passed is True

    bad = make_fake_trace(final_answer="Something else entirely.")
    res_bad = check_hard(HardAssertion(type="answer_contains_any", value=needles), bad)
    assert res_bad.passed is False


def test_answer_not_contains():
    ok = make_fake_trace(final_answer="Voyager 1 crossed the heliopause in 2012.")
    res_ok = check_hard(HardAssertion(type="answer_not_contains", value="hallucinated"), ok)
    assert res_ok.passed is True

    bad = make_fake_trace(final_answer="This is hallucinated content.")
    res_bad = check_hard(HardAssertion(type="answer_not_contains", value="Hallucinated"), bad)
    assert res_bad.passed is False


def test_tool_call_count_lte():
    ok = make_fake_trace()
    # In the fake trace: web_search + fetch_url + finish = 3 tool calls.
    res_ok = check_hard(HardAssertion(type="tool_call_count_lte", value=3), ok)
    assert res_ok.passed is True

    bad = make_fake_trace()
    res_bad = check_hard(HardAssertion(type="tool_call_count_lte", value=2), bad)
    assert res_bad.passed is False


def test_stopped_reason():
    ok = make_fake_trace(stopped_reason="finish")
    res_ok = check_hard(HardAssertion(type="stopped_reason", value="finish"), ok)
    assert res_ok.passed is True

    bad = make_fake_trace(stopped_reason="max_steps")
    res_bad = check_hard(HardAssertion(type="stopped_reason", value="finish"), bad)
    assert res_bad.passed is False


def test_citations_fetched():
    # Citation URL appears in a fetch_url call -> should pass.
    ok = make_fake_trace(citations=["https://corpus.local/voyager-timeline"])
    res_ok = check_hard(HardAssertion(type="citations_fetched", value=True), ok)
    assert res_ok.passed is True

    # Citation URL does NOT appear in any fetch_url call -> should fail (hallucinated citation).
    bad = make_fake_trace(
        citations=["https://corpus.local/voyager-timeline"],
        messages=[
            {"role": "user", "content": "Tell me about Voyager 1 and the heliopause."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_web_1",
                        "name": "web_search",
                        "args": {"query": "voyager heliopause"},
                    }
                ],
            },
            {"role": "tool", "name": "web_search", "content": [{"title": "t", "url": "u"}]},
            {
                "role": "assistant",
                "content": "I won't fetch the citation URL, but will cite it anyway.",
                "tool_calls": [
                    {
                        "id": "tc_finish_1",
                        "name": "finish",
                        "args": {
                            "answer": "Voyager 1 crossed the heliopause in 2012.",
                            "citations": ["https://corpus.local/voyager-timeline"],
                        },
                    }
                ],
            },
        ],
    )
    res_bad = check_hard(HardAssertion(type="citations_fetched", value=True), bad)
    assert res_bad.passed is False


def test_citations_fetched_empty_citations_passes():
    """Vacuous success: no URLs to verify against fetch_url args."""
    trace = make_fake_trace(citations=[])
    res = check_hard(HardAssertion(type="citations_fetched", value=True), trace)
    assert res.passed is True


def test_corpus_urls_in_answer_fetched_ok():
    trace = make_fake_trace(
        final_answer="- https://corpus.local/voyager-timeline",
        citations=["https://corpus.local/voyager-timeline"],
    )
    res = check_hard(HardAssertion(type="corpus_urls_in_answer_fetched", value=True), trace)
    assert res.passed is True


def test_corpus_urls_in_answer_fetched_missing_fetch():
    trace = make_fake_trace(
        final_answer="- https://corpus.local/voyager-timeline",
        citations=[],
        messages=[
            {"role": "user", "content": "Tell me about Voyager 1 and the heliopause."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc_finish_1",
                        "name": "finish",
                        "args": {"answer": "x", "citations": []},
                    }
                ],
            },
        ],
    )
    res = check_hard(HardAssertion(type="corpus_urls_in_answer_fetched", value=True), trace)
    assert res.passed is False


def test_corpus_urls_in_answer_fetched_vacuous_no_url_in_answer():
    trace = make_fake_trace(final_answer="No URLs here.")
    res = check_hard(HardAssertion(type="corpus_urls_in_answer_fetched", value=True), trace)
    assert res.passed is True


def test_unknown_hard_assertion_type():
    bad_assertion = HardAssertion.model_construct(type="not_a_real_type", value=None)
    trace = make_fake_trace()
    res = check_hard(bad_assertion, trace)
    assert res.passed is False
    assert "unknown hard assertion" in res.reason

