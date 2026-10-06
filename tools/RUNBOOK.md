# AI资讯速递 · 发布流程（RUNBOOK）

「AI资讯速递」每天 23:00（Asia/Shanghai，由 Codex 定时任务触发；见文末「定时任务」）
汇总当天的 Agent 工程、机器人 / 具身智能、AI 提效、模型公司与人物观点、GitHub 热门仓库，
以及 arXiv 上的 Agent 相关论文，写成中英双语内容并发布到个人网站。

本文件是这条流程的**唯一规范**。定时任务的提示词只负责触发与护栏，细节以本文件为准；
改流程改这里，不要在提示词里另写一份。

## 目录结构（工具与网站输出是分开的）

```
$PIPELINE/       ← 工具与内容数据（不在网站仓库里）
  tools/fetch_sources.py     抓取原始数据 -> data/raw/<date>.json
  tools/make_refs.py         按 URL 子串生成规范引用条目
  tools/normalize_refs.py    删除未引用条目、把引用编号压实
  tools/render_site.py       渲染页面（默认写入下面的站点仓库，可用 --site 覆盖）
  tools/brief.py             今日速读：按关注方向选 5 条（页面顶部那一段）
  tools/RUNBOOK.md           本文件
  data/<date>.json           每天撰写的成品内容（页面数据源）
  data/raw/<date>.json       原始抓取数据（不上页面）
  site/                      预渲染产物（Hexo 构建时由脚本复制进 public/）
  config/interests.json      「我关注什么」：加分方向、排除类别、方法词白名单

$SITE_SRC/                                  ← Hexo 源仓库（生成 site/ 与 public/）
  scripts/daily-trends.js    生成后把 site/ 的产物复制进 public/，并同步 sitemap
  source/_data/ai_briefing.json  首页「今日AI快讯」的最新 5 条数据

$SITE_OUT/            ← 网站静态输出仓库（Hexo 生成 + GitHub Pages 发布）
  trends/index.html          期号目录页      （由 render_site.py 生成）
  trends/<date>/index.html   当日文章        （由 render_site.py 生成）
  css/trends.css             栏目样式        （由 render_site.py 生成）
  trends/                     AI资讯速递期号目录与文章
```

> 工具之所以放在网站仓库之外：站点会被重新生成（`Site updated: ...` 提交会覆盖整个工作区），
> 放在仓库里的脚本与数据会一起被清掉。生成物放仓库，工具与内容数据放在外面。

## 今日速读（页面顶部 Top 5）

`render_site.py` 会调用同目录的 `brief.py`，按 `config/interests.json` 从当天内容里
选出 5 条放在正文之前，每条四行：是什么 / 为什么推给你 / 正文 / 边界。
被过滤掉的条目仍然完整保留在页面下方——速读只做排序与筛选，不删内容。

撰写时值得配合的一件事：**每条正文里写一句「限制/边界」**（用「限制是…」「风险是…」
这样的句式）。速读会把这句抽出来单独展示；抽不到时会显示「正文未提及限制」，
harness 侧的 `daily_trends_depth` 闸门也会把这一天记为深度不足。

## 与 Hexo 源仓库的集成（重要）

网站每次重新生成都会重建整个输出仓库，所以「AI资讯速递」必须能跟着构建走：

1. 顶部主导航直接链接 `/trends/`，技术笔记目录中不再保留本栏目入口。
2. `scripts/daily-trends.js` 是 Hexo 钩子（`after_generate`），会把
   `$PIPELINE/site/` 里的 `trends/` 与 `css/trends.css`
   复制进 `public/`，并补齐 `public/sitemap.xml` 的趋势链接。
3. 因此正常跑一次 `npm run deploy`（= `hexo clean && hexo generate && hexo deploy`）就会把
   每日趋势一起发布出去，不需要额外操作。

验证集成是否正常：

```bash
cd $SITE_SRC && ./node_modules/.bin/hexo generate | grep daily-trends
ls public/trends public/trends/<date>/index.html
```

## 每晚执行步骤

```bash
TOOLS=$PIPELINE/tools
SITE=$SITE_OUT
DATE=$(date +%F)                # 目标日期 = 今天（本地时区 UTC+8）

# 0) 同步远程站点（站点可能刚被重新生成）
cd "$SITE"
git fetch git@github.com:<you>/<you>.github.io.git main
git rebase FETCH_HEAD            # 若冲突且是生成物，取远端版本后重跑第 4 步

# 1) 抓取原始数据（自动探测本机代理；--proxy none 可强制直连）
python3 "$TOOLS/fetch_sources.py" --date "$DATE"

# 2) 读 data/raw/<date>.json，撰写 data/<date>.json（结构与要求见下）

# 3) 校验并压实引用编号
python3 "$TOOLS/normalize_refs.py" --date "$DATE"

# 4) 渲染页面：写 trends/、css/trends.css，并更新首页快讯数据
python3 "$TOOLS/render_site.py" --date "$DATE"
#    渲染会写预渲染产物、静态输出仓库，以及 Hexo 源的 source/_data/ai_briefing.json

# 5) 发布前闸门（结构 / 引用 / 可核验 / 重复 / 速读 / 深度 + 陈旧引用溯源）
cd $HARNESS
./agent verify release --days 1
#   退出码非 0 就不要部署。它会指出卡在哪一天、哪个闸门；
#   「引用只出现在别的某一天」会告警并写明来源日期，不会拦发布。
#   npm run deploy 内部也会跑这道门（blog/tools/deploy.sh），显式跳过是
#   AGENT_RELEASE_GATE=off，属于例外动作。

# 6) 重新构建并部署正式站点
cd $SITE_SRC
npm run deploy

# 7) 入库：工具与内容都在 私有内容仓库（本仓库的伴生仓库，不公开）
cd $PIPELINE
git add -A && git commit -m "content: <目标日期>" && git push
#    这一步只关系历史，不影响线上：推送失败不要重跑发布，在汇报里说明、下次补推即可。
```

首页“今日AI快讯”没有独立定时任务。只有本期 `data/<date>.json`、文章页面和
`source/_data/ai_briefing.json` 全部生成并校验成功后，`npm run deploy` 才会把首页快讯
与当天文章一起发布。若生成失败或日期不一致，首页继续保留上一期，不发布半成品。

`source/_data/ai_briefing.json` 由 `render_site.py` 写入，内容是**最新一期「今日速读」的
同一批 5 条**（`brief.select_brief` 选出 → 每条的卡片文案取正文里被 `**...**` 标出的那句，
标不出就取首句）。所以首页快讯、文章顶部速读、校验脚本看到的永远是同一份选择：
不存在「首页按 tldr 顺序取、文章按关注方向选」这种两套规则。

> `origin` 是 https 地址，推送必须用 SSH 别名（见你的站点仓库配置）
> （对应 `~/.ssh/<your-deploy-key>`）。

### 站点被重新生成之后

重新生成会重建 `trends/` 与 `css/trends.css`。恢复只需要重跑渲染脚本并重新部署：

```bash
python3 $PIPELINE/tools/render_site.py
cd $SITE_SRC
npm run deploy
```

## 代理与数据源

`fetch_sources.py` 默认 `--proxy auto`：先读 `HTTPS_PROXY` / `ALL_PROXY` 环境变量，再依次探测
`socks5h://127.0.0.1:7890`（Clash / FlClash 混合端口）、`http://127.0.0.1:1087`（privoxy），
都不可用则直连。走代理后可以访问直连环境下被拦掉的源。

| `--only` 名称 | 内容 |
| --- | --- |
| `hn` | Hacker News 当日高分讨论 |
| `github` | GitHub Search API，Agent / 机器人近期热门仓库 |
| `arxiv` | arXiv cs.AI / cs.RO / cs.CL / cs.LG 最新提交 |
| `feeds` | OpenAI / Google / DeepMind / TechCrunch / The Verge / Ars / WIRED / Simon Willison |
| `anthropic` / `qwen` | Anthropic 新闻页、Qwen 博客 |
| `techmeme` | Techmeme 当日归档（跨媒体交叉核对当日头条）|
| `x_trends` | X 当日热榜快照（trends24 公开记录，按技术关键词筛出）|
| `x_search` | X API v2 关键词 + 时间范围搜索（**需付费读取额度**，未开通时自动跳过）|
| `huggingface` | HuggingFace Daily Papers |
| `tweets` | **免费的 X 原帖读取路径**：搜索引擎（Brave / Bing）发现 `x.com/status` 链接 + 文章内嵌推文 → 用 X 官方嵌入接口读取正文与互动量；输出 `tweets_recent`（目标日 ±3 天）与 `tweets_evergreen`（更早的高互动帖）|

### X 侧读取：优先用免费路径

**默认（不需要任何付费）**：

1. `x_trends`：当天 X 热榜快照（trends24 公开记录），用于「什么事在被大范围讨论」。
2. `tweets`：搜索引擎发现帖子链接 → `https://cdn.syndication.twimg.com/tweet-result?id=<id>&token=a`
   读取正文、作者与点赞/转发数。这条接口不需要登录、不需要 API 额度。
3. 两者结合即可支撑「热榜 + 可验证原帖 + 媒体转述」三层写法；注意它是**抽样**（搜索引擎索引 + 文章内嵌），不是全量。

### X API 凭证（可选，需付费额度）

如果以后开通了付费读取额度，`x_search` 可以做全量关键词 + 任意时间范围检索。当前账号状态：
token 合法，但所有读取接口返回 `402 credits depleted`（额度未开通），因此该源会自动跳过。

1. 打开 https://developer.x.com/en/portal/dashboard → 你的 Project / App → **Keys and tokens** → 复制 **Bearer Token**。
2. 写入 `~/.codex/x-api.env`（权限 600，不在任何仓库里）：

   ```
   X_BEARER_TOKEN=你的token
   ```

3. 自检：

   ```bash
   python3 $PIPELINE/tools/x_api.py --check
   python3 $PIPELINE/tools/x_api.py --date 2026-09-20 --min-likes 50
   ```

   返回 `status=200` 表示可用；`401` 是 token 不对；`402 credits depleted` 是额度未开通；
   `403` 通常是档位不支持。以上任一情况，`fetch_sources.py` 都会自动跳过 `x_search` 并把
   原因写进 `data/raw/<date>.json` 的 `x_search_status`，其余数据源照常工作。

## 内容 JSON 结构（data/<date>.json）

```jsonc
{
  "date": "2026-09-20",
  "title":   { "zh": "...", "en": "..." },
  "summary": { "zh": "一句话概括，用于目录页", "en": "..." },
  "tldr":    { "zh": ["要点 1 [[1]]", "..."], "en": ["..."] },
  "sections": [
    {                       // 第一部分：行业热点，最多 20 条
      "id": "insights",
      "title": { "zh": "...", "en": "..." },
      "intro": { "zh": "...", "en": "..." },
      "groups": [           // 分组：agent 工程 / 机器人 / AI 提效 / 公司与人物
        { "id": "agent-engineering",
          "title": { "zh": "...", "en": "..." },
          "items": [
            { "title":   { "zh": "...", "en": "..." },
              "meta":    { "zh": "可选，如 ⭐ 816 · 2026-09-06 创建", "en": "..." },
              "prose":   { "zh": "一段文字：提出了什么 / 解决什么问题 / 为什么这么做 / 可以参考什么 [[3]]", "en": "..." },
              "sources": [3, 7] }
          ] }
      ]
    },
    {                       // 第二部分：GitHub 热点，最多 20 条
      "id": "github",
      "title": { "zh": "...", "en": "..." },
      "intro": { "zh": "...", "en": "..." },
      "items": [
        { "title":   { "zh": "...", "en": "..." },
          "meta":    { "zh": "⭐ 1,234 · Python · 2026-09-05 创建", "en": "..." },
          "prose":   { "zh": "一段文字：它做什么 / 为谁解决什么问题 / 实现关键点 / 值得借鉴的地方", "en": "..." },
          "sources": [12] }
      ]
    }
  ],
  "references": [ { "id": 1, "title": "...", "url": "https://...", "source": "Hacker News", "date": "2026-09-20" } ],
  "coverage": { "zh": "覆盖口径：北京时间 2026-09-20 00:00–23:00。", "en": "Coverage window: 2026-09-20 00:00-23:00 (UTC+8)." },
  "notes": { "zh": "内部留档：数据源清单 + X 侧三层核验 + 局限与降级说明（不上页面）", "en": "..." },
  "stats": { "insights": 20, "repos": 11 }
}
```

## 写作要求（每次都要遵守）

1. 数量：第一部分 ≤ 20 条，第二部分 ≤ 20 条；宁少勿滥，优先选可核查、有信息量的事件。
2. 双语：每个字段都要有 `zh` 与 `en`；中文写事实与判断，英文是等价意思的自然表达，不要机翻腔。
3. 引用：正文用 `[[编号]]` 标注来源，编号对应 `references` 的 `id`；文末会自动生成「来源与链接」。
   重要观点、数字、技术名必须带引用。
   · **每日论文模块（Part 3，必须做）**：从当日 `raw/<date>.json` 的 `arxiv` 列表里挑 **约 8 篇** Agent 相关论文
     （记忆 / 编排 / 框架 / harness / 多 Agent / 自进化 / 可检查·可重放·可信任 / 编码 Agent，也可跟随当天热点），
    在 `sections` 里追加一个 `id: "papers"` 的段落，标题写「三、每日论文：arXiv 上的 Agent 研究 / Part 3 · Daily Papers」。
     每篇论文同样用 `prose` 一段式写法，但重点回答四件事：① 论文针对什么问题；② 用了什么方法 / 机制；
     ③ 如何解决（关键设计）；④ 优化了什么、效果如何（有具体数字就写数字，没有就写清对比基线）。
     论文条目用 `meta` 标注「arXiv 编号 · 分类 · 提交日期」，`sources` 指向该论文的 arXiv 链接。
     · **当日 `arxiv` 为空时（抓取失败或返回 0 条）：该段写 0 条并在 `notes` 说明
       「arXiv 抓取失败，本期无论文」，不要凭记忆补写，也不要把前一天的论文搬过来。**
       2026-10-05 出现过一次：当天 arXiv 抓取为 0，稿子却引用了 8 篇 10-04 的论文，
       闸门判定可核验率 0.79 并溯源到前一天。宁可少一部分，也不要让读者以为那是今天的论文。
4. 版式：**每条只写一段连贯文字**（`prose` 字段，中英各一份），不要分点、不要字段标签。段落里要交代：
   ① 它提出了什么；② 用来解决什么问题；③ 技术上为什么这么做（关键机制/参数/协议）；④ 对我的参考价值（能借鉴什么、注意什么）。
   第二部分（GitHub）同理，重点写「它做什么、为谁解决什么问题、实现关键点、值得借鉴的地方」。
   · 重点标注：在 `prose` 里用 `**...**` 标出最该被看到的一句或一个术语（渲染会转成加粗并带淡底色）；
     如果一段里没有写 `**...**`，渲染脚本会自动把该段首句加粗（首句通常就是「提出了什么」）。
     建议每条至少显式标注一处，例如 **7B 开放权重**、**约 30ms**、**50.1% 投票权**。
5. 缩略语：技术缩写首次出现要给出英文全称或简短解释，例如 VLM（Vision-Language Model，视觉语言模型）、
   METR（Model Evaluation and Threat Research）、overclaiming（把未完成的工作说成已完成）。
6. 不造假：所有条目必须来自 `data/raw/<date>.json` 里抓到的真实数据，或 URL 可核查的公开来源。
   X 侧按「热榜快照（`x_trends`）+ 关键账号原帖核验（`tweets`）+ 媒体转述」三层写进 `notes`；
   没有抓到原帖时不要编造推文内容。
7. 去重：同一事件多家报道合并成一条，`sources` 里并列引用。
8. 语言切换：页面用站点自带的 `data-lang-content="zh|en"` 机制，渲染脚本自动处理，无需手工写双语 DOM。
9. 文末只发布一句覆盖口径：写进 `coverage` 字段（zh / en 各一句，例如「覆盖口径：北京时间 <日期> 00:00–23:00。」）。
   数据源清单、X 侧方法论、局限与降级说明一律写进 `notes`（内部留档，页面不展示）；渲染脚本只取 `coverage`。

## 生成引用的小技巧

```bash
# 用 URL / 标题子串自动生成规范引用条目（含标题、日期、来源）
python3 $PIPELINE/tools/make_refs.py --date 2026-09-20 2609.20804 harnesstax qwen-image-2.1
```

把输出粘进 `references`，再跑 `normalize_refs.py` 压缩编号即可。

## 降级策略

- 某个源抓取失败：用 `--only hn,github,arxiv` 单独重跑某个源，结果会合并进已有 raw 数据。

## 定时任务（Codex automation）

触发：每天 23:00（Asia/Shanghai）一次。提示词只做四件事——护栏、按本文件执行、
跑闸门、如实汇报；细节都在本文件里。

0. **时间护栏**：若在 00:00–06:00 被触发，目标日期取前一天；若
   `site/trends/<目标日期>/index.html` 已存在且 `source/_data/ai_briefing.json` 的
   `date` 已等于该日期，说明这一期已经发布：**不要重复抓取、不要重复部署**，直接报告结束。
1. 目标日期 = 运行当天的本地日期（按上一步修正）；覆盖口径写「北京时间 <目标日期> 00:00–23:00」。
2. 抓取（代理自动探测，Clash Verge 常用 7897，已在候选列表首位；必要时 `--proxy http://127.0.0.1:7897` 补抓）。
3. 撰写 `data/<目标日期>.json`（写作要求见上）。
4. `normalize_refs.py` → `render_site.py --date <目标日期>`。
5. `./agent verify release --days 1`（在 `$HARNESS`）。
6. `cd $SITE_SRC && npm run deploy`（文章与首页快讯同一次上线）。
7. 验证线上：首页「今日AI快讯」日期 == 目标日期，且
   `https://www.simon-zj.top/trends/<目标日期>/` 可访问（Pages 约 10 分钟缓存，可用 `?cb=` 复核）。
8. 入库：`cd $PIPELINE && git add -A && git commit -m "content: <目标日期>" && git push`
   （工具与内容的历史都在 私有内容仓库；推送失败不影响发布，说明并下次补推）。

汇报格式：行业热点 / GitHub / 论文三部分条数、文章链接、首页快讯日期、验证到的 X 原帖条数、
数据源降级情况（哪些源失败、怎么补的）。任何一步失败就如实说明，不发布半成品，
也不发布日期对不上的首页快讯。
- 内容不足 20 条：如实减少条数，不要用低质量信息凑数。
- 代理不可用（`x_trends` / `tweets` / `techmeme` 抓不到）：在 `notes` 中说明推特信号由
  Hacker News / 官方博客 / 科技媒体等效替代。
- X 付费额度未开通（`x_search_status` 为 402/403）：这是当前默认状态，照常用 `x_trends` + `tweets`
  免费路径，并在 `notes` 里写明「付费读取额度未开通，原帖为抽样而非全量」。
- 站点刚被重新生成：按上面「站点被重新生成之后」一节重跑 `render_site.py` 恢复入口与页面。
