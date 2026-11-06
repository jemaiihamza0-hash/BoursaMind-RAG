# Ragas Reference Notes

Relevant ideas studied from `/references/ragas`:

- Evaluation is dataset-driven: cases declare inputs and expected behavior.
- Metrics should be explicit and comparable across runs.
- Context precision and context recall measure retriever quality separately from generation quality.
- Faithfulness and response groundedness measure whether answers stay inside retrieved evidence.
- Experiment-style reports are better long-term than a single opaque score.

Implementation decision:

- BoursaMind-RAG ships a lightweight local evaluator now.
- Direct Ragas integration remains optional behind the `eval` extra because adding it to the hot path would increase runtime dependencies without being required for the current extraction.
- The financial dataset now separates retriever metrics (`context_precision`,
  `context_recall`, `context_relevance`) from answer metrics (`faithfulness`,
  `answer_relevance`) and safety/no-answer behavior.
- Ragas faithfulness is treated as claim support against retrieved contexts: a
  financial answer is not considered good merely because it is fluent.
