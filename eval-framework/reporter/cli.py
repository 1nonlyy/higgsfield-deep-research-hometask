from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


class _AssertionLike(Protocol):
    passed: bool
    reason: str


class _TraceLike(Protocol):
    cost_usd: float
    wall_time_ms: int
    messages: list[Any]
    error: str | None
    stopped_reason: str


class CaseResult(Protocol):
    case_id: str
    passed: bool
    assertion_results: list[_AssertionLike]
    trace: _TraceLike
    cost_usd: float
    wall_time_ms: int
    tool_call_count: int
    repeats_summary: Any | None


_ANSI = {
    "reset": "\x1b[0m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    xs = sorted(values)
    k = (len(xs) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(xs[int(k)])
    d0 = xs[int(f)] * (c - k)
    d1 = xs[int(c)] * (k - f)
    return float(d0 + d1)


def _first_failure_reason(r: CaseResult) -> str:
    for a in (getattr(r, "assertion_results", None) or []):
        try:
            if not bool(getattr(a, "passed", True)):
                reason = str(getattr(a, "reason", "") or "").strip()
                if reason:
                    return reason
        except Exception:
            continue

    trace = getattr(r, "trace", None)
    if trace is not None:
        err = str(getattr(trace, "error", "") or "").strip()
        if err:
            return err
        stopped = str(getattr(trace, "stopped_reason", "") or "").strip()
        if stopped:
            return f"stopped_reason={stopped}"

    return "failed"


def _format_money(x: float) -> str:
    try:
        v = float(x)
    except Exception:
        v = 0.0
    return f"${v:.4f}"


def _format_ms(ms: float) -> str:
    try:
        v = float(ms)
    except Exception:
        v = 0.0
    if v >= 10_000:
        return f"{v/1000.0:.1f}s"
    if v >= 1000:
        return f"{v/1000.0:.2f}s"
    return f"{v:.0f}ms"


def _coerce_prev_mapping(obj: Any) -> dict[str, bool]:
    """
    Accepts a few shapes:
      - {"results": [...]}  where each item has case_id and passed
      - [{"case_id": "...", "passed": true}, ...]
      - {"case_id": true/false, ...}
    """
    if obj is None:
        return {}

    if isinstance(obj, dict):
        if "results" in obj and isinstance(obj["results"], list):
            out: dict[str, bool] = {}
            for item in obj["results"]:
                if isinstance(item, dict):
                    cid = item.get("case_id") or item.get("id")
                    if isinstance(cid, str):
                        out[cid] = bool(item.get("passed", False))
                else:
                    cid = getattr(item, "case_id", None) or getattr(item, "id", None)
                    if isinstance(cid, str):
                        out[cid] = bool(getattr(item, "passed", False))
            return out

        # mapping of case_id -> passed
        if all(isinstance(k, str) for k in obj.keys()):
            # If values are objects, try to read `.passed`
            out: dict[str, bool] = {}
            for k, v in obj.items():
                if isinstance(v, bool):
                    out[k] = v
                elif isinstance(v, dict):
                    out[k] = bool(v.get("passed", False))
                else:
                    out[k] = bool(getattr(v, "passed", False))
            return out

    if isinstance(obj, list):
        out: dict[str, bool] = {}
        for item in obj:
            if isinstance(item, dict):
                cid = item.get("case_id") or item.get("id")
                if isinstance(cid, str):
                    out[cid] = bool(item.get("passed", False))
            else:
                cid = getattr(item, "case_id", None) or getattr(item, "id", None)
                if isinstance(cid, str):
                    out[cid] = bool(getattr(item, "passed", False))
        return out

    return {}


def _load_prev_report(path: str) -> dict[str, bool]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        obj = json.loads(raw)
    except Exception:
        return {}
    return _coerce_prev_mapping(obj)


def _to_jsonable(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, dict):
        return {str(k): _to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]
    if is_dataclass(x):
        return _to_jsonable(asdict(x))
    if hasattr(x, "model_dump"):
        try:
            return _to_jsonable(x.model_dump())
        except Exception:
            pass
    if hasattr(x, "__dict__"):
        try:
            return _to_jsonable(vars(x))
        except Exception:
            pass
    return str(x)


def _variance_stats(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {"min": float(min(xs)), "max": float(max(xs)), "mean": float(sum(xs) / len(xs))}


def _collect_repeat_series(results: list[CaseResult]) -> dict[str, list[float]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in results:
        rs = getattr(r, "repeats_summary", None)
        if not isinstance(rs, dict):
            continue
        for row in rs.get("per_repeat") or []:
            if not isinstance(row, dict):
                continue
            buckets["cost_usd"].append(float(row.get("cost_usd", 0.0) or 0.0))
            buckets["wall_time_ms"].append(float(row.get("wall_time_ms", 0) or 0))
            buckets["tool_call_count"].append(float(row.get("tool_call_count", 0) or 0))
            for mk, mv in (row.get("metric_scores") or {}).items():
                try:
                    buckets[f"metric:{mk}"].append(float(mv))
                except (TypeError, ValueError):
                    continue
    return buckets


def _has_multi_repeat_runs(results: list[CaseResult]) -> bool:
    for r in results:
        rs = getattr(r, "repeats_summary", None)
        if isinstance(rs, dict) and int(rs.get("repeat_count", 1) or 1) > 1:
            return True
    return False


def _table(rows: list[tuple[str, str]], *, title: str | None = None) -> str:
    key_w = max((len(k) for k, _ in rows), default=0)
    val_w = max((len(v) for _, v in rows), default=0)
    line = f"+-{'-'*key_w}-+-{'-'*val_w}-+"
    out: list[str] = []
    if title:
        out.append(title)
    out.append(line)
    for k, v in rows:
        out.append(f"| {k.ljust(key_w)} | {v.ljust(val_w)} |")
    out.append(line)
    return "\n".join(out)


def print_report(results: list[CaseResult], prev_report_path: str | None = None) -> None:
    # Per-case lines
    for r in results:
        ok = bool(getattr(r, "passed", False))
        case_id = str(getattr(r, "case_id", ""))
        rs = getattr(r, "repeats_summary", None)
        repeat_suffix = ""
        if isinstance(rs, dict) and int(rs.get("repeat_count", 1) or 1) > 1:
            pc = int(rs.get("pass_count", 0) or 0)
            rc = int(rs.get("repeat_count", 0) or 0)
            flaky = bool(rs.get("flaky", pc < rc))
            flake = f" [{_ANSI['yellow']}FLAKY{_ANSI['reset']}]" if flaky else ""
            repeat_suffix = f" ({pc}/{rc} passed){flake}"

        if ok:
            print(f"✅ {case_id}{repeat_suffix}")
        else:
            reason = _first_failure_reason(r)
            print(f"❌ {case_id}{repeat_suffix} — {reason}")

    flaky_cases = [
        str(getattr(r, "case_id", ""))
        for r in results
        if isinstance(getattr(r, "repeats_summary", None), dict)
        and bool((getattr(r, "repeats_summary") or {}).get("flaky"))
    ]

    # Aggregate summary
    total = len(results)
    passed = sum(1 for r in results if bool(getattr(r, "passed", False)))
    pass_rate = (passed / total) if total else 0.0
    total_cost = sum(float(getattr(r, "cost_usd", 0.0) or 0.0) for r in results)
    lat_ms = [float(getattr(r, "wall_time_ms", 0) or 0) for r in results]
    tool_calls = [int(getattr(r, "tool_call_count", 0) or 0) for r in results]
    mean_tool_calls = (sum(tool_calls) / total) if total else 0.0

    rows = [
        ("pass rate", f"{passed}/{total} ({pass_rate*100:.1f}%)"),
        ("total cost", _format_money(total_cost)),
        ("p50 latency", _format_ms(_percentile(lat_ms, 50))),
        ("p95 latency", _format_ms(_percentile(lat_ms, 95))),
        ("mean tool calls", f"{mean_tool_calls:.2f}"),
    ]
    print("")
    print(_table(rows, title="Aggregate"))

    if _has_multi_repeat_runs(results):
        series = _collect_repeat_series(results)
        var_rows: list[tuple[str, str]] = []
        for key in ("cost_usd", "wall_time_ms", "tool_call_count"):
            xs = series.get(key, [])
            if not xs:
                continue
            st = _variance_stats(xs)
            if key == "cost_usd":
                var_rows.append(
                    (
                        f"{key} (all runs)",
                        f"min {_format_money(st['min'])} / mean {_format_money(st['mean'])} / max {_format_money(st['max'])}",
                    )
                )
            elif key == "wall_time_ms":
                var_rows.append(
                    (
                        f"{key} (all runs)",
                        f"min {_format_ms(st['min'])} / mean {_format_ms(st['mean'])} / max {_format_ms(st['max'])}",
                    )
                )
            else:
                var_rows.append(
                    (
                        f"{key} (all runs)",
                        f"min {st['min']:.0f} / mean {st['mean']:.2f} / max {st['max']:.0f}",
                    )
                )
        for mk in sorted(k for k in series if k.startswith("metric:")):
            xs = series[mk]
            if not xs:
                continue
            st = _variance_stats(xs)
            name = mk.split(":", 1)[1]
            var_rows.append(
                (f"score:{name} (all runs)", f"min {st['min']:.4f} / mean {st['mean']:.4f} / max {st['max']:.4f}")
            )
        if var_rows:
            print("")
            print(_table(var_rows, title="Repeat variance (min / mean / max)"))

    if flaky_cases:
        print("")
        print("Flaky cases (pass rate < 100%)")
        for cid in flaky_cases:
            print(f"  - {cid}")

    # Diff vs previous report
    if prev_report_path:
        prev = _load_prev_report(prev_report_path)
        if prev:
            regressed: list[str] = []
            improved: list[str] = []
            for r in results:
                cid = str(getattr(r, "case_id", ""))
                if cid not in prev:
                    continue
                was = bool(prev[cid])
                now = bool(getattr(r, "passed", False))
                if was and not now:
                    regressed.append(cid)
                elif (not was) and now:
                    improved.append(cid)

            if regressed or improved:
                print("")
                print("Diff vs previous")
                if regressed:
                    for cid in regressed:
                        print(f"{_ANSI['red']}REGRESSED{_ANSI['reset']} {cid}")
                if improved:
                    for cid in improved:
                        print(f"{_ANSI['green']}IMPROVED{_ANSI['reset']} {cid}")
            else:
                print("")
                print(f"{_ANSI['dim']}Diff vs previous: no changes{_ANSI['reset']}")
        else:
            print("")
            print(f"{_ANSI['dim']}Diff vs previous: could not read {prev_report_path!r}{_ANSI['reset']}")

