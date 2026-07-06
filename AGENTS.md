# Crypto Daily Bot — 操作手册

> **通用文件**：适用于任何 AI 工具。  
> 专属上下文见 `CLAUDE.md`（在此基础上做了精简，避免冗余）。

---

## 工作流概述

这是一个 **加密市场日报系统**。主脚本 `crypto_report.py` 每天由 launchd 自动触发，负责聚合行情数据与新闻并生成深度市场报告。

### 数据流

```
[数据源]                             [处理]              [输出]
CoinGecko /simple/price  ──┐
CoinGecko /search/trending ─┤
CoinGecko /global          ─┼──▶  build_news_context()
CoinGecko /global/decentralized_finance_defi ─┤   │
CoinGecko /coins/categories ─┤             │
alternative.me /fng/       ─┘          ▼
                                  call_deepseek() × 2
RSS × 3 源 ─────────────────────▶  ① PROMPT_ANALYSIS  ──▶  Telegram 消息①（市场晨报）
                                   ② PROMPT_NEWS       ──▶  Telegram 消息②（新闻播报）
```

### 自动化调度

```
08:00  launchd → crypto_report.py
                       │
                  run.log [OK/FAIL]

08:30  launchd → health_check.sh
                       │
           ┌── [OK] ───┴── .ok_streak +1
           │                streak ≥ 3 → 删除 changelog 中 [x] 条目
           │
           └── [FAIL] ─── changelog 新增 [ ] 条目
                       └─▶ auto_repair.sh（后台触发）
                                 │
                         瞬时错误？
                         ├─ Yes → 等 30s → 直接重跑
                         │         ├─ 成功 → changelog [x]
                         │         └─ 失败 → 升级
                         └─ No  → claude CLI 分析修复 → 重跑
                                   ├─ 成功 → changelog [x]
                                   └─ 失败 → macOS 通知 → 人工介入
```

---

## 文件结构与职责

| 文件 | 职责 | 修改频率 |
|------|------|---------|
| `crypto_report.py` | 主脚本：抓取→生成→推送 | 偶尔 |
| `health_check.sh` | 按日期检查今天 [OK]/[FAIL] 状态，含 60s 等待防竞态；BTC 数据质量通知；触发 auto_repair | 极少 |
| `~/Desktop/bot_ops/shared/bot_utils.py` | 共享工具库（两个 Bot 共用）：sanitize_html / with_retry / fetch_rss / parse_entry_date / already_ran_today | 偶尔 |
| `auto_repair.sh` | 薄包装：设置 BOT_NAME/SCRIPT/ERROR，委托 `bot_ops/auto_repair_base.sh` 执行 | 极少 |
| `~/Desktop/bot_ops/auto_repair_base.sh` | 共享修复逻辑（Level 1 重跑 / Level 2 Claude CLI）；两个 Bot 共用 | 极少 |
| `logs/run.log` | 单行摘要日志（人类可读） | 每日写入 |
| `logs/run.jsonl` | 结构化指标（程序可读） | 每日写入 |
| `logs/launchd.log` | launchd 的 stdout/stderr | 每日写入 |
| `logs/health_check.log` | health_check 运行日志 | 每日写入 |
| `changelog.md` | 问题追踪，与 health_check 联动 | 按需 |
| `pending_messages.json` | Telegram 发送缓存（降级保护） | 临时 |
| `com.shirley.crypto-daily-bot.plist.example` | 主脚本 launchd 配置模板（正式配置在 `~/Library/LaunchAgents/`，是端口/密钥的唯一权威源，`catchup.sh` 也读它） | 极少 |
| `com.shirley.crypto-daily-bot-health.plist` | health_check launchd 配置 | 极少 |

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

### 日志格式（不得改动）
```
YYYY-MM-DD HH:MM  [OK/FAIL/WARN]  消息内容
```
`health_check.sh` 用 `grep "$TODAY.*[OK]"` / `grep "$TODAY.*[FAIL]"` 按日期匹配，改动格式会导致健康检查失效。

### 重复推送防护
`already_ran_today()` 在 `run.log` 中检测到今天已有 `[OK]` 记录时直接退出，防止 launchd 补跑导致重复推送。  
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
- 端口在 `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist` 的 `HTTP_PROXY`/`HTTPS_PROXY` 里配置（唯一权威源）；改完 `launchctl unload && launchctl load` 重载
- `requests` 通过 `SESSION` 显式配置，`feedparser` 通过 `HTTP_PROXY` 环境变量

### 重试策略
- Telegram：最多 3 次，指数退避（5 → 10 → 20s）
- DeepSeek API：最多 2 次，指数退避（10 → 20s）
- `OpenAI` 客户端的 `max_retries=0`，由外层装饰器统一控制

### 消息缓存降级与部分发送保护
- AI 生成完成后立即写 `pending_messages.json`（含消息①和②）
- 消息①发送成功后立即更新缓存为 `[消息②]`，防止重跑时重发消息①
- 全部发送成功后删除缓存文件
- 下次启动时 `flush_pending()` 只重发剩余未发消息；重发成功后直接写 `[OK]` 并退出，不重新抓取数据

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

# 查看 launchd 原始输出
tail -20 logs/launchd.log

# 手动运行主脚本
/opt/homebrew/bin/python3.11 crypto_report.py

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

