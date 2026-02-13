#!/usr/bin/env python3
"""Fetch latest AIBase news and push new items to a Feishu custom bot webhook."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

LIST_URL = os.getenv("LIST_URL", "https://news.aibase.com/zh/news")
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
TOP_N = int(os.getenv("TOP_N", "5"))
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
    except (json.JSONDecodeError, OSError):
        return {"seen_ids": []}

    if not isinstance(data, dict):
        return {"seen_ids": []}
    if "seen_ids" not in data or not isinstance(data["seen_ids"], list):
        data["seen_ids"] = []
    return data


def save_state(path: Path, state: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_news() -> list[tuple[int, str, str]]:
    resp = requests.get(LIST_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

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


def main() -> int:
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        print("ERROR: FEISHU_WEBHOOK is required.", file=sys.stderr)
        return 1

    if TOP_N <= 0:
        print("Nothing to do: TOP_N <= 0")
        return 0

    state = load_state(STATE_FILE)
    seen_ids = {str(x) for x in state.get("seen_ids", [])}

    try:
        all_items = fetch_news()
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: failed to fetch news: {exc}", file=sys.stderr)
        return 1
    if not all_items:
        print(
            "ERROR: fetched page but found 0 news links; page structure may have changed.",
            file=sys.stderr,
        )
        return 1

    new_items = [item for item in all_items if str(item[0]) not in seen_ids]
    if not new_items:
        print("No new items found.")
        return 0

    selected = new_items[:TOP_N]

    try:
        post_to_feishu(webhook, selected)
    except Exception as exc:  # pragma: no cover
        print(f"ERROR: failed to post to Feishu: {exc}", file=sys.stderr)
        return 1

    for news_id, _, _ in selected:
        seen_ids.add(str(news_id))
    state["seen_ids"] = sorted(seen_ids, key=int, reverse=True)[:MAX_SEEN_IDS]
    save_state(STATE_FILE, state)
    print(f"Pushed {len(selected)} item(s) to Feishu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
