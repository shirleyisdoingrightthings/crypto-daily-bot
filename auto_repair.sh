#!/bin/bash
# auto_repair.sh — Crypto Daily Bot
# 检测到 [FAIL] 时由 health_check.sh 触发
# 修复逻辑统一维护在同级 shared/auto_repair_base.sh（路径由 $DIR 相对推导）
# （2026-07-20 起从 ~/Desktop/bot_ops/ 迁入，旧文件不再引用）

DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_NAME="Crypto Daily Bot"
SCRIPT="$DIR/crypto_report.py"
ERROR="${1:-unknown error}"

# 稿件新鲜度检查的对象随"本次是否含新闻"而变：行情每天播，新闻每 3 天播一次。
# 非新闻日磁盘上的 report_news.txt 是几天前的旧稿，若把它列进 DRAFTS，
# drafts_fresh() 会因它过期而误判"当日稿件缺失"，白白触发一次无头补跑。
DRAFTS="logs/report_analysis.txt"
if [ "$(python3 -c "
import json,sys
try:
    d = json.load(open('$DIR/logs/fetch_meta.json'))
    print('1' if d.get('metrics', {}).get('news_included', True) else '0')
except Exception:
    print('1')
" 2>/dev/null)" = "1" ]; then
    DRAFTS="$DRAFTS logs/report_news.txt"
fi

source "$DIR/../shared/auto_repair_base.sh"
