"""
Baidu Baike disease crawler.

The script reads crawler_config.json, crawls the configured keywords, and stores
the parsed documents in MongoDB.
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from bs4.element import Tag
from pymongo import MongoClient


CONFIG_PATH = "crawler_config.json"
BAIKE_PAGE_URL = "https://baike.baidu.com/item/{keyword}"
BAIKE_CARD_API = (
    "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
    "?scope=103&format=json&appid=379020&bk_key={keyword}&bk_length=1200"
)

REFERENCE_RE = re.compile(r"\[\d+(?:-\d+)?\]")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
MIN_FULL_TEXT_LENGTH = 200


def clean_text(value: str) -> str:
    value = html.unescape(value).replace("\xa0", " ").replace("\u3000", " ")
    value = REFERENCE_RE.sub("", value)
    return "\n".join(
        WHITESPACE_RE.sub(" ", line).strip()
        for line in value.splitlines()
        if WHITESPACE_RE.sub(" ", line).strip()
    )


def tag_text(tag: Tag | None, separator: str = "\n") -> str:
    if tag is None:
        return ""
    return clean_text(tag.get_text(separator=separator, strip=True))


def html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment, "html.parser")
    return clean_text(soup.get_text(separator="", strip=True))


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        config = json.load(file)

    required_paths = [
        ("keywords",),
        ("mongodb", "uri"),
        ("mongodb", "database"),
        ("mongodb", "collection"),
        ("request", "timeout"),
        ("request", "interval"),
    ]
    for path in required_paths:
        current: Any = config
        for key in path:
            if not isinstance(current, dict) or key not in current:
                raise ValueError(f"配置缺少字段：{'.'.join(path)}")
            current = current[key]

    if not isinstance(config["keywords"], list) or not config["keywords"]:
        raise ValueError("配置字段 keywords 必须是非空数组")

    return config


def decode_response(raw: bytes, charset: str | None) -> str:
    for candidate in (charset, "utf-8", "gb18030"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_page(keyword: str, timeout: int, cookie: str = "") -> tuple[str, str]:
    url = BAIKE_PAGE_URL.format(keyword=quote(keyword))
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Referer": "https://www.baidu.com/",
    }
    if cookie:
        headers["Cookie"] = cookie

    request = Request(
        url,
        headers=headers,
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return decode_response(raw, response.headers.get_content_charset()), response.geturl()


def parse_page(keyword: str, html_text: str, source_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    page_title = tag_text(soup.find("title"), separator="")
    if "百度安全验证" in page_title:
        raise RuntimeError("百度百科返回安全验证页，无法解析正文 HTML")

    summary = tag_text(
        soup.find(
            lambda tag: isinstance(tag, Tag)
            and (
                "lemma-summary" in (tag.get("class") or [])
                or tag.get("label-module") == "lemmaSummary"
            )
        )
    )

    basic_info: dict[str, str] = {}
    for name_tag, value_tag in zip(
        soup.select("dt.basicInfo-item.name"),
        soup.select("dd.basicInfo-item.value"),
    ):
        key = tag_text(name_tag, separator="").strip("：: ")
        value = tag_text(value_tag, separator="")
        if key and value:
            basic_info[key] = value

    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for tag in soup.select(".para-title, h2, h3, h4, .para"):
        if "para-title" in (tag.get("class") or []) or tag.name in {"h2", "h3", "h4"}:
            heading = tag_text(tag, separator="").strip("：: ")
            if heading:
                current = {"heading": heading, "content": ""}
                sections.append(current)
            continue

        if current is not None:
            text = tag_text(tag)
            if text:
                current["content"] = f"{current['content']}\n{text}".strip()

    images = []
    for image in soup.find_all("img"):
        src = image.get("data-src") or image.get("src")
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        images.append(urljoin(source_url, src))

    title = tag_text(soup.find("h1"), separator="") or keyword
    return build_document(
        keyword=keyword,
        title=title,
        source_url=source_url,
        summary=summary,
        basic_info=basic_info,
        sections=[section for section in sections if section["content"]],
        images=list(dict.fromkeys(images))[:12],
        content_source="html",
    )


def decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    index = 0
    while True:
        line_end = body.find(b"\r\n", index)
        if line_end == -1:
            return bytes(decoded)
        size = int(body[index:line_end].split(b";", 1)[0], 16)
        index = line_end + 2
        if size == 0:
            return bytes(decoded)
        decoded.extend(body[index : index + size])
        index += size + 2


def parse_raw_http_response(response: bytes, url: str) -> str:
    header_bytes, _, body = response.partition(b"\r\n\r\n")
    header_text = header_bytes.decode("iso-8859-1", errors="replace")
    headers: dict[str, str] = {}
    status_line, *header_lines = header_text.split("\r\n")
    status = int(status_line.split(" ", 2)[1])

    if status >= 400:
        raise HTTPError(url, status, status_line, {}, None)

    for line in header_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = decode_chunked(body)
    if headers.get("content-encoding", "").lower() == "gzip":
        body = gzip.decompress(body)

    charset_match = re.search(r"charset=([^;]+)", headers.get("content-type", ""), re.I)
    charset = charset_match.group(1).strip() if charset_match else None
    return decode_response(body, charset)


def fetch_card_api(keyword: str, timeout: int, connect_host: str | None = None) -> dict[str, Any] | None:
    url = BAIKE_CARD_API.format(keyword=keyword)
    parsed = urlsplit(url)
    host = parsed.netloc
    server_name = host.split(":", 1)[0]
    target_host = connect_host or server_name
    path = f"{parsed.path}?{parsed.query}"

    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: Mozilla/5.0\r\n"
        "Accept: application/json,text/plain,*/*\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("utf-8")

    chunks: list[bytes] = []
    with socket.create_connection((target_host, 443), timeout=timeout) as sock:
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=server_name) as secure_sock:
            secure_sock.settimeout(timeout)
            secure_sock.sendall(request)
            while True:
                chunk = secure_sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)

    payload = json.loads(parse_raw_http_response(b"".join(chunks), url))
    if payload.get("errno") is not None:
        return None
    return payload


def fetch_card_api_with_ip_retry(keyword: str, timeout: int) -> dict[str, Any]:
    hosts: list[str | None] = [None]
    try:
        resolved_hosts = list(
            dict.fromkeys(
                item[4][0] for item in socket.getaddrinfo("baike.baidu.com", 443, type=socket.SOCK_STREAM)
            )
        )
        hosts.extend(resolved_hosts[:3])
    except socket.gaierror:
        pass

    for index, host in enumerate(hosts):
        try:
            payload = fetch_card_api(keyword, timeout, connect_host=host)
        except (OSError, HTTPError, json.JSONDecodeError, ValueError):
            payload = None
        if payload:
            return payload
        if index < len(hosts) - 1:
            time.sleep(0.2)

    raise RuntimeError(f"百度百科 API 未返回有效数据：{keyword}")


def parse_card_api(keyword: str, payload: dict[str, Any]) -> dict[str, Any]:
    basic_info: dict[str, str] = {}
    for item in payload.get("card", []):
        if not isinstance(item, dict):
            continue
        key = clean_text(str(item.get("name") or item.get("key") or "")).strip("：: ")
        value = item.get("value")
        if isinstance(value, list):
            value_text = "、".join(dict.fromkeys(html_to_text(str(part)) for part in value))
        else:
            value_text = html_to_text(str(value or ""))
        if key and value_text:
            basic_info[key] = value_text

    image = payload.get("image") or payload.get("picUrl")
    source_url = payload.get("url") or payload.get("wapUrl") or BAIKE_PAGE_URL.format(keyword=quote(keyword))
    if isinstance(source_url, str) and source_url.startswith("http://"):
        source_url = f"https://{source_url.removeprefix('http://')}"

    return build_document(
        keyword=keyword,
        title=str(payload.get("title") or keyword),
        source_url=str(source_url),
        summary=clean_text(str(payload.get("abstract") or "")),
        basic_info=basic_info,
        sections=[],
        images=[str(image)] if image else [],
        content_source="card_api",
    )


def verify_content(content_source: str, sections: list[dict[str, str]], full_text: str) -> dict[str, Any]:
    section_count = len([section for section in sections if section.get("content")])
    full_text_length = len(full_text)
    has_full_content = content_source == "html" and (
        section_count > 0 or full_text_length >= MIN_FULL_TEXT_LENGTH
    )

    return {
        "has_full_content": has_full_content,
        "content_source": content_source,
        "section_count": section_count,
        "full_text_length": full_text_length,
        "min_full_text_length": MIN_FULL_TEXT_LENGTH,
    }


def build_document(
    keyword: str,
    title: str,
    source_url: str,
    summary: str,
    basic_info: dict[str, str],
    sections: list[dict[str, str]],
    images: list[str],
    content_source: str,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    full_text = "\n\n".join(
        f"{section['heading']}\n{section['content']}" for section in sections if section.get("content")
    )
    content_check = verify_content(content_source, sections, full_text)
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "title": title,
                "summary": summary,
                "basic_info": basic_info,
                "sections": sections,
                "full_text": full_text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "keyword": keyword,
        "title": title,
        "source": "百度百科",
        "source_url": source_url,
        "summary": summary,
        "basic_info": basic_info,
        "sections": sections,
        "full_text": full_text,
        "images": images,
        "content_source": content_source,
        "content_check": content_check,
        "has_full_content": content_check["has_full_content"],
        "content_hash": content_hash,
        "status": "raw",
        "fetched_at": fetched_at,
        "updated_at": fetched_at,
    }


def crawl_keyword(keyword: str, timeout: int, cookie: str = "") -> dict[str, Any]:
    try:
        html_text, source_url = fetch_page(keyword, timeout, cookie=cookie)
        document = parse_page(keyword, html_text, source_url)
        if document["summary"] or document["basic_info"] or document["sections"]:
            return document
    except Exception:
        pass

    payload = fetch_card_api_with_ip_retry(keyword, timeout)
    return parse_card_api(keyword, payload)


def save_document(collection, document: dict[str, Any]) -> None:
    collection.update_one(
        {"source": document["source"], "keyword": document["keyword"]},
        {"$set": document},
        upsert=True,
    )


def content_check_message(document: dict[str, Any]) -> str:
    content_check = document.get("content_check", {})
    section_count = content_check.get("section_count", len(document.get("sections", [])))
    full_text_length = content_check.get("full_text_length", len(document.get("full_text", "")))
    content_source = document.get("content_source", "unknown")

    if document.get("has_full_content"):
        return f"已抓到正文，来源：{content_source}，章节数：{section_count}，正文长度：{full_text_length}"
    return f"未抓到正文，来源：{content_source}，章节数：{section_count}，正文长度：{full_text_length}"


def main() -> None:
    config = load_config()
    timeout = int(config["request"]["timeout"])
    interval = float(config["request"]["interval"])
    baike_cookie = str(config["request"].get("baike_cookie", "")).strip()
    mongodb = config["mongodb"]

    client = MongoClient(mongodb["uri"], serverSelectionTimeoutMS=5000)
    collection = client[mongodb["database"]][mongodb["collection"]]
    collection.create_index([("source", 1), ("keyword", 1)])

    try:
        for index, raw_keyword in enumerate(config["keywords"], start=1):
            if index > 1:
                time.sleep(interval)

            keyword = str(raw_keyword).strip()
            if not keyword:
                print("跳过空关键词", flush=True)
                continue

            try:
                document = crawl_keyword(keyword, timeout, cookie=baike_cookie)
                save_document(collection, document)
                print(
                    f"已保存：{document['keyword']} -> {document['title']}；{content_check_message(document)}",
                    flush=True,
                )
            except Exception as exc:
                print(f"抓取失败，已跳过：{keyword}，原因：{exc}", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
