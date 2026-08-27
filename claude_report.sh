#!/bin/bash
# claude_report.sh — 供 Claude routine 调用的 fetch/send 封装
#
# 用途：把 launchd plist 里的环境变量（代理、飞书 webhook、CoinGecko key）加载好，
#       再以指定模式运行 crypto_report.py。密钥统一来自 LaunchAgents 权威 plist，
#       避免在 routine prompt 里重复贴 PlistBuddy 逻辑。
# 用法：
#   bash claude_report.sh fetch   # 抓取行情+新闻并把 context 打到 stdout（供 Claude 写两稿）
#   bash claude_report.sh send    # 读取 logs/report_analysis.txt + report_news.txt 并依次推送飞书
# 密钥：运行时从 ~/Library/LaunchAgents 的权威 plist 读取，脚本本身不含密钥。

set -uo pipefail
cd "$(dirname "$0")" || exit 1

MODE="${1:-}"
if [ "$MODE" != "fetch" ] && [ "$MODE" != "send" ]; then
    echo "ERROR: 用法 claude_report.sh fetch|send" >&2
    exit 2
fi

PLIST="$HOME/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist"
PY="/usr/bin/python3"

if [ ! -f "$PLIST" ]; then
    echo "ERROR: 找不到 $PLIST，无法加载环境变量" >&2
    exit 1
fi

# 从 plist 加载环境变量（单一密钥来源，不在本脚本中重复）
while IFS= read -r line; do
    export "$line"
done < <(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables" "$PLIST" \
    | sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\) = \(.*\)$/\1=\2/p')

exec "$PY" crypto_report.py --mode "$MODE"
