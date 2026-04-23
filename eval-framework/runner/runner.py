"""Evaluation runner.

Runs the `deep-research-lite` agent as a black box for each test case and
captures a structured trace suitable for scoring and reporting.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from .loader import TestCase, Trace
from .retry import with_retry

_agent_import_lock = threading.Lock()
_run_agent_by_dir: dict[str, Callable[..., Any]] = {}


def _import_run_agent(agent_dir: str) -> Callable[..., Any]:
    """Import `run_agent` from the deep-research-lite agent directory (cached, thread-safe)."""
    agent_path = str(Path(agent_dir).resolve())
    with _agent_import_lock:
        cached = _run_agent_by_dir.get(agent_path)
        if cached is not None:
            return cached
        if agent_path not in sys.path:
            sys.path.insert(0, agent_path)
        mod = import_module("agent")
        run_agent = getattr(mod, "run_agent", None)
        if run_agent is None:
            raise ImportError(f"`run_agent` not found in {agent_dir}/agent.py")
        _run_agent_by_dir[agent_path] = run_agent
        return run_agent


def run_case(case: TestCase, agent_dir: str) -> Trace:
    """Run a single eval case and persist its trace to traces/<run_id>.json."""
    run_agent = _import_run_agent(agent_dir)

    try:
        result = run_agent(case.input)
        trace_dict = result.to_dict() if hasattr(result, "to_dict") else result
        trace = Trace.model_validate(trace_dict)
    except Exception as e:
        # If the agent crashes before emitting a trace, return a minimal error trace.
        trace = Trace.model_validate(
            {
                "run_id": "error",
                "question": case.input,
                "model": "",
                "messages": [],
                "final_answer": None,
                "citations": [],
                "stopped_reason": "error",
                "total_tokens": {"input": 0, "output": 0},
                "cost_usd": 0.0,
                "wall_time_ms": 0,
                "error": f"{type(e).__name__}: {e}",
            }
        )
        trace_dict = trace.model_dump()

    traces_dir = Path(agent_dir) / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    out_path = traces_dir / f"{trace.run_id}.json"
    out_path.write_text(json.dumps(trace_dict, indent=2, default=str), encoding="utf-8")

    return trace


def run_suite(cases: list[TestCase], concurrency: int = 4, agent_dir: str | None = None) -> list[Trace]:
    """Run cases concurrently (bounded) and return traces in input order."""

    # Default agent_dir: repo root (two levels above this file).
    resolved_agent_dir = agent_dir or str(Path(__file__).resolve().parents[2])
    sem = asyncio.Semaphore(concurrency)

    async def run_one(i: int, case: TestCase) -> tuple[int, Trace]:
        async with sem:
            async def _call() -> Trace:
                # run_agent is sync; offload to a worker thread.
                return await asyncio.to_thread(run_case, case, resolved_agent_dir)

            trace = await with_retry(_call, max_retries=3, base_delay=1.0)
            # Never retry on agent errors.
            if trace.stopped_reason == "error":
                return i, trace
            return i, trace  # type: ignore[return-value]

    async def _run_all() -> list[Trace]:
        tasks = [run_one(i, c) for i, c in enumerate(cases)]
        results = await asyncio.gather(*tasks)
        out: list[Trace] = [None] * len(cases)  # type: ignore[list-item]
        for idx, trace in results:
            out[idx] = trace
        return out

    return asyncio.run(_run_all())

