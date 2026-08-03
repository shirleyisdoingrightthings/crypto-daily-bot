# Crypto Daily Bot

每天早上由 Claude 自动写稿：拉取 6 大主流币实时价格、恐惧贪婪指数、赛道资金流向，加上 3 家加密媒体的当日头条，生成一份《市场晨报》+《新闻播报》，两条消息推送到 Telegram。

---

## 核心特性

**数据来源 — 价格、情绪、资金、新闻，四维覆盖**

| 类型 | 数据源 |
|------|------|
| 实时价格 | BTC / ETH / SOL / BNB / XRP / HYPE（CoinGecko） |
| 市场情绪 | 恐惧贪婪指数（alternative.me） |
| 资金流向 | 总市值 / BTC 市占率 / DeFi 占比 / 24h 赛道涨幅 Top 5 |
| 行业新闻 | Cointelegraph · CoinDesk · Decrypt |

**取材 — 新闻优先抓正文全文，而不是只吃 RSS 摘要**

- 选中的新闻会 best-effort 抓取文章正文全文（JSON-LD `articleBody` → `<p>` 启发式，纯标准库零依赖）
- 抓取失败 / 被反爬拦截 / 正文过短时，自动回退到 RSS 摘要，绝不因此漏发
- CoinDesk 等源的 RSS 摘要经常为空，抓正文在这里收益最大

**内容处理 — 数据 + 叙事，不是数字堆砌**

- **市场晨报**：价格 + 情绪 + 赛道三合一，给出今日市场判断
- **新闻播报**：开头「⚡ 30秒速览」固定 5 条，每条带星级 + 至少一个具体数字，与正文前 5 条一一对应；下面逐条列出完整事实
- **双消息结构**：晨报和新闻分开推送，各自独立阅读
- **跨天去重**：`send` 成功后归档稿件里实际用到的链接（保留 7 天），下次抓取自动排除
- **超长自动分页**：超过 Telegram 单条 4096 上限时按段落边界切分，每条顶部标 `（n/N）` 页码

**稳定性 — 出了问题自己修**

- 每日体检：11:00 检查当天是否成功出稿，异常自动记 changelog 并触发自愈
- 两级自愈：瞬时故障等 30 秒重跑；持续故障调用 Claude CLI 诊断修复
- 无头补跑：当天根本没出稿（机器睡眠错过 10:00）或自愈无效时，claude CLI 自动完整重走一遍流程（自动版 Run Now）
- 消息缓存：发送失败 / 代理不可用时，把两稿缓存到 pending_messages.json，避免内容丢失
- 源淘汰监测：统计每个 RSS 源"过滤后还剩几条"，连续 3 天零产即告警建议移除（详见下文）

---

## Demo 预览

<details>
<summary>点击展开查看 Bot 推送到 Telegram 的长图预览</summary>
<br>

![Crypto Daily Bot 运行效果图](full_demo.png)

</details>

---

## 系统架构与工作流

```
【数据采集层】
CoinGecko（价格 / 趋势币 / 总市值 / DeFi / 赛道热力图）──┐
alternative.me（恐惧贪婪指数）                            ├──▶ crypto_report.py --mode fetch
RSS × 3（Cointelegraph / CoinDesk / Decrypt）             ┘     │  build_news_context()
                                                                │  ├─ URL 去重 + 跨天去重（排除 logs/sent_urls.json）
                                                                │  ├─ 3 天时间窗
                                                                │  ├─ 并发 best-effort 抓正文全文
                                                                │  └─ 抓不到 → 回退 RSS 摘要
                                                                ▼
                                                   Claude 按 prompt 写两稿
                                                   ├─ prompt_analysis.md → logs/report_analysis.txt（消息①）
                                                   └─ prompt_news.md     → logs/report_news.txt（消息②）
                                                                │
                                                                ▼
                                                   crypto_report.py --mode send
                                                                │  清洗 HTML，依次发送（含部分发送保护）
                                                                │  超 4096 → 按段落分页 + (n/N) 页码
                                                                │  发送成功 → 归档链接供跨天去重
                                                                ▼
                                                     Telegram（2 条 HTML 消息）

【自动化调度】
10:00  Claude 定时任务（唯一写稿入口）
         └─ claude_report.sh fetch → Claude 写两稿 → claude_report.sh send → run.log [OK/FAIL]

11:00  launchd ──▶ health_check.sh
                        │
                  [OK] ──┼── .ok_streak +1（连续 3 次后清理已解决的 changelog 条目）
                        │
                  [无记录] ── claude_catchup.sh 无头补跑（自动版 Run Now：
                        │       claude CLI 完整重走 fetch → 写两稿 → send，同一天只补跑一次）
                        │
                  [FAIL] ── changelog 新增条目
                        └──▶ auto_repair.sh（前台运行；缺当日稿件时直接转无头补跑）
                                  ├─ Level 1：等 30s 重跑 send（瞬时网络错误）
                                  ├─ Level 2：claude CLI 诊断修复 → 重跑 send
                                  └─ 最终兜底：claude_catchup.sh 无头补跑
                                              ├─ 成功 → changelog 标记 [x]
                                              └─ 失败 → macOS 通知，需人工介入
```

> 写稿由本地 Claude 定时任务完成（抓取 → 按 prompt 写两稿 → 推送）。`crypto_report.py` 本身只负责抓取（`fetch`）与发送（`send`），全程零第三方大模型 API、零 token 成本。

---

## RSS 源健康与淘汰

本 bot 只有 3 个新闻源、时间窗 3 天，而 limit 之和恰好等于每日入选数——实测"超 3 天"
过滤命中 0，说明**时间窗从未真正生效**，limit 才是唯一约束。配合每天跑一次，同一条
新闻原本最多可以连播 3 天。现在真正在防重复的是跨天去重档案。

源健康的判定口径是**过滤后零产**（源可能天天拉得到、却条条被过滤）：

| 环节 | 行为 |
|---|---|
| `fetch` | 记录每个源的 `{fetched, kept}` 到 `run.jsonl` 的 `rss_source_stats` |
| `fetch` | 过滤后 `kept == 0` 的源计入 `logs/.zero_streak.json`，连续天数 +1；有产出则清零并移出档案 |
| `fetch` | 连续 **3 天**零产 → stdout 输出 `=== SOURCE_ALERT ===` 块，并写入 metrics 的 `rss_stale_sources` |
| 10:00 routine | 读到 SOURCE_ALERT 后，在日报汇报末尾单列「RSS 源健康」，说明哪个源连续几天没贡献、可以移除或更换 |
| 11:00 health_check | 读 metrics 发 macOS 通知；单日零产只记 INFO 不打扰 |

连续天数由 `fetch` **单点写入**，health_check 只读不写——两处各加一次会让天数翻倍。

收到告警后，把该源从 `crypto_report.py` 的 `RSS_SOURCES` 里删掉或换成新源即可。

---

## 文件结构

```
~/Desktop/bots/shared/bot_utils.py     # 外部共享工具库（含抓正文 fetch_article_text，与 AI Daily News Bot 共用）
~/Desktop/bots/shared/auto_repair_base.sh         # 共享修复逻辑（与 AI Daily News Bot 共用，2026-07 从 ~/Desktop/bot_ops/ 迁入并修复重跑缺陷）
~/Desktop/bots/shared/headless_catchup_base.sh    # 共享无头补跑逻辑（自动版 Run Now，与 AI Daily News Bot 共用）

Crypto Daily Bot/
├── crypto_report.py                   # 主脚本：--mode fetch（抓行情+新闻+抓正文）/ send（清洗+依次推送两稿）
├── claude_report.sh                   # 供 Claude 定时任务调用的 fetch/send 封装（从 plist 加载环境变量）
├── prompt_analysis.md                 # 消息①市场晨报的写稿规范（唯一权威源）
├── prompt_news.md                     # 消息②新闻播报的写稿规范（唯一权威源）
├── health_check.sh                    # 健康检查（失败时触发 auto_repair）
├── auto_repair.sh                     # 薄包装：设置参数后委托 ~/Desktop/bots/shared/auto_repair_base.sh
├── claude_catchup.sh                  # 薄包装：无头补跑（委托 ~/Desktop/bots/shared/headless_catchup_base.sh）
├── logs/                              # 所有日志与产物集中存放（运行时生成）
│   ├── report_analysis.txt           # 当日 Claude 写好的消息①（send 读取）
│   ├── report_news.txt               # 当日 Claude 写好的消息②（send 读取）
│   ├── fetch_meta.json               # fetch 边车：日志摘要 + 指标（send 回填，供体检监控）
│   ├── run.log                        # 单行摘要日志（人类可读）
│   ├── run.jsonl                      # 结构化指标日志（程序可读，含分源 fetched/kept 统计）
│   ├── sent_urls.json                 # 跨天去重档案：已推送链接 → 日期（保留 7 天）
│   ├── .zero_streak.json              # 各源连续零产天数（fetch 单点写入，达 3 天告警）
│   ├── launchd.log                    # （历史）旧 09:15 launchd 兜底的输出，兜底已移除，不再写入
│   ├── health_check.log              # health_check 运行日志
│   └── .ok_streak                     # 连续成功计数
├── changelog.md                       # 问题追踪，与 health_check 联动
├── pending_messages.json              # Telegram 缓存（仅发送失败时存在）
├── AGENTS.md                          # 通用 AI 操作手册（适用于任意 AI 工具）
├── CLAUDE.md                          # Claude Code 专属上下文（引用 AGENTS.md）
├── com.shirley.crypto-daily-bot.plist.example        # 环境变量 plist 模板（正式配置在 ~/Library/LaunchAgents/，是端口/密钥的唯一权威源；不含调度，09:15 launchd 兜底已于 2026-07 移除）
├── com.shirley.crypto-daily-bot-health.plist         # health_check launchd 配置（11:00 触发）
├── requirements.txt                   # Python 依赖清单
└── README.md                          # 本文件（人类阅读）
```

> `logs/` 下的文件均为运行时自动生成，不预置。`pending_messages.json` 仅在 Telegram 发送失败时存在。

---

## 环境变量

所有变量写在**唯一权威配置源** `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist` 中，`claude_report.sh` 从这里读取并自动注入。仓库内只保留 `.plist.example` 模板（不含密钥）。改端口/密钥请直接编辑 LaunchAgents 里那份，改完即生效（`claude_report.sh` 每次运行时直接读文件，无需重载 launchd）。

> 该 plist 已不承担任何调度职责：09:15 的 launchd 兜底于 2026-07 移除（它因缺 `--mode` 参数且 launchd 环境下 `import bot_utils` 失败，从未成功运行过），失败兜底由 11:00 的 health_check + auto_repair 承担。plist 仅作为环境变量配置源保留。

| 变量 | 说明 | 来源 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | plist（需手动填入） |
| `TELEGRAM_CHAT_ID` | 目标 Chat ID | plist（已配置）|
| `COINGECKO_API_KEY` | CoinGecko Demo Key | plist（已配置）|
| `HTTPS_PROXY` / `HTTP_PROXY` | 本地代理地址 | plist（已配置，127.0.0.1:YOUR_PORT）|

---

## 快速开始

**手动抓取 / 发送（测试）**
```bash
cd ~/Desktop/bots/crypto\ daily\ bot
bash claude_report.sh fetch     # 抓行情+新闻+抓正文，把写稿素材打到 stdout
# （由 Claude 依 prompt_analysis.md / prompt_news.md 写两稿，分别存入 logs/report_analysis.txt 与 report_news.txt）
bash claude_report.sh send      # 读取两稿，清洗 HTML 后依次推送 Telegram
```

**验证调度状态**
```bash
launchctl list | grep shirley
tail -5 logs/run.log
```

---

## 依赖安装

```bash
pip3 install requests feedparser
```

---

## 调试

```bash
tail -5 logs/run.log                          # 最近运行状态
tail -3 logs/run.jsonl | python3 -m json.tool # 结构化指标
cat changelog.md                              # 当前问题清单
bash health_check.sh                          # 手动触发健康检查
```

详细操作规范见 [`AGENTS.md`](./AGENTS.md)。
