import re


COUNT_PATTERNS = [
    r"\bhow many\b",
    r"\bnumber of\b",
    r"\btotal number\b",
    r"\bcount\b",
]

LIST_PATTERNS = [
    r"\blist all\b",
    r"\bwhat products are\b",
    r"\bwhich products are\b",
    r"\bwhat are all\b",
]

EXHAUSTIVE_PATTERNS = [
    r"\ball\b",
    r"\bevery\b",
    r"\beach\b",
    r"\bcomplete list\b",
    r"\blist all\b",
]


def contains_pattern(query: str, patterns: list[str]) -> bool:
    query = query.lower()

    return any(
        re.search(pattern, query)
        for pattern in patterns
    )