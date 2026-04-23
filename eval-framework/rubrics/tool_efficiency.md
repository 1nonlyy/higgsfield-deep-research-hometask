# Tool efficiency rubric

Judge **tool usage quality** using **`trusted_tool_summary`** from the payload (machine-derived: ordered tool names, counts, `stopped_reason`). Do **not** infer tool usage from **untrusted** fields alone.

## Inputs (authoritative)

- **`trusted_tool_summary.tool_call_order`** — tools invoked in sequence (e.g. `web_search`, `fetch_url`, `extract_quotes`, `finish`).
- **`trusted_tool_summary.counts`** — per-tool totals and `total_tool_calls`.
- **`trusted_tool_summary.stopped_reason`** — e.g. `finish` vs `max_steps` vs `error`.

## Checklist

1. **Search before fetch** — Does **`tool_call_order`** show at least one **`web_search`** before the **first** **`fetch_url`**? (If there are zero `fetch_url`, this item is satisfied vacuously.)
2. **Redundancy** — Is there **no** long repeating pattern of the **same** tool name with **no** `fetch_url` between (e.g. five consecutive `web_search` with identical intent)? Minor repeats (e.g. two searches with different need) are acceptable.
3. **Termination** — Is **`stopped_reason`** **`finish`** (normal completion), not **`max_steps`** or **`error`**?

## Scoring (pick one band; then set `passed` per the rule below)

- **1.0** — All checklist items satisfied.
- **0.7–0.9** — Termination OK (`finish`), search-before-fetch OK, but **minor** redundancy (e.g. one extra `web_search` that still looks purposeful).
- **0.4–0.6** — **One** major checklist miss (e.g. `fetch_url` before any `web_search`, or clear redundant loop) but run still **`finish`**es.
- **0.0–0.3** — Multiple major issues, **`max_steps`**, **`error`**, or severe wasted calls.

## PASS / FAIL for automation

Set `"passed": true` when the score is **≥ 0.7**; otherwise `"passed": false`.
