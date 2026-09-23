# processors/entity_detector.py
import re
from functools import lru_cache
from typing import Optional, Dict, Any, List

try:
    import spacy
    _nlp = spacy.load("en_core_web_sm")  # ~12MB, fast
except Exception:
    _nlp = None


# Map spaCy labels -> your internal entity_type vocabulary
SPACY_TO_ENTITY = {
    "GPE": "country",      # countries, cities, states
    "LOC": "location",
    "NORP": "nation",      # nationalities, religious/political groups
    "PRODUCT": "product",
    "ORG": "organization",
    "PERSON": "person",
    "EVENT": "event",
    "WORK_OF_ART": "work",
    "LAW": "law",
    "FAC": "facility",
    "MONEY": "money",
    "DATE": "date",
    "CARDINAL": "number",
}

# Dominant-label thresholds
_MIN_ENTITIES = 1
_DOMINANCE_RATIO = 0.34  # a label must be >=34% of all entities to win


def detect_entities(text: str, max_chars: int = 4000) -> List[Dict[str, Any]]:
    """Return raw spaCy entities as dicts."""
    if not _nlp or not text:
        return []
    doc = _nlp(text[:max_chars])
    return [
        {"text": ent.text, "label": ent.label_, "start": ent.start_char,
         "end": ent.end_char}
        for ent in doc.ents
    ]


def dominant_entity_type(text: str) -> Optional[str]:
    """Pick the most common *mapped* entity type, if it dominates."""
    entities = detect_entities(text)
    if not entities:
        return None

    counts: Dict[str, int] = {}
    for e in entities:
        mapped = SPACY_TO_ENTITY.get(e["label"])
        if mapped:
            counts[mapped] = counts.get(mapped, 0) + 1

    if not counts:
        return None

    top_label, top_count = max(counts.items(), key=lambda kv: kv[1])
    total = sum(counts.values())
    if top_count >= _MIN_ENTITIES and (top_count / total) >= _DOMINANCE_RATIO:
        return top_label
    return None