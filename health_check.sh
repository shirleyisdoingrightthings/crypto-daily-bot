#!/bin/bash
# health_check.sh — Crypto Daily Bot
# 功能：
#   1. 检查今天是否有 [OK] 记录（基于日期，而非最后一行）
#      └─ 若今天有 [FAIL] → 触发 auto_repair.sh
#      └─ 若今天无任何记录（脚本可能仍在运行）→ 等待 60s 后重判
#      └─ 等待后仍无记录 → WARN 通知人工介入
#   2. 成功时核销 changelog.md 中已修复的条目（连续 3 次 OK 后删除）

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$DIR/logs/run.log"
CHANGELOG="$DIR/changelog.md"
OK_COUNT_FILE="$DIR/logs/.ok_streak"
TODAY=$(date '+%Y-%m-%d')
HOUR=$(date '+%H'); HOUR=${HOUR#0}; HOUR=${HOUR:-0}

# 无头补跑的时间窗。RunAtLoad 打开后，本脚本会在每次开机/登录时也跑一遍，
# 没有这个窗口就会出现两种误触发：
#   · 早上 8 点开机 → 10:00 的 routine 还没到点，却被判成"今天没跑"而抢先补跑
#   · 深夜 23 点开机 → 补出一份当天已经没人看的稿子，白烧 token
# 窗口外只通知、不补跑。10:00 的 routine + 11:00 的定时体检都落在窗口内。
CATCHUP_FROM=11
CATCHUP_UNTIL=20
# 缺跑回看天数：只用来"告诉你哪几天彻底没跑"，不触发任何补救动作，也不推送告警
# （见下面 1.5 节；.missed_notified 时间戳已随推送一起废弃）
MISSED_LOOKBACK=7

# ── 0. 告警通道：桌面通知 + 飞书 ─────────────────────────────────────
# 桌面通知只在人坐在电脑前时有效。2026-08-30 查出三个 health job 因 EX_CONFIG
# 连续 11 天没跑成，而唯一的告警渠道恰好也是最看不见的那个——故障本身把报警器
# 一起带走了。飞书是已经在手的送达渠道，这里让每条告警同时走两边。
BOT_NAME="Crypto Daily Bot"
ALERT_PY="$DIR/../shared/alert.py"
MAIN_PLIST="$HOME/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist"

# 只从主 plist 取 FEISHU_* ——那里还放着 PATH 等键，整份 export 会覆盖体检
# 自己的 PATH，进而影响 claude_catchup 找 claude 可执行文件。
# 想把运维消息分流到单独的群，在主 plist 里加 FEISHU_ALERT_WEBHOOK 即可。
if [ -f "$MAIN_PLIST" ]; then
    while IFS= read -r kv; do export "$kv"; done < <(
        /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables" "$MAIN_PLIST" 2>/dev/null \
        | sed -n 's/^[[:space:]]*\(FEISHU[A-Z_]*\) = \(.*\)$/\1=\2/p')
fi

# notify <FAIL|WARN|INFO> <正文>
notify() {
    local level="$1" msg="$2" icon
    case "$level" in
        FAIL) icon="🔴" ;;
        WARN) icon="⚠️" ;;
        *)    icon="ℹ️" ;;
    esac
    osascript -e "display notification \"$msg\" with title \"$icon $BOT_NAME\"" 2>/dev/null
    # 告警发不出去是小事，让体检因此中断是大事，故整条容错
    [ -f "$ALERT_PY" ] && /usr/bin/python3 "$ALERT_PY" "$BOT_NAME" "$level" "$msg" 2>&1 \
        | grep -v NotOpenSSLWarning | grep -v "warnings.warn" || true
    return 0
}

# ── 1. 检查 run.log 是否存在 ─────────────────────────────────────────
if [ ! -f "$LOG" ]; then
    notify FAIL "run.log 不存在，脚本可能从未运行"
    exit 1
fi

# ── 1.5 缺跑扫描：最近 N 天里哪几天 run.log 一行记录都没有 ────────────
# 「一行都没有」= 那天机器没开 / 进程压根没起来，与「跑了但跳过」（WARN 有记录）
# 是两回事。这类静默缺失过去无人知晓：2026-08-28、08-29 两天全丢，直到 08-30
# 写周回顾时才从存档少了两份发现。这里只做告知，不做补救——过期的日报没有补的意义。
#
# ⚠️ 2026-09-03：这一段**只写本地日志，不再推送告警**。原来它每天把整个 7 天窗口
# 里的缺跑日期推一遍，同一批旧日期（08-28、08-29）会连推 7 天才滚出窗口，读起来
# 像「今天又出问题了」，而当天其实跑得好好的。当天缺跑不需要靠这里发现——下面
# 第 2/3 节的 MISSING 分支本来就会在补跑窗口内发一条「今天主脚本未运行」。
# 所以这里保留扫描（排查时看 health_check.log 仍能知道哪几天丢了），去掉推送。
# 不要因为「历史缺跑没人告诉我」把 notify 加回来——要加也只加当天那一天。
MISSED=""
for i in $(seq 1 "$MISSED_LOOKBACK"); do
    D=$(date -v-"${i}"d '+%Y-%m-%d' 2>/dev/null) || break
    grep -q "^$D" "$LOG" || MISSED="$D${MISSED:+, }$MISSED"
done
if [ -n "$MISSED" ]; then
    echo "[health_check] 缺跑（仅本地记录，不推送）：最近 $MISSED_LOOKBACK 天内这些日期无任何运行记录 — $MISSED"
fi

# ── 2. 判断今天的运行状态（基于日期，而不是 tail -1）────────────────
get_today_status() {
    if grep -q "$TODAY.*\[OK\]" "$LOG"; then
        echo "OK"
    elif grep -q "$TODAY.*\[FAIL\]" "$LOG"; then
        echo "FAIL"
    elif grep -q "$TODAY.*\[WARN\].*无有效新闻" "$LOG"; then
        # 终态 WARN：当天确实没有值得播的新闻，不是故障。
        # 补跑也只会再抓一次同样的空结果，白烧 token 还弹"需人工介入"。
        # 注意：代理不可用 / 行情数据缺失这两类 WARN 不在此列——它们是暂时性的，
        # 到体检时刻可能已恢复，仍按 MISSING 处理以触发补跑。
        echo "NO_NEWS"
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
    echo "[health_check] FAIL — $ERR_LINE"
    # 前台执行：launchd 在 job 主进程退出时会回收整个进程组，
    # 用 `&` 起的后台子进程会被立即杀掉（2026-07-23 修复）
    bash "$DIR/auto_repair.sh" "$ERR"
    exit 2

elif [ "$STATUS" = "MISSING" ]; then
    # 10:00 routine 今天未运行（机器睡眠 / App 未开等）
    # → 触发无头补跑（自动版 Run Now），由 claude CLI 完整重走 fetch → 写稿 → send
    if [ "$HOUR" -lt "$CATCHUP_FROM" ] || [ "$HOUR" -ge "$CATCHUP_UNTIL" ]; then
        # 窗口外（多半是 RunAtLoad 在清早或深夜触发的这一次）：不补跑，只留一行记录。
        # 清早不补是因为 10:00 的 routine 还没轮到；深夜不补是因为稿子已经没人看。
        echo "[health_check] 今天（$TODAY）无运行记录，但当前 ${HOUR} 点不在补跑窗口 ${CATCHUP_FROM}-${CATCHUP_UNTIL} 点内，跳过补跑"
        exit 0
    fi
    notify WARN "今天主脚本未运行，已触发无头补跑"
    echo "[health_check] WARN: 今天（$TODAY）无任何运行记录，触发无头补跑..."
    # 前台执行，理由同 auto_repair 分支（launchd 进程组回收）
    bash "$DIR/claude_catchup.sh"
    exit 1
fi

if [ "$STATUS" = "NO_NEWS" ]; then
    # 当天无有效新闻：正常终态，不补跑、不自愈、不计入 OK streak
    notify INFO "今天无有效新闻，未出稿（非故障）"
    echo "[health_check] NO_NEWS: 今天（$TODAY）无有效新闻，属正常终态，不触发补跑"
    exit 0
fi

# ── 4. 今天 OK：内容质量校验（BTC 价格是否缺失）────────────────────
JSONL="$DIR/logs/run.jsonl"
if [ -f "$JSONL" ]; then
    LAST_BTC=$(grep "$TODAY" "$JSONL" | tail -1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('btc','ok'))" 2>/dev/null)
    if [ "$LAST_BTC" = "未知" ]; then
        notify WARN "BTC 价格数据缺失，请检查 CoinGecko API Key"
        echo "[health_check] WARN: BTC 价格数据缺失"
    fi
fi

# ── 5. 分源监控：读取 fetch 阶段算好的"连续零产"结论 ──────────────
# 连续天数由 *_report.py 的 fetch 单点维护（logs/.zero_streak.json），
# 这里只读不写——两处各加一次会让天数翻倍。
if [ -f "$JSONL" ]; then
    STALE=$(grep "$TODAY" "$JSONL" | tail -1 | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    st = d.get('rss_stale_sources', {}) or {}
    print(', '.join(f'{k}({v}天)' for k, v in sorted(st.items())))
except Exception: print('')
" 2>/dev/null)
    if [ -n "$STALE" ]; then
        notify WARN "RSS 源连续零产，建议移除：$STALE"
        echo "[health_check] WARN: RSS 源连续零产，建议移除或更换: $STALE"
    fi

    ZERO_TODAY=$(grep "$TODAY" "$JSONL" | tail -1 | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(','.join(d.get('rss_zero_sources', []) or []))
except Exception: print('')
" 2>/dev/null)
    if [ -n "$ZERO_TODAY" ]; then
        echo "[health_check] INFO: 今日零产源（未达连续 3 天，暂不告警）: $ZERO_TODAY"
    fi
fi

# ── 6. 更新 OK streak，核销 changelog ────────────────────────────────
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
