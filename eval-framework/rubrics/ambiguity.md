# Ambiguity rubric

Judge how the agent handled an **unclear or underspecified** question: **untrusted.case_question** admits multiple valid readings, omits key constraints, or uses vague referents so a single answer is not well-defined.

## When this rubric applies

Apply only if **two or more materially different answers** would be reasonable without extra assumptions. If the question is specific enough that one reading is clearly intended, score against that single reading (treat as not ambiguous for this rubric).

## Checklist

1. **Acknowledgment** — Does **untrusted.agent_final_answer** **explicitly** note uncertainty, multiple meanings, missing constraints, or the need to assume something?
2. **Resolution strategy** — Did the agent either **ask for clarification** **or** **list interpretations** and address them (e.g. separate bullets: “If you mean X … If you mean Y …”)?
3. **Silent single guess** — Did the agent **avoid** answering as if only one reading existed **without** acknowledging others?

## Scoring (pick exactly one)

- **1.0** — Clear acknowledgment **and** either a clarification request **or** explicit multi-interpretation coverage.
- **0.0** — Single interpretation chosen **without** acknowledgment, alternatives, or a clarifying question, **and** the question meets the “When this rubric applies” rule above.

## PASS / FAIL for automation

Set `"passed": true` **only** when the score is **1.0**; otherwise `"passed": false`.
