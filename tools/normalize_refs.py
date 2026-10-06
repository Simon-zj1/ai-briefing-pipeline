#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引用编号归一化：删掉未被引用的条目，把编号压实成连续整数，并同步重写正文里的
[[n]] 引用标记与每个条目的 sources 数组。

用法:
    python3 tools/daily-trends/normalize_refs.py --date 2026-09-20 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"

CITE_RE = re.compile(r"\[\[([0-9,\s]+)\]\]")


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


def substitute_strings(node, fn):
    if isinstance(node, str):
        return fn(node)
    if isinstance(node, dict):
        return {k: substitute_strings(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute_strings(v, fn) for v in node]
    return node


def main() -> int:
    parser = argparse.ArgumentParser(description="归一化引用编号")
    parser.add_argument("--date", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = DATA_DIR / f"{args.date}.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    refs = content.get("references", [])
    known = {r["id"] for r in refs}

    used: set[int] = set()
    missing: set[int] = set()
    for text in walk_strings(content):
        for match in CITE_RE.finditer(text):
            for token in match.group(1).split(","):
                token = token.strip()
                if not token:
                    continue
                num = int(token)
                (used if num in known else missing).add(num)
    for section in content.get("sections", []):
        items = list(section.get("items", []))
        for group in section.get("groups", []):
            items += list(group.get("items", []))
        for item in items:
            for sid in item.get("sources", []):
                (used if sid in known else missing).add(sid)

    if missing:
        print(f"[warn] 正文引用了不存在的编号: {sorted(missing)}", file=sys.stderr)

    kept = [r for r in sorted(refs, key=lambda r: r["id"]) if r["id"] in used]
    mapping = {r["id"]: i + 1 for i, r in enumerate(kept)}
    dropped = sorted(known - used)
    if dropped:
        print(f"[info] 删除未被引用的条目: {dropped}")

    def rewrite(text: str) -> str:
        def repl(match: re.Match) -> str:
            nums = [t.strip() for t in match.group(1).split(",") if t.strip()]
            new = [str(mapping.get(int(n), n)) for n in nums]
            return "[[" + ",".join(new) + "]]"

        return CITE_RE.sub(repl, text)

    content = substitute_strings(content, rewrite)
    for section in content.get("sections", []):
        items = list(section.get("items", []))
        for group in section.get("groups", []):
            items += list(group.get("items", []))
        for item in items:
            if item.get("sources"):
                item["sources"] = [mapping.get(s, s) for s in item["sources"]]
    content["references"] = [
        {**{k: v for k, v in ref.items()}, "id": mapping[ref["id"]]} for ref in kept
    ]

    if args.dry_run:
        print(f"[dry-run] 保留 {len(kept)} 条引用，未写回文件")
        return 0
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] 写回 {path.relative_to(BASE)}，保留 {len(kept)} 条引用（编号 1–{len(kept)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
