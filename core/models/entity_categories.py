"""Canonical entity categories shared by extraction and typed review."""
SPACY_LABEL_MAP = {
    # spaCy distinguishes political places (GPE), natural/geographic places
    # (LOC), and facilities (FAC).  That distinction is useful extractor
    # provenance but confusing as the canonical product ontology: users expect
    # all of these under one clearly named Place category.
    "PERSON": "PERSON", "ORG": "ORG", "GPE": "LOCATION", "LOC": "LOCATION",
    "FAC": "LOCATION", "NORP": "NORP", "DATE": "DATE", "TIME": "DATE",
    "MONEY": "MONEY", "QUANTITY": "QUANTITY", "PERCENT": "QUANTITY",
    "EVENT": "EVENT", "WORK_OF_ART": "WORK", "LAW": "LAW", "PRODUCT": "PRODUCT", "LANGUAGE": "LANGUAGE",
}

ENTITY_TYPES = frozenset(SPACY_LABEL_MAP.values()) | frozenset({"METHOD", "DATASET", "METRIC", "CONCEPT", "WORK", "TITLE", "UNKNOWN"})

# Values rather than names: owned by the dates and tables stages, dropped from the
# name stream (spaCy labels included, before mapping).
VALUE_TYPES = frozenset({"DATE", "TIME", "QUANTITY", "MONEY", "PERCENT", "CARDINAL", "ORDINAL"})
# Values plus measured values ("12 ms", "0.9 F1"): never linked as co-occurring
# names, and hidden from name lists unless the user picks them.
MEASURE_TYPES = VALUE_TYPES | frozenset({"METRIC"})

TYPE_LABELS = {"PERSON": "Person", "ORG": "Organisation", "LOCATION": "Place", "NORP": "Group", "LAW": "Law",
               "WORK": "Work", "EVENT": "Event", "PRODUCT": "Product", "LANGUAGE": "Language", "CONCEPT": "Concept",
               "DATE": "Date", "METHOD": "Method", "DATASET": "Dataset", "FAC": "Facility", "METRIC": "Measure",
               "MONEY": "Money", "QUANTITY": "Quantity", "TITLE": "Title / role", "UNKNOWN": "Untyped name"}


def type_label(entity_type) -> str:
    return TYPE_LABELS.get(entity_type or "UNKNOWN", str(entity_type or "Entity").replace("_", " ").capitalize())
