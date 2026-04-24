#!/bin/bash
# health_check.sh — Crypto Daily Bot
# 功能：
#   1. 检查今天是否有 [OK] 记录（基于日期，而非最后一行）
#      └─ 若今天有 [FAIL] → 触发 auto_repair.sh
#      └─ 若今天无任何记录（脚本可能仍在运行）→ 等待 60s 后重判
#      └─ 等待后仍无记录 → WARN 通知人工介入
#   2. 成功时核销 changelog.md 中已修复的条目（连续 3 次 OK 后删除）

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/run.log"
CHANGELOG="$DIR/changelog.md"
OK_COUNT_FILE="$DIR/.ok_streak"
TODAY=$(date '+%Y-%m-%d')

# ── 1. 检查 run.log 是否存在 ─────────────────────────────────────────
if [ ! -f "$LOG" ]; then
    osascript -e 'display notification "run.log 不存在，脚本可能从未运行" with title "⚠️ Crypto Daily Bot"'
    exit 1
fi

# ── 2. 判断今天的运行状态（基于日期，而不是 tail -1）────────────────
get_today_status() {
    if grep -q "$TODAY.*\[OK\]" "$LOG"; then
        echo "OK"
    elif grep -q "$TODAY.*\[FAIL\]" "$LOG"; then
        echo "FAIL"
    else
        echo "MISSING"
    fi
}

STATUS=$(get_today_status)

# 若今天无记录，等待 60s 再判一次（应对补跑竞态：脚本可能仍在运行中）
if [ "$STATUS" = "MISSING" ]; then
    echo "[health_check] 今天暂无运行记录，等待 60s 后重判（可能为补跑中）..."
    sleep 60
    STATUS=$(get_today_status)
fi

# ── 3. 根据状态分支处理 ───────────────────────────────────────────────
if [ "$STATUS" = "FAIL" ]; then
    ERR_LINE=$(grep "$TODAY.*\[FAIL\]" "$LOG" | tail -1)
    ERR=$(echo "$ERR_LINE" | sed 's/.*\[FAIL\]  //')
    SHORT=$(echo "$ERR" | cut -c1-120)
    TS=$(echo "$ERR_LINE" | cut -c1-16)

    if [ ! -f "$CHANGELOG" ]; then
        echo "# Changelog — Crypto Daily Bot" > "$CHANGELOG"
        echo "" >> "$CHANGELOG"
        echo "> 格式：[ ] 待处理 · [/] 修复中 · [x] 待验证（连续3次OK后自动删除）" >> "$CHANGELOG"
        echo "" >> "$CHANGELOG"
    fi
    if ! tail -10 "$CHANGELOG" | grep -qF "$SHORT"; then
        echo "- [ ] \`$TS\` $SHORT" >> "$CHANGELOG"
    fi

    echo "0" > "$OK_COUNT_FILE"
    echo "[health_check] FAIL 检测到，触发 auto_repair..."
    bash "$DIR/auto_repair.sh" "$ERR" &
    echo "[health_check] FAIL — $ERR_LINE"
    exit 2

elif [ "$STATUS" = "MISSING" ]; then
    osascript -e 'display notification "今天主脚本未运行，请检查 launchd 配置" with title "⚠️ Crypto Daily Bot"'
    echo "[health_check] WARN: 今天（$TODAY）无任何运行记录，人工介入"
    exit 1
fi

# ── 4. 今天 OK：内容质量校验（BTC 价格是否缺失）────────────────────
JSONL="$DIR/run.jsonl"
if [ -f "$JSONL" ]; then
    LAST_BTC=$(grep "$TODAY" "$JSONL" | tail -1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('btc','ok'))" 2>/dev/null)
    if [ "$LAST_BTC" = "未知" ]; then
        osascript -e 'display notification "BTC 价格数据缺失，请检查 CoinGecko API Key" with title "⚠️ Crypto Daily Bot"'
        echo "[health_check] WARN: BTC 价格数据缺失"
    fi
fi

# ── 5. 更新 OK streak，核销 changelog ────────────────────────────────
STREAK=0
if [ -f "$OK_COUNT_FILE" ]; then
    STREAK=$(cat "$OK_COUNT_FILE")
fi
STREAK=$((STREAK + 1))
echo "$STREAK" > "$OK_COUNT_FILE"

OK_LINE=$(grep "$TODAY.*\[OK\]" "$LOG" | tail -1)
echo "[health_check] OK (streak=$STREAK) — $OK_LINE"

if [ "$STREAK" -ge 3 ] && [ -f "$CHANGELOG" ]; then
    BEFORE=$(wc -l < "$CHANGELOG")
    grep -v "^\- \[x\]" "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"
    AFTER=$(wc -l < "$CHANGELOG")
    REMOVED=$((BEFORE - AFTER))
    if [ "$REMOVED" -gt 0 ]; then
        echo "[health_check] 已核销 $REMOVED 条已修复条目"
        echo "0" > "$OK_COUNT_FILE"
    fi
fi

exit 0
