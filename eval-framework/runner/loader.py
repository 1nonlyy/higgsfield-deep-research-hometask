"""YAML test case loader + Pydantic schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class HardAssertion(BaseModel):
    type: Literal[
        "tool_called",
        "tool_not_called",
        "answer_contains",
        "answer_contains_any",
        "answer_not_contains",
        "tool_call_count_lte",
        "stopped_reason",
        "citations_fetched",
        "corpus_urls_in_answer_fetched",
    ]
    value: Any


class SoftAssertion(BaseModel):
    metric: str  # e.g. "correctness", "refusal_correct"
    rubric_file: str  # path relative to eval-framework/rubrics/
    weight: float = 1.0


class ExpectedBehavior(BaseModel):
    hard: list[HardAssertion] = Field(default_factory=list)
    soft: list[SoftAssertion] = Field(default_factory=list)


class TestCase(BaseModel):
    """Represents one YAML test case."""

    # Not a pytest test class; name matches Test* pattern.
    __test__: ClassVar[bool] = False

    id: str
    input: str
    expected_behavior: ExpectedBehavior
    tags: list[str] | None = None


def _ensure_list(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _load_cases_from_yaml_paths(yaml_paths: list[Path]) -> list[TestCase]:
    cases: list[TestCase] = []
    for p in yaml_paths:
        raw_text = p.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw_text)

        # Allow either a single case dict, or a list of case dicts per file.
        for obj in _ensure_list(loaded):
            if not isinstance(obj, dict):
                raise ValueError(f"{p.name}: expected mapping, got {type(obj).__name__}")
            try:
                cases.append(TestCase.model_validate(obj))
            except Exception as e:
                raise ValueError(f"{p.name}: invalid test case schema: {e}") from e

    return cases


def load_cases_from_paths(paths: list[str]) -> list[TestCase]:
    """Load cases from directories (all *.yaml/*.yml) and/or individual YAML files."""
    if not paths:
        raise ValueError("At least one --cases path is required")

    yaml_paths: list[Path] = []
    for raw in paths:
        base = Path(raw).resolve()
        if not base.exists():
            raise FileNotFoundError(f"Cases path not found: {base}")
        if base.is_dir():
            yaml_paths.extend(sorted([*base.glob("*.yaml"), *base.glob("*.yml")]))
        elif base.suffix.lower() in (".yaml", ".yml"):
            yaml_paths.append(base)
        else:
            raise ValueError(f"Cases path must be a directory or .yaml/.yml file: {base}")

    seen: set[Path] = set()
    deduped: list[Path] = []
    for p in yaml_paths:
        key = p.resolve()
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    return _load_cases_from_yaml_paths(deduped)


def load_cases(directory: str) -> list[TestCase]:
    """Read all .yaml/.yml files in `directory` and validate them."""
    base = Path(directory)
    if not base.exists():
        raise FileNotFoundError(f"Cases directory not found: {directory}")
    if not base.is_dir():
        raise NotADirectoryError(f"Cases path is not a directory: {directory}")
    paths = sorted([*base.glob("*.yaml"), *base.glob("*.yml")])
    return _load_cases_from_yaml_paths(paths)


# ---------------------------------------------------------------------------
# Trace schema (matches `agent.RunResult.to_dict()` / README "Trace format")
# ---------------------------------------------------------------------------


class TraceToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class TraceMessage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    role: str
    # Agent trace uses `content` for system/user, but `text` for assistant.
    content: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("content", "text"),
        serialization_alias="content",
    )
    tool_calls: list[TraceToolCall] | None = None
    latency_ms: int | None = None


class Trace(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    run_id: str
    question: str
    model: str
    messages: list[TraceMessage]
    final_answer: str | None = None
    citations: list[str] = Field(default_factory=list)
    stopped_reason: str
    total_tokens: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    wall_time_ms: int = 0
    error: str | None = None

