"""Crawl Baidu Baike article body with a real browser session.

Run:
    python playwright_baike_crawler.py

If Baidu shows a security verification page, complete it manually in the opened
browser window, then press Enter in the terminal. The browser profile is stored
in .baike_browser_profile and reused on later runs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag
from pymongo import MongoClient

from baike_disease_crawler import (
    BAIKE_PAGE_URL,
    build_document,
    clean_text,
    content_check_message,
    load_config,
    save_document,
    tag_text,
)


BROWSER_PROFILE_DIR = ".baike_browser_profile"
PAGE_SETTLE_SECONDS = 2
MIN_SECTION_TEXT_LENGTH = 80

STOP_MARKERS = {
    "参考资料",
    "投诉建议",
    "©2026 Baidu",
    "使用百度前必读",
    "百科协议",
    "隐私政策",
    "百度百科合作平台",
}

NOISE_LINES = {
    "播报",
    "编辑",
    "收藏",
    "查看",
    "目录",
    "展开",
    "收起",
    "百度健康医典",
    "|",
}

COMMON_SECTION_HEADINGS = {
    "病因",
    "症状",
    "就医",
    "治疗",
    "预后",
    "预防",
    "概述",
    "疾病类型",
    "发病原因",
    "典型症状",
    "常见症状",
    "临床表现",
    "检查",
    "诊断",
    "鉴别诊断",
    "并发症",
    "药物治疗",
    "手术治疗",
    "流行病学",
}


def baike_url(keyword: str) -> str:
    return BAIKE_PAGE_URL.format(keyword=quote(keyword))


def is_security_page(title: str, html_text: str) -> bool:
    return "百度安全验证" in title or "百度安全验证" in html_text or "seccaptcha" in html_text


def wait_for_manual_verification(page, timeout_ms: int) -> None:
    while is_security_page(page.title(), page.content()):
        print("检测到百度安全验证。请在打开的浏览器中手动完成验证，然后回到终端按 Enter 继续。", flush=True)
        input()
        page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(1500)


def scroll_page(page) -> None:
    page.evaluate(
        """
        async () => {
            await new Promise((resolve) => {
                let totalHeight = 0;
                const distance = 600;
                const timer = setInterval(() => {
                    window.scrollBy(0, distance);
                    totalHeight += distance;
                    if (totalHeight >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 120);
            });
            window.scrollTo(0, 0);
        }
        """
    )


def has_class(tag: Tag, class_name: str) -> bool:
    return class_name in (tag.get("class") or [])


def class_contains(tag: Tag, fragment: str) -> bool:
    return any(fragment in class_name for class_name in (tag.get("class") or []))


def is_summary_tag(tag: Tag) -> bool:
    return has_class(tag, "lemma-summary") or tag.get("label-module") == "lemmaSummary"


def is_heading_tag(tag: Tag) -> bool:
    if tag.name in {"h2", "h3", "h4"}:
        return True
    return has_class(tag, "para-title") or class_contains(tag, "title")


def is_paragraph_tag(tag: Tag) -> bool:
    return tag.name == "p" or has_class(tag, "para")


def is_noise_text(text: str) -> bool:
    text = clean_text(text)
    if not text:
        return True
    if text in NOISE_LINES:
        return True
    if any(marker in text for marker in STOP_MARKERS):
        return True
    if text.startswith("©") or text.startswith("京ICP") or text.startswith("京公网安备"):
        return True
    if text.endswith("．") and len(text) < 80:
        return True
    return False


def should_stop_at_text(text: str) -> bool:
    return any(marker in clean_text(text) for marker in STOP_MARKERS)


def is_useful_section(section: dict[str, str]) -> bool:
    heading = clean_text(section.get("heading", ""))
    content = clean_text(section.get("content", ""))
    if not content or is_noise_text(heading) or is_noise_text(content):
        return False
    return len(content) >= MIN_SECTION_TEXT_LENGTH or heading in COMMON_SECTION_HEADINGS


def looks_like_heading(line: str) -> bool:
    line = clean_text(line).strip(":： ")
    if line in COMMON_SECTION_HEADINGS:
        return True
    if len(line) <= 14 and not line.endswith(("。", "，", "；", "、", ".")):
        return True
    return False


def parse_basic_info(soup: BeautifulSoup) -> dict[str, str]:
    basic_info: dict[str, str] = {}
    for name_tag, value_tag in zip(
        soup.select("dt.basicInfo-item.name"),
        soup.select("dd.basicInfo-item.value"),
    ):
        key = tag_text(name_tag, separator="").strip(":： ")
        value = tag_text(value_tag, separator="")
        if key and value:
            basic_info[key] = value
    return basic_info


def parse_sections(soup: BeautifulSoup) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for tag in soup.find_all(lambda item: isinstance(item, Tag) and (is_heading_tag(item) or is_paragraph_tag(item))):
        if tag.find_parent(is_summary_tag):
            continue

        text = tag_text(tag)
        if not text:
            continue
        if should_stop_at_text(text):
            break
        if is_noise_text(text):
            continue

        if is_heading_tag(tag):
            heading = clean_text(text).strip(":： ")
            if heading and not is_noise_text(heading):
                current = {"heading": heading, "content": ""}
                sections.append(current)
            continue

        if current is None:
            current = {"heading": "正文", "content": ""}
            sections.append(current)
        current["content"] = f"{current['content']}\n{text}".strip()

    return [section for section in sections if is_useful_section(section)]


def parse_visible_text_sections(visible_text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in visible_text.splitlines():
        line = clean_text(raw_line).strip()
        if not line:
            continue
        if should_stop_at_text(line):
            break
        if is_noise_text(line):
            continue

        if looks_like_heading(line):
            current = {"heading": line.strip(":： "), "content": ""}
            sections.append(current)
            continue

        if current is None:
            current = {"heading": "正文", "content": ""}
            sections.append(current)
        current["content"] = f"{current['content']}\n{line}".strip()

    useful_sections = [section for section in sections if is_useful_section(section)]
    if useful_sections:
        return useful_sections

    body_text = "\n".join(
        clean_text(line).strip()
        for line in visible_text.splitlines()
        if clean_text(line).strip() and not is_noise_text(line)
    )
    if len(body_text) >= MIN_SECTION_TEXT_LENGTH:
        return [{"heading": "正文", "content": body_text}]
    return []


def parse_images(soup: BeautifulSoup, source_url: str) -> list[str]:
    images: list[str] = []
    for image in soup.find_all("img"):
        src = image.get("data-src") or image.get("src")
        if not src or src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        images.append(urljoin(source_url, src))
    return list(dict.fromkeys(images))[:12]


def parse_rendered_page(
    keyword: str,
    html_text: str,
    source_url: str,
    visible_text: str = "",
) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title = tag_text(soup.find("h1"), separator="") or keyword
    summary = tag_text(soup.find(is_summary_tag))

    if not summary:
        meta = soup.find("meta", attrs={"name": "description"})
        summary = clean_text(meta.get("content", "")) if isinstance(meta, Tag) else ""

    sections = parse_sections(soup)
    if visible_text and sum(len(section["content"]) for section in sections) < MIN_SECTION_TEXT_LENGTH:
        sections = parse_visible_text_sections(visible_text)

    return build_document(
        keyword=keyword,
        title=title,
        source_url=source_url,
        summary=summary,
        basic_info=parse_basic_info(soup),
        sections=sections,
        images=parse_images(soup, source_url),
        content_source="html",
    )


def launch_context(playwright, timeout_ms: int):
    profile_path = Path(BROWSER_PROFILE_DIR)
    profile_path.mkdir(exist_ok=True)

    launch_options = {
        "user_data_dir": str(profile_path),
        "headless": False,
        "viewport": {"width": 1280, "height": 900},
        "locale": "zh-CN",
        "timeout": timeout_ms,
    }

    try:
        return playwright.chromium.launch_persistent_context(channel="msedge", **launch_options)
    except Exception:
        return playwright.chromium.launch_persistent_context(**launch_options)


def crawl_keyword_with_browser(context, keyword: str, timeout_ms: int) -> dict[str, Any]:
    page = context.new_page()
    try:
        page.goto(baike_url(keyword), wait_until="domcontentloaded", timeout=timeout_ms)
        wait_for_manual_verification(page, timeout_ms)
        page.wait_for_timeout(PAGE_SETTLE_SECONDS * 1000)
        scroll_page(page)

        html_text = page.content()
        visible_text = page.locator("body").inner_text(timeout=timeout_ms)
        document = parse_rendered_page(keyword, html_text, page.url, visible_text=visible_text)
        if not document.get("has_full_content"):
            check = document.get("content_check", {})
            raise RuntimeError(
                "未解析到有效正文，"
                f"章节数：{check.get('section_count', 0)}，"
                f"正文长度：{check.get('full_text_length', 0)}"
            )
        document["crawler"] = {
            "name": "playwright_baike_crawler",
            "mode": "manual_browser_session",
        }
        return document
    finally:
        page.close()


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖 playwright。请先运行：\n"
            ".\\.venv\\Scripts\\python.exe -m pip install playwright\n"
            ".\\.venv\\Scripts\\python.exe -m playwright install chromium"
        ) from exc

    config = load_config()
    timeout_ms = int(config["request"]["timeout"]) * 1000
    interval = float(config["request"]["interval"])
    mongodb = config["mongodb"]

    client = MongoClient(mongodb["uri"], serverSelectionTimeoutMS=5000)
    collection = client[mongodb["database"]][mongodb["collection"]]
    collection.create_index([("source", 1), ("keyword", 1)])

    with sync_playwright() as playwright:
        context = launch_context(playwright, timeout_ms)
        try:
            for index, raw_keyword in enumerate(config["keywords"], start=1):
                keyword = str(raw_keyword).strip()
                if not keyword:
                    print("跳过空关键词", flush=True)
                    continue
                if index > 1:
                    time.sleep(interval)

                try:
                    document = crawl_keyword_with_browser(context, keyword, timeout_ms)
                    save_document(collection, document)
                    print(
                        f"已保存：{document['keyword']} -> {document['title']}；"
                        f"{content_check_message(document)}",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"抓取失败，已跳过：{keyword}，原因：{exc}", flush=True)
        finally:
            context.close()
            client.close()


if __name__ == "__main__":
    main()
