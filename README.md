# AI 资讯速递 · 发布流水线

> **English TL;DR** — A daily bilingual AI-briefing pipeline. It fetches 10+ sources (Hacker News, GitHub Search, arXiv, Techmeme, Hugging Face, X, official blogs), writes Chinese/English issues in chunks, normalises and compacts citations, renders static pages, and refuses to publish until a release gate passes. The gate distinguishes "citation came from another day" (warn, with the source date attached) from "citation can't be found on any day" (fabrication risk, block). Runs unattended at 23:00 Asia/Shanghai.

每天 23:00 自动跑完一条流水线：**多源抓取 → 中英双语撰写 → 引用归一化 → 渲染 → 过闸门 → 发布**。产物是个人网站上的「AI资讯速递」栏目：<https://www.simon-zj.top/trends/>

本仓库是这条流水线的**工具与流程规范**（内容与原始抓取快照不入库，见「已知边界」）。

## 它解决的不是"再写一个爬虫"

1. **写完不等于可以发。** 发布是一门独立的事：结构、引用完整性、防编造可核验率、跨源重复、今日速读结构、摘要深度、生成物漂移，各自有判据；不通过就不部署。
2. **引用必须可回溯。** 正文里每条引用都要能在当日抓取快照里命中。区分两种失败：引用只出现在**别的某一天**的抓取里（陈旧素材，告警并写明来源日期）与**任何一天都找不到**（编造风险，拦发布）。
3. **长文不能一次写完。** 一篇 20 条的双语稿件塞不进一次模型响应（实测会被输出上限截断），所以拆成「一次总览 + 四个分组 + 一次 GitHub」多次生成再合并，合并时统一引用编号并强制条目上限。
4. **关注点可配置，而流程不变。** `config/interests.json` 决定"我关注什么"：加分方向、排除类别、方法词白名单。改 JSON 就改每天先看到什么，不用改代码。

## 每晚的 8 步

| # | 步骤 | 命令 | 说明 |
| --- | --- | --- | --- |
| 0 | 同步站点 | `git fetch <site> && git rebase FETCH_HEAD` | 站点可能刚被重新生成，避免在过期副本上渲染 |
| 1 | 抓取原始数据 | `python3 tools/fetch_sources.py --date $DATE` | 代理自动探测，可 `--proxy none` 强制直连 |
| 2 | 撰写成品 | 由 Agent 完成 | 读 `data/raw/<date>.json`，写 `data/<date>.json` |
| 3 | 引用归一化 | `tools/make_refs.py` + `tools/normalize_refs.py` | 删未引用条目、压实编号 |
| 4 | 渲染页面 | `tools/render_site.py` | 写 `trends/`、样式与首页快讯数据 |
| 5 | 发布前闸门 | `./agent verify release --days 1` | 见下节；退出码非 0 就不要部署 |
| 6 | 构建并部署 | `npm run deploy`（Hexo） | 内部会再跑一次同一道门 |
| 7 | 入库 | `git add -A && git commit && git push` | 推进私有内容仓库；失败不影响线上 |

规范只有一份：[`tools/RUNBOOK.md`](tools/RUNBOOK.md)。流程变更只改它，不在别处再写一份。

## 目录结构

```
tools/
  fetch_sources.py    多源抓取 → data/raw/<date>.json
  make_refs.py        按 URL 生成规范引用条目
  normalize_refs.py   删未引用条目、压实引用编号
  render_site.py      渲染文章 / 目录页 / 样式，并写首页快讯数据
  brief.py            「今日速读」：按关注方向选 5 条
  x_api.py            X 侧读取（热榜快照 + 抽样原帖，付费 API 可选）
  RUNBOOK.md          唯一规范：步骤、写作要求、闸门、汇报格式
config/
  interests.json      「我关注什么」的可执行版本
data/                 <date>.json 每期成品；raw/<date>.json 当日抓取快照（不入库）
drafts/               部分期数的可复现生成脚本，便于复盘与重放
```

## 数据契约

**抓取快照** `data/raw/<date>.json`

| 字段 | 内容 |
| --- | --- |
| `hn` | 标题 / URL / 分数 / 评论数 / 作者 / 时间 |
| `github` | 仓库全名 / 星数 / forks / 语言 / topics / 创建与推送时间 / 每日涨星 |
| `arxiv` | 标题 / 摘要 / 发布日期 / 分类 |
| `feeds` | 按来源分组（OpenAI、DeepMind、Google AI、媒体等）：标题 / URL / 摘要 |
| `techmeme` | 标题与链接 |
| `huggingface` | 每日论文与模型 |
| `tweets` / `x_trends` / `x_search` | X 侧三层：热榜快照、抽样原帖（含 in-window 标记）、关键词检索 |
| `date` / `generated_at` / `tz_offset` / `proxy` / `notes` | 运行环境与可复现信息 |

**成品** `data/<date>.json`

| 字段 | 内容 |
| --- | --- |
| `title` / `summary` / `tldr` | 均为 `{zh, en}` 双语 |
| `sections[]` | `id` + `title{zh,en}` + `intro{zh,en}` + `groups[]` |
| `groups[]` | `id` + `title{zh,en}` + `items[]` |
| `items[]` | `title{zh,en}` + `summary{zh,en}` + `comment{zh,en}` + `sources[]`（引用编号） |
| `references[]` | `id` / `title` / `url` / `source` / `date` / `extra` |
| `coverage` / `notes` / `stats` | 覆盖口径、内部留档、条目计数 |

引用编号在正文里写成 `[[1]]`，与 `references[].id` 对应；`normalize_refs.py` 负责压实编号，闸门负责校验它们真的存在。

## 发布闸门

闸门实现不在本仓库，而在配套的运行时 [`agent-harness`](https://github.com/Simon-zj1/agent-harness)（`./agent verify release`），因为同一套判定内核也用在"PR 能否自动合并"和"退款能否放行"上。本仓库只负责生成与自检。

按后果分级，而不是把一切都做成拦截：

| 判据 | 后果 |
| --- | --- |
| 结构破坏、引用图断裂、速读结构不完整、引用编号悬空、超出条目上限、同栏目硬重复 | **拦发布** |
| 引用只出现在别的某一天的抓取里（陈旧素材） | 告警 + 在报告里写明来源日期 |
| 引用在任何一天的抓取里都找不到（编造风险） | **拦发布** |
| 孤立参考文献（声明了但正文没引用）、跨栏目重复、摘要深度不足 | 记录为告警 |

把装饰性问题也做成拦截，只会训练操作者绕过闸门——这一条写在规范里。

## 怎么跑

```bash
export PIPELINE=$(pwd)
python3 tools/fetch_sources.py --date 2026-09-25          # 需要网络；代理自动探测
python3 tools/make_refs.py  --date 2026-09-25            # 生成规范引用
python3 tools/normalize_refs.py --date 2026-09-25        # 压实编号
python3 tools/render_site.py --date 2026-09-25           # 渲染到 site/
python3 tools/brief.py --date 2026-09-25                 # 今日速读 Top 5
```

第 2 步（撰写）由 Agent 完成：把 `data/raw/<date>.json` 按 `tools/RUNBOOK.md` 的写作要求写成 `data/<date>.json`。本仓库不含抓取快照，所以第一次跑需要先自己抓一天的数据。

## 已知边界

- **只发布标题、摘要与链接，不重发全文**；抓取快照默认不入库（约 350KB/天，含第三方原文片段）。合规边界与"仍需外部确认"的事项单独记录。
- X 侧优先走免费路径（热榜快照 + 抽样原帖），付费 API 可选；抽样不是全量，规范里写明了这一点。
- 渲染产物与站点输出不在本仓库（`site/` 已 gitignore），本仓库只维护工具与规范。
- 它不负责"判断内容是否有趣"以外的编辑决策——选题偏好集中在 `config/interests.json`，一眼可查。

## 相关

- 线上产物：<https://www.simon-zj.top/trends/>
- 运行时与全部验收闸门：[Simon-zj1/agent-harness](https://github.com/Simon-zj1/agent-harness)
- 项目介绍页：<https://www.simon-zj.top/tech/agent/local-agent-harness/>

MIT License.
