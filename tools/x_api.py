#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X（Twitter）API v2 客户端：按关键词 + 时间范围搜索推文。

凭证读取顺序（只认 token，不落盘、不写日志）：
    1. 环境变量 X_BEARER_TOKEN
    2. ~/.codex/x-api.env
    3. <daily-trends>/.env
  文件格式：X_BEARER_TOKEN=xxxxx        （支持用引号包起来，支持行内 # 注释）

用法:
    python3 tools/x_api.py --check                          # 检查凭证是否可用
    python3 tools/x_api.py --date 2026-09-20                # 用默认关键词组抓当天讨论度最高的推文
    python3 tools/x_api.py --date 2026-09-20 --query 'harness' --min-likes 20

注意：`/2/tweets/search/recent` 需要 App 具备搜索权限（X 的付费档位；免费档只能发帖）。
      没有权限时会返回 403，本脚本会把原始错误信息打印出来，方便判断。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
TOKEN_FILES = [Path.home() / ".codex" / "x-api.env", BASE / ".env"]
PROXY_CANDIDATES = (
    "socks5h://127.0.0.1:7897",
    "http://127.0.0.1:7897",
    "socks5h://127.0.0.1:7890",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:1087",
    "socks5h://127.0.0.1:1086",
)
API = "https://api.x.com/2/tweets/search/recent"

# 最近一次失败的原始状态，供上层（fetch_sources / 定时任务）写进说明
LAST_ERROR: str | None = None

# 默认关键词组：Agent 工程 / 机器人具身智能 / AI 提效 / 模型公司动向（中英各一组）
DEFAULT_QUERIES: list[str] = [
    '(context engineering OR "agent harness" OR "agent orchestration" OR "multi-agent") -is:retweet lang:en',
    '("world model" OR "world action model" OR VLA OR humanoid OR "embodied AI") -is:retweet lang:en',
    '( "coding agent" OR "AI productivity" OR "agent memory" OR "agent eval" ) -is:retweet lang:en',
    '(OpenAI OR Anthropic OR DeepMind OR Qwen OR DeepSeek OR "Google DeepMind") -is:retweet lang:en',
    '(智能体 OR 世界模型 OR 具身智能 OR 机器人 OR 大模型 OR 上下文工程) -is:retweet lang:zh',
]


# --------------------------------------------------------------------------
# 凭证
# --------------------------------------------------------------------------

def describe_status(status: int, body) -> str:
    """把 HTTP 状态翻译成一句人话，方便在文章的「数据源说明」里写清楚。"""
    detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    detail = detail[:200]
    if status == 200:
        return "ok"
    if status == 401:
        return "凭证无效（401）：检查 X_BEARER_TOKEN 是否复制完整"
    if status == 402:
        return "额度不足（402 credits depleted）：token 有效，但需要去 X 开发者后台充值/订阅读取额度"
    if status == 403:
        return "无权限（403）：当前档位不支持该接口（Free 档只能发帖）"
    if status == 429:
        return "超出速率限制（429）：稍后重试或降低请求频率"
    return f"HTTP {status}：{detail}"

def load_token() -> str | None:
    token = os.environ.get("X_BEARER_TOKEN")
    if token:
        return token.strip().strip('"').strip("'")
    for path in TOKEN_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in {"X_BEARER_TOKEN", "BEARER_TOKEN", "TWITTER_BEARER_TOKEN"}:
                return value.split("#")[0].strip().strip('"').strip("'")
    return None


def token_source() -> str:
    if os.environ.get("X_BEARER_TOKEN"):
        return "环境变量 X_BEARER_TOKEN"
    for path in TOKEN_FILES:
        if path.exists() and "X_BEARER_TOKEN" in path.read_text(encoding="utf-8", errors="ignore"):
            return str(path)
    return "（未找到）"


# --------------------------------------------------------------------------
# HTTP（复用本机代理，curl 同时支持 socks5h）
# --------------------------------------------------------------------------

def _curl(url: str, headers: list[str], timeout: int = 30) -> tuple[int, str]:
    if shutil.which("curl") is None:
        return 0, "系统缺少 curl"
    cmd = ["curl", "-sS", "-L", "--max-time", str(timeout), "-w", "\n%{http_code}"]
    for header in headers:
        cmd += ["-H", header]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY")
    if not proxy:
        for candidate in PROXY_CANDIDATES:
            probe = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-m", "5", "-x", candidate, "-w", "%{http_code}",
                 "https://api.x.com/"],
                capture_output=True,
            ).stdout.decode().strip()
            if probe in {"200", "401", "403", "404"}:
                proxy = candidate
                break
    if proxy:
        cmd += ["-x", proxy]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    body = proc.stdout.decode("utf-8", "replace")
    status, _, payload = body.rpartition("\n")
    try:
        return int(payload.strip()), status
    except ValueError:
        return 0, body


def search_recent(
    query: str,
    start: dt.datetime,
    end: dt.datetime,
    max_results: int = 100,
    token: str | None = None,
) -> tuple[int, dict | list | str]:
    token = token or load_token()
    if not token:
        return 0, "no-token"
    params = {
        "query": query,
        "start_time": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_results": str(max(10, min(max_results, 100))),
        "tweet.fields": "created_at,public_metrics,lang,author_id",
        "expansions": "author_id",
        "user.fields": "username,name,verified",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    status, body = _curl(url, [f"Authorization: Bearer {token}"])
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def daily_search(
    day: dt.date,
    queries: list[str] | None = None,
    tz_offset: int = 8,
    min_likes: int = 30,
    per_query: int = 100,
    verbose: bool = False,
) -> list[dict]:
    """抓取目标日（本地时区）内、互动量较高的推文。"""
    global LAST_ERROR
    LAST_ERROR = None
    tz = dt.timezone(dt.timedelta(hours=tz_offset))
    start = dt.datetime.combine(day, dt.time.min, tz)
    end = start + dt.timedelta(days=1)
    token = load_token()
    if not token:
        return []

    posts: dict[str, dict] = {}
    for query in queries or DEFAULT_QUERIES:
        status, data = search_recent(query, start, end, per_query, token)
        if status != 200 or not isinstance(data, dict):
            detail = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]
            LAST_ERROR = describe_status(status, data)
            if verbose:
                print(f"  [warn] X 搜索失败 status={status} query={query[:50]}… {detail}", file=sys.stderr)
            continue
        users = {
            u["id"]: u for u in (data.get("includes", {}) or {}).get("users", [])
        }
        for tweet in data.get("data", []) or []:
            metrics = tweet.get("public_metrics", {}) or {}
            likes = metrics.get("like_count", 0)
            if likes < min_likes:
                continue
            author = users.get(tweet.get("author_id"), {})
            posts[tweet["id"]] = {
                "id": tweet["id"],
                "url": f"https://x.com/{author.get('username', 'i')}/status/{tweet['id']}",
                "text": tweet.get("text", ""),
                "created_at": tweet.get("created_at", ""),
                "lang": tweet.get("lang", ""),
                "author": author.get("username"),
                "author_name": author.get("name"),
                "likes": likes,
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "quotes": metrics.get("quote_count", 0),
                "query": query,
            }
    return sorted(posts.values(), key=lambda p: (p["likes"], p["retweets"]), reverse=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="X API v2 关键词搜索")
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD（默认昨天）")
    parser.add_argument("--query", action="append", help="自定义查询（可重复）")
    parser.add_argument("--min-likes", type=int, default=30)
    parser.add_argument("--tz-offset", type=int, default=8)
    parser.add_argument("--check", action="store_true", help="只检查凭证与权限")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    token = load_token()
    print(f"[x-api] token: {'已找到（' + token_source() + '）' if token else '未配置'}")
    if not token:
        print("        配置方法：把 X_BEARER_TOKEN=xxxx 写入 ~/.codex/x-api.env")
        return 2

    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today() - dt.timedelta(days=1)
    queries = args.query or DEFAULT_QUERIES[:1]
    tz = dt.timezone(dt.timedelta(hours=args.tz_offset))
    start = dt.datetime.combine(day, dt.time.min, tz)
    status, data = search_recent(queries[0], start, start + dt.timedelta(days=1), token=token)
    print(f"[x-api] 探测请求 status={status}")
    if status != 200:
        print("        " + (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))[:400])
        if status in (401, 403):
            print("        说明：401 通常是 token 不对；403 通常是 App 档位没有搜索权限（Free 档只能发帖）。")
        return 1
    if args.check:
        print("[x-api] 凭证可用，搜索权限正常")
        return 0

    posts = daily_search(day, queries if args.query else None, args.tz_offset, args.min_likes, verbose=True)
    if LAST_ERROR and not posts:
        print(f"[x-api] 抓取失败：{LAST_ERROR}")
        return 1
    print(f"[x-api] {day} 共 {len(posts)} 条（赞 ≥ {args.min_likes}）")
    if args.json:
        print(json.dumps(posts, ensure_ascii=False, indent=2))
    else:
        for post in posts[:15]:
            print(f"  {post['likes']:5d}❤ {post['retweets']:4d}↻ @{post['author']}: {post['text'][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
