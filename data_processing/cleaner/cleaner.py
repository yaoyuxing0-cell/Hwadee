"""
Clean crawler documents from MongoDB.

Reads raw crawler output from the configured source collection and writes the
normalized result to the configured target collection.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from pymongo import MongoClient, UpdateOne


CONFIG_PATH = Path(__file__).resolve().parents[1] / "crawler" / "crawler_config.json"
REFERENCE_RE = re.compile(r"\[\d+(?:-\d+)?\]")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        config = json.load(file)

    required_paths = [
        ("mongodb", "uri"),
        ("mongodb", "database"),
        ("cleaning", "source_collection"),
        ("cleaning", "target_collection"),
    ]
    for path in required_paths:
        current: Any = config
        for key in path:
            if not isinstance(current, dict) or key not in current:
                raise ValueError(f"Missing config field: {'.'.join(path)}")
            current = current[key]
    return config


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = REFERENCE_RE.sub("", text)
    return "\n".join(
        WHITESPACE_RE.sub(" ", line).strip()
        for line in text.splitlines()
        if WHITESPACE_RE.sub(" ", line).strip()
    )


def clean_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    cleaned: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = clean_text(raw_key).strip(":： ")
        item = clean_text(raw_value)
        if key and item:
            cleaned[key] = item
    return cleaned


def clean_sections(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    sections: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue

        heading = clean_text(item.get("heading")).strip(":： ") or "正文"
        content = clean_text(item.get("content"))
        if not content:
            continue

        marker = (heading, content)
        if marker in seen:
            continue

        sections.append({"heading": heading, "content": content})
        seen.add(marker)
    return sections


def clean_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    images: list[str] = []
    seen: set[str] = set()
    for item in value:
        url = clean_text(item)
        if not url or url in seen:
            continue
        images.append(url)
        seen.add(url)
    return images


def build_full_text(summary: str, sections: list[dict[str, str]]) -> str:
    parts = [summary] if summary else []
    parts.extend(
        f"{section['heading']}\n{section['content']}"
        for section in sections
        if section.get("content")
    )
    return "\n\n".join(parts)


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def build_doc_id(raw: dict[str, Any], source: str, keyword: str) -> str:
    existing = clean_text(raw.get("doc_id"))
    if existing:
        return existing
    if source and keyword:
        return stable_hash({"source": source, "keyword": keyword})
    return str(raw.get("_id"))


def clean_document(raw: dict[str, Any], cleaned_at: datetime) -> dict[str, Any]:
    keyword = clean_text(raw.get("keyword"))
    title = clean_text(raw.get("title")) or keyword
    source = clean_text(raw.get("source"))
    source_url = clean_text(raw.get("source_url"))
    summary = clean_text(raw.get("summary"))
    basic_info = clean_mapping(raw.get("basic_info"))
    sections = clean_sections(raw.get("sections"))
    images = clean_images(raw.get("images"))
    full_text = build_full_text(summary, sections)

    content_payload = {
        "keyword": keyword,
        "title": title,
        "summary": summary,
        "basic_info": basic_info,
        "sections": sections,
        "full_text": full_text,
    }

    return {
        "doc_id": build_doc_id(raw, source, keyword),
        "raw_id": raw.get("_id"),
        "keyword": keyword,
        "title": title,
        "source": source,
        "source_url": source_url,
        "summary": summary,
        "basic_info": basic_info,
        "sections": sections,
        "full_text": full_text,
        "images": images,
        "content_source": clean_text(raw.get("content_source")),
        "raw_content_hash": raw.get("content_hash"),
        "clean_hash": stable_hash(content_payload),
        "content_stats": {
            "section_count": len(sections),
            "summary_length": len(summary),
            "full_text_length": len(full_text),
            "image_count": len(images),
        },
        "status": "cleaned",
        "fetched_at": raw.get("fetched_at"),
        "cleaned_at": cleaned_at,
        "updated_at": cleaned_at,
    }


def has_clean_content(document: dict[str, Any]) -> bool:
    return bool(
        document.get("summary")
        or document.get("basic_info")
        or document.get("sections")
        or document.get("full_text")
    )


def iter_raw_documents(collection, keyword: str | None, limit: int) -> Iterable[dict[str, Any]]:
    query: dict[str, Any] = {}
    if keyword:
        query["keyword"] = keyword

    cursor = collection.find(query).sort([("keyword", 1), ("updated_at", -1)])
    if limit > 0:
        cursor = cursor.limit(limit)
    return cursor


def write_report(collection, report: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    collection.update_one(
        {"run_id": report["run_id"]},
        {"$set": report},
        upsert=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean raw crawler documents into MongoDB.")
    parser.add_argument("--keyword", help="Only clean one keyword.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum raw documents to process.")
    parser.add_argument("--dry-run", action="store_true", help="Run cleaning without writing results.")
    args = parser.parse_args()

    config = load_config()
    mongodb = config["mongodb"]
    cleaning = config["cleaning"]

    client = MongoClient(mongodb["uri"], serverSelectionTimeoutMS=5000)
    database = client[mongodb["database"]]
    source_collection = database[cleaning["source_collection"]]
    target_collection = database[cleaning["target_collection"]]
    report_collection = database[cleaning.get("report_collection", "cleaning_reports")]

    if not args.dry_run:
        target_collection.create_index("raw_id")
        target_collection.create_index([("source", 1), ("keyword", 1)])

    started_at = datetime.now(timezone.utc)
    run_id = uuid4().hex
    stats = {
        "scanned": 0,
        "cleaned": 0,
        "skipped": 0,
        "failed": 0,
    }
    failures: list[dict[str, str]] = []
    operations: list[UpdateOne] = []

    try:
        for raw in iter_raw_documents(source_collection, args.keyword, args.limit):
            stats["scanned"] += 1
            try:
                cleaned = clean_document(raw, datetime.now(timezone.utc))
                if not has_clean_content(cleaned):
                    stats["skipped"] += 1
                    continue

                stats["cleaned"] += 1
                if args.dry_run:
                    print(f"[dry-run] {cleaned['keyword']} -> {cleaned['title']}")
                    continue

                update_filter: dict[str, Any] = {"doc_id": cleaned["doc_id"]}
                if cleaned.get("source") and cleaned.get("keyword"):
                    update_filter = {
                        "$or": [
                            {"doc_id": cleaned["doc_id"]},
                            {
                                "source": cleaned["source"],
                                "keyword": cleaned["keyword"],
                                "doc_id": {"$in": [None, ""]},
                            },
                        ]
                    }

                operations.append(UpdateOne(update_filter, {"$set": cleaned}, upsert=True))
                if len(operations) >= 100:
                    target_collection.bulk_write(operations, ordered=False)
                    operations.clear()
            except Exception as exc:
                stats["failed"] += 1
                failures.append(
                    {
                        "raw_id": str(raw.get("_id")),
                        "keyword": clean_text(raw.get("keyword")),
                        "error": str(exc),
                    }
                )

        if operations:
            target_collection.bulk_write(operations, ordered=False)

        finished_at = datetime.now(timezone.utc)
        report = {
            "run_id": run_id,
            "status": "dry_run" if args.dry_run else "completed",
            "source_collection": cleaning["source_collection"],
            "target_collection": cleaning["target_collection"],
            "started_at": started_at,
            "finished_at": finished_at,
            "stats": stats,
            "failures": failures[:20],
        }
        write_report(report_collection, report, args.dry_run)

        print(
            "cleaning finished: "
            f"scanned={stats['scanned']}, "
            f"cleaned={stats['cleaned']}, "
            f"skipped={stats['skipped']}, "
            f"failed={stats['failed']}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
