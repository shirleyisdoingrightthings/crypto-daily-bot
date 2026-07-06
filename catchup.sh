#!/bin/bash
# catchup.sh — 补跑当天未成功的 Crypto 日报
#
# 用途：8:00 的 launchd 定时若因为电脑睡眠/未开机而漏跑，
#       由 Claude Routine 在上午调用本脚本补跑一次。
# 幂等：当天 run.log 已有 [OK] 则跳过，避免重复推送。
# 密钥：运行时从 launchd 实际加载的 plist 读取环境变量，脚本本身不含密钥。
#       复用 ~/Library/LaunchAgents 那份权威副本，避免与目录内副本端口/密钥不一致。

set -uo pipefail
cd "$(dirname "$0")" || exit 1

PLIST="$HOME/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist"
PYFILE="crypto_report.py"
PY="/usr/bin/python3"
TODAY="$(date +%Y-%m-%d)"

# 1) 当天已成功则跳过
if grep -q "^${TODAY} .*\[OK\]" logs/run.log 2>/dev/null; then
    echo "SKIP: ${TODAY} 已有成功记录，无需补跑"
    exit 0
fi

# 2) 从 plist 加载环境变量（单一密钥来源，不在本脚本中重复）
if [ ! -f "$PLIST" ]; then
    echo "ERROR: 找不到 $PLIST，无法加载环境变量"
    exit 1
fi
while IFS= read -r line; do
    export "$line"
done < <(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables" "$PLIST" \
    | sed -n 's/^[[:space:]]*\([A-Za-z_][A-Za-z0-9_]*\) = \(.*\)$/\1=\2/p')

# 3) 补跑主脚本（脚本内部会写 run.log 并推送 Telegram）
echo "CATCHUP: ${TODAY} 未成功，开始补跑 $PYFILE ..."
"$PY" "$PYFILE"
rc=$?
echo "DONE: 退出码 $rc"
echo "LASTLOG: $(tail -1 logs/run.log 2>/dev/null)"
exit $rc
