from __future__ import annotations

import html as _html
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment


class _AssertionLike(Protocol):
    passed: bool
    reason: str


class _TraceLike(Protocol):
    cost_usd: float
    wall_time_ms: int
    messages: list[Any]
    final_answer: str | None
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
    repeats_summary: Any | None  # optional: pass_count, repeat_count, flaky, per_repeat


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


def _safe_text(x: Any, *, max_chars: int = 80_000) -> str:
    s = "" if x is None else str(x)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if len(s) > max_chars:
        s = s[: max_chars - 1] + "…"
    return s


def _first_failure_reason(assertion_results: list[_AssertionLike]) -> str:
    for a in (assertion_results or []):
        try:
            if not bool(getattr(a, "passed", True)):
                reason = str(getattr(a, "reason", "") or "").strip()
                if reason:
                    return reason
        except Exception:
            continue
    return ""


def _pair_tool_calls(trace_messages: list[Any]) -> list[dict[str, Any]]:
    """
    Produces a linear "timeline" of steps:
      - user/assistant text messages
      - tool call blocks with (name, args, output)

    Heuristic pairing: tool outputs are matched by scanning forward for the
    next tool-role message with the same name; if not found, we still render
    the call without output.
    """
    msgs = trace_messages or []

    # Pre-normalize to dicts to avoid template surprises.
    normalized: list[dict[str, Any]] = []
    for m in msgs:
        if isinstance(m, dict):
            normalized.append(m)
        elif hasattr(m, "model_dump"):
            try:
                normalized.append(m.model_dump())
            except Exception:
                normalized.append(_to_jsonable(m))
        else:
            normalized.append(_to_jsonable(m))

    # Build index of tool messages by name in order.
    tool_positions: dict[str, list[int]] = {}
    for i, m in enumerate(normalized):
        if str(m.get("role", "")) == "tool":
            name = str(m.get("name", "") or "")
            tool_positions.setdefault(name, []).append(i)

    # For each tool name, keep pointer to next unused tool msg position.
    tool_ptr: dict[str, int] = {k: 0 for k in tool_positions.keys()}

    steps: list[dict[str, Any]] = []
    for i, m in enumerate(normalized):
        role = str(m.get("role", "") or "")
        if role in {"user", "system"}:
            steps.append(
                {
                    "kind": "message",
                    "role": role,
                    "text": _safe_text(m.get("content")),
                }
            )
            continue

        if role == "assistant":
            # If assistant has tool calls, render calls as separate steps.
            tcs = m.get("tool_calls") or []
            if isinstance(tcs, list) and tcs:
                # Optional assistant text before tool calls (rare)
                assistant_text = _safe_text(m.get("content"))
                if assistant_text.strip():
                    steps.append({"kind": "message", "role": "assistant", "text": assistant_text})

                for tc in tcs:
                    if not isinstance(tc, dict):
                        tc = _to_jsonable(tc)
                    name = str(tc.get("name", "") or "")
                    args = tc.get("args") or {}
                    output = None

                    # Consume next unused tool message of this name that appears after i.
                    positions = tool_positions.get(name, [])
                    p = tool_ptr.get(name, 0)
                    while p < len(positions) and positions[p] <= i:
                        p += 1
                    if p < len(positions):
                        out_idx = positions[p]
                        tool_ptr[name] = p + 1
                        output = normalized[out_idx].get("content")

                    steps.append(
                        {
                            "kind": "tool",
                            "name": name,
                            "args_json": json.dumps(_to_jsonable(args), ensure_ascii=False, indent=2),
                            "output_json": json.dumps(_to_jsonable(output), ensure_ascii=False, indent=2),
                        }
                    )
            else:
                steps.append(
                    {
                        "kind": "message",
                        "role": "assistant",
                        "text": _safe_text(m.get("content")),
                    }
                )
            continue

        # tool messages are rendered as part of the tool call block above; skip them here
        if role == "tool":
            continue

        # fallback
        steps.append({"kind": "message", "role": role or "unknown", "text": _safe_text(m)})

    return steps


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Eval Report</title>
  <style>
    :root{
      --bg: #0b1020;
      --panel: #0f1733;
      --panel2: #0c132b;
      --text: #e7ecff;
      --muted: #a9b4da;
      --border: rgba(255,255,255,0.10);
      --green: #2bd576;
      --red: #ff4d4d;
      --yellow: #ffd166;
      --chip: rgba(255,255,255,0.08);
      --code: #0a0f1f;
      --shadow: 0 14px 40px rgba(0,0,0,0.45);
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji","Segoe UI Emoji";
    }
    *{ box-sizing: border-box; }
    body{
      margin:0;
      font-family: var(--sans);
      color: var(--text);
      background: radial-gradient(1200px 800px at 20% 0%, rgba(69,117,255,0.18), transparent 60%),
                  radial-gradient(900px 600px at 90% 10%, rgba(43,213,118,0.10), transparent 55%),
                  var(--bg);
      height: 100vh;
      overflow: hidden;
    }
    .app{
      display: grid;
      grid-template-columns: 320px 1fr;
      height: 100vh;
    }
    .sidebar{
      border-right: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent 30%), var(--panel2);
      padding: 14px 12px;
      overflow: auto;
    }
    .header{
      display:flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      padding: 6px 6px 10px;
    }
    .header h1{
      font-size: 14px;
      margin:0;
      letter-spacing: 0.2px;
      color: var(--text);
    }
    .header .meta{
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .search{
      width: 100%;
      margin: 8px 6px 12px;
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--panel2);
      padding-top: 8px;
    }
    .search input{
      width: 100%;
      padding: 10px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,0.25);
      color: var(--text);
      outline: none;
    }
    .case-list{
      display:flex;
      flex-direction: column;
      gap: 8px;
      padding: 0 6px 14px;
    }
    .case{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 10px;
      background: rgba(255,255,255,0.02);
      cursor: pointer;
      box-shadow: 0 0 0 rgba(0,0,0,0);
      transition: transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease;
    }
    .case:hover{
      transform: translateY(-1px);
      box-shadow: var(--shadow);
      border-color: rgba(255,255,255,0.20);
    }
    .case.active{
      border-color: rgba(69,117,255,0.55);
      background: rgba(69,117,255,0.08);
    }
    .case-top{
      display:flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .badge{
      font-size: 12px;
      font-weight: 650;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--chip);
      border: 1px solid var(--border);
    }
    .badge.pass{ color: var(--green); border-color: rgba(43,213,118,0.35); background: rgba(43,213,118,0.08);}
    .badge.fail{ color: var(--red); border-color: rgba(255,77,77,0.35); background: rgba(255,77,77,0.07);}
    .badge.flaky{ color: var(--yellow); border-color: rgba(255,209,102,0.45); background: rgba(255,209,102,0.10);}
    .case-id{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--text);
      word-break: break-word;
      line-height: 1.2;
    }
    .case-sub{
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
      display:flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .chip{
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--chip);
      border: 1px solid var(--border);
      font-family: var(--mono);
      font-size: 11px;
    }

    .main{
      padding: 16px 18px 18px;
      overflow: auto;
    }
    .panel{
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent 40%), var(--panel);
      border-radius: 16px;
      padding: 14px 14px;
      box-shadow: var(--shadow);
    }
    .case-title{
      display:flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 12px;
    }
    .case-title h2{
      margin:0;
      font-size: 14px;
      letter-spacing: 0.2px;
      font-family: var(--mono);
    }
    .case-title .right{
      display:flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .assertions{
      margin: 10px 0 14px;
      padding: 10px 10px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: rgba(0,0,0,0.18);
    }
    .assertions h3{
      margin: 0 0 8px;
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.2px;
      text-transform: uppercase;
    }
    .assertion{
      display:flex;
      align-items: flex-start;
      gap: 10px;
      padding: 7px 6px;
      border-radius: 10px;
    }
    .assertion.fail{
      background: rgba(255,77,77,0.08);
      border: 1px solid rgba(255,77,77,0.20);
    }
    .assertion.pass{
      background: rgba(43,213,118,0.06);
      border: 1px solid rgba(43,213,118,0.16);
    }
    .assertion .mark{
      font-family: var(--mono);
      width: 20px;
      text-align:center;
      margin-top: 1px;
    }
    .assertion .text{
      font-size: 12px;
      color: var(--text);
      line-height: 1.35;
      white-space: pre-wrap;
      word-break: break-word;
      flex: 1;
    }

    .timeline{
      display:flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 10px;
    }
    .step{
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      background: rgba(255,255,255,0.02);
    }
    .step .step-head{
      display:flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,0.07);
      background: rgba(0,0,0,0.12);
    }
    .role{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    .step .body{
      padding: 10px 12px;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .toolname{
      font-family: var(--mono);
      font-size: 12px;
      color: var(--text);
    }
    details.tool{
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 14px;
      background: rgba(0,0,0,0.12);
    }
    details.tool > summary{
      cursor: pointer;
      list-style: none;
      padding: 10px 12px;
      display:flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-family: var(--mono);
      font-size: 12px;
      color: var(--text);
    }
    details.tool > summary::-webkit-details-marker{ display:none; }
    .caret{
      color: var(--muted);
      font-size: 12px;
    }
    .kv{
      display:grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      padding: 0 12px 12px;
    }
    .kv .box{
      border: 1px solid rgba(255,255,255,0.09);
      border-radius: 12px;
      background: var(--code);
      overflow: hidden;
    }
    .kv .label{
      padding: 8px 10px;
      font-size: 11px;
      color: var(--muted);
      border-bottom: 1px solid rgba(255,255,255,0.08);
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }
    pre{
      margin:0;
      padding: 10px 10px;
      overflow: auto;
      font-family: var(--mono);
      font-size: 11px;
      line-height: 1.4;
      color: #dbe3ff;
    }
    .empty{
      color: var(--muted);
      font-size: 13px;
      padding: 16px;
    }
    .hint{
      font-size: 12px;
      color: var(--muted);
      margin-top: 10px;
    }
    @media (max-width: 960px){
      body{ overflow:auto; height:auto;}
      .app{ grid-template-columns: 1fr; height:auto;}
      .sidebar{ position: relative; height:auto; }
      .main{ height:auto; overflow: visible; }
      .kv{ grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="header">
        <h1>Eval report</h1>
        <div class="meta">{{ passed_count }}/{{ total_count }} cases passed{% if total_runs > total_count %} ({{ total_runs }} runs){% endif %}</div>
      </div>
      <div class="search">
        <input id="search" type="text" placeholder="Filter cases (id / reason)..." autocomplete="off" />
      </div>
      <div id="caseList" class="case-list">
        {% for c in cases %}
        <div class="case" data-case-id="{{ c.case_id }}" data-search="{{ (c.case_id ~ ' ' ~ (c.failure_reason or '')).lower()|e }}" onclick="selectCase('{{ c.case_id }}')">
          <div class="case-top">
            <div class="case-id">{{ c.case_id }}</div>
            <div style="display:flex; align-items:center; gap:6px; flex-wrap: wrap; justify-content: flex-end;">
            {% if c.repeat_label %}
              <div class="badge" style="color: var(--muted); border-color: rgba(255,255,255,0.18);">{{ c.repeat_label }}</div>
            {% endif %}
            {% if c.is_flaky %}
              <div class="badge flaky">FLAKY</div>
            {% endif %}
            {% if c.passed %}
              <div class="badge pass">PASS</div>
            {% else %}
              <div class="badge fail">FAIL</div>
            {% endif %}
            </div>
          </div>
          <div class="case-sub">
            <span class="chip">lat {{ c.wall_time_ms }}ms</span>
            <span class="chip">tools {{ c.tool_call_count }}</span>
            <span class="chip">cost ${{ "%.4f"|format(c.cost_usd) }}</span>
            {% if not c.passed and c.failure_reason %}
              <span class="chip" style="border-color: rgba(255,77,77,0.30); background: rgba(255,77,77,0.06); color: #ffd6d6;">{{ c.failure_reason }}</span>
            {% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
      <div class="hint">Tip: click a case, then use Ctrl/Cmd+F inside the timeline.</div>
    </aside>

    <main class="main">
      <div class="panel" id="detailPanel">
        <div class="empty">Select a case on the left.</div>
      </div>
    </main>
  </div>

  <script>
    const CASES = {{ cases_json | safe }};

    function esc(s){
      return (s ?? "").toString()
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function buildAssertions(c){
      const items = (c.assertions ?? []);
      if(items.length === 0) return "";
      let html = `<div class="assertions"><h3>Assertions</h3>`;
      for(const a of items){
        const ok = !!a.passed;
        const cls = ok ? "pass" : "fail";
        const mark = ok ? "✅" : "❌";
        const reason = esc(a.reason ?? "");
        html += `<div class="assertion ${cls}"><div class="mark">${mark}</div><div class="text">${reason}</div></div>`;
      }
      html += `</div>`;
      return html;
    }

    function buildTimeline(c){
      const steps = (c.timeline ?? []);
      if(steps.length === 0) return `<div class="empty">No trace messages for this case.</div>`;

      let out = `<div class="timeline">`;
      for(const s of steps){
        if(s.kind === "message"){
          const role = esc(s.role ?? "unknown");
          const text = esc(s.text ?? "");
          out += `
            <div class="step">
              <div class="step-head">
                <div class="role">${role}</div>
              </div>
              <div class="body">${text}</div>
            </div>`;
        } else if(s.kind === "tool"){
          const name = esc(s.name ?? "tool");
          const args = esc(s.args_json ?? "");
          const outp = esc(s.output_json ?? "");
          out += `
            <details class="tool step">
              <summary>
                <span class="toolname">tool: ${name}</span>
                <span class="caret">click to expand</span>
              </summary>
              <div class="kv">
                <div class="box">
                  <div class="label">inputs</div>
                  <pre>${args}</pre>
                </div>
                <div class="box">
                  <div class="label">outputs</div>
                  <pre>${outp}</pre>
                </div>
              </div>
            </details>`;
        }
      }
      out += `</div>`;
      return out;
    }

    function selectCase(caseId){
      const c = CASES.find(x => x.case_id === caseId);
      if(!c) return;

      for(const el of document.querySelectorAll(".case")){
        el.classList.toggle("active", el.getAttribute("data-case-id") === caseId);
      }

      const repeatBadges = [];
      if(c.repeat_label){
        repeatBadges.push(`<div class="badge" style="color: var(--muted); border-color: rgba(255,255,255,0.18);">${esc(c.repeat_label)}</div>`);
      }
      if(c.is_flaky){
        repeatBadges.push(`<div class="badge flaky">FLAKY</div>`);
      }
      const badge = c.passed ? `<div class="badge pass">PASS</div>` : `<div class="badge fail">FAIL</div>`;
      const reason = (!c.passed && c.failure_reason) ? `<span class="chip" style="border-color: rgba(255,77,77,0.35); background: rgba(255,77,77,0.06); color: #ffd6d6;">${esc(c.failure_reason)}</span>` : "";

      const header = `
        <div class="case-title">
          <div style="display:flex; align-items:center; gap:10px; flex-wrap: wrap;">
            <h2>${esc(c.case_id)}</h2>
            ${repeatBadges.join("")}
            ${badge}
            ${reason}
          </div>
          <div class="right">
            <span class="chip">lat ${esc(c.wall_time_ms)}ms</span>
            <span class="chip">tools ${esc(c.tool_call_count)}</span>
            <span class="chip">cost $${esc((c.cost_usd ?? 0).toFixed(4))}</span>
          </div>
        </div>
      `;

      const assertions = buildAssertions(c);
      const timeline = buildTimeline(c);

      document.getElementById("detailPanel").innerHTML = header + assertions + timeline;
      window.location.hash = encodeURIComponent(caseId);
    }

    function applyFilter(q){
      const query = (q ?? "").trim().toLowerCase();
      for(const el of document.querySelectorAll(".case")){
        const hay = (el.getAttribute("data-search") ?? "");
        const ok = !query || hay.includes(query);
        el.style.display = ok ? "" : "none";
      }
    }

    document.getElementById("search").addEventListener("input", (e) => {
      applyFilter(e.target.value);
    });

    // On load: select first case, or hash.
    (function(){
      const fromHash = decodeURIComponent((window.location.hash || "").replace(/^#/, ""));
      if(fromHash){
        selectCase(fromHash);
        return;
      }
      if(CASES.length > 0) selectCase(CASES[0].case_id);
    })();
  </script>
</body>
</html>
"""


def render_html(results: list[CaseResult]) -> str:
    cases: list[dict[str, Any]] = []
    passed_count = 0
    total_runs = 0

    for r in results:
        case_id = str(getattr(r, "case_id", ""))
        passed = bool(getattr(r, "passed", False))
        if passed:
            passed_count += 1

        rs = getattr(r, "repeats_summary", None)
        repeat_label: str | None = None
        is_flaky = False
        run_n = 1
        if isinstance(rs, dict):
            run_n = int(rs.get("repeat_count", 1) or 1)
            if run_n > 1:
                pc = int(rs.get("pass_count", 0) or 0)
                repeat_label = f"{pc}/{run_n} passed"
                is_flaky = bool(rs.get("flaky", pc < run_n))
        total_runs += max(1, run_n)

        assertion_results = getattr(r, "assertion_results", None) or []
        assertions = [
            {"passed": bool(getattr(a, "passed", False)), "reason": _safe_text(getattr(a, "reason", ""))}
            for a in assertion_results
        ]
        failure_reason = _first_failure_reason(assertion_results)
        trace = getattr(r, "trace", None)
        timeline = _pair_tool_calls(getattr(trace, "messages", None) if trace is not None else [])

        cases.append(
            {
                "case_id": case_id,
                "passed": passed,
                "cost_usd": float(getattr(r, "cost_usd", 0.0) or 0.0),
                "wall_time_ms": int(getattr(r, "wall_time_ms", 0) or 0),
                "tool_call_count": int(getattr(r, "tool_call_count", 0) or 0),
                "failure_reason": _safe_text(failure_reason, max_chars=240),
                "assertions": assertions,
                "timeline": timeline,
                "repeat_label": repeat_label,
                "is_flaky": is_flaky,
            }
        )

    env = Environment(autoescape=True)
    tmpl = env.from_string(_TEMPLATE)
    return tmpl.render(
        cases=cases,
        total_count=len(cases),
        passed_count=passed_count,
        total_runs=total_runs,
        cases_json=json.dumps(_to_jsonable(cases), ensure_ascii=False),
    )


def write_html_report(results: list[CaseResult], out_path: str) -> str:
    html_text = render_html(results)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return str(path)

