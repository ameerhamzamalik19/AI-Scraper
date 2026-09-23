from enum import Enum
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Optional
import re
import logging


logger = logging.getLogger(__name__)


class QueryIntent(str, Enum):
    SEMANTIC = "semantic"
    EXACT_LOOKUP = "exact_lookup"
    COUNT = "count"
    LIST = "list"
    FILTER = "filter"
    SORT = "sort"
    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    MULTI_HOP = "multi_hop"
    SUMMARY = "summary"
    EXHAUSTIVE_SEARCH = "exhaustive_search"


class QueryScope(str, Enum):
    LOCAL = "local"
    GLOBAL = "global"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: QueryIntent
    scope: QueryScope

    entity: str | None = None

    filters: dict[str, Any] = Field(default_factory=dict)

    aggregation: str | None = None

    sort_by: str | None = None
    sort_order: str | None = None

    entities: list[str] = Field(default_factory=list)

    exhaustive: bool = False

    semantic_search: bool = False
    structured_search: bool = False

    rewritten_query: str | None = None
    completeness_required: bool = False
    confidence: float = 0.0


class QueryPlanValidationError(ValueError):
    pass


class QueryPlanValidator:
    @staticmethod
    def validate(plan: QueryPlan) -> QueryPlan:
        if not plan.rewritten_query:
            raise QueryPlanValidationError("rewritten_query is required")

        if plan.intent in {
            QueryIntent.COUNT,
            QueryIntent.LIST,
            QueryIntent.FILTER,
            QueryIntent.SORT,
            QueryIntent.AGGREGATION,
            QueryIntent.EXHAUSTIVE_SEARCH,
        }:
            if plan.scope != QueryScope.GLOBAL:
                raise QueryPlanValidationError("global intent requires global scope")
            plan.completeness_required = True
            plan.exhaustive = True

        if plan.intent == QueryIntent.COUNT and not plan.entity:
            raise QueryPlanValidationError("count plans require an entity")

        unsupported_fields = set(plan.filters) - {"price"}
        if unsupported_fields:
            raise QueryPlanValidationError(
                f"unsupported structured fields: {sorted(unsupported_fields)}"
            )

        if plan.sort_order and plan.sort_order not in {"asc", "desc"}:
            raise QueryPlanValidationError("sort_order must be asc or desc")

        if not 0.0 <= plan.confidence <= 1.0:
            raise QueryPlanValidationError("confidence must be between 0 and 1")

        return plan


class QueryAnalyzer:
    """Create safe routing plans without answering the user's question."""

    _COUNT = re.compile(r"\b(how many|number of|total number|count|total)\b", re.I)
    _AVERAGE = re.compile(r"\b(average|mean)\b", re.I)
    _CHEAPEST = re.compile(r"\b(cheapest|least expensive|lowest price)\b", re.I)
    _MOST_EXPENSIVE = re.compile(r"\b(most expensive|highest price|costliest)\b", re.I)
    _COMPARE = re.compile(r"\b(compare|difference between|versus| vs\.? )\b", re.I)
    _PRICE_FILTER = re.compile(
        r"\b(under|below|less than|over|above|more than)\s*\$?([0-9]+(?:\.[0-9]+)?)",
        re.I,
    )
    _EXHAUSTIVE = re.compile(
        r"\b(all|every|each|complete list|list all|what products|which products)\b",
        re.I,
    )
    _LIST_REQUEST = re.compile(
        r"\b(?:name|list|give me|what are)\s+(?:any|some|a few)?\s*"
        r"(?:of the\s+)?(?:countries|products|items|articles)\b",
        re.I,
    )

    @classmethod
    def normalize(cls, query: str) -> str:
        return re.sub(r"\s+", " ", query.strip())

    @classmethod
    def analyze_with_retry(
        cls,
        query: str,
        rewritten_query: Optional[str] = None,
        max_attempts: int = 3,
    ) -> QueryPlan:
        """Generate a validated plan, retrying bounded planner failures."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        last_error: Optional[QueryPlanValidationError] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return cls.analyze(query, rewritten_query)
            except QueryPlanValidationError as error:
                last_error = error
                logger.warning(
                    "[PLAN] validation_failed attempt=%s/%s error=%s",
                    attempt,
                    max_attempts,
                    error,
                )
                # A failed structured plan must not be reused as a retrieval request.
                # Retry with the original query so a bad rewrite cannot poison planning.
                rewritten_query = query
                if attempt == max_attempts:
                    break

        raise QueryPlanValidationError(
            f"query plan remained invalid after {max_attempts} attempts: {last_error}"
        )

    @classmethod
    def analyze(
        cls,
        query: str,
        rewritten_query: Optional[str] = None,
    ) -> QueryPlan:
        normalized = cls.normalize(rewritten_query or query)
        lowered = normalized.lower()
        entity = cls._entity(lowered)
        filters: dict[str, Any] = {}

        price_match = cls._PRICE_FILTER.search(normalized)
        if price_match:
            operator = price_match.group(1).lower()
            filters["price"] = {
                "operator": "lt" if operator in {"under", "below", "less than"} else "gt",
                "value": float(price_match.group(2)),
            }

        if cls._COUNT.search(normalized):
            intent = QueryIntent.COUNT
        elif cls._AVERAGE.search(normalized):
            intent = QueryIntent.AGGREGATION
        elif cls._CHEAPEST.search(normalized) or cls._MOST_EXPENSIVE.search(normalized):
            intent = QueryIntent.SORT
        elif cls._COMPARE.search(normalized):
            intent = QueryIntent.COMPARISON
        elif price_match:
            intent = QueryIntent.FILTER
        elif cls._EXHAUSTIVE.search(normalized) or cls._LIST_REQUEST.search(normalized):
            intent = QueryIntent.LIST
        elif re.search(r"\b(price|cost|title|name|version|address)\b", lowered):
            intent = QueryIntent.EXACT_LOOKUP
        elif re.search(r"\b(summarize|summary|overview)\b", lowered):
            intent = QueryIntent.SUMMARY
        else:
            intent = QueryIntent.SEMANTIC

        global_intent = intent in {
            QueryIntent.COUNT,
            QueryIntent.LIST,
            QueryIntent.FILTER,
            QueryIntent.SORT,
            QueryIntent.AGGREGATION,
            QueryIntent.EXHAUSTIVE_SEARCH,
        }
        exhaustive = global_intent or bool(
            cls._EXHAUSTIVE.search(normalized) or cls._LIST_REQUEST.search(normalized)
        )
        plan = QueryPlan(
            intent=intent,
            scope=QueryScope.GLOBAL if global_intent else QueryScope.LOCAL,
            entity=entity,
            filters=filters,
            aggregation="avg" if intent == QueryIntent.AGGREGATION else None,
            sort_by="price" if intent == QueryIntent.SORT else None,
            sort_order=("asc" if cls._CHEAPEST.search(normalized) else "desc")
            if intent == QueryIntent.SORT else None,
            exhaustive=exhaustive,
            completeness_required=exhaustive,
            semantic_search=intent in {QueryIntent.SEMANTIC, QueryIntent.SUMMARY},
            structured_search=global_intent,
            rewritten_query=normalized,
            confidence=0.95 if intent != QueryIntent.SEMANTIC else 0.7,
        )
        return QueryPlanValidator.validate(plan)

    @staticmethod
    def _entity(query: str) -> Optional[str]:
        # URL path segments such as /pages/simple are source locators, not
        # entities requested by the user.
        query_without_urls = re.sub(r"https?://\S+", " ", query)
        known_entities = {
            "product": "product",
            "products": "product",
            "country": "country",
            "countries": "country",
            "page": "page",
            "pages": "page",
            "article": "article",
            "articles": "article",
            "faq": "faq",
            "faqs": "faq",
            "item": "item",
            "items": "item",
        }
        for term, entity in known_entities.items():
            if re.search(rf"\b{term}\b", query_without_urls):
                return entity
        return "content"