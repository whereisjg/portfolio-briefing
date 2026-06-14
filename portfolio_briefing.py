#!/usr/bin/env python3
"""
Daily portfolio briefing for GitHub Actions.

This version does not call an AI API. It fetches prices directly from Yahoo
Finance, applies simple rule-based guidance, sends Telegram, and saves markdown.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, unquote
import xml.etree.ElementTree as ET

import pytz
import requests


def env_value(name, default=""):
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


KST = pytz.timezone("Asia/Seoul")
TELEGRAM_BOT_TOKEN = env_value("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_value("TELEGRAM_CHAT_ID")
SEND_TELEGRAM = env_value("SEND_TELEGRAM", "true").lower()
CLAUDE_API_KEY = env_value("CLAUDE_API_KEY")

PORTFOLIO_FILE = "portfolio.json"
SCREENER_FILE = "screener.json"


def configure_console_output():
    """Avoid UnicodeEncodeError during local Windows previews."""
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_portfolio():
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)

    indexes = config.get("indexes", [])
    assets = config.get("assets", [])
    if not assets:
        raise ValueError("portfolio.json에 assets가 없습니다.")
    return indexes, assets


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def quote_from_price(asset, price, previous_close, provider):
    if price is None or previous_close in (None, 0):
        raise ValueError(f"가격 데이터를 찾지 못했습니다: {asset['ticker']}")

    chg_amount = float(price) - float(previous_close)
    chg_pct = (chg_amount / float(previous_close)) * 100
    return {
        **asset,
        "price": float(price),
        "prev_close": float(previous_close),
        "chg_amount": float(chg_amount),
        "chg_pct": float(chg_pct),
        "provider": provider,
    }


def fetch_yahoo_quote(asset):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{asset['symbol']}"
    params = {"range": "1d", "interval": "1m"}
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()

    result = response.json()["chart"]["result"][0]
    meta = result["meta"]

    price = meta.get("regularMarketPrice")
    previous_close = meta.get("previousClose") or meta.get("chartPreviousClose")

    if price is None:
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [close for close in closes if close is not None]
        price = closes[-1] if closes else None

    return quote_from_price(asset, price, previous_close, "Yahoo")


def load_screener_config():
    if not os.path.exists(SCREENER_FILE):
        return None

    with open(SCREENER_FILE, "r", encoding="utf-8") as file:
        config = json.load(file)

    if not config.get("enabled", True):
        return None
    return config


def raw_value(value):
    if isinstance(value, dict):
        return value.get("raw")
    return value


def float_or_none(value):
    value = raw_value(value)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_screen_symbol(item):
    if isinstance(item, str):
        return {"symbol": item, "ticker": item, "currency": "USD"}
    symbol = item.get("symbol") or item.get("ticker")
    return {
        "symbol": symbol,
        "ticker": item.get("ticker") or symbol,
        "currency": item.get("currency", "USD"),
        "name": item.get("name"),
    }


def fetch_yahoo_fundamentals(symbol):
    modules = ",".join(["price", "summaryDetail", "defaultKeyStatistics", "financialData"])
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote_plus(symbol)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params={"modules": modules}, headers=headers, timeout=20)
    response.raise_for_status()

    result = response.json().get("quoteSummary", {}).get("result") or []
    if not result:
        raise ValueError("Yahoo fundamentals not found")

    data = result[0]
    price = data.get("price", {})
    summary = data.get("summaryDetail", {})
    stats = data.get("defaultKeyStatistics", {})
    financial = data.get("financialData", {})

    return {
        "symbol": symbol,
        "name": raw_value(price.get("shortName")) or raw_value(price.get("longName")) or symbol,
        "currency": raw_value(price.get("currency")) or "USD",
        "roe": float_or_none(financial.get("returnOnEquity")),
        "per": float_or_none(summary.get("trailingPE")) or float_or_none(stats.get("trailingPE")),
        "psr": float_or_none(summary.get("priceToSalesTrailing12Months"))
        or float_or_none(stats.get("priceToSalesTrailing12Months")),
        "pbr": float_or_none(stats.get("priceToBook")),
        "provider": "Yahoo fundamentals",
    }


def passes_fundamental_screen(item, criteria):
    roe = item.get("roe")
    per = item.get("per")
    psr = item.get("psr")
    pbr = item.get("pbr")
    return (
        roe is not None
        and per is not None
        and psr is not None
        and pbr is not None
        and roe >= float(criteria.get("min_roe", 0.15))
        and per <= float(criteria.get("max_per", 15))
        and psr < float(criteria.get("exclude_psr_gte", 3))
        and pbr <= float(criteria.get("max_pbr", 1.5))
    )


def screen_fundamental_candidates(config):
    criteria = config.get("criteria", {})
    candidates = []
    errors = []

    for raw_symbol in config.get("symbols", []):
        candidate = normalize_screen_symbol(raw_symbol)
        symbol = candidate.get("symbol")
        if not symbol:
            continue
        try:
            data = fetch_yahoo_fundamentals(symbol)
            data.update({key: value for key, value in candidate.items() if value})
            data["passes"] = passes_fundamental_screen(data, criteria)
            if data["passes"]:
                candidates.append(data)
            print(
                f"SCREEN {symbol}: "
                f"ROE={data.get('roe')} PER={data.get('per')} "
                f"PSR={data.get('psr')} PBR={data.get('pbr')} pass={data['passes']}"
            )
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            print(f"ERROR SCREEN {symbol}: {exc}")

    candidates.sort(key=lambda item: (-(item.get("roe") or 0), item.get("per") or 999))
    return candidates, errors


def format_ratio(value, multiplier=1.0, suffix=""):
    if value is None:
        return "-"
    return f"{value * multiplier:.2f}{suffix}"


def build_screening_sections(candidates, screen_errors):
    if not candidates and not screen_errors:
        return [], []

    telegram_lines = ["", "🔎 가치 조건 검색"]
    markdown_lines = ["", "## 🔎 가치 조건 검색", ""]

    if candidates:
        for item in candidates[:5]:
            line = (
                f"{item['symbol']} ROE {format_ratio(item.get('roe'), 100, '%')}, "
                f"PER {format_ratio(item.get('per'), suffix='배')}, "
                f"PSR {format_ratio(item.get('psr'), suffix='배')}, "
                f"PBR {format_ratio(item.get('pbr'), suffix='배')}"
            )
            telegram_lines.append(f"  • {line}")
            markdown_lines.append(f"- {line}")
    else:
        telegram_lines.append("  • 조건 통과 종목 없음")
        markdown_lines.append("- 조건 통과 종목 없음")

    if screen_errors:
        telegram_lines.extend(["", "⚠️ 검색 데이터 확인 필요"])
        markdown_lines.extend(["", "### ⚠️ 검색 데이터 확인 필요", ""])
        for error in screen_errors[:5]:
            telegram_lines.append(f"  • {error}")
            markdown_lines.append(f"- {error}")

    return telegram_lines, markdown_lines


def fetch_quote(asset):
    return fetch_yahoo_quote(asset)


def fetch_prices(assets, require_any=True):
    quotes = []
    errors = []

    for asset in assets:
        try:
            quote = fetch_quote(asset)
            quotes.append(quote)
            print(f"OK {asset['ticker']}: {quote['price']} ({quote['chg_pct']:+.2f}%)")
        except Exception as exc:
            errors.append(f"{asset['ticker']}: {exc}")
            print(f"ERROR {asset['ticker']}: {exc}")

    if require_any and not quotes:
        raise ValueError("모든 가격 조회에 실패했습니다.")

    return quotes, errors


def news_queries_for_asset(asset):
    queries = asset.get("news_queries")
    if queries:
        return queries
    return [asset["news_query"]]


def fetch_news_for_query(query_text, limit):
    query = quote_plus(f"{query_text} when:1d")
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    titles = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        if not title:
            continue
        if pub_date:
            published_at = parsedate_to_datetime(pub_date)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            if published_at.astimezone(timezone.utc) < cutoff:
                continue
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def is_excluded_news(asset, title):
    excluded_terms = asset.get("news_exclude", [])
    normalized_title = title.casefold()
    return any(term.casefold() in normalized_title for term in excluded_terms)


def news_relevance_score(asset, title):
    include_terms = asset.get("news_include", [])
    if not include_terms:
        return 1

    normalized_title = title.casefold()
    score = 0
    for term in include_terms:
        normalized_term = str(term).casefold().strip()
        if not normalized_term:
            continue
        if normalized_term in normalized_title:
            score += 2 if len(normalized_term) >= 6 else 1

    headline, _source = split_news_source(title)
    if len(headline.strip()) < 12:
        score -= 1
    return score


def news_dedupe_key(title):
    headline, _source = split_news_source(title)
    return " ".join(headline.casefold().split())


def fetch_news_for_asset(asset, limit=2):
    if limit <= 0:
        return []

    seen = set()
    ranked_titles = []
    candidate_limit = max(limit * 4, 6)
    order = 0

    for query_text in news_queries_for_asset(asset):
        for raw_title in fetch_news_for_query(query_text, candidate_limit):
            if is_excluded_news(asset, raw_title):
                continue

            raw_score = news_relevance_score(asset, raw_title)
            if raw_score <= 0:
                continue

            title = translate_title_if_needed(raw_title)
            if is_excluded_news(asset, title):
                continue

            score = max(raw_score, news_relevance_score(asset, title))
            if score <= 0:
                continue

            title_key = news_dedupe_key(title)
            if title_key in seen:
                continue
            seen.add(title_key)
            ranked_titles.append((score, order, title))
            order += 1

    ranked_titles.sort(key=lambda item: (-item[0], item[1]))
    return [title for _score, _order, title in ranked_titles[:limit]]


def has_korean(text):
    return any("가" <= char <= "힣" for char in text)


def split_news_source(title):
    if " - " not in title:
        return title, ""
    headline, source = title.rsplit(" - ", 1)
    return headline.strip(), source.strip()


def clean_news_headline(title):
    import re
    headline, _ = split_news_source(title)
    # 끝에 붙는 ,펀드명,TICKER 패턴 반복 제거 (대문자 약어 포함된 세그먼트)
    for _ in range(3):
        cleaned = re.sub(r',\s*(?=[^,]*\b[A-Z]{2,}\b)[^,]+$', '', headline).strip()
        if cleaned == headline:
            break
        headline = cleaned
    return headline


def translate_to_korean(text):
    if CLAUDE_API_KEY:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 256,
            "messages": [{
                "role": "user",
                "content": f"다음 영어 뉴스 제목을 자연스러운 한국어로 번역해줘. 번역문만 출력해:\n{text}"
            }]
        }
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        return response.json()["content"][0]["text"].strip()

    # fallback: Google 번역
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "ko",
        "dt": "t",
        "q": text,
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    translated = "".join(part[0] for part in data[0] if part and part[0])
    return unquote(translated).strip()


def translate_title_if_needed(title):
    headline, source = split_news_source(title)
    if has_korean(headline):
        return title

    try:
        translated = translate_to_korean(headline)
    except Exception as exc:
        print(f"TRANSLATE SKIP: {exc}")
        return f"[원문] {title}"

    if not translated:
        return f"[원문] {title}"
    if source:
        return f"{translated} - {source}"
    return translated


def fetch_news(assets):
    news = {}
    errors = []

    for asset in assets:
        try:
            titles = fetch_news_for_asset(asset)
            news[asset["ticker"]] = titles
            print(f"NEWS {asset['ticker']}: {len(titles)} titles")
        except Exception as exc:
            news[asset["ticker"]] = []
            errors.append(f"{asset['ticker']} 뉴스: {exc}")
            print(f"ERROR NEWS {asset['ticker']}: {exc}")

    return news, errors


def format_price(item):
    if item["currency"] == "POINT":
        return f"{item['price']:,.2f}"
    if item["currency"] == "KRW":
        return f"₩{item['price']:,.0f}"
    return f"${item['price']:.2f}"


def format_signed_amount(amount, currency):
    if currency == "KRW":
        return f"{amount:+,.0f}원"
    if currency == "USD":
        sign = "+" if amount >= 0 else "-"
        return f"{sign}${abs(amount):,.2f}"
    return f"{amount:+,.2f}"


def format_change_amount(item):
    amount = item.get("chg_amount", 0)
    return format_signed_amount(amount, item["currency"])


def format_position_effect(item):
    daily_profit_loss = item.get("daily_profit_loss_amount")
    if daily_profit_loss not in (None, ""):
        return f", 당일손익 {format_signed_amount(float(daily_profit_loss), item['currency'])}"

    shares = item.get("shares")
    if shares in (None, ""):
        return ""

    effect = item.get("chg_amount", 0) * float(shares)
    return f", 평가손익 {format_signed_amount(effect, item['currency'])}"


def format_weight(item):
    weight = item.get("weight_pct")
    if weight in (None, ""):
        return ""
    return f", 비중 {float(weight):.1f}%"


def movement_emoji(chg_pct):
    if chg_pct > 0:
        return "🔴"
    if chg_pct < 0:
        return "🔵"
    return "⚪"


def generate_actions_with_claude(quotes, news):
    if not CLAUDE_API_KEY:
        return {}

    lines = []
    for item in quotes:
        ticker = item["ticker"]
        chg = item["chg_pct"]
        news_titles = news.get(ticker, [])
        news_text = " / ".join(clean_news_headline(t) for t in news_titles) if news_titles else "뉴스 없음"
        lines.append(f"{ticker}: {chg:+.2f}%, 뉴스: {news_text}")

    prompt = (
        "다음 포트폴리오 종목들의 오늘 등락률과 뉴스를 보고, "
        "각 종목에 대한 투자 대응 멘트를 한 줄로 작성해줘. "
        "뉴스 맥락을 반영해서 구체적으로 써줘. "
        "형식: 티커: 멘트 (줄바꿈으로 구분)\n\n"
        + "\n".join(lines)
    )

    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        text = response.json()["content"][0]["text"].strip()

        actions = {}
        for line in text.splitlines():
            if ":" in line:
                ticker, _, msg = line.partition(":")
                ticker = ticker.strip()
                if ticker in {q["ticker"] for q in quotes}:
                    actions[ticker] = f"{ticker}: {msg.strip()}"
        return actions
    except Exception as exc:
        print(f"Claude action generation failed: {exc}")
        return {}


def action_for(item):
    ticker = item["ticker"]
    chg = item["chg_pct"]

    if chg >= 5:
        return f"{ticker}: 하루 +5% 이상 급등. 변동성 확대 구간, 일부 차익실현 또는 손절선 점검."
    if chg >= 3:
        return f"{ticker}: 하루 +3% 이상 상승. 단기 과열 가능성, 추격 매수보다 관망."
    if chg >= 0.5:
        return f"{ticker}: 완만한 상승 흐름. 기존 비중 유지, 큰 조정 시 분할 매수 검토."
    if chg > -0.5:
        return f"{ticker}: 보합권 움직임. 방향성 확인 전까지 기존 전략 유지."
    if chg <= -5:
        return f"{ticker}: 하루 -5% 이상 급락. 손절선과 추가 매수 기준을 먼저 점검."
    if chg <= -3:
        return f"{ticker}: 하루 -3% 이상 하락. 성급한 물타기보다 지지선 확인."
    return f"{ticker}: 약세 흐름. 무리한 신규 매수보다 관망."


def market_summary(quotes):
    positives = [item for item in quotes if item["chg_pct"] > 0]
    negatives = [item for item in quotes if item["chg_pct"] < 0]
    surges = [item for item in quotes if item["chg_pct"] >= 3]
    drops = [item for item in quotes if item["chg_pct"] <= -3]

    if len(positives) == len(quotes) and surges:
        return "레버리지 ETF 전반 강세", "위험자산 선호", surges, drops
    if len(negatives) == len(quotes) and drops:
        return "레버리지 ETF 전반 약세", "위험 회피", surges, drops
    if len(positives) > len(negatives):
        return "상승 우위의 혼조세", "부분적 위험자산 선호", surges, drops
    if len(negatives) > len(positives):
        return "하락 우위의 혼조세", "방어적 대응 우위", surges, drops
    return "방향성 확인 구간", "중립", surges, drops


def market_snapshot(quotes):
    if not quotes:
        return "가격 데이터가 없습니다."

    positives = [item for item in quotes if item["chg_pct"] > 0]
    negatives = [item for item in quotes if item["chg_pct"] < 0]
    flat = len(quotes) - len(positives) - len(negatives)
    strongest = max(quotes, key=lambda item: item["chg_pct"])
    weakest = min(quotes, key=lambda item: item["chg_pct"])

    parts = [f"{len(quotes)}개 중 상승 {len(positives)}개, 하락 {len(negatives)}개"]
    if flat:
        parts.append(f"보합 {flat}개")
    parts.append(f"상대강세 {strongest['ticker']} {strongest['chg_pct']:+.2f}%")
    parts.append(f"최대약세 {weakest['ticker']} {weakest['chg_pct']:+.2f}%")
    return " / ".join(parts)


def focused_headline(quotes, headline):
    if not quotes:
        return headline

    return f"{headline}. {market_snapshot(quotes)}."


def build_alert_lines(quotes, errors, news):
    alerts = []
    for item in quotes:
        if item["chg_pct"] >= 3:
            alerts.append(f"급등: {item['ticker']} {item['chg_pct']:+.2f}%")
        elif item["chg_pct"] <= -3:
            alerts.append(f"급락: {item['ticker']} {item['chg_pct']:+.2f}%")

        if not item.get("news_optional") and not news.get(item["ticker"]):
            alerts.append(f"뉴스 없음: {item['ticker']}")

    if errors:
        alerts.extend(f"데이터 확인: {error}" for error in errors)

    return alerts[:6] if alerts else ["특이사항 없음"]


def build_content(indexes, quotes, news, errors, screen_result=None):
    today_full = datetime.now(KST).strftime("%Y-%m-%d")
    today_short = datetime.now(KST).strftime("%m/%d")
    headline, mood, surges, drops = market_summary(quotes)
    headline_text = focused_headline(quotes, headline)
    providers = sorted({item.get("provider", "Unknown") for item in indexes + quotes})
    provider_text = ", ".join(providers)

    index_lines = [
        f"{movement_emoji(item['chg_pct'])} {item['display']} {format_price(item)} ({item['chg_pct']:+.2f}%)"
        for item in indexes
    ]
    price_lines = [
        (
            f"{movement_emoji(item['chg_pct'])} {item['display']} "
            f"{format_price(item)} ({format_change_amount(item)}, {item['chg_pct']:+.2f}%"
            f"{format_weight(item)}{format_position_effect(item)})"
        )
        for item in quotes
    ]
    alert_lines = [f"  ▸ {line}" for line in build_alert_lines(quotes, errors, news)]
    action_lines = [f"  ▸ {action_for(item)}" for item in quotes]
    surge_text = ", ".join(item["ticker"] for item in surges) if surges else "없음"
    drop_text = ", ".join(item["ticker"] for item in drops) if drops else "없음"
    news_sections = []
    for item in quotes:
        titles = news.get(item["ticker"], [])
        if not titles:
            continue
        news_sections.append((item["display"], titles))
    screen_result = screen_result or {}
    screen_telegram_lines, screen_markdown_lines = build_screening_sections(
        screen_result.get("candidates", []),
        screen_result.get("errors", []),
    )

    # 지수 요약 한 줄
    index_summary = " | ".join(
        f"{item['display']} {format_price(item)} ({item['chg_pct']:+.2f}%)"
        for item in indexes
    )

    # 상승/하락 카운트
    pos_count = sum(1 for q in quotes if q["chg_pct"] > 0)
    neg_count = sum(1 for q in quotes if q["chg_pct"] < 0)
    count_str = f"🔴{pos_count} 🔵{neg_count}"

    # 종목별 한 줄 요약
    def price_row(item):
        alert = "🚨" if abs(item["chg_pct"]) >= 5 else ("⚠️" if abs(item["chg_pct"]) >= 3 else "")
        shares = item.get("shares")
        if shares not in (None, ""):
            effect = item.get("chg_amount", 0) * float(shares)
            if item["currency"] == "USD":
                effect_str = f"  +${effect:,.0f}" if effect >= 0 else f"  -${abs(effect):,.0f}"
            else:
                effect_str = f"  {effect:+,.0f}원"
        else:
            effect_str = ""
        return (
            f"{movement_emoji(item['chg_pct'])} {item['ticker']:<5} "
            f"{format_price(item):>9}  {item['chg_pct']:>+6.2f}%{alert}{effect_str}"
        )

    compact_rows = [price_row(item) for item in quotes]

    # Claude로 대응 멘트 생성 (실패 시 규칙 기반 fallback)
    claude_actions = generate_actions_with_claude(quotes, news)

    # 주목 종목만 대응 한 줄로
    alert_action_lines = []
    for item in quotes:
        if abs(item["chg_pct"]) >= 3:
            icon = "🚨" if abs(item["chg_pct"]) >= 5 else "⚠️"
            if item["ticker"] in claude_actions:
                action_text = claude_actions[item["ticker"]].split(": ", 1)[-1]
            else:
                action_text = action_for(item).split(": ", 1)[-1]
            alert_action_lines.append(f"{icon} {item['ticker']} {item['chg_pct']:+.2f}% → {action_text}")

    # 뉴스 압축 (종목당 1줄)
    flat_news = []
    for item in quotes:
        for title in news.get(item["ticker"], [])[:1]:
            flat_news.append(f"📰 {item['ticker']}: {clean_news_headline(title)}")

    telegram_lines = [f"📈 포트폴리오 브리핑 {today_short}", ""]

    if indexes:
        telegram_lines.append(index_summary)

    telegram_lines.extend([
        f"분위기: {mood} · {count_str}",
        "",
        "─" * 26,
        *compact_rows,
        "─" * 26,
    ])

    if alert_action_lines:
        telegram_lines.extend(["", *alert_action_lines])

    if flat_news:
        telegram_lines.extend(["", *flat_news])

    if errors:
        telegram_lines.extend(["", "⚠️ 오류", *[f"  • {e}" for e in errors]])

    if screen_telegram_lines:
        telegram_lines.extend(screen_telegram_lines)

    md_lines = [
        "# 📈 포트폴리오 일일 브리핑",
        "",
        f"> {today_full} KST",
        "",
        "## 🔎 한 줄 판단",
        "",
        headline_text,
        "",
        f"- 분위기: {mood}",
        f"- 가격 출처: {provider_text}",
        "",
        "## ⚠️ 먼저 볼 것",
        "",
        *[f"- {line}" for line in build_alert_lines(quotes, errors, news)],
        "",
        "## 💰 가격 요약",
        "",
        "### 주요 지수",
        "",
        "| 지수 | 현재가 | 전일비 |",
        "| --- | ---: | ---: |",
    ]

    for item in indexes:
        md_lines.append(f"| {item['name']} | {format_price(item)} | {item['chg_pct']:+.2f}% |")

    md_lines.extend(
        [
            "",
            "### 포트폴리오",
            "",
            "| 종목 | 현재가 | 등락폭 | 등락률 | 비중 | 평가손익 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for item in quotes:
        weight = f"{float(item['weight_pct']):.1f}%" if item.get("weight_pct") not in (None, "") else "-"
        shares = item.get("shares")
        if shares in (None, ""):
            effect = "-"
        elif item.get("daily_profit_loss_amount") not in (None, ""):
            effect = format_signed_amount(float(item["daily_profit_loss_amount"]), item["currency"])
        else:
            effect_value = item.get("chg_amount", 0) * float(shares)
            effect = f"{effect_value:+,.0f}원" if item["currency"] == "KRW" else f"${effect_value:+,.2f}"
        md_lines.append(
            f"| {item['name']} | {format_price(item)} | {format_change_amount(item)} | "
            f"{item['chg_pct']:+.2f}% | {weight} | {effect} |"
        )

    md_lines.extend(
        [
            "",
            "## 🎯 오늘의 대응",
            "",
            *[f"- {action_for(item)}" for item in quotes],
            "",
            "## 📊 변동성 체크",
            "",
            f"- 급등 종목: {surge_text}",
            f"- 급락 종목: {drop_text}",
            f"- 전체 분위기: {mood}",
        ]
    )

    if news_sections:
        md_lines.extend(["", "## 📰 참고 뉴스", ""])
        for display, titles in news_sections:
            md_lines.extend([f"### {display}", ""])
            md_lines.extend(f"- {title}" for title in titles)
            md_lines.append("")

    if errors:
        md_lines.extend(["", "## ⚠️ 데이터 확인 필요", "", *[f"- {error}" for error in errors]])

    if screen_markdown_lines:
        md_lines.extend(screen_markdown_lines)

    return "\n".join(telegram_lines), "\n".join(md_lines) + "\n"


def save_markdown(content):
    today = datetime.now(KST).strftime("%Y%m%d")
    output_dir = "briefings"
    os.makedirs(output_dir, exist_ok=True)

    filename = os.path.join(output_dir, f"briefing_{today}.md")
    with open(filename, "w", encoding="utf-8") as file:
        file.write(content)

    print(f"Saved: {filename}")
    return filename


def send_telegram(message):
    if SEND_TELEGRAM in {"0", "false", "no", "off"}:
        print("SEND_TELEGRAM is disabled. Skipping send.")
        return True

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing. Skipping send.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=20)
    except Exception as exc:
        print(f"Telegram failed: {exc}")
        return False

    if response.status_code == 200:
        print("Telegram sent.")
        return True

    print(f"Telegram failed: {response.status_code} - {response.text[:200]}")
    return False


def main():
    configure_console_output()
    print("=" * 50)
    print(f"Portfolio briefing - {now_kst()}")
    print("=" * 50)

    try:
        screener_config = load_screener_config()
        total_steps = 5
        if screener_config:
            total_steps += 1

        print(f"[1/{total_steps}] Fetching prices...")
        indexes_config, assets_config = load_portfolio()
        indexes, index_errors = fetch_prices(indexes_config, require_any=False)
        quotes, quote_errors = fetch_prices(assets_config)

        next_step = 2
        print(f"[{next_step}/{total_steps}] Fetching news titles...")
        news, news_errors = fetch_news(assets_config)
        errors = index_errors + quote_errors + news_errors

        screen_result = None
        if screener_config:
            next_step += 1
            print(f"[{next_step}/{total_steps}] Screening fundamentals...")
            screen_candidates, screen_errors = screen_fundamental_candidates(screener_config)
            screen_result = {
                "candidates": screen_candidates,
                "errors": screen_errors,
            }

        next_step += 1
        print(f"[{next_step}/{total_steps}] Building rule-based briefing...")
        telegram_msg, md_content = build_content(indexes, quotes, news, errors, screen_result)

        next_step += 1
        print(f"[{next_step}/{total_steps}] Saving markdown...")
        save_markdown(md_content)

        next_step += 1
        print(f"[{next_step}/{total_steps}] Sending Telegram...")
        print(telegram_msg)
        if not send_telegram(telegram_msg):
            raise RuntimeError("Telegram message was not sent.")

        print("Done.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
