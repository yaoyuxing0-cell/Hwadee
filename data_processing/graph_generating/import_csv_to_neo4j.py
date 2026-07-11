"""Import cleaned NLP graph CSV files into Neo4j.

Default input:
    data_processing/nlp/output_cleaned/nodes.csv
    data_processing/nlp/output_cleaned/relationships.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from neo4j_config import GRAPH_CONFIG, NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER


DATA_PROCESSING_DIR = Path(__file__).resolve().parents[1]
NLP_DIR = DATA_PROCESSING_DIR / "nlp"
GRAPH_DIR = Path(__file__).resolve().parent

if str(NLP_DIR) not in sys.path:
    sys.path.insert(0, str(NLP_DIR))

from graph_schema import (  # noqa: E402
    NODE_DESC,
    NODE_DOCS,
    NODE_ID,
    NODE_LABEL,
    NODE_NAME,
    NODE_TYPES,
    REL_CONF,
    REL_DOC,
    REL_END,
    REL_EVIDENCE,
    REL_SECTION,
    REL_START,
    REL_TYPE,
    RELATION_SCHEMA,
    RELATION_TYPES,
)


DEFAULT_BATCH_SIZE = int(GRAPH_CONFIG.get("batch_size", 500))


def resolve_config_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((GRAPH_DIR / path).resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import nodes.csv and relationships.csv into Neo4j."
    )
    parser.add_argument(
        "--csv-dir",
        default=resolve_config_path(str(GRAPH_CONFIG.get("csv_dir", r"..\nlp\output_cleaned"))),
        help="Directory containing nodes.csv and relationships.csv.",
    )
    parser.add_argument(
        "--uri",
        default=NEO4J_URI,
        help="Neo4j Bolt URI.",
    )
    parser.add_argument(
        "--user",
        default=NEO4J_USER,
        help="Neo4j username.",
    )
    parser.add_argument(
        "--password",
        default=NEO4J_PASSWORD,
        help="Neo4j password. You can also set NEO4J_PASSWORD.",
    )
    parser.add_argument(
        "--database",
        default=NEO4J_DATABASE,
        help="Neo4j database name. Leave empty for the default database.",
    )
    parser.add_argument(
        "--clear",
        action=argparse.BooleanOptionalAction,
        default=bool(GRAPH_CONFIG.get("clear", False)),
        help="Delete existing graph nodes with supported labels before importing.",
    )
    parser.add_argument(
        "--check",
        action=argparse.BooleanOptionalAction,
        default=bool(GRAPH_CONFIG.get("check", False)),
        help="Only verify Neo4j connectivity. Do not read CSV files or import data.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Rows imported per Neo4j transaction batch.",
    )
    return parser.parse_args()


def import_graph_database():
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: neo4j. Install it with "
            "`pip install -r data_processing/requirements.txt`."
        ) from exc

    return GraphDatabase


def token(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe Neo4j token: {name}")
    return f"`{name}`"


def node_label(node_id: str) -> str:
    return node_id.split(":", 1)[0] if ":" in node_id else ""


def parse_doc_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_nodes(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            label = (row.get(NODE_LABEL) or "").strip()
            node_id = (row.get(NODE_ID) or "").strip()
            name = (row.get(NODE_NAME) or "").strip()
            if label not in NODE_TYPES or not node_id or not name:
                skipped += 1
                continue
            rows.append(
                {
                    "node_id": node_id,
                    "name": name,
                    "label": label,
                    "source_doc_ids": parse_doc_ids(row.get(NODE_DOCS) or ""),
                    "description": (row.get(NODE_DESC) or "").strip(),
                }
            )
    return rows, skipped


def read_relationships(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rel_type = (row.get(REL_TYPE) or "").strip()
            start_id = (row.get(REL_START) or "").strip()
            end_id = (row.get(REL_END) or "").strip()
            if rel_type not in RELATION_TYPES or not start_id or not end_id:
                skipped += 1
                continue

            expected_start, expected_end = RELATION_SCHEMA[rel_type]
            if node_label(start_id) != expected_start or node_label(end_id) != expected_end:
                skipped += 1
                continue

            rows.append(
                {
                    "start_id": start_id,
                    "end_id": end_id,
                    "type": rel_type,
                    "source_doc_id": (row.get(REL_DOC) or "").strip(),
                    "evidence": (row.get(REL_EVIDENCE) or "").strip(),
                    "confidence": parse_float(row.get(REL_CONF) or "0"),
                    "section": (row.get(REL_SECTION) or "").strip(),
                }
            )
    return rows, skipped


def chunked(rows: list[dict[str, Any]], size: int):
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def session_kwargs(database: str) -> dict[str, str]:
    return {"database": database} if database else {}


def create_constraints(session) -> None:
    for label in sorted(NODE_TYPES):
        session.run(
            f"""
            CREATE CONSTRAINT {label.lower()}_node_id IF NOT EXISTS
            FOR (n:{token(label)}) REQUIRE n.node_id IS UNIQUE
            """
        ).consume()


def clear_existing_graph(session) -> None:
    labels = sorted(NODE_TYPES)
    session.run(
        """
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $labels)
        DETACH DELETE n
        """,
        labels=labels,
    ).consume()


def import_nodes(session, rows: list[dict[str, Any]], batch_size: int) -> int:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["label"]].append(row)

    imported = 0
    for label, label_rows in grouped.items():
        query = f"""
        UNWIND $rows AS row
        MERGE (n:{token(label)} {{node_id: row.node_id}})
        SET n.name = row.name,
            n.label = row.label,
            n.type = row.label,
            n.source_doc_ids = row.source_doc_ids,
            n.description = row.description
        RETURN count(n) AS count
        """
        for batch in chunked(label_rows, batch_size):
            imported += session.run(query, rows=batch).single()["count"]
    return imported


def import_relationships(session, rows: list[dict[str, Any]], batch_size: int) -> int:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["type"]].append(row)

    imported = 0
    for rel_type, rel_rows in grouped.items():
        query = f"""
        UNWIND $rows AS row
        MATCH (source {{node_id: row.start_id}})
        MATCH (target {{node_id: row.end_id}})
        MERGE (source)-[r:{token(rel_type)} {{source_doc_id: row.source_doc_id}}]->(target)
        SET r.type = row.type,
            r.evidence = row.evidence,
            r.confidence = row.confidence,
            r.section = row.section
        RETURN count(r) AS count
        """
        for batch in chunked(rel_rows, batch_size):
            imported += session.run(query, rows=batch).single()["count"]
    return imported


def main() -> None:
    args = parse_args()
    GraphDatabase = import_graph_database()
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        driver.verify_connectivity()
        if args.check:
            with driver.session(**session_kwargs(args.database)) as session:
                record = session.run("RETURN 1 AS ok").single()
            print(f"Connected to Neo4j: {args.uri}, user={args.user}, ok={record['ok']}")
            return

        csv_dir = Path(args.csv_dir).resolve()
        nodes_path = csv_dir / "nodes.csv"
        relationships_path = csv_dir / "relationships.csv"

        if not nodes_path.exists() or not relationships_path.exists():
            raise SystemExit(f"CSV files not found in {csv_dir}")

        nodes, skipped_nodes = read_nodes(nodes_path)
        relationships, skipped_relationships = read_relationships(relationships_path)

        with driver.session(**session_kwargs(args.database)) as session:
            create_constraints(session)
            if args.clear:
                clear_existing_graph(session)
            imported_nodes = import_nodes(session, nodes, args.batch_size)
            imported_relationships = import_relationships(
                session, relationships, args.batch_size
            )
    finally:
        driver.close()

    print(f"CSV directory: {csv_dir}")
    print(f"Batch size: {args.batch_size}")
    print(f"Clear before import: {args.clear}")
    print(f"Imported nodes: {imported_nodes}, skipped nodes: {skipped_nodes}")
    print(
        "Imported relationships: "
        f"{imported_relationships}, skipped relationships: {skipped_relationships}"
    )


if __name__ == "__main__":
    main()
