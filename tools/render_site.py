#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI资讯速递 —— 静态页面渲染（适配本站 2026-09 版设计系统）

用法:
    python3 tools/daily-trends/render_site.py                 # 渲染全部期号
    python3 tools/daily-trends/render_site.py --date 2026-09-20

输入（本工具目录）:
    data/<YYYY-MM-DD>.json           每日内容（结构见 RUNBOOK.md）
输出（网站仓库，默认 ~/Documents/ChatGPT/个人网站，可用 --site 覆盖）:
    trends/index.html                 期号目录页
    trends/<YYYY-MM-DD>/index.html    当日文章
    css/trends.css                    本栏目样式（自动生成）
页面入口位于站点主导航，技术目录中不再保留本栏目卡片。

设计约定（与全站一致）:
    · 页面骨架复用 tech/ 栏目的文章页 shell
    · 中英切换使用站点自带的 data-lang-content="zh|en" / data-i18n 机制
    · 颜色、圆角、阴影全部使用 site.css 里的 CSS 变量，自动适配暗色模式
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

# 工具与内容数据放在本目录（与网站仓库分离，站点重新生成后也不会丢）
BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
# 预渲染产物：Hexo 源仓库的 scripts/daily-trends.js 会在生成后把它复制进 public/
ARTIFACT_ROOT = BASE / "site"
# Hexo 源仓库（把入口写进 source/tech/index.md，保证每次重新生成都带着它）
HEXO_SOURCE = Path("/Users/simon-zj/blog/source")
HEXO_DATA = HEXO_SOURCE / "_data"
# 网站静态输出仓库（可用 --site 覆盖）
SITE_ROOT = Path("/Users/simon-zj/Documents/ChatGPT/个人网站")
TRENDS_DIR = SITE_ROOT / "trends"
SHELL_PAGE = Path("/Users/simon-zj/blog/public/tech/tools/paper-reading-workflow/index.html")
TECH_INDEX = SITE_ROOT / "tech" / "index.html"
SITE_URL = "https://www.simon-zj.top"

#: 壳页面（站点导航/头部/尾部）来源。以前只认 Hexo 构建产物 public/，于是
#: `hexo clean` 之后跑一次渲染就会直接崩——顺序依赖藏在常量里。现在按顺序回退：
#: 构建产物 → 站点仓库里已发布的同名页面 → 最小可用壳（只保证 <main>）。
SHELL_CANDIDATES = (
    SHELL_PAGE,
    SITE_ROOT / "tech" / "tools" / "paper-reading-workflow" / "index.html",
)


def resolve_shell() -> Path:
    for candidate in SHELL_CANDIDATES:
        if candidate.is_file() and "<main" in candidate.read_text(encoding="utf-8", errors="replace"):
            return candidate
    fallback = BASE / ".shell-fallback.html"
    if not fallback.is_file():
        fallback.write_text(
            '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            "<title>daily trends</title>"
            '<link rel="stylesheet" href="/css/trends.css">'
            '<link rel="stylesheet" href="/css/site.css"></head>'
            '<body><main class="trend-page"></main></body></html>',
            encoding="utf-8",
        )
    return fallback

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def norm(value, key: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.get(key, "") or ""


def esc(text: str) -> str:
    return html.escape(text or "")


def cite(text: str, refs: list[dict]) -> str:
    """[[1]] / [[1,2]] -> 上标引用链接"""
    by_id = {str(r["id"]): r for r in refs}

    def repl(match: re.Match) -> str:
        ids = [i.strip() for i in match.group(1).split(",") if i.strip()]
        parts = []
        for i in ids:
            parts.append(f'<a href="#ref-{i}">{i}</a>' if i in by_id else i)
        return '<sup class="trend-cite">[' + ",".join(parts) + "]</sup>"

    return re.sub(r"\[\[([0-9,\s]+)\]\]", repl, text)


def bi(tag: str, zh: str, en: str, cls: str = "", raw: bool = False) -> str:
    """整段照抄的两个语言版本元素（与站点 tech/ 栏目一致）。"""
    klass = f' class="{cls}"' if cls else ""
    zh_text = zh if raw else esc(zh)
    en_text = en if raw else esc(en)
    return (
        f'<{tag}{klass} data-lang-content="zh">{zh_text}</{tag}>'
        f'<{tag}{klass} data-lang-content="en">{en_text}</{tag}>'
    )


def bi_block(value, refs: list[dict], cls: str = "") -> str:
    """多段落文本块：每种语言一个 div，段落按空行拆分。"""
    out = []
    for key in ("zh", "en"):
        text = norm(value, key)
        if not text:
            continue
        pending = [p.strip() for p in str(text).split("\n\n") if p.strip()]
        body = "".join(f"<p>{cite(emphasize(p), refs)}</p>" for p in pending)
        klass = f' class="{cls}"' if cls else ""
        out.append(f'<div{klass} data-lang-content="{key}">{body}</div>')
    return "".join(out)


def emphasize(text: str) -> str:
    """突出每条新闻的重点：优先用作者写的 **...** 标记；没有标记时自动加粗首句。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    if "<strong>" in text:
        return text
    # 首句通常是「提出了什么」，作为默认重点（长句也兼容）
    match = re.match(r"^(.{6,260}?[。.!?！？])", text) or re.match(r"^(.{20,80})", text)
    if match:
        first = match.group(1)
        rest = text[len(first):]
        return f"<strong>{first}</strong>{rest}"
    return text


def bi_inline(value, refs: list[dict]) -> str:
    """行内双语（用于 meta 这类短语）。"""
    if isinstance(value, str):
        return f"<span>{cite(esc(value), refs)}</span>"
    return (
        f'<span data-lang-content="zh">{cite(esc(norm(value, "zh")), refs)}</span>'
        f'<span data-lang-content="en">{cite(esc(norm(value, "en")), refs)}</span>'
    )


# --------------------------------------------------------------------------
# article body
# --------------------------------------------------------------------------

def render_overview(content: dict, refs: list[dict]) -> str:
    """全局总结：本批主线 / 最值得关注 / 可跳过的噪音 / 需要交叉验证。"""
    overview = content.get("overview")
    if not overview:
        return ""
    blocks = [
        ("本批资讯的 3 条主线", "Three threads in this batch", overview.get("threads")),
        ("最值得关注的一条", "Most worth reading", overview.get("top")),
        ("可跳过的噪音", "Skippable noise", overview.get("noise")),
        ("需要交叉验证的信息", "Needs cross-verification", overview.get("verify")),
    ]
    if not any(value for _, _, value in blocks):
        return ""
    out = ['<section id="overview" class="workflow-section trend-overview">']
    out.append(bi("h2", "🧭 全局总结", "🧭 Batch Summary"))
    for zh_label, en_label, value in blocks:
        if not value:
            continue
        out.append('<div class="trend-overview__block">')
        out.append(bi("h3", zh_label, en_label))
        out.append(bi_block(value, refs))
        out.append("</div>")
    out.append("</section>")
    return "".join(out)


def render_brief(content: dict, refs: list[dict]) -> str:
    """今日速读：按关注方向选出的 Top N，固定在正文之前。

    选什么由 tools/brief.py 决定（harness 的 daily_trends_brief 闸门复用同一份
    规则，所以「页面上的简报」和「被验收的简报」不会各说一套）；这里只负责把它
    渲染成站点既有的双语版式。
    """
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import brief as brief_mod  # noqa: PLC0415 - optional sibling module
    except ImportError:
        return ""
    try:
        payload = brief_mod.select_brief(content, refs=refs)
    except Exception as exc:  # noqa: BLE001 - a brief failure must not break the page
        print(f"[warn] 今日速读生成失败，跳过：{exc}", file=sys.stderr)
        return ""
    entries = payload.get("entries") or []
    if not entries:
        return ""

    out = ['<section id="brief" class="workflow-section trend-brief">']
    out.append(bi("h2", "今日速读", "Today's brief"))
    out.append(
        bi(
            "p",
            f"从 {payload['candidates']} 条候选里按你的关注方向挑出 {len(entries)} 条，先读这些；"
            f"另有 {payload['excluded']} 条按关注方向过滤（正文仍完整保留在下方）。",
            f"{len(entries)} items picked from {payload['candidates']} by your interest profile; "
            f"{payload['excluded']} filtered out (the full article remains below).",
            cls="trend-brief__lead",
        )
    )
    out.append('<ol class="trend-brief__list">')
    for entry in entries:
        title_zh = entry["title"]["zh"] or entry["title"]["en"]
        title_en = entry["title"]["en"] or entry["title"]["zh"]
        out.append('<li class="trend-brief__item">')
        out.append('<div class="trend-brief__head">')
        out.append(f'<span class="trend-brief__rank">{entry["rank"]:02d}</span>')
        out.append(bi("h3", title_zh, title_en))
        out.append("</div>")
        if entry.get("why_zh") or entry.get("why_en"):
            out.append(
                bi(
                    "p",
                    f"为什么推给你：{entry['why_zh']}",
                    f"Why it is here: {entry['why_en']}",
                    cls="trend-brief__why",
                )
            )
        out.append(bi_block(entry["body"], refs, cls="trend-brief__body"))
        out.append(
            bi(
                "p",
                f"边界：{entry.get('limit_zh') or '正文未提及限制（值得补一句）'}",
                f"Limits: {entry.get('limit_en') or 'no limitation stated (worth adding)'}",
                cls="trend-brief__limit",
            )
        )
        if entry.get("urls"):
            links = "".join(
                f'<a href="{esc(item["url"])}" target="_blank" rel="noopener">{esc(item["label"])}</a>'
                for item in entry["urls"][:3]
            )
            out.append(
                '<p class="trend-brief__src">'
                '<span data-lang-content="zh">来源：</span>'
                '<span data-lang-content="en">Sources: </span>'
                f"{links}</p>"
            )
        out.append("</li>")
    out.append("</ol>")
    out.append("</section>")
    return "".join(out)


def render_tldr(content: dict, refs: list[dict]) -> str:
    tldr = content.get("tldr") or {}
    if not tldr:
        return ""
    out = ['<section id="tldr" class="workflow-section">']
    out.append(bi("h2", "📌 今日速览（TL;DR）", "📌 Today at a Glance (TL;DR)"))
    for key in ("zh", "en"):
        items = tldr.get(key) or []
        if not items:
            continue
        lis = "".join(f"<li>{cite(esc(str(x)), refs)}</li>" for x in items)
        out.append(f'<ul class="trend-tldr" data-lang-content="{key}">{lis}</ul>')
    out.append("</section>")
    return "".join(out)


def render_item(item: dict, index: int, refs: list[dict]) -> str:
    out = ['<article class="trend-item">']
    out.append('<div class="trend-item__head">')
    out.append(f'<span class="trend-item__index">{index:02d}</span>')
    out.append(
        bi(
            "h4",
            norm(item["title"], "zh"),
            norm(item["title"], "en"),
            cls="trend-item__title",
        )
    )
    out.append("</div>")
    if item.get("meta"):
        out.append(f'<div class="trend-item__meta">{bi_inline(item["meta"], refs)}</div>')
    # 最早几期用的是 summary + comment：这里合并成一段，保持「一段式」版式统一
    merged = None
    if not item.get("prose") and not item.get("fields") and item.get("summary"):
        merged = {
            "zh": (norm(item["summary"], "zh") + ("。值得参考的是，" + norm(item["comment"], "zh") if item.get("comment") else "")).strip(),
            "en": (norm(item["summary"], "en") + (" What to take from it: " + norm(item["comment"], "en") if item.get("comment") else "")).strip(),
        }
        out.append(bi_block(merged, refs, cls="trend-item__prose"))
    else:
        out.append(bi_block(item.get("summary") or item.get("body"), refs, cls="trend-item__body"))
    # 新版：直接给一段介绍（提出什么 / 解决什么问题 / 技术要点 / 参考价值）
    if item.get("prose"):
        out.append(bi_block(item["prose"], refs, cls="trend-item__prose"))
    # 旧版兼容：把原来的七个字段串成一段连贯文字，不再分点展示
    elif item.get("fields"):
        lead = {
            "关键事实": ("补充事实是，", "Key facts: "),
            "技术要点": ("技术上，", "Technically, "),
            "影响与意义": ("其意义在于，", "Its significance: "),
            "风险与限制": ("需要注意的是，", "Caveats: "),
            "关注等级": ("关注等级：", "Priority: "),
            "下一步关注": ("可以重点参考的是，", "What you can take away: "),
        }
        joined = {"zh": "", "en": ""}
        for key in ("zh", "en"):
            parts = []
            for f in item["fields"]:
                label = norm(f.get("label"), "zh")
                text = norm(f.get("value"), key).strip()
                if not text:
                    continue
                if label in lead:
                    text = lead[label][0 if key == "zh" else 1] + text
                parts.append(text)
            parts = [p for p in parts if p]
            joined[key] = "".join(p if p.endswith(("。", ".", "！", "？", "!")) else p + "。" for p in parts)
        out.append(bi_block(joined, refs, cls="trend-item__prose"))
    if item.get("fields") and False:
        out.append('<div class="trend-fields">')
        for field in item["fields"]:
            label = bi(
                "span",
                norm(field.get("label"), "zh"),
                norm(field.get("label"), "en"),
                cls="trend-field__label",
            )
            out.append('<div class="trend-field">' + label)
            out.append(bi_block(field.get("value"), refs, cls="trend-field__value"))
            out.append("</div>")
        out.append("</div>")
    if item.get("comment") and merged is None:
        out.append('<div class="trend-comment">')
        out.append(
            bi(
                "span",
                "💡 评价与分析",
                "💡 Analysis",
                cls="trend-comment__label",
            )
        )
        out.append(bi_block(item["comment"], refs))
        out.append("</div>")
    sources = item.get("sources") or []
    if sources:
        by_id = {str(r["id"]): r for r in refs}
        links = []
        for sid in sources:
            ref = by_id.get(str(sid))
            if not ref:
                continue
            links.append(
                f'<a href="{esc(ref["url"])}" target="_blank" rel="noopener noreferrer">'
                f'[{ref["id"]}] {esc(ref.get("source") or ref["title"])}</a>'
            )
        if links:
            out.append('<div class="trend-item__sources">🔗 ' + " ".join(links) + "</div>")
    out.append("</article>")
    return "".join(out)


def render_section(section: dict, refs: list[dict]) -> str:
    out = [f'<section id="section-{section["id"]}" class="workflow-section">']
    out.append(bi("h2", norm(section["title"], "zh"), norm(section["title"], "en")))
    if section.get("intro"):
        out.append(bi_block(section["intro"], refs, cls="trend-section__intro"))
    counter = 0
    if section.get("groups"):
        for group in section["groups"]:
            out.append(f'<div class="trend-group" id="group-{group["id"]}">')
            out.append(bi("h3", norm(group["title"], "zh"), norm(group["title"], "en")))
            if group.get("intro"):
                out.append(bi_block(group["intro"], refs, cls="trend-group__intro"))
            for item in group.get("items", []):
                counter += 1
                out.append(render_item(item, counter, refs))
            out.append("</div>")
    else:
        for item in section.get("items", []):
            counter += 1
            out.append(render_item(item, counter, refs))
    out.append("</section>")
    return "".join(out)


def render_references(refs: list[dict]) -> str:
    if not refs:
        return ""
    out = ['<section id="references" class="workflow-section trend-refs">']
    out.append(bi("h2", "📚 来源与链接", "📚 References"))
    out.append("<ol>")
    for ref in sorted(refs, key=lambda r: r["id"]):
        meta_bits = [b for b in (ref.get("source"), ref.get("date")) if b]
        meta = f' <span class="trend-refs__meta">· {" · ".join(meta_bits)}</span>' if meta_bits else ""
        out.append(
            f'<li id="ref-{ref["id"]}"><a href="{esc(ref["url"])}" target="_blank" '
            f'rel="noopener noreferrer">{esc(ref.get("title") or ref["url"])}</a>{meta}</li>'
        )
    out.append("</ol></section>")
    return "".join(out)


def render_toc(content: dict) -> str:
    out = ['<div class="content-index">']
    out.append('<span class="content-index__label" data-lang-content="zh">目录</span>')
    out.append('<span class="content-index__label" data-lang-content="en">Contents</span>')
    entries = [("#brief", "今日速读", "Today's brief"), ("#tldr", "今日速览", "TL;DR")]
    for section in content.get("sections", []):
        entries.append(
            (
                f'#section-{section["id"]}',
                norm(section["title"], "zh"),
                norm(section["title"], "en"),
            )
        )
        for group in section.get("groups", []):
            entries.append(
                (
                    f'#group-{group["id"]}',
                    norm(group["title"], "zh"),
                    norm(group["title"], "en"),
                )
            )
    entries.append(("#references", "来源与链接", "References"))
    icon = '<i data-lucide="arrow-up-right" aria-hidden="true"></i>'
    for href, zh, en in entries:
        zh_clean = re.sub(r"^[^\w\u4e00-\u9fff]+", "", zh)
        out.append(
            f'<a href="{esc(href)}">'
            f'<span data-lang-content="zh">{esc(zh_clean)}</span>'
            f'<span data-lang-content="en">{esc(en)}</span>{icon}</a>'
        )
    out.append("</div>")
    return "".join(out)


def render_article(content: dict) -> str:
    refs = content.get("references", [])
    stats = content.get("stats", {})
    date = content["date"]
    out = ['<div class="editorial-page trends-page">']
    out.append('<nav class="topic-breadcrumb" aria-label="当前位置">')
    out.append(
        '<a href="/"><span data-lang-content="zh">首页</span>'
        '<span data-lang-content="en">Home</span></a><span>/</span>'
    )
    out.append(
        '<a href="/trends/"><span data-lang-content="zh">AI资讯速递</span>'
        '<span data-lang-content="en">AI News Digest</span></a><span>/</span>'
    )
    out.append(f"<span>{date}</span></nav>")

    out.append('<header class="trend-hero">')
    out.append(f'<span class="trend-hero__eyebrow">AI News Digest / {date}</span>')
    out.append(
        bi(
            "h1",
            f"AI资讯速递 · {date}",
            f"AI News Digest · {date}",
        )
    )
    count_text = (
        f'<span data-lang-content="zh">行业热点 {stats.get("insights", "-")} 条 · '
        f'GitHub 热点 {stats.get("repos", "-")} 条</span>'
        f'<span data-lang-content="en">{stats.get("insights", "-")} industry items · '
        f'{stats.get("repos", "-")} GitHub items</span>'
    )
    out.append(f'<div class="trend-hero__meta">{count_text}</div>')
    out.append(bi_block(content.get("summary"), refs, cls="trend-hero__summary"))
    out.append("</header>")

    out.append(render_toc(content))
    out.append(render_brief(content, refs))
    out.append(render_tldr(content, refs))
    out.append(render_overview(content, refs))
    for section in content.get("sections", []):
        out.append(render_section(section, refs))
    out.append(render_references(refs))

    if content.get("notes"):
        out.append('<section class="content-note trend-notes">')
        out.append(bi("h2", "📅 覆盖口径", "📅 Coverage"))
        # 只发布「覆盖口径」这一段：优先取 coverage 字段，否则取 notes 的第一段
        coverage = content.get("coverage")
        if not coverage:
            notes = content["notes"]
            coverage = {
                "zh": (notes.get("zh") or "").split("\n\n")[0],
                "en": (notes.get("en") or "").split("\n\n")[0],
            }
        out.append(bi_block(coverage, refs))
        out.append(
            bi(
                "p",
                "本文由自动化「AI资讯速递」工作流抓取公开信息后整理，评价与分析部分为个人观点，"
                "不构成投资或技术选型建议。",
                "Compiled by an automated daily-trends workflow from public sources; the analysis "
                "reflects the author's personal views only.",
                cls="trend-disclaimer",
            )
        )
        out.append("</section>")
    out.append("</div>")
    return "".join(out)


# --------------------------------------------------------------------------
# date index page
# --------------------------------------------------------------------------

def render_index(contents: list[dict]) -> str:
    out = ['<div class="editorial-page tech-child trends-index">']
    out.append('<nav class="topic-breadcrumb" aria-label="当前位置">')
    out.append(
        '<a href="/"><span data-lang-content="zh">首页</span>'
        '<span data-lang-content="en">Home</span></a><span>/</span>'
    )
    out.append(
        '<span><span data-lang-content="zh">AI资讯速递</span>'
        '<span data-lang-content="en">AI News Digest</span></span></nav>'
    )
    out.append('<div class="tech-child__intro">')
    out.append(bi("h1", "AI资讯速递", "AI News Digest"))
    out.append(
        bi(
            "p",
            "每天 00:00 自动汇总当天的 Agent 工程（上下文工程、多 Agent 协同、编排）、机器人"
            "（感知 / 预测 / 世界模型）、AI 提效，以及模型公司与行业人物的动向与判断；第二部分"
            "是当天 GitHub 上与 Agent、机器人相关的热门仓库与方法。每条都提供中英双语摘要、"
            "来源引用与个人点评。",
            "Every day at 00:00 this section collects the day's agent engineering (context "
            "engineering, multi-agent collaboration, orchestration), robotics (perception, "
            "prediction, world models) and AI productivity signals, plus moves and opinions from "
            "frontier labs and practitioners. The second part covers trending GitHub repositories "
            "and methods for agents and robotics. Every item ships with a bilingual summary, "
            "citations and a short commentary.",
        )
    )
    out.append("</div>")
    out.append('<div class="trend-issue-list">')
    for content in sorted(contents, key=lambda c: c["date"], reverse=True):
        stats = content.get("stats", {})
        refs = content.get("references", [])
        out.append(f'<a class="trend-issue-card" href="/trends/{content["date"]}/">')
        out.append('<div class="trend-issue-card__top">')
        out.append(f'<span class="trend-issue-card__date">{content["date"]}</span>')
        out.append(
            '<span class="trend-issue-card__count">'
            f'<span data-lang-content="zh">行业热点 {stats.get("insights", "-")} 条 · '
            f'GitHub {stats.get("repos", "-")} 条</span>'
            f'<span data-lang-content="en">{stats.get("insights", "-")} industry · '
            f'{stats.get("repos", "-")} GitHub</span></span>'
        )
        out.append("</div>")
        out.append(bi_block(content.get("summary"), refs, cls="trend-issue-card__summary"))
        out.append(
            '<span class="trend-issue-card__more">'
            '<span data-lang-content="zh">阅读全文</span>'
            '<span data-lang-content="en">Read the full issue</span>'
            '<i data-lucide="arrow-right" aria-hidden="true"></i></span>'
        )
        out.append("</a>")
    out.append("</div>")
    out.append("</div>")
    return "".join(out)


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------

def build_page(main_html: str, title_zh: str, title_en: str, desc_zh: str, desc_en: str, canonical: str) -> str:
    shell_path = resolve_shell()
    shell = shell_path.read_text(encoding="utf-8")
    main_open_match = re.search(r"<main[^>]*>", shell)
    if not main_open_match:
        raise SystemExit(f"[error] shell 里找不到 <main>：{shell_path}")
    main_open = main_open_match.group(0)
    head = shell[: main_open_match.start()]
    after = shell[shell.find("</main>", main_open_match.end()) + len("</main>") :]

    head = re.sub(
        r"<title>.*?</title>",
        f"<title>{esc(title_zh)} | Simon</title>",
        head,
        count=1,
        flags=re.S,
    )
    head = re.sub(
        r'<meta name="description" content="[^"]*">',
        f'<meta name="description" content="{html.escape(desc_zh, quote=True)}">',
        head,
        count=1,
    )
    head = re.sub(
        r'<meta property="og:title" content="[^"]*">',
        f'<meta property="og:title" content="{html.escape(title_zh, quote=True)}">',
        head,
        count=1,
    )
    head = re.sub(
        r'<meta property="og:url" content="[^"]*">',
        f'<meta property="og:url" content="{canonical}">',
        head,
        count=1,
    )
    head = re.sub(
        r'<meta property="og:description" content="[^"]*">',
        f'<meta property="og:description" content="{html.escape(desc_zh, quote=True)}">',
        head,
        count=1,
    )
    if '<link rel="canonical"' in head:
        head = re.sub(
            r'<link rel="canonical" href="[^"]*">',
            f'<link rel="canonical" href="{canonical}">',
            head,
            count=1,
        )
    if 'href="/css/trends.css"' not in head:
        head = head.replace(
            '<link rel="stylesheet" href="/css/site.css">',
            '<link rel="stylesheet" href="/css/site.css"><link rel="stylesheet" href="/css/trends.css">',
            1,
        )

    body_attrs = (
        f'<body class="page trends-page" data-page-title-zh="{html.escape(title_zh, quote=True)}" '
        f'data-page-title-en="{html.escape(title_en, quote=True)}" '
        f'data-page-description-zh="{html.escape(desc_zh, quote=True)}" '
        f'data-page-description-en="{html.escape(desc_en, quote=True)}">'
    )
    head = re.sub(r"<body[^>]*>", body_attrs, head, count=1)

    return head + main_open + main_html + "</main>" + after


def write_article(content: dict) -> Path:
    date = content["date"]
    page = build_page(
        render_article(content),
        title_zh=f"AI资讯速递 · {date}",
        title_en=f"AI News Digest · {date}",
        desc_zh=norm(content.get("summary"), "zh")[:180],
        desc_en=norm(content.get("summary"), "en")[:200],
        canonical=f"{SITE_URL}/trends/{date}/",
    )
    rel = f"trends/{date}/index.html"
    _write_file(ARTIFACT_ROOT, rel, page)
    return _write_file(SITE_ROOT, rel, page)


def write_index(contents: list[dict]) -> Path:
    page = build_page(
        render_index(contents),
        title_zh="AI资讯速递",
        title_en="AI News Digest",
        desc_zh="每天 00:00 更新的中英双语速览：Agent 工程、机器人、AI 提效与模型公司动向，附来源引用与点评。",
        desc_en="A daily bilingual digest of agent engineering, robotics, AI productivity and frontier-lab moves, with citations and commentary.",
        canonical=f"{SITE_URL}/trends/",
    )
    rel = "trends/index.html"
    _write_file(ARTIFACT_ROOT, rel, page)
    return _write_file(SITE_ROOT, rel, page)


def write_home_briefing(contents: list[dict]) -> Path:
    """首页「今日AI快讯」= 最新一期的「今日速读」Top 5。

    以前这里取的是 tldr 的前 5 条，于是同一期内容有两套「最重要」的规则：
    页首速读按关注方向选，首页按写作顺序取。现在两者都来自 `brief.select_brief`，
    首页每张卡片的文案用条目里被 `**...**` 标出的那句（作者自己的重点），
    没有标注时回退到首句。tldr 只在速读选不出 5 条时用来补位。
    """
    latest = max(contents, key=lambda item: item["date"])
    refs = latest.get("references") or []
    zh_items = (latest.get("tldr") or {}).get("zh") or []
    en_items = (latest.get("tldr") or {}).get("en") or []
    items: list[dict[str, str]] = []

    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import brief as brief_mod

        payload = brief_mod.select_brief(latest, refs=refs)
        for entry in payload.get("entries") or []:
            zh = entry.get("line_zh") or entry["title"]["zh"]
            en = entry.get("line_en") or entry["title"]["en"] or zh
            if zh:
                items.append({"zh": zh, "en": en})
    except Exception as exc:  # noqa: BLE001 - 首页不能因为速读失败而空白
        print(f"[warn] 首页快讯取速读失败，回退 tldr：{exc}", file=sys.stderr)

    seen = {item["zh"] for item in items}
    for index, zh in enumerate(zh_items[:5]):
        def tidy(text: str) -> str:
            text = re.sub(r"\[\[[0-9,\s]+\]\]", "", str(text))
            text = text.replace(" 。", "。").replace(" .", ".").replace(" ，", "，").replace(" ,", ",")
            return re.sub(r"\s{2,}", " ", text).strip()

        clean_zh = tidy(zh)
        clean_en = tidy(en_items[index] if index < len(en_items) else zh)
        if len(items) >= 5:
            break
        if clean_zh and clean_zh not in seen:
            seen.add(clean_zh)
            items.append({"zh": clean_zh, "en": clean_en})
    payload = {
        "date": latest["date"],
        "url": f"/trends/{latest['date']}/",
        "items": items[:5],
    }
    path = HEXO_DATA / "ai_briefing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_file(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# 样式文件
# --------------------------------------------------------------------------

TRENDS_CSS = """/* AI资讯速递 —— 本文件由 tools/daily-trends/render_site.py 生成，请勿手改 */

[data-lang-content][hidden] {
  display: none !important;
}

/* ---------- 文章头部 ---------- */
.trend-hero {
  max-width: 860px;
  padding: 8px 0 42px;
  border-bottom: 1px solid var(--site-line);
}

/* 今日速读：正文之前的一屏，按关注方向选出的 Top N */
.trend-brief {
  margin-bottom: 8px;
}

.trend-brief__lead {
  margin: 0 0 26px;
  color: var(--site-muted);
  font-size: 14px;
}

.trend-brief__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.trend-brief__item {
  padding: 26px 0;
  border-top: 1px solid var(--site-line);
}

.trend-brief__item:last-child {
  border-bottom: 1px solid var(--site-line);
}

.trend-brief__head {
  display: flex;
  gap: 14px;
  align-items: baseline;
}

.trend-brief__rank {
  color: var(--page-accent, var(--site-accent));
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.trend-brief__head h3 {
  margin: 0;
  color: var(--site-ink);
  font-size: 19px;
  line-height: 1.45;
}

.trend-brief__why {
  margin: 10px 0 0;
  color: var(--page-accent, var(--site-accent));
  font-size: 13px;
}

.trend-brief__body {
  margin-top: 10px;
  color: var(--site-ink);
  font-size: 15px;
  line-height: 1.75;
}

.trend-brief__limit {
  margin: 10px 0 0;
  padding-left: 12px;
  border-left: 2px solid var(--site-line);
  color: var(--site-muted);
  font-size: 13.5px;
}

.trend-brief__src {
  margin: 8px 0 0;
  color: var(--site-muted);
  font-size: 12.5px;
}

.trend-brief__src a {
  margin-right: 10px;
}

.trend-hero__eyebrow {
  color: var(--page-accent, var(--site-accent));
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.trend-hero h1 {
  margin: 16px 0 0;
  color: var(--site-ink);
  font-size: clamp(30px, 4.4vw, 48px);
  font-weight: 640;
  line-height: 1.12;
  text-wrap: balance;
}

.trend-hero__meta {
  margin-top: 18px;
  color: var(--site-muted);
  font-size: 13px;
}

.trend-hero__summary {
  max-width: 760px;
  margin-top: 20px;
  color: var(--site-muted);
  font-size: 17px;
  line-height: 1.8;
}

/* ---------- 速览 ---------- */
.trend-tldr {
  display: grid;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.trend-tldr li {
  position: relative;
  padding: 16px 20px 16px 46px;
  border: 1px solid var(--site-line);
  border-radius: var(--site-radius);
  background: var(--site-surface);
  line-height: 1.78;
}

.trend-tldr li::before {
  content: '↗';
  position: absolute;
  top: 16px;
  left: 20px;
  color: var(--page-accent, var(--site-accent));
  font-weight: 700;
}

/* ---------- 分组与条目 ---------- */
.trend-section__intro,
.trend-group__intro {
  max-width: 760px;
  color: var(--site-muted);
  line-height: 1.8;
}

.trend-group {
  margin-top: 42px;
}

.trend-group h3 {
  margin: 0 0 6px;
  color: var(--site-ink);
  font-size: 22px;
  line-height: 1.35;
}

.trend-item {
  margin-top: 20px;
  padding: 24px 26px;
  border: 1px solid var(--site-line);
  border-radius: var(--site-radius);
  background: var(--site-surface);
}

.trend-item__head {
  display: flex;
  gap: 14px;
  align-items: baseline;
}

.trend-item__index {
  flex: 0 0 auto;
  color: var(--page-accent, var(--site-accent));
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.trend-item__title {
  margin: 0;
  color: var(--site-ink);
  font-size: 19px;
  line-height: 1.45;
}

.trend-item__meta {
  margin: 10px 0 0 30px;
  color: var(--site-muted);
  font-size: 12px;
}

.trend-item__body {
  margin-top: 14px;
  color: var(--site-ink);
  font-size: 15.5px;
  line-height: 1.85;
}

.trend-item__body p {
  margin: 0 0 10px;
}

/* 重点突出：加粗 + 极淡的底色，保持段落观感 */
.trend-item__prose strong,
.trend-item__body strong {
  color: var(--site-ink);
  font-weight: 700;
  background: linear-gradient(transparent 62%, rgba(224, 90, 54, 0.18) 62%);
}

.trend-comment {
  margin-top: 16px;
  padding: 16px 18px;
  border-left: 3px solid var(--page-accent, var(--site-accent));
  border-radius: 0 var(--site-radius) var(--site-radius) 0;
  background: var(--site-surface-soft);
  font-size: 15px;
  line-height: 1.8;
}

.trend-comment__label {
  display: block;
  margin-bottom: 6px;
  color: var(--page-accent, var(--site-accent));
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.trend-comment p {
  margin: 0;
}

.trend-item__sources {
  margin-top: 14px;
  color: var(--site-muted);
  font-size: 12.5px;
  line-height: 1.9;
}

.trend-item__sources a {
  margin-right: 12px;
  color: var(--page-accent, var(--site-accent));
  text-decoration: none;
}

.trend-item__sources a:hover {
  text-decoration: underline;
}

.trend-cite {
  font-size: 0.72em;
  white-space: nowrap;
}

.trend-cite a {
  color: var(--page-accent, var(--site-accent));
  font-weight: 650;
  text-decoration: none;
}

/* ---------- 结构化条目字段（主编式总结） ---------- */
.trend-fields {
  margin-top: 14px;
  border-top: 1px dashed var(--site-line);
}

.trend-field {
  display: grid;
  grid-template-columns: 104px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--site-line);
}

.trend-field__label {
  color: var(--page-accent, var(--site-accent));
  font-size: 12px;
  font-weight: 680;
  line-height: 1.7;
}

.trend-field__value {
  color: var(--site-ink);
  font-size: 14.5px;
  line-height: 1.75;
}

.trend-field__value p {
  margin: 0 0 6px;
}

.trend-field__value p:last-child {
  margin-bottom: 0;
}

/* ---------- 全局总结 ---------- */
.trend-overview__block {
  margin-top: 18px;
}

.trend-overview__block h3 {
  margin: 0 0 6px;
  font-size: 15px;
}

.trend-overview__block .trend-field__value,
.trend-overview__block div[data-lang-content] {
  color: var(--site-muted);
  font-size: 15px;
  line-height: 1.8;
}

@media (max-width: 768px) {
  .trend-field {
    grid-template-columns: 1fr;
    gap: 2px;
  }
}

/* ---------- 来源与说明 ---------- */
.trend-refs ol {
  margin: 0;
  padding-left: 1.5em;
}

.trend-refs li {
  margin-bottom: 10px;
  font-size: 14.5px;
  line-height: 1.75;
}

.trend-refs a {
  color: var(--site-ink);
}

.trend-refs a:hover {
  color: var(--page-accent, var(--site-accent));
}

.trend-refs__meta {
  color: var(--site-muted);
  font-size: 12.5px;
}

.trend-notes p {
  color: var(--site-muted);
  font-size: 15px;
  line-height: 1.8;
}

.trend-disclaimer {
  margin-top: 18px;
  color: var(--site-muted);
  font-size: 12.5px;
  line-height: 1.7;
}

/* ---------- 期号目录 ---------- */
.trend-issue-list {
  display: grid;
  gap: 16px;
  margin-top: 8px;
}

.trend-issue-card {
  display: block;
  padding: 26px 28px;
  border: 1px solid var(--site-line);
  border-radius: var(--site-radius);
  background: var(--site-surface);
  color: var(--site-ink);
  text-decoration: none;
  transition: transform 200ms ease, border-color 200ms ease, box-shadow 200ms ease;
}

.trend-issue-card:hover {
  transform: translateY(-3px);
  border-color: var(--site-line-strong);
  box-shadow: var(--site-shadow);
}

.trend-issue-card__top {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: baseline;
  justify-content: space-between;
}

.trend-issue-card__date {
  color: var(--site-ink);
  font-size: 24px;
  font-weight: 640;
  letter-spacing: 0.01em;
}

.trend-issue-card__count {
  color: var(--site-muted);
  font-size: 12px;
}

.trend-issue-card__summary {
  margin-top: 14px;
  color: var(--site-muted);
  font-size: 15px;
  line-height: 1.8;
}

.trend-issue-card__summary p {
  margin: 0;
}

.trend-issue-card__more {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
  color: var(--page-accent, var(--site-accent));
  font-size: 13px;
  font-weight: 650;
}

.trend-issue-card__more svg {
  width: 16px;
  height: 16px;
}

@media (max-width: 768px) {
  .trend-item {
    padding: 20px 18px;
  }

  .trend-item__title {
    font-size: 17px;
  }

  .trend-item__meta {
    margin-left: 0;
  }

  .trend-issue-card {
    padding: 20px;
  }
}
"""


def write_trends_css() -> Path:
    _write_file(ARTIFACT_ROOT, "css/trends.css", TRENDS_CSS)
    return _write_file(SITE_ROOT, "css/trends.css", TRENDS_CSS)


def remove_tech_directory_entry() -> bool:
    """从技术目录中移除旧版「每日技术趋势」入口（幂等）。"""
    pattern = re.compile(
        r'\s*<a class="tech-directory__item" href="/trends/">[\s\S]*?</a>'
    )
    changed = False
    for path in (TECH_INDEX, HEXO_SOURCE / "tech" / "index.md"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        updated = pattern.sub("", text, count=1)
        if updated == text:
            continue
        path.write_text(updated, encoding="utf-8")
        changed = True
    return changed


def update_sitemap(contents: list[dict]) -> None:
    path = SITE_ROOT / "sitemap.xml"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    # 幂等：先把已有的 trends 条目全部删干净（含它周围的空白），再统一插回一次。
    # 旧实现只删每条的 "\n  <url>…</url>\n"，反复运行会不断累积空行。
    text = re.sub(
        r"[ \t]*<url>\s*<loc>https://www\.simon-zj\.top/trends/[^<]*</loc>.*?</url>[ \t]*\n?",
        "",
        text,
        flags=re.S,
    )
    text = re.sub(r"(?:[ \t]*\n){3,}", "\n\n", text)
    entries = [(f"{SITE_URL}/trends/", contents[0]["date"] if contents else dt.date.today().isoformat(), "0.7")]
    for content in sorted(contents, key=lambda c: c["date"], reverse=True):
        entries.append((f"{SITE_URL}/trends/{content['date']}/", content["date"], "0.6"))
    blocks = "".join(
        f"\n  <url>\n    <loc>{loc}</loc>\n    <lastmod>{lastmod}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        f"    <priority>{prio}</priority>\n  </url>\n"
        for loc, lastmod, prio in entries
    )
    head = re.sub(r"\s*</urlset>\s*$", "", text).rstrip("\n")
    path.write_text(head + "\n" + blocks + "</urlset>\n", encoding="utf-8")
    # 给 Hexo 构建用：脚本会把这段片段插进 public/sitemap.xml
    _write_file(ARTIFACT_ROOT, "sitemap-fragment.xml", blocks)


def write_hexo_artifacts() -> None:
    """移除旧版技术入口产物，保留样式与 sitemap 片段。"""
    legacy_entry = ARTIFACT_ROOT / "tech-entry.html"
    if legacy_entry.exists():
        legacy_entry.unlink()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_contents(only_date: str | None = None) -> list[dict]:
    contents = []
    for path in sorted(DATA_DIR.glob("20*.json")):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[warn] 跳过无法解析的 {path.name}: {exc}", file=sys.stderr)
            continue
        if only_date and content.get("date") != only_date:
            continue
        contents.append(content)
    return contents


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 AI 资讯速递页面")
    parser.add_argument("--date", help="只渲染指定日期（YYYY-MM-DD）")
    parser.add_argument("--no-inject", action="store_true", help="跳过技术笔记入口注入")
    args = parser.parse_args()

    contents = load_contents(args.date)
    if not contents:
        print("[error] 没有找到 data/<date>.json", file=sys.stderr)
        return 1

    write_hexo_artifacts()
    print(f"[render] 首页速览 {write_home_briefing(load_contents()).relative_to(HEXO_SOURCE)}")
    for content in contents:
        print(f"[render] article {write_article(content).relative_to(SITE_ROOT)}")
    print(f"[render] index   {write_index(load_contents()).relative_to(SITE_ROOT)}")
    print(f"[render] styles  {write_trends_css().relative_to(SITE_ROOT)}")
    print(f"[render] 预渲染产物 {ARTIFACT_ROOT}")

    update_sitemap(load_contents())
    print("[render] sitemap.xml updated")

    changed = remove_tech_directory_entry()
    print("[render] 技术目录旧入口：" + ("已移除" if changed else "无需处理"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
