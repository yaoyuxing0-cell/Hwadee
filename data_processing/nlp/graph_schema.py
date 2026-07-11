"""Shared graph CSV schema for NLP extraction and post-processing."""

from __future__ import annotations

import hashlib


NODE_ID = "node_id:ID"
NODE_NAME = "name"
NODE_LABEL = "type:LABEL"
NODE_DOCS = "source_doc_ids"
NODE_DESC = "description"
NODE_FIELDS = [NODE_ID, NODE_NAME, NODE_LABEL, NODE_DOCS, NODE_DESC]

REL_START = ":START_ID"
REL_END = ":END_ID"
REL_TYPE = ":TYPE"
REL_DOC = "source_doc_id"
REL_EVIDENCE = "evidence"
REL_CONF = "confidence:float"
REL_SECTION = "section"
RELATION_FIELDS = [REL_START, REL_END, REL_TYPE, REL_DOC, REL_EVIDENCE, REL_CONF, REL_SECTION]

NODE_TYPES = frozenset({"Disease", "Symptom", "Drug", "Examination", "Treatment"})
RELATION_SCHEMA = {
    "HAS_SYMPTOM": ("Disease", "Symptom"),
    "REQUIRES_EXAM": ("Disease", "Examination"),
    "TREATS_DISEASE": ("Drug", "Disease"),
    "TREATED_BY": ("Disease", "Treatment"),
    "HAS_COMPLICATION": ("Disease", "Disease"),
}
RELATION_TYPES = frozenset(RELATION_SCHEMA)


def stable_node_id(label: str, name: str) -> str:
    digest = hashlib.sha1(f"{label}:{name}".encode("utf-8")).hexdigest()
    return f"{label}:{digest}"
