#!/bin/bash
# health_check.sh — Crypto Daily Bot
# 薄封装：设好本 bot 的差异点后，交给同级 shared/health_check_base.sh 执行。
# 完整流程与踩坑记录见那个文件，别把逻辑抄回来。

DIR="$(cd "$(dirname "$0")" && pwd)"

BOT_NAME="Crypto Daily Bot"
MAIN_PLIST="$HOME/Library/LaunchAgents/com.shirley.crypto-daily-bot.plist"

# 终态 WARN 的匹配模式。⚠️ 必须与 crypto_report.py 里 write_log("WARN", ...) 的实际措辞
# 一致——对不上就会把"正常的没东西可播"误判成缺跑，白派一次无头补跑。
NO_NEWS_PATTERN="无有效新闻"
NO_NEWS_MSG="今天无有效新闻，未出稿（非故障）"

STALE_KEY="rss_stale_sources"
ZERO_KEY="rss_zero_sources"

# 第 4 节：BTC 价格缺失说明 CoinGecko 那边出了问题
content_check() {
    local btc
    btc=$(jsonl_field btc ok)
    if [ "$btc" = "未知" ]; then
        notify WARN "BTC 价格数据缺失，请检查 CoinGecko API Key"
        echo "[health_check] WARN: BTC 价格数据缺失"
    fi
}

source "$DIR/../shared/health_check_base.sh"
