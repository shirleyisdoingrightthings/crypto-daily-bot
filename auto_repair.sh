#!/bin/bash
# auto_repair.sh — Crypto Daily Bot
# 检测到 [FAIL] 时由 health_check.sh 触发
# 修复逻辑统一维护在 ~/Desktop/bot_ops/auto_repair_base.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_NAME="Crypto Daily Bot"
SCRIPT="$DIR/crypto_report.py"
ERROR="${1:-unknown error}"

source "$HOME/Desktop/bot_ops/auto_repair_base.sh"
