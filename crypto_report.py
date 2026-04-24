#!/opt/homebrew/bin/python3.11
"""
Crypto 市场晨报 — 每天早上 8:00 由 launchd 自动触发
数据源：
  · CoinGecko: BTC/ETH/SOL/BNB/XRP/HYPE 价格、趋势币 Top5、全球市值、DeFi 数据、赛道热力图
  · alternative.me: 恐惧贪婪指数
  · RSS × 4: Cointelegraph / CoinDesk / The Block / Decrypt
AI 输出：① 市场晨报（仪表盘+趋势+叙事）② 新闻播报列表
推送：2 条 Telegram HTML 消息
"""

import os
import sys
import time
import json
import socket
import calendar
import traceback
import functools
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from openai import OpenAI

LOG_FILE   = Path(__file__).parent / "run.log"
JSONL_FILE = Path(__file__).parent / "run.jsonl"
CACHE_FILE = Path(__file__).parent / "pending_messages.json"

# ===== P0: 显式代理配置 =====
_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
SESSION = requests.Session()
SESSION.proxies = {"http": _PROXY, "https": _PROXY}
# feedparser 内部使用 urllib，通过环境变量注入代理
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
DEEPSEEK_API_KEY   = os.getenv("DEEPSEEK_API_KEY",   "your_deepseek_api_key")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "your_telegram_chat_id")
COINGECKO_API_KEY  = os.getenv("COINGECKO_API_KEY",  "your_coingecko_demo_api_key")

RSS_SOURCES = [
    ("https://cointelegraph.com/editors_pick_rss",     5),
    ("https://www.coindesk.com/arc/outboundfeeds/rss", 5),
    ("https://www.theblock.co/rss.xml",                4),
    ("https://decrypt.co/feed",                        4),
]

PROMPT_ANALYSIS = """\
角色：你是一位擅长宏观叙事与链上数据分析的 Web3 资深研究员。

任务：结合【今日核心数据】（币价 / 情绪指数 / 全球市场 / DeFi 赛道 / 赛道热力图 / 趋势币）与【新闻摘要】，撰写一份《每日加密市场晨报》。

分析原则：
1. 数据与情绪对比：观察价格变动与新闻情绪是否背离
2. BTC 市占率与牛熊信号：结合市占率判断当前是主流季还是山寨季
3. 赛道轮动分析：哪些赛道在资金流入（赛道热力图），哪些在流出
4. DeFi 健康度：DeFi 市占率是否上升，龙头是否在换手
5. 趋势币解读：今日热搜币与市场叙事的关联
6. 拒绝流水账：不复述新闻，输出洞察

严格按照以下 HTML 格式输出，禁止使用 ** 或其他 Markdown 符号：

<b>📊 市场仪表盘</b>
💰 主流币：BTC [价格及涨跌幅] | ETH [价格及涨跌幅] | SOL [价格及涨跌幅] | BNB [价格及涨跌幅] | XRP [价格及涨跌幅] | HYPE [价格及涨跌幅]
🌡 情绪指数：[数值] — [等级]（一句话评价）
🌐 加密货币总市值：[总市值] ([24h涨跌]) | 24h 成交量：[总成交量] | BTC 市占 [市占率] → [牛熊标签]
🏦 DeFi：市值 [DeFi总市值] | 市占 [DeFi市占率] | 龙头 [DeFi龙头币]

<b>🗺 赛道表现（24h 涨幅 Top 5）</b>
每条一行：[赛道名] [涨跌幅] — 一句话说明资金流入逻辑或催化剂

<b>🔥 今日热搜榜 (Top 5)</b>
列出热搜榜币种，每条一行：[名称]([代号]) [涨跌幅] — 一句话说明为何上榜

<b>🔭 核心叙事</b>
提炼 1–2 个当前市场主线故事，结合新闻内容深度解读，约 100 字。编号用「1.」「2.」，不用加粗。

<b>⚠️ 风险与机会</b>
👀 关注：[具体利好赛道或代币]
🚨 警惕：[具体风险点，如监管落地或巨鲸抛压]

<b>💡 分析师观点</b>
给出未来 24 小时趋势预判：震荡、看涨还是回调？给出具体理由。

风格：专业、克制，不用感叹号，不用"重磅""颠覆"等夸张词。\
"""






PROMPT_NEWS = """\
角色：你是一位专注加密货币与 Web3 领域的资深编辑。

任务：基于输入的新闻数据，生成一份《加密市场新闻播报》。

【处理规则】
1. 去重与合并：标题语义相似 + 主体一致 → 合并为一条，保留最权威来源
2. 时效过滤：仅收录 3 天内新闻
3. 评分标准：
   5分：监管重大决策、交易所重大事件、BTC/ETH 历史级价格突破、主权级采购
   4分：主流协议重大升级、机构大额入场/离场、监管草案或立法进展、链上异常数据
   3分：项目融资、合作、产品发布、市场分析、新币上线
4. 排序：按评分从高到低；同分下 监管 > 市场行情 > 机构 > 协议/DeFi > 交易所 > 其他
5. 标签（每条选 1–2 个）：#监管 #市场行情 #机构动向 #DeFi #协议升级 #交易所 #Layer2 #NFT #宏观 #Crypto

【输出格式】（严格使用 HTML，禁止 Markdown）

📋 加密市场播报 · [今天日期]

每条新闻格式如下（条目之间空一行）：

[星级] [标签]
[中文标题，一句话说清事件]
📄 The Details：[150字以内，陈述事实、涉及主体、关键数字]
💡 Why it matters：[80字以内，具体分析：对哪类投资者或项目的影响、触发什么连锁反应]
🔗 <a href="原文链接URL">英文原始标题 · 媒体名</a>

星级规则：5分=⭐⭐⭐⭐⭐ 4分=⭐⭐⭐⭐ 3分=⭐⭐⭐

Why it matters 禁止：
- "这标志着……""这意味着……""这推动了……"
- 任何以"这"字开头后跟宽泛影响的句子

⚠️ 整份播报控制在 4096 字符以内，宁少勿多，优先保留高分条目。\
"""


# ===== P0: 指数退避重试装饰器 =====
def with_retry(max_retries=3, base_delay=5.0, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        raise
                    delay = base_delay * (2 ** attempt)
                    print(f"[RETRY] {func.__name__} 第{attempt+1}次失败: {e}，{delay:.0f}s 后重试",
                          file=sys.stderr)
                    time.sleep(delay)
        return wrapper
    return decorator


# ===== P2: 消息缓存（降级策略）=====
def save_pending(messages: list) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now().isoformat(), "messages": messages}, f, ensure_ascii=False)


def flush_pending() -> bool:
    """启动时检查并重发上次未发送的缓存消息"""
    if not CACHE_FILE.exists():
        return False
    try:
        data    = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        pending = data.get("messages", [])
        if not pending:
            CACHE_FILE.unlink(missing_ok=True)
            return False
        print(f"[CACHE] 发现 {len(pending)} 条待发消息（来自 {data.get('ts','?')}），优先重发...")
        for msg in pending:
            _send_one(msg)
        CACHE_FILE.unlink(missing_ok=True)
        print("[CACHE] 缓存消息重发成功")
        return True
    except Exception as e:
        print(f"[WARN] 缓存重发失败: {e}", file=sys.stderr)
        return False


# ===== 获取价格数据 =====
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




# ===== P1: 抓取 RSS（socket 超时 + 重试兜底）=====
def fetch_rss(url: str, limit: int, retries: int = 2, delay: float = 3.0) -> list:
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    try:
        for attempt in range(retries + 1):
            try:
                feed = feedparser.parse(url, request_headers={"User-Agent": UA})
                if feed.entries:
                    return feed.entries[:limit]
                if attempt < retries:
                    time.sleep(delay)
            except Exception as e:
                if attempt < retries:
                    time.sleep(delay)
                else:
                    print(f"[WARN] 抓取失败（已重试 {retries} 次）{url}: {e}", file=sys.stderr)
    finally:
        socket.setdefaulttimeout(old_timeout)
    return []


def parse_entry_date(entry) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if t:
            try:
                return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
            except Exception:
                pass
    return None


# ===== 整理新闻数据 =====
def build_news_context(
    entries: list, prices: dict, fear_val: str, fear_label: str,
    trending: list, global_mkt: dict,
    defi_data: dict, sectors: list,
) -> str:
    now        = datetime.now(timezone.utc)
    time_limit = now - timedelta(days=3)
    seen_urls: set = set()
    news_lines = []

    for entry in entries:
        title = getattr(entry, "title", None)
        if not title:
            continue
        original_url = getattr(entry, "link", "") or getattr(entry, "id", "")
        url_lower    = original_url.lower()
        if not url_lower or url_lower in seen_urls:
            continue
        seen_urls.add(url_lower)
        pub_date = parse_entry_date(entry)
        if not pub_date or pub_date < time_limit:
            continue
        snippet = getattr(entry, "summary", "") or ""
        if "cointelegraph.com" in url_lower:
            media = "Cointelegraph"
        elif "coindesk.com" in url_lower:
            media = "CoinDesk"
        else:
            media = url_lower.split("/")[2] if "/" in url_lower else url_lower
        news_lines.append(
            f"[原始英文标题] {title}\n[链接] {original_url}\n"
            f"[媒体] {media}\n[摘要] {snippet[:200]}\n----"
        )

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
    return market_header + "\n".join(news_lines)


# ===== P0: DeepSeek 调用（超时 + 重试）=====
@with_retry(max_retries=2, base_delay=10, exceptions=(Exception,))
def call_deepseek(system_prompt: str, user_content: str) -> str:
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com",
        timeout=60.0,
        max_retries=0,
    )
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.4,
    )
    return resp.choices[0].message.content


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


def sanitize_html(text: str) -> str:
    """
    1. 处理由于 AI 截断或切分产生的不完整标签
    2. 保留 Telegram 合法标签（<b> </b> <a href="..."> </a>），
       把其余所有 < > 转义为 &lt; &gt;，防止纯文本触发解析错误。
    """
    import re

    # 1. 去除末尾未闭合的标签
    if text.count("<a ") > text.count("</a>"):
        text = text[:text.rfind("<a ")].rstrip()
    if text.count("<b>") > text.count("</b>"):
        text = text[:text.rfind("<b>")].rstrip()

    # 2. 先提取所有合法标签，用占位符替换
    valid_tags = re.findall(r'<b>|</b>|<a\s+href="[^"]*">|</a>', text)
    placeholders = {}
    result = text
    for i, tag in enumerate(valid_tags):
        placeholder = f"\x00TAG{i}\x00"
        placeholders[placeholder] = tag
        result = result.replace(tag, placeholder, 1)

    # 3. 转义剩余的 < >
    result = result.replace("<", "&lt;").replace(">", "&gt;")

    # 4. 还原合法标签
    for placeholder, tag in placeholders.items():
        result = result.replace(placeholder, tag)

    return result


def send_telegram(text: str) -> None:
    MAX_LEN = 4000  # 预留部分余量
    text = sanitize_html(text)
    
    chunks = []
    current_chunk = ""
    for line in text.split('\n'):
        if len(current_chunk) + len(line) + 1 > MAX_LEN:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = line + "\n"
            else:
                # 极端情况：单行超过 MAX_LEN
                chunks.append(line[:MAX_LEN])
                current_chunk = line[MAX_LEN:] + "\n"
        else:
            current_chunk += line + "\n"
            
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    for chunk in chunks:
        # 二次检查，避免极端切分情况导致的不完整标签
        chunk = sanitize_html(chunk)
        _send_one(chunk)



# ===== 主流程 =====
def main() -> None:
    t0 = time.time()

    flush_pending()

    print("💰 抓取价格数据...")
    prices               = fetch_prices()
    fear_val, fear_label = fetch_fear_greed()
    print(f"  BTC={prices['BTC']}  HYPE={prices['HYPE']}  恐惧指数={fear_val}({fear_label})")

    print("🌐 抓取全球市场数据...")
    global_mkt = fetch_global_market()
    print(f"  总市值={global_mkt['total_market_cap_b']}  BTC市占={global_mkt['btc_dominance']}  {global_mkt['bull_bear_label']}")

    print("🔥 抓取趋势币...")
    trending = fetch_trending()
    print(f"  ✓ {len(trending)} 个趋势币：" + "  ".join(f"{t['name']}({t['change']})" for t in trending))

    print("🏦 抓取 DeFi 全局数据...")
    defi_data = fetch_defi_global()
    print(f"  DeFi市值={defi_data['defi_market_cap_b']}  市占={defi_data['defi_dominance']}  龙头={defi_data['top_defi_coin']}")

    print("🗺 抓取赛道热力图...")
    sectors = fetch_top_sectors()
    print(f"  ✓ {len(sectors)} 个赛道：" + "  ".join(f"{s['name']}({s['change_24h']})" for s in sectors))

    print("\n📡 抓取 RSS 源...")
    all_entries = []
    for feed_url, limit in RSS_SOURCES:
        entries = fetch_rss(feed_url, limit)
        all_entries.extend(entries)
        print(f"  ✓ {len(entries)} 条  {feed_url}")

    print(f"\n📰 共抓取 {len(all_entries)} 条，构建 context...")
    today        = datetime.now().strftime("%Y-%m-%d")
    news_context = build_news_context(
        all_entries, prices, fear_val, fear_label,
        trending, global_mkt, defi_data, sectors,
    )
    news_count   = news_context.count("----")
    print(f"  ✓ 保留 {news_count} 条有效新闻")

    print("\n🤖 生成市场分析报告（消息①）...")
    analysis = call_deepseek(PROMPT_ANALYSIS, news_context)
    print("  ✓ 完成")

    print("🤖 生成新闻播报列表（消息②）...")
    news_report = call_deepseek(PROMPT_NEWS, f"今天日期：{today}\n\n{news_context}")
    print("  ✓ 完成")

    save_pending([analysis, news_report])

    print("\n📨 发送到 Telegram...")
    send_telegram(analysis)
    send_telegram(news_report)
    print("  ✓ 发送成功\n")

    CACHE_FILE.unlink(missing_ok=True)

    duration = round(time.time() - t0, 1)
    write_log(
        "OK",
        f"BTC={prices['BTC']} HYPE={prices['HYPE']} 恐惧指数={fear_val}({fear_label}) BTC市占={global_mkt['btc_dominance']} → 抓取{len(all_entries)}条 → 保留{news_count}条 → Telegram发送成功",
        metrics={
            "btc": prices["BTC"], "hype": prices["HYPE"],
            "fear_val": fear_val, "btc_dominance": global_mkt["btc_dominance"],
            "bull_bear": global_mkt["bull_bear_label"],
            "trending_count": len(trending),
            "rss_fetched": len(all_entries), "rss_kept": news_count,
            "ai_calls": 2, "duration_s": duration,
        },
    )





if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = traceback.format_exc().strip().splitlines()[-1]
        write_log("FAIL", err)
        raise

