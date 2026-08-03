#!/usr/bin/python3
"""
Crypto 市场晨报
数据源：
  · CoinGecko: BTC/ETH/SOL/BNB/XRP/HYPE 价格、趋势币 Top5、全球市值、DeFi 数据、赛道热力图
  · alternative.me: 恐惧贪婪指数
  · RSS × 3: Cointelegraph / CoinDesk / Decrypt（新闻 best-effort 抓正文全文，失败回退摘要）
由 Claude 写稿：① 市场晨报（仪表盘+趋势+叙事）② 新闻播报列表，推送 2 条 Telegram HTML 消息。
本脚本只负责抓取与发送，不含写稿用的第三方大模型 API。

两种运行模式（--mode，均零 API 成本）：
- fetch：抓取全部行情+新闻（含抓正文）→ 把 context 打到 stdout + 写 logs/fetch_meta.json（供 Claude 写稿）
- send ：读取 Claude 写好的两份稿子 → 依次发送 Telegram + 写日志

写稿规范存放于 prompt_analysis.md（消息①）与 prompt_news.md（消息②），由 Claude routine 读取。
"""

import os
import sys
import time
import json
import argparse
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 共享工具库
# 从脚本自身位置推导共享层（bot 目录的同级 shared/），
# 这样整个 bots 文件夹搬到任何位置都不用改路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from bot_utils import (sanitize_html, with_retry, fetch_rss, parse_entry_date,
                       already_ran_today, fetch_article_text,
                       url_key, load_sent_urls, record_sent_urls, extract_hrefs,
                       paginate_telegram, update_zero_streak, resolve_proxy)

LOG_FILE   = Path(__file__).parent / "logs" / "run.log"
JSONL_FILE = Path(__file__).parent / "logs" / "run.jsonl"
LOG_FILE.parent.mkdir(exist_ok=True)
CACHE_FILE = Path(__file__).parent / "pending_messages.json"

# Claude routine 把写好的两份稿子分别存到这里，再用 --mode send 发送
DRAFT_ANALYSIS = Path(__file__).parent / "logs" / "report_analysis.txt"   # 消息①市场晨报
DRAFT_NEWS     = Path(__file__).parent / "logs" / "report_news.txt"       # 消息②新闻播报
# fetch 模式写出、send 模式读回的边车：承载 OK 日志摘要与 health_check 所需 metrics
FETCH_META     = Path(__file__).parent / "logs" / "fetch_meta.json"
# 跨天去重档案：send 成功后记录稿件里实际用到的链接，fetch 时据此排除。
# Crypto 的时间窗是 3 天而每天跑一次，没有这层同一条新闻会连播多天。
SENT_URLS      = Path(__file__).parent / "logs" / "sent_urls.json"
# RSS 源连续零产计数（fetch 阶段唯一写入，health_check 只读）
ZERO_STREAK    = Path(__file__).parent / "logs" / ".zero_streak.json"
# 连续零产多少天就判定该源可以移除
ZERO_STREAK_THRESHOLD = 3

# ===== P0: 显式代理配置 =====
_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
SESSION = requests.Session()
SESSION.proxies = {"http": _PROXY, "https": _PROXY}
# feedparser 内部使用 urllib，通过环境变量注入代理
if _PROXY:
    os.environ.setdefault("HTTP_PROXY",  _PROXY)
    os.environ.setdefault("HTTPS_PROXY", _PROXY)


# ===== P1: 结构化日志 =====
def write_log(status: str, message: str, metrics: dict = None) -> None:
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"{ts}  [{status}]  {message}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")
    if metrics:
        record = {"ts": ts, "status": status, "msg": message, **metrics}
        with open(JSONL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ===== 配置 =====
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "your_telegram_chat_id")
COINGECKO_API_KEY  = os.getenv("COINGECKO_API_KEY",  "your_coingecko_demo_api_key")

# limit 之和原为 14，恰等于每日入选数——评分规则形同虚设（抓 14 留 14，无取舍
# 空间）。2026-08 提高到 22，让 prompt 里的 3/4/5 分筛选真正有东西可筛。
RSS_SOURCES = [
    # editors_pick 源本身只发 5 条，limit 设更高也拿不到更多
    ("https://cointelegraph.com/editors_pick_rss",     8),
    ("https://www.coindesk.com/arc/outboundfeeds/rss", 8),
    ("https://decrypt.co/feed",                        6),
]

# ===== P2: 消息缓存（降级策略）=====
# 代理不可用或 Telegram 发送失败时把稿件存到 pending_messages.json，避免内容丢失。
# 注意：**不做自动重发**——重发要判断"这稿子还是今天的吗"，跨天重发旧稿比丢一次
# 更糟。当天补救由 health_check → claude_catchup 重走完整流程负责，缓存只作为
# 人工恢复的兜底副本。（2026-08 删除了定义后从未被调用的 flush_pending。）
def save_pending(messages: list) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now().isoformat(), "messages": messages}, f, ensure_ascii=False)


# ===== 获取价格数据 =====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_prices() -> dict:
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum,solana,binancecoin,ripple,hyperliquid"
        "&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        resp = SESSION.get(url, headers={"x-cg-demo-api-key": COINGECKO_API_KEY}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        def fmt(key: str) -> str:
            d      = data.get(key, {})
            price  = d.get("usd", 0)
            change = d.get("usd_24h_change", 0) or 0
            sign   = "+" if change >= 0 else ""
            return f"${price:,.2f} ({sign}{change:.2f}%)"

        return {
            "BTC":  fmt("bitcoin"),
            "ETH":  fmt("ethereum"),
            "SOL":  fmt("solana"),
            "BNB":  fmt("binancecoin"),
            "XRP":  fmt("ripple"),
            "HYPE": fmt("hyperliquid"),
        }
    except Exception as e:
        print(f"[WARN] 价格抓取失败: {e}", file=sys.stderr)
        return {k: "未知" for k in ("BTC", "ETH", "SOL", "BNB", "XRP", "HYPE")}


# ===== 获取趋势币（/search/trending，Demo tier 可用）=====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_trending() -> list:
    """返回最多 5 条趋势币"""
    url = "https://api.coingecko.com/api/v3/search/trending"
    try:
        resp = SESSION.get(url, headers={"x-cg-demo-api-key": COINGECKO_API_KEY}, timeout=15)
        resp.raise_for_status()
        coins  = resp.json().get("coins", [])[:5]
        result = []
        for c in coins:
            item = c.get("item", {})
            data = item.get("data", {})
            pct  = data.get("price_change_percentage_24h", {}).get("usd", 0) or 0
            sign = "+" if pct >= 0 else ""
            result.append({
                "name":   item.get("name", "?"),
                "symbol": item.get("symbol", "?"),
                "rank":   item.get("market_cap_rank", "?"),
                "change": f"{sign}{pct:.1f}%",
            })
        return result
    except Exception as e:
        print(f"[WARN] 趋势币抓取失败: {e}", file=sys.stderr)
        return []


# ===== 获取全球市场数据（牛熊指标）=====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_global_market() -> dict:
    """返回总市值、BTC 市占率及综合牛熊判断"""
    url = "https://api.coingecko.com/api/v3/global"
    try:
        resp = SESSION.get(url, headers={"x-cg-demo-api-key": COINGECKO_API_KEY}, timeout=15)
        resp.raise_for_status()
        d       = resp.json().get("data", {})
        btc_dom = d.get("market_cap_percentage", {}).get("btc", 0) or 0
        total_mc = d.get("total_market_cap", {}).get("usd", 0) or 0
        mc_chg   = d.get("market_cap_change_percentage_24h_usd", 0) or 0

        if btc_dom >= 58:
            label = "🐻 BTC 主导（避险情绪，山寨承压）"
        elif btc_dom >= 52:
            label = "⚖️ 过渡区（BTC 与山寨拉锯）"
        elif btc_dom >= 46:
            label = "🌤 偏牛（资金开始轮动山寨）"
        else:
            label = "🚀 山寨季（高风险偏好，全面做多）"

        return {
            "btc_dominance":         f"{btc_dom:.1f}%",
            "total_market_cap_b":    f"${total_mc / 1e9:,.0f}B",
            "total_volume_b":        f"${d.get('total_volume', {}).get('usd', 0) / 1e9:,.0f}B",
            "market_cap_change_24h": f"{'+' if mc_chg >= 0 else ''}{mc_chg:.2f}%",
            "bull_bear_label":       label,
        }
    except Exception as e:
        print(f"[WARN] 全球市场数据抓取失败: {e}", file=sys.stderr)
        return {
            "btc_dominance": "未知", "total_market_cap_b": "未知",
            "total_volume_b": "未知",
            "market_cap_change_24h": "未知", "bull_bear_label": "未知",
        }




# ===== 获取恐惧贪婪指数 =====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_fear_greed() -> tuple:
    try:
        resp = SESSION.get("https://api.alternative.me/fng/", timeout=10)
        resp.raise_for_status()
        item = resp.json()["data"][0]
        return item["value"], item["value_classification"]
    except Exception as e:
        print(f"[WARN] 恐惧贪婪指数抓取失败: {e}", file=sys.stderr)
        return "未知", "未知"


# ===== 获取 DeFi 全局数据（/global/decentralized_finance_defi）=====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_defi_global() -> dict:
    """
    返回：DeFi 总市值、24h 交易量、DeFi 市占率、当日 DeFi 龙头币
    用于判断今天资金是否在 DeFi 赛道流入
    """
    url = "https://api.coingecko.com/api/v3/global/decentralized_finance_defi"
    try:
        resp = SESSION.get(url, headers={"x-cg-demo-api-key": COINGECKO_API_KEY}, timeout=15)
        resp.raise_for_status()
        d = resp.json().get("data", {})
        defi_mc    = float(d.get("defi_market_cap", 0) or 0)
        defi_vol   = float(d.get("trading_volume_24h", 0) or 0)
        defi_dom   = float(d.get("defi_dominance", 0) or 0)
        top_coin   = d.get("top_coin_name", "未知")
        top_dom    = float(d.get("top_coin_defi_dominance", 0) or 0)
        return {
            "defi_market_cap_b": f"${defi_mc / 1e9:,.1f}B",
            "defi_volume_24h_b": f"${defi_vol / 1e9:,.1f}B",
            "defi_dominance":    f"{defi_dom:.2f}%",
            "top_defi_coin":     f"{top_coin}（占 DeFi {top_dom:.1f}%）",
        }
    except Exception as e:
        print(f"[WARN] DeFi 全局数据抓取失败: {e}", file=sys.stderr)
        return {"defi_market_cap_b": "未知", "defi_volume_24h_b": "未知",
                "defi_dominance": "未知", "top_defi_coin": "未知"}


# ===== 获取赛道热力图（/coins/categories，按 24h 涨幅排序）=====
@with_retry(max_retries=2, base_delay=5, exceptions=(Exception,))
def fetch_top_sectors() -> list:
    """
    返回今日涨幅最大的 5 个赛道，格式：[{name, change_24h, volume_24h_b}]
    用于判断今天哪个叙事赛道（AI/L2/DeFi/Meme）资金流入最猛
    """
    url = "https://api.coingecko.com/api/v3/coins/categories"
    params = {"order": "market_cap_change_24h_desc"}
    try:
        resp = SESSION.get(url, params=params,
                           headers={"x-cg-demo-api-key": COINGECKO_API_KEY}, timeout=15)
        resp.raise_for_status()
        cats = resp.json()[:5]
        result = []
        for c in cats:
            chg = c.get("market_cap_change_24h") or 0
            vol = c.get("volume_24h") or 0
            sign = "+" if chg >= 0 else ""
            result.append({
                "name":      c.get("name", "?"),
                "change_24h": f"{sign}{chg:.1f}%",
                "volume_b":  f"${vol / 1e9:,.2f}B",
            })
        return result
    except Exception as e:
        print(f"[WARN] 赛道热力图抓取失败: {e}", file=sys.stderr)
        return []




# ===== 整理新闻数据 =====
def build_news_context(
    entries: list, prices: dict, fear_val: str, fear_label: str,
    trending: list, global_mkt: dict,
    defi_data: dict, sectors: list,
) -> str:
    now        = datetime.now(timezone.utc)
    time_limit = now - timedelta(days=3)
    seen_urls: set = set()
    sent_before    = load_sent_urls(SENT_URLS)
    picked = []   # (title, url, url_lower, media, snippet)

    kept_per_source: dict = {}
    drops = {"dup": 0, "already_sent": 0, "stale": 0}

    for entry in entries:
        title = getattr(entry, "title", None)
        if not title:
            continue
        original_url = getattr(entry, "link", "") or getattr(entry, "id", "")
        url_lower    = original_url.lower()
        if not url_lower or url_lower in seen_urls:
            drops["dup"] += 1
            continue
        seen_urls.add(url_lower)
        # 跨天去重：前几天已经播出去的条目不再重复入选（3 天窗口的核心防线）
        if url_key(original_url) in sent_before:
            drops["already_sent"] += 1
            continue
        pub_date = parse_entry_date(entry)
        if not pub_date or pub_date < time_limit:
            drops["stale"] += 1
            continue
        snippet = getattr(entry, "summary", "") or ""
        if "cointelegraph.com" in url_lower:
            media = "Cointelegraph"
        elif "coindesk.com" in url_lower:
            media = "CoinDesk"
        else:
            media = url_lower.split("/")[2] if "/" in url_lower else url_lower
        src = getattr(entry, "__src", "?")
        kept_per_source[src] = kept_per_source.get(src, 0) + 1
        picked.append((title, original_url, url_lower, media, snippet))

    # best-effort 并发抓正文全文；失败/被墙/过短回退 RSS 摘要
    # （CoinDesk 的 RSS 摘要常为空，全文抓取收益最大）。全程零 API、纯 HTTP。
    def _news_material(item):
        title, url, url_lower, media, snippet = item
        body = fetch_article_text(url)
        text = body if body else snippet[:500]
        src  = "正文" if body else "摘要"
        return f"[原始英文标题] {title}\n[链接] {url}\n[媒体] {media}\n[正文/摘要（{src}）] {text}\n----"

    news_lines = []
    if picked:
        with ThreadPoolExecutor(max_workers=8) as ex:
            news_lines = list(ex.map(_news_material, picked))

    trend_str = "  ".join(
        f"{t['name']}({t['symbol']}) {t['change']} [#{t['rank']}]"
        for t in trending
    ) if trending else "数据不可用"

    sector_str = "  ".join(
        f"{s['name']} {s['change_24h']} (Vol {s['volume_b']})"
        for s in sectors
    ) if sectors else "数据不可用"

    market_header = (
        f"【今日核心行情】\n"
        f"BTC: {prices['BTC']} | ETH: {prices['ETH']} | SOL: {prices['SOL']}\n"
        f"BNB: {prices['BNB']} | XRP: {prices['XRP']} | HYPE: {prices['HYPE']}\n"
        f"恐惧贪婪指数: {fear_val} ({fear_label})\n"
        f"----------------\n"
        f"【加密货币总市值】\n"
        f"总市值: {global_mkt['total_market_cap_b']} ({global_mkt['market_cap_change_24h']}) | 24h 成交量: {global_mkt['total_volume_b']}\n"
        f"BTC 市占率: {global_mkt['btc_dominance']} → {global_mkt['bull_bear_label']}\n"
        f"----------------\n"
        f"【DeFi 赛道】\n"
        f"DeFi 总市值: {defi_data['defi_market_cap_b']} | 24h 成交量: {defi_data['defi_volume_24h_b']} | DeFi 市占: {defi_data['defi_dominance']}\n"
        f"DeFi 龙头: {defi_data['top_defi_coin']}\n"
        f"----------------\n"
        f"【赛道表现（24h 涨幅 Top 5）】\n"
        f"{sector_str}\n"
        f"----------------\n"
        f"【今日热搜榜 (Top 5)】\n"
        f"{trend_str}\n"
        f"----------------\n\n"
    )
    return market_header + "\n".join(news_lines), kept_per_source, drops


# ===== P0: 发送 Telegram（单块重试 + 整体分块）=====
@with_retry(max_retries=3, base_delay=5, exceptions=(requests.RequestException,))
def _send_one(chunk: str) -> None:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = SESSION.post(
        api_url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not resp.ok:
        raise requests.RequestException(f"Telegram 返回错误: {resp.text}")


def send_telegram(text: str) -> None:
    # sanitize 一次，在拆分前完成，避免切分点破坏标签结构；
    # 切分 + 页码由 bot_utils.paginate_telegram 统一负责（两个 bot 共用同一实现）。
    for chunk in paginate_telegram(sanitize_html(text)):
        _send_one(chunk)



# ===== 抓取阶段（fetch / full 共用）=====
def _proxy_ok() -> bool:
    """代理预检 + 端口自愈。

    配置端口不通时会探测候选端口（见 bot_utils.PROXY_CANDIDATES），命中则
    就地切换本进程的 SESSION 与环境变量——换代理软件导致端口变化时不再
    静默停摆。无代理配置直接放行（视为直连）。"""
    global _PROXY
    resolved, switched = resolve_proxy(_PROXY)
    if resolved is None:
        return not _PROXY          # 本来就没配代理 → 直连放行；配了但全不通 → 失败
    if switched:
        _PROXY = resolved
        SESSION.proxies = {"http": resolved, "https": resolved}
        # feedparser 走 urllib，必须同步环境变量（用赋值而非 setdefault）
        os.environ["HTTP_PROXY"] = resolved
        os.environ["HTTPS_PROXY"] = resolved
    return True


def gather() -> dict:
    """抓取全部行情 + 新闻并构建 news_context。核心价格全失败返回 None。
    进度打到 stderr，让 fetch 模式的 stdout 只保留干净的 context。"""
    print("💰 抓取价格数据...", file=sys.stderr)
    prices               = fetch_prices()
    fear_val, fear_label = fetch_fear_greed()
    print(f"  BTC={prices['BTC']}  HYPE={prices['HYPE']}  恐惧指数={fear_val}({fear_label})", file=sys.stderr)

    # 数据降级保护：核心价格全部缺失时放弃，避免推送空白报告
    if prices.get("BTC") == "未知" and prices.get("ETH") == "未知":
        return None

    print("🌐 抓取全球市场数据...", file=sys.stderr)
    global_mkt = fetch_global_market()
    print(f"  总市值={global_mkt['total_market_cap_b']}  BTC市占={global_mkt['btc_dominance']}  {global_mkt['bull_bear_label']}", file=sys.stderr)

    print("🔥 抓取趋势币...", file=sys.stderr)
    trending = fetch_trending()
    print(f"  ✓ {len(trending)} 个趋势币", file=sys.stderr)

    print("🏦 抓取 DeFi 全局数据...", file=sys.stderr)
    defi_data = fetch_defi_global()
    print(f"  DeFi市值={defi_data['defi_market_cap_b']}  市占={defi_data['defi_dominance']}", file=sys.stderr)

    print("🗺 抓取赛道热力图...", file=sys.stderr)
    sectors = fetch_top_sectors()
    print(f"  ✓ {len(sectors)} 个赛道", file=sys.stderr)

    print("\n📡 抓取 RSS 源...", file=sys.stderr)
    all_entries = []
    fetched_counts: dict = {}
    for feed_url, limit in RSS_SOURCES:
        entries = fetch_rss(feed_url, limit)
        domain  = feed_url.split("/")[2]
        # 打上来源标记，供统计"过滤后每个源还剩几条"
        for e in entries:
            e["__src"] = domain
        all_entries.extend(entries)
        fetched_counts[domain] = fetched_counts.get(domain, 0) + len(entries)
        print(f"  ✓ {len(entries)} 条  {feed_url}", file=sys.stderr)

    today = datetime.now().strftime("%Y-%m-%d")
    news_context, kept_per_source, drops = build_news_context(
        all_entries, prices, fear_val, fear_label,
        trending, global_mkt, defi_data, sectors,
    )
    # 按条目标记计数，不再数 "----"：行情区块的 ---------------- 分隔线会被算进去，
    # 旧口径下这个值恒为 33/34，与实际新闻条数无关。
    news_count = news_context.count("[原始英文标题]")

    # 零产源 = 过滤后一条都没剩的源（而非"RSS 拉到 0 条"）
    source_stats = {d: {"fetched": n, "kept": kept_per_source.get(d, 0)}
                    for d, n in fetched_counts.items()}
    zero_sources = [d for d, s in source_stats.items() if s["kept"] == 0]

    # 连续零产追踪：本处是 .zero_streak.json 的唯一写入方，health_check 只读不写
    stale_sources = update_zero_streak(ZERO_STREAK, zero_sources, list(source_stats),
                                       threshold=ZERO_STREAK_THRESHOLD)
    try:
        streak_now = json.loads(ZERO_STREAK.read_text(encoding="utf-8"))
    except Exception:
        streak_now = {}

    print(f"\n📰 共抓取 {len(all_entries)} 条 → 保留 {news_count} 条有效新闻", file=sys.stderr)
    print(f"   过滤明细：重复 {drops['dup']} · 已播过 {drops['already_sent']} · "
          f"超 3 天 {drops['stale']}", file=sys.stderr)
    for d, s in sorted(source_stats.items(), key=lambda kv: -kv[1]["kept"]):
        n = streak_now.get(d, 0)
        flag = f"  ⚠️ 零产（连续 {n} 天）" if s["kept"] == 0 else ""
        print(f"   {d:26s} 抓{s['fetched']:>2} → 留{s['kept']:>2}{flag}", file=sys.stderr)

    return {
        "prices": prices, "fear_val": fear_val, "fear_label": fear_label,
        "global_mkt": global_mkt, "trending": trending, "defi_data": defi_data,
        "sectors": sectors, "all_entries": all_entries, "zero_sources": zero_sources,
        "source_stats": source_stats, "stale_sources": stale_sources,
        "news_context": news_context, "news_count": news_count, "today": today,
    }


def _summary_and_metrics(data: dict) -> tuple:
    """由 gather 结果构造 OK 日志摘要与 health_check 所需 metrics（fetch 边车用）。"""
    prices = data["prices"]
    g      = data["global_mkt"]
    summary = (
        f"BTC={prices['BTC']} HYPE={prices['HYPE']} "
        f"恐惧指数={data['fear_val']}({data['fear_label']}) "
        f"BTC市占={g['btc_dominance']} → 抓取{len(data['all_entries'])}条 → 保留{data['news_count']}条"
    )
    metrics = {
        "btc": prices["BTC"], "hype": prices["HYPE"],
        "fear_val": data["fear_val"], "btc_dominance": g["btc_dominance"],
        "bull_bear": g["bull_bear_label"], "trending_count": len(data["trending"]),
        "rss_fetched": len(data["all_entries"]), "rss_kept": data["news_count"],
        "rss_zero_sources": data["zero_sources"],
        "rss_source_stats": data["source_stats"],
        "rss_stale_sources": data["stale_sources"],
    }
    return summary, metrics


# ===== 模式 1：fetch — 抓取并输出 context（零 API 成本，供 Claude 写两稿）=====
def run_fetch() -> int:
    if already_ran_today(LOG_FILE):
        print("=== SKIP_ALREADY_RAN ===")
        return 0

    if not _proxy_ok():
        print(f"=== SKIP_PROXY === {_PROXY}")
        return 0

    data = gather()
    if data is None:
        print("=== SKIP_NO_PRICES ===")
        write_log("WARN", "CoinGecko 核心价格数据全部失败，跳过本次发送，等待下次运行")
        return 0

    # 写边车：OK 日志摘要 + metrics，供 send 模式回填（保持 health_check 监控存活）
    summary, metrics = _summary_and_metrics(data)
    FETCH_META.write_text(
        json.dumps({"log_summary": summary, "metrics": metrics}, ensure_ascii=False),
        encoding="utf-8",
    )

    # stdout 只输出结构化标记 + context，供 Claude routine 稳定解析
    print("=== FETCH_OK ===")
    print(f"今天日期：{data['today']}")
    print(f"保留 {data['news_count']} 条有效新闻（共抓取 {len(data['all_entries'])} 条）")
    if data["zero_sources"]:
        print(f"零产源：{', '.join(data['zero_sources'])}")
    # 连续零产达阈值 → 结构化告警块，供 routine 在日报汇报里转述给用户
    if data["stale_sources"]:
        print("=== SOURCE_ALERT ===")
        for d, n in data["stale_sources"].items():
            print(f"{d} 已连续 {n} 天零产，建议从 RSS_SOURCES 移除或更换")
        print("=== SOURCE_ALERT_END ===")
    print("=== CONTEXT_BEGIN ===")
    print(data["news_context"])
    print("=== CONTEXT_END ===")
    return 0


# ===== 模式 2：send — 读取 Claude 写好的两稿并依次发送（零 API 成本）=====
def run_send() -> int:
    t0 = time.time()

    if already_ran_today(LOG_FILE):
        print("今天已成功运行过，跳过发送。如需强制请设置 FORCE_RUN=1。", file=sys.stderr)
        return 0

    if not DRAFT_ANALYSIS.exists() or not DRAFT_NEWS.exists():
        write_log("FAIL", f"稿子文件缺失：需要 {DRAFT_ANALYSIS.name} 与 {DRAFT_NEWS.name}")
        return 1
    analysis    = DRAFT_ANALYSIS.read_text(encoding="utf-8").strip()
    news_report = DRAFT_NEWS.read_text(encoding="utf-8").strip()
    if not analysis or not news_report:
        write_log("FAIL", "稿子文件为空（消息①或②）")
        return 1

    # 代理不可用时不丢内容：两稿一起缓存，等代理恢复后补发
    if not _proxy_ok():
        save_pending([analysis, news_report])
        write_log("WARN", f"代理不可用（{_PROXY}），两稿已缓存未发送")
        return 0

    # 部分发送保护（send_telegram 内部已做 sanitize_html）
    save_pending([analysis, news_report])
    print("📨 发送到 Telegram...", file=sys.stderr)
    send_telegram(analysis)
    save_pending([news_report])       # 消息①已发：更新缓存，防止重跑时重复推送
    send_telegram(news_report)
    CACHE_FILE.unlink(missing_ok=True)
    print("  ✓ 发送成功", file=sys.stderr)

    # 归档本次真正播出去的链接，供后续 fetch 跨天去重（两稿都扫，链接主要在消息②）。
    # 记在发送成功之后：发失败的那批不该被标成"已播"。
    hrefs = extract_hrefs(analysis) + extract_hrefs(news_report)
    if hrefs:
        total = record_sent_urls(SENT_URLS, hrefs)
        print(f"  ✓ 已归档 {len(hrefs)} 条链接用于跨天去重（档案共 {total} 条）",
              file=sys.stderr)

    # OK 日志：从 fetch 边车取摘要与 metrics，保持 health_check 监控存活
    try:
        meta = json.loads(FETCH_META.read_text(encoding="utf-8"))
    except Exception:
        meta = {"log_summary": "Claude写稿", "metrics": {}}
    duration = round(time.time() - t0, 1)
    write_log(
        "OK",
        f"Claude写稿 → {meta.get('log_summary', '')} → Telegram发送成功",
        metrics={**meta.get("metrics", {}), "ai_calls": 0, "source": "claude", "duration_s": duration},
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crypto 市场晨报")
    parser.add_argument(
        "--mode", choices=["fetch", "send"], required=True,
        help="fetch=抓取并输出 context（供 Claude 写稿，零 API）/ send=发送 Claude 写好的两稿",
    )
    parsed = parser.parse_args()

    try:
        if parsed.mode == "fetch":
            sys.exit(run_fetch())
        else:
            sys.exit(run_send())
    except Exception:
        err = traceback.format_exc().strip().splitlines()[-1]
        write_log("FAIL", err)
        raise

