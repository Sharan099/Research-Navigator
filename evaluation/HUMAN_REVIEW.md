# Research relevance and answer review

Status: **not yet human-reviewed**. This rubric defines the remaining scientific-quality evaluation;
unit tests, matching quotes, and a small local catalog cannot substitute for these judgments.

Use at least ten saved research tasks spanning a precise topic, a broad topic, an ambiguous priority,
a date refinement, influential works, recent works, older-paper reference, a two-paper comparison,
missing abstracts, and an unsupported-detail question. Save the real provider identifiers, retrieval
date, selected model, actual query/filter state, and source excerpts in a private review artifact.
Never invent titles or expected papers. Do not commit private prompts or credentials.

For each task, record:

| Dimension | 0 | 1 | 2 |
|---|---|---|---|
| Intent | Wrong topic/constraints | Partially preserved | Stated topic and explicit constraints preserved |
| Clarification | Guesses a consequential ambiguity | Question only partly helps | Resolves material ambiguity or proceeds when clear |
| Relevance | Mostly unrelated results | Mixed results | Returned sample fits the stated task |
| Claim support | Unsupported or overstated | Qualified but incomplete | Each claim follows from the supplied abstract evidence |
| Citation usefulness | Wrong source/quote | Related but weak support | Correct source with directly useful support |
| Uncertainty | Hides missingness/outage | Generic warning | Clearly identifies actual evidence and coverage limits |

Record unsupported claims separately: `unsupported claims / reviewed claims`, with actual sample
counts. Report relevance as judged precision of the returned preview, not recall across OpenAlex.
Count an unsupported superiority or causal assertion as a failure even if its quote is verbatim.
A second independent reviewer should adjudicate disputed support before publishing accuracy numbers.
Automated model judgments, if added later, must be labeled separately and calibrated against these
reviews. No score is populated here because no such reviewed dataset exists yet.
