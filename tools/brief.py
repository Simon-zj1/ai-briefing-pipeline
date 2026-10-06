"""今日速读：从当天内容里挑出「你真正要看的 5 条」，而不是再列一遍 38 条。

这个模块解决的是阅读问题，不是发布问题：

* **精准**：按 `config/interests.json` 给每条打分，命中关注方向的加分，命中
  融资/人事/政治/社会争议的排除清单直接落选——但只影响简报，正文照旧全量保留。
* **能读懂**：每条简报固定四行——是什么、为什么推给你、正文（原样，不截断）、
  边界/限制。限制句从正文里抽取；抽不到就如实写「正文未提及限制」，这也是给撰写
  环节的信号，而不是替它圆场。
* **一天一次读得完**：默认 5 条，按栏目配额（洞见 3 / 论文 2 / 仓库 1），
  权重让论文略高于新闻。

同一份实现被两条路径共用：Hexo 部署路径由 `render_site.py` 调用它渲染页面，
harness 路径由闸门 (`daily_trends_brief`) 复用它校验产出——只有一份规则，
不存在「网站上的简报」和「被验收的简报」不一致的情况。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DEFAULT_INTERESTS = BASE / "config" / "interests.json"

#: 正文里出现这些词，说明作者写了边界/前提/代价——读的人最需要这一句。
LIMIT_MARKERS = (
    "局限",
    "限制",
    "风险",
    "前提",
    "代价",
    "尚未",
    "未验证",
    "需观察",
    "仍需",
    "不适合",
    "失败",
    "边界",
)

#: 出现这些词说明正文落到了机制/证据层面，而不是只复述标题。
SUBSTANCE_MARKERS = (
    "方法",
    "机制",
    "架构",
    "训练",
    "推理",
    "损失",
    "数据集",
    "基准",
    "评测",
    "对比",
    "实验",
    "蒸馏",
    "采样",
    "策略",
)

#: 命中这些词一次就足以判定方向（它们是方法/工件名，不是领域泛词）。
#: 只命中「agent」「模型」「推理」这类泛词时要求至少两个命中，否则「AI 支出
#: 让企业难做预算」这种商业新闻会因为正文里出现 agent 而挤进速读。
STRONG_KEYWORDS = frozenset(
    {
        "mcp", "harness", "vla", "benchmark", "上下文", "context", "memory", "记忆",
        "工具调用", "tool use", "世界模型", "world model", "sim2real", "数据集",
        "评测", "eval", "蒸馏", "强化学习", "rag", "检索", "编排", "orchestration",
        "微调", "fine-tune", "agent 框架", "多智能体", "multi-agent", "点云", "slam",
        "灵巧手", "dexterous", "simulation", "仿真", "导航", "navigation",
    }
)


def load_interests(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_INTERESTS
    if not target.is_file():
        return {"limit": 5, "sections": {}, "include": [], "exclude": []}
    return json.loads(target.read_text(encoding="utf-8"))


def _text_of(item: dict) -> str:
    """All readable text of one item, for keyword matching."""
    parts: list[str] = []
    for key in ("title", "summary", "comment", "prose", "body"):
        value = item.get(key)
        if isinstance(value, dict):
            parts += [str(value.get("zh", "")), str(value.get("en", ""))]
        elif isinstance(value, str):
            parts.append(value)
    for field in item.get("fields") or []:
        if isinstance(field, dict):
            for key in ("label", "value"):
                block = field.get(key)
                if isinstance(block, dict):
                    parts += [str(block.get("zh", "")), str(block.get("en", ""))]
    return "\n".join(parts).lower()


_LATIN_KEYWORD = re.compile(r"[a-z0-9][a-z0-9 ._+-]*")


def kw_hit(keyword: str, lowered_text: str) -> bool:
    """Boundary-aware keyword match.

    Substring matching for latin keywords produced real false positives in the
    first run: `rag` matched inside `storage`, `rl` inside `url`. CJK still uses
    substring matching (no word boundaries there).
    """
    kw = (keyword or "").strip().lower()
    if not kw or not lowered_text:
        return False
    if _LATIN_KEYWORD.fullmatch(kw):
        pattern = rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])"
        return re.search(pattern, lowered_text) is not None
    return kw in lowered_text


def exclude_reason(
    title_text: str, interests: dict[str, Any]
) -> tuple[str, str] | None:
    """Return (id, label_zh) when the *headline* matches an exclusion pattern.

    Two deliberate narrowings, both learned from the first run of this module:

    * only the headline is matched. Framing lives in the title ("估值达 10 亿美元"),
      while the body of a funding story often still contains technical words;
      matching the body turned noise into false negatives for the exclude list
      and false positives for the include list at the same time.
    * a method/artifact noun (`override_keywords`) wins over the exclusion, so
      "MCP 争议" stays because it is an Agent-engineering item, while
      "RobCo 估值…机器人独角兽" still goes, because "机器人" is a domain noun,
      not evidence of technical content.
    """
    lowered = (title_text or "").lower()
    if any(kw_hit(kw, lowered) for kw in interests.get("override_keywords", [])):
        return None
    for rule in interests.get("exclude", []):
        for pattern in rule.get("patterns", []):
            if kw_hit(pattern, lowered):
                return rule.get("id", "excluded"), rule.get("label_zh", rule.get("id", "excluded"))
    return None


def match_topics(text: str, interests: dict[str, Any]) -> list[dict[str, Any]]:
    lowered = text.lower()
    hits: list[dict[str, Any]] = []
    for topic in interests.get("include", []):
        matched = [kw for kw in topic.get("keywords", []) if kw_hit(kw, lowered)]
        strong = [kw for kw in matched if kw_hit(kw, lowered) and kw.lower() in STRONG_KEYWORDS]
        if matched and (strong or len(matched) >= 2):
            hits.append(
                {
                    "id": topic["id"],
                    "label_zh": topic.get("label_zh", topic["id"]),
                    "label_en": topic.get("label_en", topic["id"]),
                    "weight": float(topic.get("weight", 1.0)),
                    "matched": (strong or matched)[:4],
                    "strong": bool(strong),
                }
            )
    return hits


def score_item(
    item: dict[str, Any], section: str, interests: dict[str, Any]
) -> dict[str, Any]:
    text = _text_of(item)
    title = bi_title(item)
    reason = exclude_reason(f"{title['zh']} {title['en']}", interests)
    topics = match_topics(text, interests)
    topics.sort(key=lambda topic: (-topic["weight"], -len(topic["matched"])))
    section_weight = float((interests.get("sections", {}).get(section) or {}).get("weight", 1.0))
    topic_weight = max((topic["weight"] for topic in topics), default=0.0)
    score = topic_weight * section_weight
    return {
        "score": round(score, 3),
        "topics": topics,
        "excluded_by": reason[0] if reason else None,
        "excluded_label": reason[1] if reason else None,
    }


def iter_items(content: dict[str, Any]) -> list[tuple[str, str, dict]]:
    """(section_id, location, item) in document order."""
    out: list[tuple[str, str, dict]] = []
    for section in content.get("sections", []):
        section_id = str(section.get("id", ""))
        index = 0
        for group in section.get("groups") or []:
            for item in group.get("items") or []:
                index += 1
                out.append((section_id, f"{section_id}/{group.get('id')}[{index}]", item))
        for item in section.get("items") or []:
            index += 1
            out.append((section_id, f"{section_id}[{index}]", item))
    return out


def bi_text(item: dict[str, Any]) -> dict[str, str]:
    """Bilingual body text of one item, whatever shape it uses."""
    for key in ("prose", "body", "summary"):
        block = item.get(key)
        if isinstance(block, dict) and (block.get("zh") or block.get("en")):
            zh = str(block.get("zh", ""))
            en = str(block.get("en", ""))
            comment = item.get("comment")
            if key == "summary" and isinstance(comment, dict):
                zh = (zh + " " + str(comment.get("zh", ""))).strip()
                en = (en + " " + str(comment.get("en", ""))).strip()
            return {"zh": zh, "en": en}
    fields = item.get("fields") or []
    zh_parts: list[str] = []
    en_parts: list[str] = []
    for field in fields:
        value = field.get("value") if isinstance(field, dict) else None
        if isinstance(value, dict):
            zh_parts.append(str(value.get("zh", "")))
            en_parts.append(str(value.get("en", "")))
    return {"zh": " ".join(p for p in zh_parts if p), "en": " ".join(p for p in en_parts if p)}


def bi_title(item: dict[str, Any]) -> dict[str, str]:
    title = item.get("title")
    if isinstance(title, dict):
        return {"zh": str(title.get("zh", "")), "en": str(title.get("en", ""))}
    if isinstance(title, str):
        return {"zh": title, "en": title}
    return {"zh": "", "en": ""}


def extract_limit(text: str) -> str:
    """The sentence that states a limitation, if the author wrote one.

    Two splits, because the first version only handled CJK punctuation and so
    silently failed for every English paragraph: Chinese sentences end with
    。！？；, Latin ones with `. ` following a word character.
    """
    text = text or ""
    if any(marker in text for marker in LIMIT_MARKERS):
        sentences = re.split(r"(?<=[。！？；])", text)
        for index, chunk in enumerate(sentences):
            sentence = chunk.strip()
            # 段首句是主题句（「它要解决的是……成本与合规问题」），不是边界。
            # 只在正文有多句时跳过它，单句正文仍照常检查。
            if index == 0 and len(sentences) > 1:
                continue
            if sentence and any(marker in sentence for marker in LIMIT_MARKERS):
                return sentence
    lowered = text.lower()
    if any(marker in lowered for marker in _LATIN_LIMIT_MARKERS):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for index, chunk in enumerate(sentences):
            sentence = chunk.strip()
            if index == 0 and len(sentences) > 1:
                continue
            hits = [m for m in _LATIN_LIMIT_MARKERS if m in sentence.lower()]
            if not sentence or not hits:
                continue
            # English paragraphs often pack the takeaway and the limitation into
            # one long sentence; show the clause that carries the limitation.
            if len(sentence) > 200:
                parts = re.split(r"(?<=[;,]) ", sentence)
                for part in parts:
                    if any(marker in part.lower() for marker in hits):
                        return part.strip()
            return sentence
    return ""


_LATIN_LIMIT_MARKERS = (
    "limit",
    "limitation",
    "caveat",
    "risk",
    "prerequisite",
    "assumption",
    "remains",
    "unverified",
    "still needs",
    "trade-off",
    "tradeoff",
    "fails",
    "failure",
)


def depth_signals(text: str) -> dict[str, bool]:
    """Cheap, explainable proxies for「读得出技术要点」."""
    return {
        "has_number": bool(re.search(r"\d", text or "")),
        "has_substance": any(marker in (text or "") for marker in SUBSTANCE_MARKERS),
        "has_limit": bool(extract_limit(text)),
    }


_EMPHASIS = re.compile(r"\*\*(.+?)\*\*", re.S)
_CITATION = re.compile(r"\[\[[0-9,\s]+\]\]")


def digest_line(text: str, *, limit: int = 76, min_emphasis: int = 16) -> str:
    """One line for the homepage card.

    The writer already marks "the sentence worth seeing" with `**...**`（RUNBOOK 的版式
    要求），so the card reuses that choice instead of re-deciding what matters.
    Falls back to the first sentence when the emphasis is just a product name
    ("**Web Search API**" is a fine bold in an article, a useless card title),
    then to a hard cut.
    """
    text = _CITATION.sub("", text or "").replace("\n", " ").strip()
    if not text:
        return ""
    emphasised = _EMPHASIS.search(text)
    chosen = emphasised.group(1).strip() if emphasised else ""
    if len(chosen) < min_emphasis:
        chosen = ""
    if not chosen:
        for chunk in re.split(r"(?<=[。！？；.!?;])\s*", text):
            if chunk.strip():
                chosen = chunk.strip()
                break
    chosen = _EMPHASIS.sub(r"\1", chosen).strip()
    chosen = re.sub(r"\s{2,}", " ", chosen)
    if len(chosen) > limit:
        chosen = chosen[: limit - 1].rstrip() + "…"
    return chosen


def select_brief(
    content: dict[str, Any],
    *,
    interests: dict[str, Any] | None = None,
    limit: int | None = None,
    refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pick the brief. Deterministic: same content + same config = same brief."""
    interests = interests or load_interests()
    limit = int(limit or interests.get("limit", 5))
    ref_by_id = {ref.get("id"): ref for ref in (refs or content.get("references") or [])}

    scored: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for section, location, item in iter_items(content):
        verdict = score_item(item, section, interests)
        title = bi_title(item)
        if verdict["excluded_by"]:
            excluded.append(
                {
                    "location": location,
                    "title": title,
                    "reason": verdict["excluded_label"],
                }
            )
            continue
        body = bi_text(item)
        scored.append(
            {
                "location": location,
                "section": section,
                "title": title,
                "body": body,
                "sources": [s for s in (item.get("sources") or []) if isinstance(s, int)],
                "score": verdict["score"],
                "topics": verdict["topics"],
                "limit_zh": extract_limit(body["zh"]),
                "limit_en": extract_limit(body["en"]),
                "depth": depth_signals(body["zh"]),
            }
        )

    scored.sort(key=lambda entry: (-entry["score"], entry["location"]))
    quotas = {
        section: int((interests.get("sections", {}).get(section) or {}).get("max", limit))
        for section in {entry["section"] for entry in scored}
    }
    picked: list[dict[str, Any]] = []
    used: dict[str, int] = {}
    for entry in scored:
        if len(picked) >= limit:
            break
        cap = quotas.get(entry["section"], limit)
        if used.get(entry["section"], 0) >= cap:
            continue
        used[entry["section"]] = used.get(entry["section"], 0) + 1
        picked.append(entry)
    # 配额没填满时，用剩下的高分条目补足，避免「只推 3 条」这种空白。
    if len(picked) < limit:
        for entry in scored:
            if len(picked) >= limit:
                break
            if entry not in picked:
                picked.append(entry)

    for index, entry in enumerate(picked, start=1):
        entry["rank"] = index
        # One line a human can check: the strongest direction plus the words that
        # matched it. Listing all matched topics produced "Agent 工程、机器人、提效、
        # 模型动向" on almost every item, which tells the reader nothing.
        best = entry["topics"][0] if entry["topics"] else None
        entry["why_zh"] = (
            f"{best['label_zh']}（命中：{'、'.join(best['matched'])}）" if best else ""
        )
        entry["why_en"] = (
            f"{best['label_en']} (matched: {', '.join(best['matched'])})" if best else ""
        )
        # NB: must read from the entry, not from the `body` loop variable — that
        # leak gave every homepage card the same line in the first version.
        entry["line_zh"] = digest_line(entry["body"]["zh"], limit=76)
        entry["line_en"] = digest_line(entry["body"]["en"], limit=118)
        entry["urls"] = [
            {
                "url": str(ref_by_id[s].get("url", "")),
                "label": str(ref_by_id[s].get("source") or ref_by_id[s].get("title") or "source"),
            }
            for s in entry["sources"]
            if s in ref_by_id
        ]

    return {
        "date": content.get("date"),
        "limit": limit,
        "entries": picked,
        "candidates": len(scored) + len(excluded),
        "excluded": len(excluded),
        "excluded_examples": excluded[:8],
        "without_limit": sum(1 for entry in picked if not entry["limit_zh"] and not entry["limit_en"]),
    }


def to_markdown(brief: dict[str, Any]) -> str:
    lines = [
        f"# 今日速读 · {brief.get('date')}",
        "",
        f"候选 {brief.get('candidates')} 条，按关注方向过滤掉 {brief.get('excluded')} 条，"
        f"选出 {len(brief.get('entries', []))} 条。",
        "",
    ]
    for entry in brief.get("entries", []):
        topics = entry.get("why_zh") or "未命中显式方向"
        lines += [
            f"## {entry['rank']:02d}. {entry['title']['zh']}",
            "",
            f"- 为什么推给你：{topics}",
            f"- 正文：{entry['body']['zh']}",
            f"- 边界：{entry['limit_zh'] or '正文未提及限制（建议补）'}",
            f"- 来源：{', '.join(u['url'] for u in entry.get('urls', [])) or '—'}",
            "",
        ]
    if brief.get("excluded_examples"):
        lines += ["## 已按关注方向过滤", ""]
        lines += [
            f"- [{item['reason']}] {item['title']['zh']}"
            for item in brief["excluded_examples"]
        ]
        lines.append("")
    return "\n".join(lines)
