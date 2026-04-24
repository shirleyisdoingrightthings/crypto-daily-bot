#!/bin/bash
# health_check.sh — Crypto Daily Bot
# 功能：
#   1. 检查 run.log 最后一行是否失败
#      └─ 触发 auto_repair.sh（Level 1 重跑 / Level 2 Claude 修复）
#      └─ auto_repair 失败时才需要人工介入
#   2. 成功时核销 changelog.md 中已修复的条目（连续 3 次 OK 后删除）
#
# 用法：脚本执行完成后 ~30 分钟触发（launchd / cron）

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/run.log"
CHANGELOG="$DIR/changelog.md"
OK_COUNT_FILE="$DIR/.ok_streak"   # 记录连续成功次数

# ── 1. 检查 run.log ──────────────────────────────────────────────────
if [ ! -f "$LOG" ]; then
    osascript -e 'display notification "run.log 不存在，脚本可能从未运行" with title "⚠️ Crypto Daily Bot"'
    exit 1
fi

LAST=$(tail -1 "$LOG")
TS=$(echo "$LAST" | cut -c1-16)   # 前16位是时间戳

# ── 2. 失败处理：先写 changelog，再触发 auto_repair ─────────────────
if echo "$LAST" | grep -q "\[FAIL\]"; then
    ERR=$(echo "$LAST" | sed 's/.*\[FAIL\]  //')
    SHORT=$(echo "$ERR" | cut -c1-120)

    # 写入 changelog.md（如果同类错误不存在则新增）
    if [ ! -f "$CHANGELOG" ]; then
        echo "# Changelog — Crypto Daily Bot" > "$CHANGELOG"
        echo "" >> "$CHANGELOG"
        echo "> 格式：[ ] 待处理 · [/] 修复中 · [x] 待验证（连续3次OK后自动删除）" >> "$CHANGELOG"
        echo "" >> "$CHANGELOG"
    fi
    if ! tail -10 "$CHANGELOG" | grep -qF "$SHORT"; then
        echo "- [ ] \`$TS\` $SHORT" >> "$CHANGELOG"
    fi

    # 重置连续成功计数
    echo "0" > "$OK_COUNT_FILE"

    echo "[health_check] FAIL 检测到，触发 auto_repair..."
    # 调用自动修复代理（后台运行，不阻塞 health_check）
    bash "$DIR/auto_repair.sh" "$ERR" &

    echo "[health_check] FAIL — $LAST"
    exit 2
fi

# ── 3. 成功处理：更新 OK streak，核销 changelog ──────────────────────
STREAK=0
if [ -f "$OK_COUNT_FILE" ]; then
    STREAK=$(cat "$OK_COUNT_FILE")
fi
STREAK=$((STREAK + 1))
echo "$STREAK" > "$OK_COUNT_FILE"

echo "[health_check] OK (streak=$STREAK) — $LAST"

# 连续 3 次成功：核销 changelog 中标记为 [x] 的条目
if [ "$STREAK" -ge 3 ] && [ -f "$CHANGELOG" ]; then
    # 删除包含 [x] 的条目行
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
