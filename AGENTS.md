# Crypto Daily Bot — 操作手册

> **通用文件**：适用于任何 AI 工具。  
> 专属上下文见 `CLAUDE.md`（在此基础上做了精简，避免冗余）。

---

## 工作流概述

这是一个 **加密市场日报系统**。每早由本地 Claude 定时任务触发：`crypto_report.py --mode fetch` 聚合行情+新闻（新闻 best-effort 抓正文全文，失败回退 RSS 摘要）→ Claude 按 prompt 写两稿 → `crypto_report.py --mode send` 依次推送。脚本本身只做抓取与发送，零第三方大模型 API。

### 数据流

```
[数据源]                             [抓取 / 写稿]                     [输出]
CoinGecko /simple/price  ──┐
CoinGecko /search/trending ─┤
CoinGecko /global          ─┼──▶ crypto_report.py --mode fetch
CoinGecko /.../defi         ─┤        │  build_news_context()
CoinGecko /coins/categories ─┤        │  ├─ 并发 best-effort 抓正文全文
alternative.me /fng/       ─┘        │  └─ 抓不到 → 回退 RSS 摘要
RSS × 3 源 ──────────────────────────▼
                              Claude 按 prompt 写两稿
                              ├─ prompt_analysis.md → logs/report_analysis.txt（消息①）
                              └─ prompt_news.md     → logs/report_news.txt（消息②）
                                      │
                                      ▼
                              crypto_report.py --mode send ──▶ Telegram（2 条 HTML 消息）
```

### 自动化调度

```
08:30  Claude 定时任务（唯一写稿入口）
         claude_report.sh fetch → Claude 写两稿 → claude_report.sh send
                       │
                  run.log [OK/FAIL]

09:45  launchd → health_check.sh
                       │
           ┌── [OK] ───┼── .ok_streak +1
           │                streak ≥ 3 → 删除 changelog 中 [x] 条目
           │
           ├── [无记录] ── claude_catchup.sh 无头补跑（自动版 Run Now）
           │                claude CLI 重走 fetch → 写两稿 → send；同一天只补跑一次
           │
           └── [FAIL] ─── changelog 新增 [ ] 条目
                       └─▶ auto_repair.sh（后台触发；先查当日稿件，缺稿直接转无头补跑）
                                 │
                         瞬时错误？
                         ├─ Yes → 等 30s → 重跑 send
                         │         ├─ 成功 → changelog [x]
                         │         └─ 失败 → 升级
                         └─ No  → claude CLI 分析修复 → 重跑 send
                                   ├─ 成功 → changelog [x]
                                   └─ 失败 → claude_catchup.sh 无头补跑
                                             ├─ 成功 → changelog [x]
                                             └─ 失败 → macOS 通知 → 人工介入
```

---

## 文件结构与职责

| 文件 | 职责 | 修改频率 |
|------|------|---------|
| `crypto_report.py` | 主脚本：`--mode fetch`（抓行情+新闻+抓正文）/ `send`（清洗+依次推送两稿），零第三方大模型 API | 偶尔 |
| `claude_report.sh` | 供 Claude 定时任务调用的 fetch/send 封装（从 plist 加载环境变量） | 极少 |
| `prompt_analysis.md` / `prompt_news.md` | 消息①/② 的写稿规范（唯一权威源，Claude 依此写稿） | 偶尔 |
| `health_check.sh` | 按日期检查今天 [OK]/[FAIL] 状态，含 60s 等待防竞态；BTC 数据质量通知；触发 auto_repair | 极少 |
| `~/bots/shared/bot_utils.py` | 共享工具库（两个 Bot 共用）：sanitize_html / with_retry / fetch_rss / parse_entry_date / already_ran_today / fetch_article_text（抓正文） | 偶尔 |
| `auto_repair.sh` | 薄包装：设置 BOT_NAME/SCRIPT/ERROR/DRAFTS，委托 `~/bots/shared/auto_repair_base.sh` 执行 | 极少 |
| `~/bots/shared/auto_repair_base.sh` | 共享修复逻辑（Level 1 重跑 send / Level 2 Claude CLI / 最终兜底无头补跑）；两个 Bot 共用，2026-07 从 `~/Desktop/bot_ops/` 迁入并修复重跑缺陷 | 极少 |
| `claude_catchup.sh` | 无头补跑薄包装（委托 `~/bots/shared/headless_catchup_base.sh`）：当天未出稿或自愈失败时由 claude CLI 完整重走流程；同一天只补跑一次（logs/.catchup_ran 戳记） | 极少 |
| `logs/report_analysis.txt` / `logs/report_news.txt` | 当日 Claude 写好的两稿（send 读取后推送） | 每日写入 |
| `logs/fetch_meta.json` | fetch 边车：日志摘要 + 指标（send 回填，供体检监控） | 每日写入 |
| `logs/run.log` | 单行摘要日志（人类可读） | 每日写入 |
| `logs/run.jsonl` | 结构化指标（程序可读） | 每日写入 |
| `logs/launchd.log` | （历史）旧 09:15 launchd 兜底的 stdout/stderr，兜底已移除，不再写入 | 不再写入 |
| `logs/health_check.log` | health_check 运行日志 | 每日写入 |
| `logs/headless_catchup.log` | 无头补跑运行日志 | 触发时写入 |
| `changelog.md` | 问题追踪，与 health_check 联动 | 按需 |
| `pending_messages.json` | Telegram 发送缓存（降级保护） | 临时 |
| `com.shirley.crypto-daily-bot.plist.example` | 环境变量 plist 模板（正式配置在 `~/Library/LaunchAgents/`，是端口/密钥的唯一权威源，`claude_report.sh` 从中读环境变量；不含调度，09:15 launchd 兜底已于 2026-07 移除，失败兜底由 health_check + auto_repair 承担） | 极少 |
| `com.shirley.crypto-daily-bot-health.plist` | health_check launchd 配置（09:45 触发） | 极少 |

---

## 关键约定（修改前必读）

### 数据源与 API

| API | 接口 | 内容 | Plan |
|-----|------|------|------|
| CoinGecko | `/simple/price` | BTC/ETH/SOL/BNB/XRP/HYPE 价格 | Demo 免费 |
| CoinGecko | `/search/trending` | 热搜榜 Top 15（取前 5） | Demo 免费 |
| CoinGecko | `/global` | 加密货币总市值 / 24h成交量 / BTC市占率 | Demo 免费 |
| CoinGecko | `/global/decentralized_finance_defi` | DeFi 总市值 / 成交量 / 市占率 | Demo 免费 |
| CoinGecko | `/coins/categories` | 赛道表现（24h 涨幅 Top 5） | Demo 免费 |
| alternative.me | `/fng/` | 恐惧贪婪指数 | 完全免费 |
| RSS × 3 | Cointelegraph / CoinDesk / Decrypt | 新闻 | 免费 |
| 正文抓取 | 各新闻源文章页 | best-effort 抓正文（JSON-LD `articleBody` / `<p>`），失败回退 RSS 摘要 | 零依赖 |

### 日志格式（不得改动）
```
YYYY-MM-DD HH:MM  [OK/FAIL/WARN]  消息内容
```
`health_check.sh` 用 `grep "$TODAY.*[OK]"` / `grep "$TODAY.*[FAIL]"` 按日期匹配，改动格式会导致健康检查失效。

### 重复推送防护
`already_ran_today()` 在 `run.log` 中检测到今天已有 `[OK]` 记录时直接退出，防止同日重复运行导致重复推送。  
需要强制重跑时设置环境变量 `FORCE_RUN=1`。

### 数据降级防护
BTC 和 ETH 价格同时为「未知」时，跳过本次发送（`write_log("WARN", ...)`），等待下次运行，不推送空数据报告。

### Telegram 输出格式
- 所有 AI 输出必须是 **HTML 格式**，禁止 Markdown
- 只能使用 `<b>` 和 `<a href="...">` 两种标签
- 单条消息上限 4096 字符

### 新闻时效
- Crypto Daily Bot 收录 **3 天内**新闻（`timedelta(days=3)`）

### 代理
- 固定走 `127.0.0.1:YOUR_PORT` (本地代理端口)
- 端口在 `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist` 的 `HTTP_PROXY`/`HTTPS_PROXY` 里配置（唯一权威源）；改完即生效，`claude_report.sh` 每次运行时直接读文件，无需重载 launchd
- `requests` 通过 `SESSION` 显式配置，`feedparser` 通过 `HTTP_PROXY` 环境变量

### 重试策略
- Telegram：最多 3 次，指数退避（5 → 10 → 20s）
- RSS 抓取（fetch_rss）：最多 2 次，退避 3 → 6s
- 正文抓取（fetch_article_text）：best-effort、单次、失败即回退 RSS 摘要，不重试

### 消息缓存降级与部分发送保护
- send 模式发送前把两稿写入 `pending_messages.json`；代理不可用时也缓存
- 消息①发送成功后立即把缓存更新为 `[消息②]`（同一次运行内的部分发送保护）
- 两稿全部发送成功后删除缓存文件
- 缓存用于避免内容丢失（可人工恢复），当前 Claude 流程不做自动重发

### 分源零条监控
- 每次运行将各 RSS 源抓取数写入 JSONL 的 `rss_zero_sources` 字段
- `health_check.sh` 步骤 5 检测到零源时发送 macOS 通知，但不触发 auto_repair（不影响整体 OK）

---

## 修改禁区

| 禁止操作 | 原因 |
|---------|------|
| 修改 `run.log` 的 `[OK]/[FAIL]/[WARN]` 格式 | health_check.sh 依赖字符串匹配 |
| 删除 `flush_pending()` 调用 | 会导致失败消息永久丢失 |
| 修改 PROMPT 中的 HTML 输出格式 | Telegram 不支持 Markdown |
| 将 `timedelta(days=3)` 改小 | 会漏掉重要新闻 |
| 修改 `with_retry` 的 exceptions 参数 | 会影响重试覆盖范围 |
| 替换 CoinGecko 免费接口为付费 Pro 接口 | 会导致 API 认证失败 |
| 修改价格列表中的币种 ID | CoinGecko ID 必须与官方匹配 |
| 在 `bot_utils.py` 中删除或重命名工具函数 | 两个 Bot 共用，改动会同时影响 AI News Bot 和 Crypto Daily Bot |

---

## 调试入口

```bash
# 查看最近运行状态
tail -5 logs/run.log

# 查看结构化指标（含耗时）
tail -3 logs/run.jsonl | python3 -m json.tool

# 查看当前问题清单
cat changelog.md

# 手动抓取 / 发送（Claude 定时任务用同一封装）
bash claude_report.sh fetch     # 抓行情+新闻+抓正文，输出写稿素材
bash claude_report.sh send      # 读取两稿并依次推送

# 手动运行健康检查
bash health_check.sh

# 查看 launchd 任务状态
launchctl list | grep shirley
```

---

## AI 工具使用说明

### 如果你是 Claude Code
自动加载 `CLAUDE.md`（内含额外的 Claude 专属指令）。本文件提供完整上下文。

### 如果你是 Cursor / GitHub Copilot / 其他工具
直接阅读本文件（`AGENTS.md`）即可获得完整上下文。
如果工具支持自定义规则文件，将本文件路径加入即可：
- Cursor：将内容复制到 `.cursorrules`
- GitHub Copilot：将内容复制到 `.github/copilot-instructions.md`

### Auto-Repair 代理行为规范
当 `auto_repair.sh` 调用 Claude CLI 时，Claude 应当：
1. 只修复**最小范围**的问题
2. 修复后必须输出 `FIX: <一行说明>` 或 `CANNOT_FIX: <原因>`
3. 不得触碰修改禁区中的任何内容
4. 如果不确定根因，选择 `CANNOT_FIX` 而不是盲目修改

