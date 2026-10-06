#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引用条目的机械生成：避免手抄 URL / 标题 / 日期出错。

用法:
    python3 tools/daily-trends/make_refs.py --date 2026-09-20 asyncdot 2609.20804 code.claude.com ...

参数是原始数据里 URL / 标题的子串，按出现顺序匹配，输出可直接粘进
data/trends/<date>.json 的 references 数组（id 从 1 开始连续编号）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"


def collect(raw: dict) -> list[dict]:
    pool: list[dict] = []
    for item in raw.get("hn", []):
        pool.append(
            {
                "title": item["title"],
                "url": item["url"],
                "source": "Hacker News",
                "date": (item.get("created_at") or "")[:10],
                "extra": f'{item.get("points", 0)} points / {item.get("comments", 0)} comments',
                "hn_url": item.get("hn_url"),
            }
        )
    for item in raw.get("arxiv", []):
        pool.append(
            {
                "title": item["title"],
                "url": item["url"].replace("http://", "https://"),
                "source": "arXiv",
                "date": (item.get("published") or "")[:10],
                "extra": ", ".join(item.get("categories") or []),
            }
        )
    for item in raw.get("github", []):
        pool.append(
            {
                "title": f'{item["full_name"]} — {item.get("description") or ""}'.strip(" —"),
                "url": item["html_url"],
                "source": "GitHub",
                "date": (item.get("created_at") or "")[:10],
                "extra": f'{item.get("stars", 0)} stars',
            }
        )
    for name, entries in (raw.get("feeds") or {}).items():
        for entry in entries:
            pool.append(
                {
                    "title": entry.get("title") or entry.get("summary", "")[:90],
                    "url": entry["url"],
                    "source": name,
                    "date": entry.get("date") or (entry.get("published") or "")[:10],
                    "extra": "",
                }
            )
    for item in raw.get("techmeme", []):
        pool.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": "Techmeme",
                "date": raw.get("date", ""),
                "extra": f"Techmeme {item.get('hour', '')}".strip(),
            }
        )
    for item in raw.get("tweets", []):
        pool.append(
            {
                "title": f"@{item.get('author')}: {item.get('text', '')[:90]}",
                "url": item.get("url", ""),
                "source": "X",
                "date": item.get("date") or (item.get("created_at") or "")[:10],
                "extra": f"{item.get('likes', 0)} likes",
            }
        )
    for item in raw.get("huggingface", []):
        pool.append(
            {
                "title": f"{item.get('title', '')}（HuggingFace Daily Papers, {item.get('upvotes', 0)} 赞）",
                "url": item.get("url", ""),
                "source": "HuggingFace",
                "date": item.get("published", ""),
                "extra": f"{item.get('upvotes', 0)} upvotes",
            }
        )
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(description="生成引用条目 JSON")
    parser.add_argument("--date", required=True, help="原始数据日期 YYYY-MM-DD")
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument("needles", nargs="+", help="URL / 标题子串")
    args = parser.parse_args()

    raw_path = RAW_DIR / f"{args.date}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    pool = collect(raw)

    refs = []
    next_id = args.start_id
    for needle in args.needles:
        needle_l = needle.lower()
        hits = [
            item
            for item in pool
            if needle_l in item["url"].lower() or needle_l in (item["title"] or "").lower()
        ]
        if not hits:
            print(f"    !! 未匹配到: {needle}")
            continue
        # 取最贴近的一个：优先 URL 命中且标题最短的
        hits.sort(key=lambda h: (needle_l not in h["url"].lower(), len(h["title"])))
        hit = hits[0]
        refs.append(
            {
                "id": next_id,
                "title": hit["title"],
                "url": hit["url"],
                "source": hit["source"],
                "date": hit["date"],
                "extra": hit["extra"],
            }
        )
        next_id += 1

    print(json.dumps(refs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
