#!/usr/bin/env python3
"""Fetch latest AIBase news and push new items to a Feishu custom bot webhook."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover
    BeautifulSoup = None

LIST_URL = os.getenv("LIST_URL", "https://news.aibase.com/zh/news")
LIST_URL_TEMPLATE = os.getenv("LIST_URL_TEMPLATE", "").strip()
PAGE_PARAM = os.getenv("PAGE_PARAM", "page").strip() or "page"
FETCH_SOURCE = os.getenv("FETCH_SOURCE", "api").strip().lower() or "api"
API_NEWS_URL = os.getenv("API_NEWS_URL", "https://mcpapi.aibase.cn/api/aiInfo/aiNews").strip()
API_LANG_TYPE = os.getenv("API_LANG_TYPE", "zh_cn").strip() or "zh_cn"
API_T_VALUE = os.getenv("API_T_VALUE", "").strip()
NEWS_URL_PREFIX = os.getenv("NEWS_URL_PREFIX", "https://news.aibase.com/zh/news").strip().rstrip("/")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TOP_N = int(os.getenv("TOP_N", "5"))
MAX_PAGES = max(1, int(os.getenv("MAX_PAGES", "3")))
PUSH_BATCH_SIZE = int(os.getenv("PUSH_BATCH_SIZE", "0"))
MAX_SEEN_IDS = int(os.getenv("MAX_SEEN_IDS", "5000"))
FEISHU_MSG_STYLE = os.getenv("FEISHU_MSG_STYLE", "post").strip().lower()
TITLE_MAX_LEN = int(os.getenv("TITLE_MAX_LEN", "46"))
NEWS_LINK_PATTERN = re.compile(r"^/zh/news/(\d+)$")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; aibase-to-feishu-bot/1.0; "
        "+https://github.com/actions)"
    )
}


def normalize_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip()
    if "。" in title and len(title) > TITLE_MAX_LEN:
        title = title.split("。", 1)[0].strip()
    if "，" in title and len(title) > TITLE_MAX_LEN:
        title = title.split("，", 1)[0].strip()
    if len(title) > TITLE_MAX_LEN:
        title = title[: TITLE_MAX_LEN - 1].rstrip() + "…"
    return title


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_ids": []}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"State file is not valid JSON: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to read state file: {path}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"State file must contain a JSON object: {path}")
    if "seen_ids" not in data or not isinstance(data["seen_ids"], list):
        raise RuntimeError(f"State file must contain a 'seen_ids' list: {path}")
    data["seen_ids"] = normalize_seen_ids(data["seen_ids"])
    return data


def save_state(path: Path, state: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_seen_ids(raw_seen_ids: list[object]) -> list[str]:
    normalized: set[str] = set()
    for item in raw_seen_ids:
        value = str(item).strip()
        if value.isdigit():
            normalized.add(str(int(value)))
    return sorted(normalized, key=int, reverse=True)[:MAX_SEEN_IDS]


def mark_items_as_seen(state: dict, items: list[tuple[int, str, str]]) -> None:
    seen_ids = [str(item) for item in state.get("seen_ids", [])]
    seen_ids.extend(str(news_id) for news_id, _, _ in items)
    state["seen_ids"] = normalize_seen_ids(seen_ids)


def require_optional_deps() -> None:
    if requests is None:
        raise RuntimeError("Missing requests dependency. Run: pip install -r requirements.txt")


def extract_news_items_from_html(html: str) -> list[tuple[int, str, str]]:
    require_optional_deps()
    if BeautifulSoup is None:
        raise RuntimeError("Missing beautifulsoup4 dependency. Run: pip install -r requirements.txt")
    soup = BeautifulSoup(html, "html.parser")
    latest_by_id: dict[int, tuple[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        match = NEWS_LINK_PATTERN.match(href)
        if not match:
            continue

        news_id = int(match.group(1))
        title = normalize_title(a.get("title") or a.get_text(" ", strip=True))
        if not title:
            continue
        if "AI资讯" in title or "最新资讯" in title:
            continue

        url = f"https://news.aibase.com{href}"
        prev = latest_by_id.get(news_id)
        if prev is None or len(title) < len(prev[0]):
            latest_by_id[news_id] = (title, url)

    items = [(news_id, title, url) for news_id, (title, url) in latest_by_id.items()]
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def extract_news_items_from_api_payload(payload: dict) -> list[tuple[int, str, str]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    raw_list = data.get("list")
    if not isinstance(raw_list, list):
        return []

    latest_by_id: dict[int, tuple[str, str]] = {}
    for row in raw_list:
        if not isinstance(row, dict):
            continue

        try:
            news_id = int(str(row.get("oid")))
        except (TypeError, ValueError):
            continue

        title = normalize_title(str(row.get("title") or ""))
        if not title:
            continue
        if "AI资讯" in title or "最新资讯" in title:
            continue

        url = f"{NEWS_URL_PREFIX}/{news_id}"
        prev = latest_by_id.get(news_id)
        if prev is None or len(title) < len(prev[0]):
            latest_by_id[news_id] = (title, url)

    items = [(news_id, title, url) for news_id, (title, url) in latest_by_id.items()]
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def build_list_page_url(
    base_url: str,
    page: int,
    *,
    page_param: str = PAGE_PARAM,
    template: str = LIST_URL_TEMPLATE,
) -> str:
    if page <= 0:
        raise ValueError("page must be >= 1")
    if template:
        if "{page}" not in template:
            raise ValueError("LIST_URL_TEMPLATE must include '{page}' placeholder")
        return template.format(page=page)
    if page == 1:
        return base_url

    parsed = urlparse(base_url)
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != page_param]
    query_pairs.append((page_param, str(page)))
    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def fetch_news_page(page_url: str) -> list[tuple[int, str, str]]:
    require_optional_deps()
    resp = requests.get(page_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return extract_news_items_from_html(resp.text)


def fetch_news_page_api(page_no: int) -> list[tuple[int, str, str]]:
    require_optional_deps()
    params = {
        "langType": API_LANG_TYPE,
        "pageNo": page_no,
        "t": API_T_VALUE or str(int(time.time() * 1000)),
    }
    resp = requests.get(API_NEWS_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"API response is not JSON object: {payload}")
    if payload.get("code") not in (0, 200):
        raise RuntimeError(f"API returned non-success code: {payload}")
    return extract_news_items_from_api_payload(payload)


def fetch_news(
    max_pages: int = MAX_PAGES,
    source: str = FETCH_SOURCE,
) -> list[tuple[int, str, str]]:
    normalized_source = (source or "api").strip().lower()
    if normalized_source not in {"api", "html"}:
        raise ValueError("FETCH_SOURCE must be one of: api, html")

    latest_by_id: dict[int, tuple[str, str]] = {}
    for page in range(1, max_pages + 1):
        page_identifier = f"page {page}"
        try:
            if normalized_source == "api":
                page_identifier = f"{API_NEWS_URL}?langType={API_LANG_TYPE}&pageNo={page}"
                page_items = fetch_news_page_api(page)
            else:
                page_url = build_list_page_url(LIST_URL, page)
                page_identifier = page_url
                page_items = fetch_news_page(page_url)
        except Exception:
            if page == 1:
                raise
            print(
                f"WARNING: failed to fetch page {page}: {page_identifier}. Using pages fetched so far.",
                file=sys.stderr,
            )
            break
        if page > 1 and not page_items:
            break

        for news_id, title, url in page_items:
            prev = latest_by_id.get(news_id)
            if prev is None or len(title) < len(prev[0]):
                latest_by_id[news_id] = (title, url)

    items = [(news_id, title, url) for news_id, (title, url) in latest_by_id.items()]
    items.sort(key=lambda x: x[0], reverse=True)
    return items


def split_batches(
    items: list[tuple[int, str, str]],
    batch_size: int = PUSH_BATCH_SIZE,
) -> list[list[tuple[int, str, str]]]:
    if not items:
        return []
    if batch_size <= 0:
        return [items]
    return [items[idx : idx + batch_size] for idx in range(0, len(items), batch_size)]


def build_message(items: list[tuple[int, str, str]]) -> str:
    lines = ["AIBase 最新资讯："]
    for _, title, url in items:
        lines.append(f"- {title}\n  {url}")
    return "\n".join(lines)


def build_post_payload(items: list[tuple[int, str, str]]) -> dict:
    lines: list[list[dict]] = []
    for idx, (_, title, url) in enumerate(items, start=1):
        lines.append(
            [
                {"tag": "text", "text": f"{idx}. "},
                {"tag": "a", "text": title, "href": url},
            ]
        )
    lines.append([{"tag": "text", "text": "来源：news.aibase.com"}])
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "AIBase 最新资讯",
                    "content": lines,
                }
            }
        },
    }


def post_to_feishu(webhook: str, items: list[tuple[int, str, str]]) -> None:
    require_optional_deps()
    if FEISHU_MSG_STYLE == "text":
        payload = {"msg_type": "text", "content": {"text": build_message(items)}}
    else:
        payload = build_post_payload(items)

    resp = requests.post(webhook, json=payload, timeout=20)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:  # pragma: no cover
        raise RuntimeError(f"Feishu webhook did not return JSON: {resp.text}") from exc
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu webhook failed: {data}")


def deliver_batches(
    webhook: str,
    items: list[tuple[int, str, str]],
    state: dict,
    state_path: Path,
    *,
    batch_size: int = PUSH_BATCH_SIZE,
) -> int:
    delivered_count = 0
    batches = split_batches(items, batch_size)
    for idx, batch in enumerate(batches, start=1):
        post_to_feishu(webhook, batch)
        mark_items_as_seen(state, batch)
        save_state(state_path, state)
        delivered_count += len(batch)
        if len(batches) > 1:
            print(f"Pushed batch {idx}/{len(batches)} with {len(batch)} item(s).")
    return delivered_count


def main() -> int:
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        print("ERROR: FEISHU_WEBHOOK is required.", file=sys.stderr)
        return 1

    if TOP_N < 0:
        print("Nothing to do: TOP_N < 0")
        return 0

    try:
        state = load_state(STATE_FILE)
    except Exception as exc:
        print(f"ERROR: failed to load state: {exc}", file=sys.stderr)
        return 1
    seen_ids = set(state.get("seen_ids", []))

    try:
        all_items = fetch_news()
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: failed to fetch news: {exc}", file=sys.stderr)
        return 1
    if not all_items:
        print(
            "ERROR: fetched pages but found 0 news links; page structure may have changed.",
            file=sys.stderr,
        )
        return 1

    new_items = [item for item in all_items if str(item[0]) not in seen_ids]
    if not new_items:
        print("No new items found.")
        return 0

    selected = new_items if TOP_N == 0 else new_items[:TOP_N]

    try:
        deliver_batches(webhook, selected, state, STATE_FILE, batch_size=PUSH_BATCH_SIZE)
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: failed to post to Feishu: {exc}", file=sys.stderr)
        return 1

    print(f"Pushed {len(selected)} item(s) to Feishu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
