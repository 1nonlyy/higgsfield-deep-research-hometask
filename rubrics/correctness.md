# Correctness rubric

---
IMPORTANT CONTEXT FOR JUDGE:
- This agent operates against a LOCAL corpus, not the real internet.
- URLs like "https://corpus.local/..." are VALID citations in this system.
  Do not penalize them for being non-standard or unverifiable externally.
- The extracted_quotes field may be empty if the agent did not call
  extract_quotes. This alone is NOT grounds for failing correctness.
  Judge based on whether the final_answer text is factually accurate
  relative to the question asked.
- Only fail correctness if the answer contains demonstrably wrong facts
  or fabricates information not present in the question context.
---

Evaluate whether the final answer appropriately addresses the user question. Judge factual accuracy from the final answer text and the question; do not require external URL checks or populated `extracted_quotes` to pass.

**PASS:** The answer correctly addresses the question with accurate facts.

**FAIL:** The answer contains wrong facts, or fabricates specific details (wrong years, wrong names, made-up instrument readings, etc.).

Additional guidance (compatible with the above):

- Be strict on fabrication and lenient on concise paraphrase that preserves meaning.
- When the question requires it, the answer should acknowledge missing evidence in the corpus or disagreement between sources rather than inventing a single unsupported narrative.
- Failing to follow explicit question instructions (format, scope) in a way that would mislead a user counts against correctness.

Use your judgment consistently with the PASS/FAIL definitions above.
