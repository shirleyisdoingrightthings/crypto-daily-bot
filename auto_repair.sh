#!/bin/bash
# auto_repair.sh — Crypto Daily 自动修复代理
#
# 调用方：health_check.sh（检测到 [FAIL] 时触发）
# 输入参数：$1 = 错误信息字符串
#
# 修复策略：
#   Level 1 — 瞬时错误（SSL/Timeout）：直接重跑，不调用 Claude
#   Level 2 — 持久错误 / Level 1 重跑仍失败：调用 claude CLI 修复后重跑

DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$DIR/crypto_report.py"
PYTHON="/opt/homebrew/bin/python3.11"
CHANGELOG="$DIR/changelog.md"
ERROR="${1:-unknown error}"

# 瞬时错误关键词
TRANSIENT_PATTERNS="SSLError|ReadTimeout|ConnectionError|TimeoutError|ConnectTimeout"

log() { echo "[auto_repair $(date '+%H:%M:%S')] $*"; }

# ── 工具函数：更新 changelog 条目状态 ──────────────────────────────
changelog_update() {
    local pattern="$1"   # 要匹配的错误片段
    local new_status="$2"  # 替换后的状态标记
    if [ -f "$CHANGELOG" ]; then
        # 将匹配行的 [ ] 或 [/] 替换为新状态
        sed -i '' "/$pattern/ s/^\- \[.\]/- $new_status/" "$CHANGELOG"
    fi
}

changelog_append() {
    local entry="$1"
    if [ -f "$CHANGELOG" ]; then
        echo "$entry" >> "$CHANGELOG"
    fi
}

# ── Level 1：瞬时错误 → 直接重跑 ──────────────────────────────────
if echo "$ERROR" | grep -qE "$TRANSIENT_PATTERNS"; then
    log "检测到瞬时网络错误，等待 30s 后直接重跑..."
    sleep 30
    log "重跑主脚本..."
    "$PYTHON" "$SCRIPT"
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        log "✅ Level 1 重跑成功"
        SHORT=$(echo "$ERROR" | cut -c1-80)
        changelog_update "$SHORT" "[x]"
        changelog_append "  - \`$(date '+%Y-%m-%d %H:%M')\` AUTO-FIXED (transient retry)"
        exit 0
    else
        log "Level 1 重跑仍失败，升级到 Level 2（Claude 修复）"
    fi
fi

# ── Level 2：调用 claude CLI 自动修复 ──────────────────────────────
log "调用 Claude 进行自动修复..."

REPAIR_PROMPT="你是 Crypto Daily 工作流的自动修复代理。

当前错误：$ERROR

请按以下步骤操作：
1. 读取 CLAUDE.md 了解工作流规范和禁区
2. 读取 run.log 最近 10 行了解错误上下文
3. 读取 crypto_report.py 定位问题
4. 在不违反 CLAUDE.md 禁区的前提下，修复问题
5. 修复完成后，输出一行总结：FIX: <你做了什么>

注意：
- 只修复导致本次失败的最小范围
- 不要修改 Prompt 内容和 HTML 格式
- 不要修改日志格式（health_check.sh 依赖它）
- 如果无法确定根因，不要修改，输出 CANNOT_FIX: <原因>"

# 调用 claude CLI（非交互模式）
FIX_RESULT=$(claude --allowedTools "Read,Edit,Bash" -p "$REPAIR_PROMPT" 2>&1)
CLAUDE_EXIT=$?

log "Claude 返回：$(echo "$FIX_RESULT" | tail -3)"

if echo "$FIX_RESULT" | grep -q "CANNOT_FIX:"; then
    REASON=$(echo "$FIX_RESULT" | grep "CANNOT_FIX:" | head -1)
    log "⚠️  Claude 无法自动修复：$REASON"
    changelog_update "$(echo "$ERROR" | cut -c1-60)" "[/]"
    changelog_append "  - \`$(date '+%Y-%m-%d %H:%M')\` AUTO-REPAIR SKIPPED: $REASON → 需要人工介入"
    osascript -e "display notification \"需要人工介入：$REASON\" with title \"⚠️ Crypto Daily 修复失败\""
    exit 3
fi

# Claude 修复后重跑
log "Claude 修复完成，重跑主脚本验证..."
"$PYTHON" "$SCRIPT"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    FIX_SUMMARY=$(echo "$FIX_RESULT" | grep "^FIX:" | head -1)
    log "✅ Level 2 修复并重跑成功：$FIX_SUMMARY"
    SHORT=$(echo "$ERROR" | cut -c1-80)
    changelog_update "$SHORT" "[x]"
    changelog_append "  - \`$(date '+%Y-%m-%d %H:%M')\` AUTO-FIXED (Claude): $FIX_SUMMARY"
    osascript -e "display notification \"已自动修复并重跑成功\" with title \"✅ Crypto Daily Auto-Repair\""
    exit 0
else
    log "❌ Claude 修复后重跑仍失败"
    changelog_update "$(echo "$ERROR" | cut -c1-60)" "[/]"
    changelog_append "  - \`$(date '+%Y-%m-%d %H:%M')\` AUTO-REPAIR FAILED → 需要人工介入"
    osascript -e "display notification \"自动修复失败，需要人工介入\" with title \"❌ Crypto Daily\""
    exit 4
fi
