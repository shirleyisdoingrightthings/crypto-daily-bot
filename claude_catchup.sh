#!/bin/bash
# claude_catchup.sh — Crypto Daily Bot
# 当天未成功出稿时的无头补跑（自动版 Run Now）
# 由 health_check.sh（MISSING 分支）或 auto_repair 最终兜底触发
# 补跑逻辑统一维护在 ~/bots/shared/headless_catchup_base.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_NAME="Crypto Daily Bot"
PLIST="$HOME/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist"
WRITE_SPEC="严格按 prompt_analysis.md 与 prompt_news.md 分别写出消息①与消息②，写入 logs/report_analysis.txt 与 logs/report_news.txt"

source "$HOME/bots/shared/headless_catchup_base.sh"
