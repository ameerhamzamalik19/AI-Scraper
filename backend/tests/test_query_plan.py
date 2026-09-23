import pytest

from api.routes.query_plan import (
    QueryAnalyzer,
    QueryIntent,
    QueryPlan,
    QueryPlanValidationError,
    QueryPlanValidator,
    QueryScope,
)
from api.routes import retrieval_pipeline


def test_global_count_plan_is_exhaustive():
    plan = QueryAnalyzer.analyze("How many products are there?")

    assert plan.intent == QueryIntent.COUNT
    assert plan.scope == QueryScope.GLOBAL
    assert plan.entity == "product"
    assert plan.exhaustive is True
    assert plan.structured_search is True
    assert plan.semantic_search is False


def test_count_ignores_page_word_inside_source_url():
    plan = QueryAnalyzer.analyze(
        "How many countries are listed on https://www.scrapethissite.com/pages/simple?"
    )

    assert plan.intent == QueryIntent.COUNT
    assert plan.scope == QueryScope.GLOBAL
    assert plan.entity == "country"
    assert plan.structured_search is True


def test_global_filter_plan_captures_price_operator():
    plan = QueryAnalyzer.analyze("Which products cost less than $100?")

    assert plan.intent == QueryIntent.FILTER
    assert plan.scope == QueryScope.GLOBAL
    assert plan.filters == {"price": {"operator": "lt", "value": 100.0}}


def test_local_exact_and_semantic_plans_remain_distinct():
    exact = QueryAnalyzer.analyze("What is the price of Product X?")
    semantic = QueryAnalyzer.analyze("What does Product X do?")

    assert exact.intent == QueryIntent.EXACT_LOOKUP
    assert exact.scope == QueryScope.LOCAL
    assert semantic.intent == QueryIntent.SEMANTIC
    assert semantic.scope == QueryScope.LOCAL


def test_global_sort_and_comparison_plans():
    cheapest = QueryAnalyzer.analyze("What is the cheapest product?")
    comparison = QueryAnalyzer.analyze("Compare Product A and Product B.")

    assert cheapest.intent == QueryIntent.SORT
    assert cheapest.sort_by == "price"
    assert cheapest.sort_order == "asc"
    assert comparison.intent == QueryIntent.COMPARISON
    assert comparison.scope == QueryScope.LOCAL


def test_exhaustive_product_search_is_not_semantic_top_k():
    plan = QueryAnalyzer.analyze("Which products mention AI?")

    assert plan.intent == QueryIntent.LIST
    assert plan.scope == QueryScope.GLOBAL
    assert plan.exhaustive is True
    assert plan.structured_search is True


def test_naming_any_countries_uses_structured_list_search():
    plan = QueryAnalyzer.analyze("Can you name any countries present?")

    assert plan.intent == QueryIntent.LIST
    assert plan.scope == QueryScope.GLOBAL
    assert plan.entity == "country"
    assert plan.exhaustive is True
    assert plan.structured_search is True


def test_fifty_product_count_uses_structured_evidence(monkeypatch):
    calls = []

    def fake_query(query, params=()):
        calls.append((query, params))
        return [{"result": 50}]

    monkeypatch.setattr(retrieval_pipeline, "execute_query", fake_query)
    monkeypatch.setattr(retrieval_pipeline, "generate_response", lambda *args: "There are 50 products.")
    monkeypatch.setattr(
        retrieval_pipeline,
        "get_embedding",
        lambda *_args: (_ for _ in ()).throw(AssertionError("count must not embed")),
    )

    answer = retrieval_pipeline.answer_user_question(
        "How many products are there?",
        chat_id="chat-50-products",
    )

    assert answer == "There are 50 products."
    assert len(calls) == 1
    assert calls[0][1] == ("chat-50-products", "product")


def test_unknown_structured_field_is_rejected():
    plan = QueryPlan(
        intent=QueryIntent.FILTER,
        scope=QueryScope.GLOBAL,
        entity="product",
        filters={"rating": {"operator": "gt", "value": 4.5}},
        rewritten_query="Which products have a rating above 4.5?",
        confidence=0.9,
    )

    with pytest.raises(QueryPlanValidationError):
        QueryPlanValidator.validate(plan)


def test_invalid_plan_generation_retries(monkeypatch):
    attempts = []
    original_analyze = QueryAnalyzer.analyze

    def flaky_analyze(cls, query, rewritten_query=None):
        attempts.append((query, rewritten_query))
        if len(attempts) == 1:
            raise QueryPlanValidationError("invalid structured plan")
        return original_analyze(query, rewritten_query)

    monkeypatch.setattr(QueryAnalyzer, "analyze", classmethod(flaky_analyze))

    plan = QueryAnalyzer.analyze_with_retry(
        "How many products are there?",
        "invalid rewrite",
        max_attempts=3,
    )

    assert plan.intent == QueryIntent.COUNT
    assert len(attempts) == 2
    assert attempts[1][1] == "How many products are there?"


def test_invalid_plan_generation_stops_after_max_attempts(monkeypatch):
    def always_invalid(cls, query, rewritten_query=None):
        raise QueryPlanValidationError("still invalid")

    monkeypatch.setattr(QueryAnalyzer, "analyze", classmethod(always_invalid))

    with pytest.raises(QueryPlanValidationError, match="after 2 attempts"):
        QueryAnalyzer.analyze_with_retry("What is this?", max_attempts=2)
