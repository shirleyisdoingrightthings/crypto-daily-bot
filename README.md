# Crypto Daily Bot

每天早上由 Claude 写稿：拉 6 大主流币价格、恐惧贪婪指数、赛道资金流向，加上 3 家加密
媒体的当日头条，生成《市场晨报》+《新闻播报》推送到飞书。全程零第三方大模型 API——
脚本只负责抓取与发送，稿子由本地 Claude 定时任务按 prompt 写。

## 数据来源

| 类型 | 数据源 |
|---|---|
| 实时价格 | BTC / ETH / SOL / BNB / XRP / HYPE（CoinGecko） |
| 市场情绪 | 恐惧贪婪指数（alternative.me） |
| 资金流向 | 总市值 / BTC 市占率 / DeFi 占比 / 24h 赛道涨幅 Top 5 |
| 行业新闻 | Cointelegraph · CoinDesk · Decrypt |

## 主要行为

- 两条消息分开推送：消息①市场晨报（`prompt_analysis.md`），消息②新闻播报（`prompt_news.md`）。
- 不同频率：行情每天播（价格易腐），新闻每 3 天播一次。新闻的时间窗本就是 3 天，
  每天播会让同一条新闻有 3 次入选机会。非新闻日 send 只发消息①，并会校验磁盘上那份
  旧新闻稿的新鲜度，不会误发几天前的内容。
- 取材：新闻 best-effort 抓正文全文，失败回退 RSS 摘要。CoinDesk 等源的 RSS 摘要经常
  为空，抓正文在这里收益最大。
- 跨天去重：`send` 成功后归档稿件里用到的链接（留 7 天），下次抓取排除。
- 分页：超过飞书 webhook 单条 20KB 上限时按段落切分并标 `(n/N)` 页码。

## 工作流

```
CoinGecko（价格/总市值/DeFi/赛道/热搜）──┐
alternative.me（恐惧贪婪指数）           ├─▶ crypto_report.py --mode fetch
RSS × 3（Cointelegraph/CoinDesk/Decrypt）┘     │  ├─ URL 去重 + 跨天去重
                                               │  ├─ 3 天时间窗
                                               │  └─ 并发抓正文，抓不到回退摘要
                                               ▼
                              Claude 写两稿 → logs/report_analysis.txt
                                            → logs/report_news.txt
                                               ▼
                              crypto_report.py --mode send
                                               │  清洗 HTML，依次发送（含部分发送保护）
                                               │  超长分页 + (n/N)
                                               ▼
                                         飞书「加密日报」

10:00  Claude 定时任务（唯一写稿入口）
11:00  launchd → health_check.sh
         [OK]    .ok_streak +1，连续 3 次清理 changelog
         [无记录] claude_catchup.sh 无头补跑
         [FAIL]  记 changelog → auto_repair.sh
                   Level 1 等 30s 重跑 → Level 2 claude CLI 诊断 → 兜底无头补跑
```

## 源健康与淘汰

判定口径是过滤后零产，而不是"RSS 拉到 0 条"——源可能天天拉得到、却条条被过滤。
`fetch` 记录每个源的 `{fetched, kept}`，`kept == 0` 计入 `logs/.zero_streak.json`，
连续 3 天即输出 `=== SOURCE_ALERT ===`。连续天数由 `fetch` 单点写入，health_check
只读不写（两处各加会让天数翻倍）。收到告警后从 `RSS_SOURCES` 删掉或换掉即可。

> 本 bot 只有 3 个新闻源、时间窗 3 天，而 limit 之和恰好等于每日入选数——实测"超 3 天"
> 过滤命中 0，说明时间窗从未真正生效，limit 才是唯一约束。真正在防重复的是跨天去重档案。

## 文件结构

```
crypto_report.py       主脚本：--mode fetch（行情+新闻+抓正文）/ send（依次推送两稿）
claude_report.sh       供定时任务调用的封装，从 plist 加载环境变量
prompt_analysis.md     消息①市场晨报的写稿规范
prompt_news.md         消息②新闻播报的写稿规范
health_check.sh        体检薄封装，逻辑在 ../shared/health_check_base.sh
auto_repair.sh         自愈薄封装，逻辑在 ../shared/auto_repair_base.sh
claude_catchup.sh      无头补跑薄封装，逻辑在 ../shared/headless_catchup_base.sh
changelog.md           问题追踪，与 health_check 联动
logs/                  运行时生成，不预置
  report_analysis.txt    消息①（send 读取）
  report_news.txt        消息②（send 读取）
  run.log / run.jsonl    单行摘要 / 结构化指标
  sent_urls.json         跨天去重档案（留 7 天）
  .zero_streak.json      各源连续零产天数
  last_context.txt       最近一次 fetch 的完整输出
```

## 环境变量

写在 `~/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist`，`claude_report.sh`
每次运行时直接读文件，改完即生效，不用重载 launchd。仓库里只留 `.plist.example` 模板。
该 plist 不承担调度，只作为配置源。

| 变量 | 说明 |
|---|---|
| `FEISHU_WEBHOOK` | 飞书机器人 webhook |
| `FEISHU_ALERT_WEBHOOK` | 运维告警走的监测机器人，不进日报群 |
| `FEISHU_SECRET` | 签名密钥，未开签名校验则留空 |
| `COINGECKO_API_KEY` | CoinGecko Demo Key |
| `HTTPS_PROXY` / `HTTP_PROXY` | 本地代理 |

## 用法

```bash
bash claude_report.sh fetch     # 抓行情+新闻，把素材打到 stdout
bash claude_report.sh send      # 读两稿，清洗后依次推送
```

调试：

```bash
tail -5 logs/run.log                          # 最近运行状态
tail -3 logs/run.jsonl | python3 -m json.tool # 结构化指标
bash health_check.sh                          # 手动体检
```

依赖：`pip3 install requests feedparser`

详细操作规范见 [`AGENTS.md`](./AGENTS.md)；换机与排障见 [TROUBLESHOOTING.md](https://github.com/shirleyisdoingrightthings/bot-ops/blob/main/TROUBLESHOOTING.md)。
