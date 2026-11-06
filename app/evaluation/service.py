"""Dataset-driven RAG evaluation inspired by Ragas metrics."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.auth.security import Principal
from app.retrieval import scoring
from app.retrieval.service import QueryService
from app.schemas import EvaluationCase, EvaluationCaseResult, EvaluationReport, QueryRequest

DATASET_DIR = Path(__file__).resolve().parent / "datasets"


class EvaluationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, principal: Principal, dataset: str = "generic") -> EvaluationReport:
        cases = self._load_cases(dataset)
        results: list[EvaluationCaseResult] = []
        for case in cases:
            response = QueryService(self.session).query(
                principal,
                QueryRequest(
                    query=case.question,
                    filters=case.filters,
                    top_k=5,
                    include_answer=True,
                ),
            )
            retrieval_results = response.retrieval.get("results", [])
            contexts = [row.get("content", "") for row in retrieval_results]
            best = float(response.retrieval.get("best_score", 0.0))
            expected_terms = [scoring.fold(term) for term in case.expected_terms]
            context_blob = scoring.fold(" ".join(contexts))
            answer_blob = scoring.fold(response.answer)
            has_terms = all(term in context_blob for term in expected_terms)
            has_answer_terms = all(term in answer_blob for term in expected_terms)
            title_match = not case.expected_document_title or any(
                case.expected_document_title.lower()
                in (row.get("citation", {}).get("title") or "").lower()
                for row in retrieval_results
            )
            if case.should_abstain:
                passed = response.abstained and not response.citations
                observed = "ABSTAIN" if response.abstained else "ANSWER"
            else:
                passed = not response.abstained and has_terms and title_match
                observed = "ANSWER" if not response.abstained else "ABSTAIN"

            context_precision = self._context_precision(case, retrieval_results)
            context_recall = 1.0 if has_terms or case.should_abstain else 0.0
            citation_correctness = 1.0 if (case.should_abstain or response.citations) else 0.0
            faithfulness = 1.0 if (case.should_abstain or has_answer_terms) else 0.5
            answer_relevance = scoring.citation_support(case.question, response.answer)
            context_relevance = max(
                (scoring.citation_support(case.question, context) for context in contexts),
                default=0.0,
            )
            results.append(
                EvaluationCaseResult(
                    id=case.id,
                    passed=passed,
                    observed=observed,
                    best_score=best,
                    context_precision=context_precision,
                    context_recall=context_recall,
                    citation_correctness=citation_correctness,
                    faithfulness=faithfulness,
                    context_relevance=context_relevance,
                    answer_relevance=answer_relevance,
                    detail=""
                    if passed
                    else "Expected terms/title not found or abstention mismatch.",
                )
            )

        total = len(results)
        passed_count = sum(1 for result in results if result.passed)
        metric_names = (
            "context_precision",
            "context_recall",
            "citation_correctness",
            "faithfulness",
            "context_relevance",
            "answer_relevance",
        )
        metrics = {
            name: round(
                sum(float(getattr(result, name)) for result in results) / total,
                4,
            )
            if total
            else 0.0
            for name in metric_names
        }
        return EvaluationReport(
            dataset=dataset,
            passed=passed_count,
            total=total,
            pass_rate=round(passed_count / total, 4) if total else 0.0,
            metrics=metrics,
            cases=results,
        )

    def _load_cases(self, dataset: str) -> list[EvaluationCase]:
        path = DATASET_DIR / f"{dataset}.json"
        if not path.exists():
            raise FileNotFoundError(f"Unknown evaluation dataset: {dataset}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [EvaluationCase(**row) for row in payload["cases"]]

    def _context_precision(self, case: EvaluationCase, rows: list[dict]) -> float:
        if case.should_abstain:
            return 1.0 if not rows else 0.0
        if not rows:
            return 0.0
        expected_terms = [scoring.fold(term) for term in case.expected_terms]
        relevant_at_k = []
        relevant = 0
        for index, row in enumerate(rows, start=1):
            blob = scoring.fold(row.get("content", ""))
            is_relevant = all(term in blob for term in expected_terms)
            if is_relevant:
                relevant += 1
                relevant_at_k.append(relevant / index)
        return round(sum(relevant_at_k) / len(relevant_at_k), 4) if relevant_at_k else 0.0
