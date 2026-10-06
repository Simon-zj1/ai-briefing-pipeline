#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日技术趋势 —— 原始数据抓取（支持本地代理，可访问 X / Google / Bing / HuggingFace 等）

用法:
    python3 tools/daily-trends/fetch_sources.py --date 2026-09-20
    python3 tools/daily-trends/fetch_sources.py --date 2026-09-20 --proxy none
    python3 tools/daily-trends/fetch_sources.py --date 2026-09-20 --only x_trends,tweets

产出:
    data/trends/raw/<date>.json

代理策略（--proxy auto 为默认）:
    1. 环境变量 HTTPS_PROXY / ALL_PROXY
    2. 本机常见端口：socks5h://127.0.0.1:7890（Clash / FlClash 混合端口）
       与 http://127.0.0.1:1087（privoxy）
    3. 都不可用时直连

抓取源:
    hn          Hacker News（当日高分讨论）
    github      GitHub Search API（近期热门 Agent / 机器人仓库）
    arxiv       arXiv 最新 cs.AI / cs.RO / cs.CL / cs.LG
    feeds       官方博客与科技媒体 RSS（OpenAI / Google / DeepMind / TechCrunch / The Verge / Ars / WIRED / Simon Willison）
    anthropic   Anthropic 新闻页
    qwen        Qwen 博客
    techmeme    Techmeme 当日归档（跨媒体交叉核对）
    x_trends    X（推特）当日热榜（数据来自 trends24 对 X trends 的公开快照）
    x_search    X API v2 关键词搜索（需配置 X_BEARER_TOKEN，见 tools/x_api.py）
    huggingface HuggingFace Daily Papers
    tweets      从已抓取页面中发现推文链接，再用 X 官方嵌入接口读取原文与互动量
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 30
# 常见代理端口（Clash/Verge 混用 7890 与 7897；其余为 privoxy / ss-local / v2ray / Surge 等）
COMMON_PROXY_PORTS = (
    7897, 7890, 7891, 7892, 1087, 1086, 1080, 8118, 6152, 8889, 9000,
    10808, 10809, 20171, 2080, 8888, 12334, 12335,
)
PROXY_CANDIDATES = tuple(
    f"{scheme}://127.0.0.1:{port}"
    for port in COMMON_PROXY_PORTS
    for scheme in ("http", "socks5h")   # 实测 HTTP 入口更稳，优先
)

AI_KEYWORDS = re.compile(
    r"\b(ai|agent|agents|agentic|llm|llms|gpt|chatgpt|claude|gemini|copilot|qwen|deepseek|"
    r"grok|xai|openai|anthropic|deepmind|mistral|nvidia|gpu|model models|robot|robots|"
    r"humanoid|robotaxi|vla|world model|inference|chip|datacenter|data center|alignment|"
    r"misalignment|harness|context engineering|reasoning|coding)\b",
    re.I,
)

_PROXY: str | None = None


# --------------------------------------------------------------------------
# HTTP（走 curl，天然支持 socks5h）
# --------------------------------------------------------------------------

def _curl_ok(proxy: str | None) -> bool:
    cmd = ["curl", "-s", "-o", "/dev/null", "-m", "6", "-w", "%{http_code}"]
    if proxy:
        cmd += ["-x", proxy]
    cmd.append("https://www.google.com/")
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=12).stdout.decode().strip()
    except (subprocess.SubprocessError, OSError):
        return False
    return out in {"200", "301", "302"}


def _listening_local_ports() -> list[int]:
    """扫描本机监听端口，用于自动发现代理端口（代理软件换端口时也能跟上）。"""
    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    text = proc.stdout.decode("utf-8", "replace")
    ports = set()
    for line in text.splitlines()[1:]:
        for match in re.finditer(r"127\.0\.0\.1:(\d+)", line):
            ports.add(int(match.group(1)))
    common = [p for p in COMMON_PROXY_PORTS if p in ports]
    rest = sorted(p for p in ports if p not in COMMON_PROXY_PORTS)
    return common + rest


def resolve_proxy(mode: str) -> str | None:
    global _PROXY
    if mode == "none":
        _PROXY = None
        return None
    if mode != "auto":
        _PROXY = mode
        return mode
    for env in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY"):
        value = os.environ.get(env)
        if value:
            _PROXY = value
            return value
    # 先试已知的常见端口，再扫描本机监听端口逐个探测
    dynamic = tuple(
        f"{scheme}://127.0.0.1:{port}"
        for port in _listening_local_ports()
        for scheme in ("http", "socks5h")
    )
    for candidate in PROXY_CANDIDATES + dynamic[:40]:
        if _curl_ok(candidate):
            _PROXY = candidate
            print(f"[fetch] 自动探测到可用代理：{candidate}", file=sys.stderr)
            return candidate
    _PROXY = None
    return None


def http_get(url: str, accept: str | None = None, timeout: int = TIMEOUT) -> bytes | None:
    if shutil.which("curl") is None:
        print("[warn] 系统缺少 curl，无法抓取", file=sys.stderr)
        return None
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA]
    if _PROXY:
        cmd += ["-x", _PROXY]
    if accept:
        cmd += ["-H", f"Accept: {accept}"]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"  [warn] fetch failed: {url} ({exc})", file=sys.stderr)
        return None
    if proc.returncode != 0 or not proc.stdout:
        print(f"  [warn] fetch failed: {url} (curl exit {proc.returncode})", file=sys.stderr)
        return None
    return proc.stdout


def http_get_json(url: str, timeout: int = TIMEOUT):
    raw = http_get(url, accept="application/json", timeout=timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def strip_html(text: str | None, limit: int = 600) -> str:
    if not text:
        return ""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_any_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    fmts = (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d",
    )
    for fmt in fmts:
        try:
            parsed = dt.datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# 社区与代码源
# --------------------------------------------------------------------------

def fetch_hn(day: dt.date, tz_offset: int = 8) -> list[dict]:
    tz = dt.timezone(dt.timedelta(hours=tz_offset))
    start = dt.datetime.combine(day, dt.time.min, tz)
    end = start + dt.timedelta(days=1)
    # 注意：numericFilters 里的 > < 必须做 URL 编码，否则部分代理会直接返回 400
    params = {
        "tags": "story",
        "numericFilters": (
            f"created_at_i>{int(start.timestamp())},"
            f"created_at_i<{int(end.timestamp())},points>20"
        ),
        "hitsPerPage": "1000",
    }
    url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(params)
    data = http_get_json(url)
    if not data:
        return []
    out = []
    for hit in data.get("hits", []):
        out.append(
            {
                "title": hit.get("title"),
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "points": hit.get("points", 0),
                "comments": hit.get("num_comments", 0),
                "created_at": hit.get("created_at"),
                "author": hit.get("author"),
                "text": strip_html(hit.get("story_text"), 500),
            }
        )
    out.sort(key=lambda x: (x["points"], x["comments"]), reverse=True)
    return out


GITHUB_QUERIES: list[tuple[str, str]] = [
    ("topic:ai-agent created:>{cut} sort:stars", "agent"),
    ("topic:llm-agent created:>{cut} sort:stars", "agent"),
    ("topic:agent-framework created:>{cut} sort:stars", "agent"),
    ("topic:mcp created:>{cut} sort:stars", "agent"),
    ("topic:context-engineering created:>{cut} sort:stars", "agent"),
    ("topic:multi-agent created:>{cut} sort:stars", "agent"),
    ("topic:robotics created:>{cut} sort:stars", "robotics"),
    ("topic:humanoid-robot created:>{cut} sort:stars", "robotics"),
    ("topic:vla created:>{cut} sort:stars", "robotics"),
    ("topic:embodied-ai created:>{cut} sort:stars", "robotics"),
    ("topic:world-model created:>{cut} sort:stars", "robotics"),
    ("topic:diffusion-policy created:>{cut} sort:stars", "robotics"),
]


def fetch_github(day: dt.date, lookback_days: int = 45) -> list[dict]:
    cut = (day - dt.timedelta(days=lookback_days)).isoformat()
    seen: dict[str, dict] = {}
    for query, group in GITHUB_QUERIES:
        q = query.format(cut=cut)
        url = (
            "https://api.github.com/search/repositories?q="
            + urllib.parse.quote(q)
            + "&sort=stars&order=desc&per_page=20"
        )
        data = http_get_json(url)
        if not data:
            time.sleep(6)
            continue
        for repo in data.get("items", []):
            full = repo["full_name"]
            entry = seen.setdefault(
                full,
                {
                    "full_name": full,
                    "html_url": repo["html_url"],
                    "description": repo.get("description"),
                    "stars": repo.get("stargazers_count", 0),
                    "forks": repo.get("forks_count", 0),
                    "language": repo.get("language"),
                    "created_at": repo.get("created_at"),
                    "pushed_at": repo.get("pushed_at"),
                    "topics": repo.get("topics", []),
                    "groups": set(),
                },
            )
            entry["groups"].add(group)
            entry["stars"] = max(entry["stars"], repo.get("stargazers_count", 0))
        time.sleep(6)
    repos = list(seen.values())
    for repo in repos:
        repo["groups"] = sorted(repo["groups"])
        repo["age_days"] = (
            dt.date.today() - dt.date.fromisoformat(repo["created_at"][:10])
        ).days
        repo["stars_per_day"] = round(repo["stars"] / max(repo["age_days"], 1), 1)
    repos.sort(key=lambda r: r["stars"], reverse=True)
    return repos


ARXIV_CATEGORIES = ["cs.AI", "cs.RO", "cs.CL", "cs.LG"]


def fetch_arxiv(day: dt.date, max_results: int = 60) -> list[dict]:
    window_start = day - dt.timedelta(days=3)
    query = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    url = (
        "https://export.arxiv.org/api/query?search_query="
        + urllib.parse.quote(query)
        + f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )
    raw = http_get(url)
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        published = entry.findtext("a:published", default="", namespaces=ns)
        pub_date = parse_any_date(published)
        if pub_date and pub_date.date() < window_start:
            continue
        out.append(
            {
                "title": strip_html(entry.findtext("a:title", default="", namespaces=ns), 300),
                "url": entry.findtext("a:id", default="", namespaces=ns),
                "summary": strip_html(entry.findtext("a:summary", default="", namespaces=ns), 700),
                "published": published,
                "categories": [c.get("term") for c in entry.findall("a:category", ns)],
            }
        )
    return out


# --------------------------------------------------------------------------
# 官方博客 / 媒体
# --------------------------------------------------------------------------

FEEDS: dict[str, str] = {
    "openai": "https://openai.com/news/rss.xml",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "google_ai": "https://blog.google/technology/ai/rss/",
    "simonwillison": "https://simonwillison.net/atom/everything/",
    "techcrunch": "https://techcrunch.com/feed/",
    "theverge": "https://www.theverge.com/rss/index.xml",
    "arstechnica_ai": "https://arstechnica.com/ai/feed/",
    "wired": "https://www.wired.com/feed/rss",
}


def fetch_feeds(day: dt.date, window_days: int = 4) -> dict[str, list[dict]]:
    window_start = day - dt.timedelta(days=window_days)
    result: dict[str, list[dict]] = {}
    for name, url in FEEDS.items():
        raw = http_get(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
        if raw is None:
            result[name] = []
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            result[name] = []
            continue
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//a:entry", ns)
        entries = []
        for item in items:
            title = item.findtext("title", default="", namespaces=ns)
            if title.startswith("<![CDATA[") and title.endswith("]]>"):
                title = title[9:-3]
            link = item.findtext("link", default="", namespaces=ns)
            if not link:
                link_el = item.find("a:link", ns)
                link = link_el.get("href") if link_el is not None else ""
            date_raw = (
                item.findtext("pubDate")
                or item.findtext("a:updated", namespaces=ns)
                or item.findtext("a:published", namespaces=ns)
                or item.findtext("published")
                or item.findtext("updated")
            )
            published = parse_any_date(date_raw)
            if published and published.date() < window_start:
                continue
            summary = (
                item.findtext("description")
                or item.findtext("a:summary", namespaces=ns)
                or item.findtext("a:content", namespaces=ns)
                or item.findtext("content")
                or ""
            )
            entries.append(
                {
                    "title": strip_html(title, 300) or strip_html(summary, 90),
                    "url": link,
                    "published": date_raw,
                    "date": published.date().isoformat() if published else "",
                    "summary": strip_html(summary, 700),
                }
            )
        result[name] = entries[:25]
        time.sleep(0.3)
    return result


def _scrape_links(url: str, pattern: str, base: str, limit: int = 25) -> list[dict]:
    raw = http_get(url)
    if raw is None:
        return []
    page = raw.decode("utf-8", "replace")
    out, seen = [], set()
    for match in re.finditer(pattern, page, re.S):
        slug, inner = match.group(1), match.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        text = strip_html(inner, 400)
        if len(text) < 8:
            continue
        out.append({"title": text, "url": base + slug, "published": "", "date": "", "summary": ""})
    return out[:limit]


def fetch_anthropic_news(day: dt.date) -> list[dict]:
    return _scrape_links("https://www.anthropic.com/news", r'href="(/news/[^"#?]+)"[^>]*>(.*?)</a>',
                         "https://www.anthropic.com")


def fetch_qwen_blog(day: dt.date) -> list[dict]:
    return _scrape_links(
        "https://qwen.ai/blog",
        r'href="(/blog/[^"#?]+)"[^>]*>(.*?)</a>',
        "https://qwen.ai",
    )


# --------------------------------------------------------------------------
# X（推特）侧信号
# --------------------------------------------------------------------------

def fetch_x_trends(day: dt.date, tz_offset: int = 8) -> dict:
    """X 当日热榜快照（trends24 公开页面），按关键词筛出技术相关趋势。"""
    tz = dt.timezone(dt.timedelta(hours=tz_offset))
    day_start = dt.datetime.combine(day, dt.time.min, tz)
    day_end = day_start + dt.timedelta(days=1)
    result: dict = {"regions": {}, "tech_trends": [], "snapshots": 0, "note": ""}
    for label, url in (("worldwide", "https://trends24.in/"), ("united-states", "https://trends24.in/united-states/")):
        raw = http_get(url)
        if raw is None:
            continue
        page = raw.decode("utf-8", "replace")
        blocks = re.findall(
            r"<div class=list-container><h3 class=title data-timestamp=([0-9.]+)>(.*?)</h3>"
            r"<ol class=trend-card__list>(.*?)</ol>",
            page,
            re.S,
        )
        region = {"snapshots": 0, "hours": set(), "trends": {}}
        for ts, _, body in blocks:
            moment = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).astimezone(tz)
            if not (day_start <= moment < day_end):
                continue
            region["snapshots"] += 1
            region["hours"].add(moment.strftime("%H:%M"))
            for name in re.findall(r"class=trend-link>(.*?)</a>", body):
                name = html_mod.unescape(name).strip()
                region["trends"][name] = region["trends"].get(name, 0) + 1
        result["regions"][label] = {
            "snapshots": region["snapshots"],
            "hours": sorted(region["hours"]),
            "trends": region["trends"],
        }
        result["snapshots"] += region["snapshots"]
        for name, count in region["trends"].items():
            if AI_KEYWORDS.search(name):
                result["tech_trends"].append({"region": label, "name": name, "snapshots": count})
    merged: dict[str, int] = {}
    for item in result["tech_trends"]:
        merged[item["name"]] = merged.get(item["name"], 0) + item["snapshots"]
    result["tech_trends"] = sorted(
        ({"name": k, "snapshots": v} for k, v in merged.items()), key=lambda x: -x["snapshots"]
    )
    if not result["snapshots"]:
        result["note"] = "trends24 未返回目标日期的快照（通常只保留最近 24 小时）"
    return result


def verify_tweet_ids(ids: list[str], limit: int = 40) -> list[dict]:
    """用 X 官方嵌入接口读取推文原文与互动量（无需登录、不需要 API 额度）。"""
    out = []
    for tid in ids[:limit]:
        data = http_get_json(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=a")
        if not data or "text" not in data:
            continue
        user = data.get("user") or {}
        out.append(
            {
                "id": data.get("id_str", tid),
                "url": f"https://x.com/{user.get('screen_name', 'i')}/status/{data.get('id_str', tid)}",
                "author": user.get("screen_name"),
                "author_name": user.get("name"),
                "text": data.get("text", ""),
                "likes": data.get("favorite_count", 0),
                "created_at": data.get("created_at", ""),
            }
        )
    return out


def fetch_tweets_from_html_blobs(blobs: list[str], limit: int = 25) -> list[dict]:
    """从已抓取的页面里发现推文链接，再读取原文与互动量。"""
    ids: list[str] = []
    for blob in blobs:
        for tid in re.findall(r"(?:twitter\.com|x\.com)/[A-Za-z0-9_]+/status/(\d{15,25})", blob):
            if tid not in ids:
                ids.append(tid)
    return verify_tweet_ids(ids, limit)


def annotate_tweet_dates(posts: list[dict], day: dt.date, window_days: int = 3) -> tuple[list[dict], list[dict]]:
    """给推文标注日期，并分出「目标日附近」与「更早的高互动帖」。"""
    start = day - dt.timedelta(days=window_days)
    end = day + dt.timedelta(days=1)
    recent: list[dict] = []
    for post in posts:
        parsed = parse_any_date(post.get("created_at"))
        if not parsed:
            post["date"] = ""
            post["in_window"] = False
            continue
        post["date"] = parsed.date().isoformat()
        post["in_window"] = start <= parsed.date() < end
        if post["in_window"]:
            recent.append(post)
    recent.sort(key=lambda p: (p["likes"], p.get("retweets", 0)), reverse=True)
    return recent, [p for p in posts if not p["in_window"]]


# 用搜索引擎发现 X 帖子（不需要 API 额度）：Brave 对 x.com 的收录相对可用
X_SEARCH_QUERIES: list[str] = [
    'site:x.com ("context engineering" OR "agent harness" OR "agent orchestration" OR "multi-agent")',
    'site:x.com ("world model" OR "world action model" OR VLA OR humanoid OR "embodied AI")',
    'site:x.com ("coding agent" OR "agent memory" OR "agent eval" OR "AI productivity")',
    'site:x.com (OpenAI OR Anthropic OR DeepMind OR Qwen OR DeepSeek) (agent OR model OR release)',
    'site:x.com (智能体 OR 世界模型 OR 具身智能 OR 大模型 OR 上下文工程)',
]


def discover_tweet_ids_via_search(queries: list[str] | None = None, per_query: int = 1) -> list[str]:
    """通过搜索引擎找 x.com/status 链接，拿到推文 ID 列表。"""
    engines = (
        "https://search.brave.com/search?q={q}",
        "https://www.bing.com/search?q={q}&count=30&setlang=en",
    )
    ids: list[str] = []
    for query in queries or X_SEARCH_QUERIES:
        for template in engines:
            page = http_get(template.format(q=urllib.parse.quote(query)), timeout=25)
            if not page:
                continue
            text = page.decode("utf-8", "replace")
            for tid in re.findall(r"(?:x|twitter)\.com/[A-Za-z0-9_]+/status/(\d{15,25})", text):
                if tid not in ids:
                    ids.append(tid)
            time.sleep(0.3)
    return ids


def discover_tweet_blobs(feeds: dict[str, list[dict]], extra_urls: list[str] | None = None,
                         per_feed: int = 6, limit_pages: int = 30) -> list[str]:
    """抓取当天各来源的文章页面，供扫描内嵌推文用（新闻稿常内嵌当事人原推）。"""
    urls: list[str] = list(extra_urls or [])
    for entries in feeds.values():
        for entry in entries[:per_feed]:
            url = entry.get("url")
            if url and url not in urls:
                urls.append(url)
    blobs: list[str] = []
    for url in urls[:limit_pages]:
        raw = http_get(url, timeout=20)
        if raw:
            blobs.append(raw.decode("utf-8", "replace"))
        time.sleep(0.2)
    return blobs


# --------------------------------------------------------------------------
# 交叉核对：Techmeme 当日归档
# --------------------------------------------------------------------------

TECHMEME_ITEM = re.compile(
    r'<DIV CLASS="ii">.*?<STRONG CLASS="L1"><A CLASS="ourh" HREF="(.*?)">(.*?)</A></STRONG>(.*?)</DIV>',
    re.S | re.I,
)


def fetch_techmeme(day: dt.date, hours: tuple[int, ...] = (2, 8, 12, 16, 20, 23)) -> list[dict]:
    stamp = day.strftime("%y%m%d")
    out, seen = [], set()
    for hour in hours:
        page = http_get(f"https://www.techmeme.com/{stamp}/h{hour:02d}00")
        if page is None:
            continue
        text = page.decode("utf-8", "replace")
        for url, headline, rest in TECHMEME_ITEM.findall(text):
            title = strip_html(headline, 300)
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(
                {
                    "title": title,
                    "url": html_mod.unescape(url),
                    "summary": strip_html(rest, 300),
                    "hour": f"{hour:02d}:00",
                }
            )
        time.sleep(0.4)
    return out


# --------------------------------------------------------------------------
# HuggingFace Daily Papers
# --------------------------------------------------------------------------

def fetch_huggingface(day: dt.date, window_days: int = 6, limit: int = 60) -> list[dict]:
    data = http_get_json(f"https://huggingface.co/api/daily_papers?limit={limit}")
    if not data:
        return []
    window_start = day - dt.timedelta(days=window_days)
    out = []
    for entry in data:
        published = (entry.get("publishedAt") or "")[:10]
        paper = entry.get("paper") or {}
        try:
            pub_date = dt.date.fromisoformat(published)
        except ValueError:
            continue
        if pub_date < window_start:
            continue
        out.append(
            {
                "title": paper.get("title", ""),
                "url": f"https://huggingface.co/papers/{paper.get('id', '')}",
                "published": published,
                "upvotes": paper.get("upvotes", 0),
                "summary": strip_html(paper.get("summary"), 500),
            }
        )
    out.sort(key=lambda x: -x["upvotes"])
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def default_date() -> dt.date:
    return dt.date.today() - dt.timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取每日技术趋势原始数据")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD，默认昨天")
    parser.add_argument("--out", help="输出文件路径，默认 data/trends/raw/<date>.json")
    parser.add_argument("--tz-offset", type=int, default=8, help="本地时区偏移，默认 8（北京时间）")
    parser.add_argument("--proxy", default="auto", help="auto / none / socks5h://127.0.0.1:7890 等")
    parser.add_argument("--min-likes", type=int, default=30, help="x_search 的最低点赞数阈值")
    parser.add_argument("--tweets-window-days", type=int, default=3,
                        help="tweets 源判定「目标日附近」的窗口（默认 ±3 天）")
    parser.add_argument(
        "--only",
        help="只抓取指定源（逗号分隔）：hn,github,arxiv,feeds,anthropic,qwen,techmeme,x_trends,x_search,huggingface,tweets",
    )
    args = parser.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else default_date()
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    out_path = Path(args.out) if args.out else RAW_DIR / f"{day.isoformat()}.json"

    proxy = resolve_proxy(args.proxy)
    print(f"[fetch] date={day} tz=UTC{args.tz_offset:+d} proxy={proxy or 'direct'}"
          + (f" only={sorted(only)}" if only else ""))

    payload: dict = {}
    if out_path.exists():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}

    def wanted(name: str) -> bool:
        return only is None or name in only

    html_blobs: list[str] = []

    if wanted("hn"):
        payload["hn"] = fetch_hn(day, args.tz_offset)
        print(f"[fetch] hn          {len(payload['hn'])} stories")
    if wanted("github"):
        payload["github"] = fetch_github(day)
        print(f"[fetch] github      {len(payload['github'])} repos")
    if wanted("arxiv"):
        payload["arxiv"] = fetch_arxiv(day)
        print(f"[fetch] arxiv       {len(payload['arxiv'])} papers")
    if wanted("feeds") or wanted("anthropic") or wanted("qwen"):
        feeds = payload.get("feeds") or {}
        if wanted("feeds"):
            for name, entries in fetch_feeds(day).items():
                # 重跑单个源时不要把已抓到的数据覆盖成空
                if not entries and feeds.get(name):
                    print(f"[fetch] feed:{name:<12} 保留已有 {len(feeds[name])} 条（本次抓取为空）")
                    continue
                feeds[name] = entries
                print(f"[fetch] feed:{name:<12} {len(entries)}")
        if wanted("anthropic"):
            feeds["anthropic"] = fetch_anthropic_news(day)
            print(f"[fetch] anthropic   {len(feeds['anthropic'])}")
        if wanted("qwen"):
            feeds["qwen"] = fetch_qwen_blog(day)
            print(f"[fetch] qwen        {len(feeds['qwen'])}")
        payload["feeds"] = feeds
    if wanted("techmeme"):
        payload["techmeme"] = fetch_techmeme(day)
        print(f"[fetch] techmeme    {len(payload['techmeme'])} items")
    if wanted("x_trends"):
        payload["x_trends"] = fetch_x_trends(day, args.tz_offset)
        print(
            f"[fetch] x_trends    {payload['x_trends'].get('snapshots', 0)} snapshots, "
            f"{len(payload['x_trends'].get('tech_trends', []))} tech trends"
        )
    if wanted("huggingface"):
        payload["huggingface"] = fetch_huggingface(day)
        print(f"[fetch] huggingface {len(payload['huggingface'])} papers")
    if wanted("x_search"):
        try:
            import x_api  # 同目录模块

            if x_api.load_token():
                payload["x_search"] = x_api.daily_search(
                    day, tz_offset=args.tz_offset, min_likes=args.min_likes, verbose=True
                )
                payload["x_search_status"] = x_api.LAST_ERROR or "ok"
                if payload["x_search"]:
                    print(f"[fetch] x_search    {len(payload['x_search'])} posts (like_count >= {args.min_likes})")
                else:
                    print(f"[fetch] x_search    0 posts｜{payload['x_search_status']}")
            else:
                payload["x_search"] = []
                payload["x_search_status"] = "未配置 X_BEARER_TOKEN"
                print("[fetch] x_search    跳过：未配置 X_BEARER_TOKEN（见 tools/x_api.py 顶部说明）")
        except ImportError as exc:
            payload["x_search"] = []
            payload["x_search_status"] = f"模块缺失：{exc}"
            print(f"[fetch] x_search    跳过：{exc}")

    if wanted("tweets"):
        # 1) 已知会内嵌原推的页面（Simon Willison 的每日观察）
        blobs = []
        for url in (
            "https://simonwillison.net/2026/Sep/18/thariq-shihipar/",
            "https://simonwillison.net/2026/Sep/18/gemini-hacked-three-companies/",
            "https://simonwillison.net/2026/Sep/16/mustafa-suleyman/",
            "https://simonwillison.net/2026/Sep/17/compaction-summaries/",
        ):
            blob = http_get(url)
            if blob:
                blobs.append(blob.decode("utf-8", "replace"))
        # 2) 当天各来源的文章页面（新闻稿常内嵌当事人原推）
        blobs += discover_tweet_blobs(payload.get("feeds") or {})
        # 3) 已抓到的 HTML（若上层已缓存）
        blobs += html_blobs
        ids: list[str] = []
        for blob in blobs:
            for tid in re.findall(r"(?:x|twitter)\.com/[A-Za-z0-9_]+/status/(\d{15,25})", blob):
                if tid not in ids:
                    ids.append(tid)
        page_hits = len(ids)
        # 4) 搜索引擎发现（Brave / Bing，免费且不需要 API 额度）
        for tid in discover_tweet_ids_via_search():
            if tid not in ids:
                ids.append(tid)
        verified = verify_tweet_ids(ids, limit=45)
        recent, older = annotate_tweet_dates(verified, day, args.tweets_window_days)
        payload["tweets"] = verified
        payload["tweets_recent"] = recent
        payload["tweets_evergreen"] = older
        payload["tweets_source_stats"] = {
            "pages_scanned": len(blobs),
            "ids_from_pages": page_hits,
            "ids_total": len(ids),
            "verified": len(verified),
            "in_window": len(recent),
        }
        print(f"[fetch] tweets      {len(verified)} verified X posts，其中 {len(recent)} 条在目标日 ±{args.tweets_window_days} 天"
              f"（{page_hits} 个来自 {len(blobs)} 个页面，{len(ids)} 个候选 ID）")

    payload["date"] = day.isoformat()
    payload["generated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    payload["tz_offset"] = args.tz_offset
    payload["proxy"] = proxy or "direct"
    payload.setdefault("notes", [])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch] wrote {out_path.relative_to(BASE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
