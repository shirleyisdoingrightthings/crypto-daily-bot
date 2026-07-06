# Crypto Daily Bot

每天早上 8 点自动运行。拉取 6 大主流币实时价格、恐惧贪婪指数、赛道资金流向，加上 3 家加密媒体的当日头条，由 DeepSeek 生成一份市场晨报 + 新闻播报，直接推送到 Telegram。

---

## 核心特性

**数据来源 — 价格、情绪、资金、新闻，四维覆盖**

| 类型 | 数据源 |
|------|------|
| 实时价格 | BTC / ETH / SOL / BNB / XRP / HYPE（CoinGecko） |
| 市场情绪 | 恐惧贪婪指数（alternative.me） |
| 资金流向 | 总市值 / BTC 市占率 / DeFi 占比 / 24h 赛道涨幅 Top 5 |
| 行业新闻 | Cointelegraph · CoinDesk · Decrypt |

**内容处理 — 数据 + 叙事，不是数字堆砌**

- **市场晨报**：价格 + 情绪 + 赛道三合一，DeepSeek 直接给出今日市场判断
- **新闻播报**：筛选当日最值得关注的加密事件，带背景和影响分析
- **双消息结构**：晨报和新闻分开推送，各自独立阅读，不互相干扰

**稳定性 — 出了问题自己修**

- 代理预检：启动时先验证网络可用，不通立即报错退出，不浪费等待时间
- 两级自愈：瞬时故障等 30 秒重跑；持续故障调用 Claude CLI 自动诊断修复
- 消息缓存：发送失败不丢消息，下次运行优先补发

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
alternative.me（恐惧贪婪指数）                            ├──▶ build_news_context()
RSS × 3（Cointelegraph / CoinDesk / Decrypt）             ┘         │
                                                                     ▼
                                                            call_deepseek() × 2
                                                            ┌─── ① PROMPT_ANALYSIS ──▶ 市场晨报
                                                            └─── ② PROMPT_NEWS     ──▶ 新闻播报
                                                                        │
                                                                        ▼
                                                               Telegram（2 条 HTML 消息）

【自动化调度】
08:00  launchd ──▶ crypto_report.py ──▶ run.log [OK/FAIL]
08:30  launchd ──▶ health_check.sh
                        │
                  [OK] ──┴── .ok_streak +1（连续 3 次后清理已解决的 changelog 条目）
                        │
                  [FAIL] ── changelog 新增条目
                        └──▶ auto_repair.sh（后台运行）
                                  ├─ Level 1：等 30s 直接重跑（瞬时网络错误）
                                  └─ Level 2：claude CLI 诊断修复 → 重跑
                                              ├─ 成功 → changelog 标记 [x]
                                              └─ 失败 → macOS 通知，需人工介入
```

---

## 文件结构

```
~/Desktop/bot_ops/shared/bot_utils.py      # 外部共享工具库（与 AI Daily News Bot 共用）
~/Desktop/bot_ops/auto_repair_base.sh     # 外部共享修复逻辑（与 AI Daily News Bot 共用）

Crypto Daily Bot/
├── crypto_report.py                    # 主脚本（抓取 → 分析 → 推送）
├── health_check.sh                     # 健康检查（失败时触发 auto_repair）
├── auto_repair.sh                      # 薄包装：设置参数后委托 bot_ops/auto_repair_base.sh
├── logs/                               # 所有日志集中存放
│   ├── run.log                         # 单行摘要日志（人类可读）
│   ├── run.jsonl                       # 结构化指标日志（程序可读）
│   ├── launchd.log                     # launchd stdout/stderr
│   ├── health_check.log               # health_check 运行日志
│   └── .ok_streak                      # 连续成功计数
├── changelog.md                        # 问题追踪，与 health_check 联动
├── pending_messages.json               # Telegram 缓存（仅 Telegram 失败时存在）
├── AGENTS.md                           # 通用 AI 操作手册（适用于任意 AI 工具）
├── CLAUDE.md                           # Claude Code 专属上下文（引用 AGENTS.md）
├── com.shirley.crypto-daily-bot.plist.example  # 主脚本 launchd 配置模板（正式配置在 ~/Library/LaunchAgents/）
├── com.shirley.crypto-daily-bot-health.plist  # launchd 健康检查配置（08:30 触发）
├── requirements.txt                    # Python 依赖清单
└── README.md                           # 本文件（人类阅读）
```

> `logs/` 目录下的文件均为运行时自动生成，不会预置在文件夹中。`pending_messages.json` 仅在 Telegram 发送失败时存在。  
> `__pycache__/` 是 Python 自动创建的字节码缓存目录，可安全忽略，建议加入 `.gitignore`。

---

## 环境变量

所有变量写在**唯一权威配置源** `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist` 中，launchd 定时与 `catchup.sh` 补跑都从这里读取并自动注入，无需手动配置 shell profile。仓库内只保留 `com.shirley.crypto-daily-bot.plist.example` 模板（不含密钥）。修改端口/密钥请直接编辑 LaunchAgents 里那份，然后 `launchctl unload && launchctl load` 重新加载。

| 变量 | 说明 | 来源 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | plist（需手动填入） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | plist（需手动填入） |
| `TELEGRAM_CHAT_ID` | 目标 Chat ID | plist（已配置）|
| `COINGECKO_API_KEY` | CoinGecko Demo Key | plist（已配置）|
| `HTTPS_PROXY` | 代理地址 | plist（已配置，127.0.0.1:YOUR_PORT）|

---

## 快速开始

**手动运行（测试）**
```bash
cd ~/Desktop/Crypto\ Daily\ Bot
/opt/homebrew/bin/python3.11 crypto_report.py
```

**激活自动调度**

1. 将样板文件直接拷贝到 LaunchAgents 作为正式配置（单一权威源，不在项目目录留副本）：
```bash
cp com.shirley.crypto-daily-bot.plist.example ~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist
```
2. **重要**：编辑 `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist`，填入你的 API Key、路径和代理端口。以后改端口/密钥也改这一份。
3. 拷贝健康检查配置并加载两个任务：
```bash
cp com.shirley.crypto-daily-bot-health.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist
launchctl load ~/Library/LaunchAgents/com.shirley.crypto-daily-bot-health.plist
```

**验证调度状态**
```bash
launchctl list | grep shirley
tail -5 run.log
```

---

## 依赖安装

```bash
pip3.11 install requests feedparser openai
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
