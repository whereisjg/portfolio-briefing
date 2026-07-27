#!/usr/bin/env python3
"""
Daily portfolio briefing for GitHub Actions using KIS Open API.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import kis_client
import run_state


def env_value(name, default=""):
    return kis_client.env_value(name, default)


KST = kis_client.KST
TELEGRAM_BOT_TOKEN = env_value("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_value("TELEGRAM_CHAT_ID")
SEND_TELEGRAM = env_value("SEND_TELEGRAM", "true").lower()
TELEGRAM_MESSAGE_FILE = env_value("TELEGRAM_MESSAGE_FILE")
KIS_BALANCE_SNAPSHOT_FILE = env_value("KIS_BALANCE_SNAPSHOT_FILE")
KIS_ACCESS_TOKEN_CACHE_FILE = env_value("KIS_ACCESS_TOKEN_CACHE_FILE")
KIS_TREND_STATE_FILE = env_value("KIS_TREND_STATE_FILE")
KRX_MARKET_NOTICE = env_value("KRX_MARKET_NOTICE")
KIS_ACCESS_TOKEN_MAX_AGE_SECONDS = 6 * 60 * 60

PORTFOLIO_FILE = "portfolio.json"
SIGNIFICANT_MOVE_PCT = 3.0
CRITICAL_MOVE_PCT = 5.0


def get_http_session(retries=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 504)):
    return kis_client.get_http_session(retries, backoff_factor, status_forcelist)

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
    if not os.path.exists(PORTFOLIO_FILE):
        raise FileNotFoundError(f"설정 파일({PORTFOLIO_FILE})을 찾을 수 없습니다.")

    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{PORTFOLIO_FILE}의 JSON 형식이 올바르지 않습니다: {exc}")

    indexes = config.get("indexes", [])
    assets = config.get("assets", [])
    if not assets:
        raise ValueError("portfolio.json에 assets가 없습니다.")

    try:
        target_weights = [
            float(asset["target_weight_pct"])
            for asset in assets
            if str(asset.get("target_weight_pct", "")).strip() not in ("", "None")
        ]
        if target_weights:
            total = sum(target_weights)
            if abs(total - 100) > 1:
                print(f"WARNING: 목표 비중 합계 {total:.2f}% (100%와 {total - 100:+.2f}% 차이)")
    except (ValueError, TypeError) as exc:
        print(f"WARNING: target_weight_pct 변환 오류, 비중 검증 건너뜀: {exc}")

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


def kis_enabled():
    return env_value("KIS_BALANCE_ENABLED", "false").lower() == "true"


def kis_is_paper():
    return kis_client.is_paper()


def kis_tr_id(real, paper):
    return kis_client.transaction_id(real, paper)


def kis_required(name):
    return kis_client.required(name)


def load_cached_kis_access_token():
    return kis_client.load_cached_access_token(
        KIS_ACCESS_TOKEN_CACHE_FILE,
        KIS_ACCESS_TOKEN_MAX_AGE_SECONDS,
    )


def save_kis_access_token(access_token):
    kis_client.save_access_token(KIS_ACCESS_TOKEN_CACHE_FILE, access_token)


def get_kis_access_token(app_key, app_secret, base_url):
    return kis_client.get_access_token(
        app_key,
        app_secret,
        base_url,
        KIS_ACCESS_TOKEN_CACHE_FILE,
        get_http_session,
    )


def fetch_kis_balance():
    return kis_client.fetch_balance(get_http_session, KIS_ACCESS_TOKEN_CACHE_FILE)


def fetch_kis_index_quote(index, access_token):
    """KIS 해외지수 일별시세에서 최신 지수와 전일 종가를 읽는다."""
    app_key = kis_required("KIS_APP_KEY")
    app_secret = kis_required("KIS_APP_SECRET")
    symbol = index.get("kis_symbol")
    if not symbol:
        raise ValueError(f"KIS 지수 코드가 없습니다: {index.get('ticker')}")

    today = datetime.now(KST).date()
    params = {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": (today - timedelta(days=10)).strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": today.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": "D",
    }
    response = get_http_session(retries=1).get(
        f"{env_value('KIS_API_BASE_URL', 'https://openapi.koreainvestment.com:9443')}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice",
        headers={
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "FHKST03030100",
            "custtype": "P",
        },
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 지수조회 실패({symbol}): {payload.get('msg1', '알 수 없는 오류')}")

    output = payload.get("output1") or {}
    price = as_float(output.get("ovrs_nmix_prpr"), None)
    previous_close = as_float(output.get("ovrs_nmix_prdy_clpr"), None)
    return quote_from_price(index, price, previous_close, "KIS")


def fetch_kis_indexes(indexes, access_token):
    quotes = []
    errors = []
    for index in indexes:
        try:
            quote = fetch_kis_index_quote(index, access_token)
            quotes.append(quote)
            print(f"OK {index['ticker']}: {quote['price']} ({quote['chg_pct']:+.2f}%)")
        except Exception as exc:
            errors.append(f"{index['ticker']}: {exc}")
            print(f"ERROR {index['ticker']}: {exc}")
    return quotes, errors


def save_kis_balance_snapshot(holdings, summary, access_token):
    """Share one KIS balance response with later workflow steps."""
    if not KIS_BALANCE_SNAPSHOT_FILE:
        return
    run_state.save_balance_snapshot(KIS_BALANCE_SNAPSHOT_FILE, holdings, summary, access_token)
    print(f"KIS balance snapshot saved: {KIS_BALANCE_SNAPSHOT_FILE}")


def as_float(value, default=0.0):
    return kis_client.as_float(value, default)


def fetch_kis_domestic_quotes(codes, access_token):
    """잔고 응답의 0% 변동값 대신 KIS 현재가 API에서 등락률을 읽는다."""
    app_key = kis_required("KIS_APP_KEY")
    app_secret = kis_required("KIS_APP_SECRET")
    base_url = env_value("KIS_API_BASE_URL", "https://openapi.koreainvestment.com:9443")
    session = get_http_session(retries=1)
    quotes = {}
    errors = []

    for index, code in enumerate(codes):
        if index:
            time.sleep(1.1)
        try:
            response = session.get(
                f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers={
                    "authorization": f"Bearer {access_token}",
                    "appkey": app_key,
                    "appsecret": app_secret,
                    "tr_id": "FHKST01010100",
                    "custtype": "P",
                },
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("rt_cd") != "0":
                raise ValueError(payload.get("msg1", "알 수 없는 오류"))
            output = payload.get("output") or {}
            price = as_float(output.get("stck_prpr"), None)
            change_pct = as_float(output.get("prdy_ctrt"), None)
            change_amount = as_float(output.get("prdy_vrss"), None)
            sign = str(output.get("prdy_vrss_sign", "")).strip()
            if price is None or price <= 0 or change_pct is None:
                raise ValueError("현재가 또는 등락률이 없습니다.")
            if sign in {"4", "5"} and change_amount is not None:
                change_amount = -abs(change_amount)
            elif sign in {"1", "2"} and change_amount is not None:
                change_amount = abs(change_amount)
            previous_close = price / (1 + change_pct / 100) if change_pct != -100 else price
            quotes[code] = {
                "price": price,
                "prev_close": previous_close,
                "chg_amount": change_amount if change_amount is not None else price - previous_close,
                "chg_pct": change_pct,
            }
        except Exception as exc:
            errors.append(f"{code} KIS 현재가 조회 실패: {exc}")
    return quotes, errors


def assets_from_kis_balance(configured_assets, holdings, market_quotes=None):
    """설정의 뉴스·목표비중 정보와 KIS의 실제 보유 수량을 결합한다."""
    configured_by_code = {
        asset.get("symbol", "").split(".")[0]: asset for asset in configured_assets
    }
    assets = []
    for holding in holdings:
        shares = as_float(holding.get("hldg_qty"))
        if shares <= 0:
            continue
        code = str(holding.get("pdno", "")).strip()
        base = configured_by_code.get(code, {})
        market_quote = (market_quotes or {}).get(code, {})
        price = market_quote.get("price", as_float(holding.get("prpr")))
        previous_close = market_quote.get("prev_close", price - as_float(holding.get("prdy_vrss")))
        if previous_close <= 0:
            previous_close = price
        assets.append({
            **base,
            "ticker": base.get("ticker") or code,
            "symbol": base.get("symbol") or f"{code}.KS",
            "name": base.get("name") or holding.get("prdt_name") or code,
            "display": base.get("display") or holding.get("prdt_name") or code,
            "currency": "KRW",
            "shares": shares,
            "price": price,
            "prev_close": previous_close,
            "chg_amount": market_quote.get("chg_amount", price - previous_close),
            "chg_pct": market_quote.get("chg_pct", (price - previous_close) / previous_close * 100 if previous_close else 0),
            "average_price": as_float(holding.get("pchs_avg_pric")),
            "evaluation_profit_loss_amount": as_float(holding.get("evlu_pfls_amt")),
            "provider": "KIS",
        })
    return assets


def load_trend_state():
    if not KIS_TREND_STATE_FILE or not os.path.exists(KIS_TREND_STATE_FILE):
        return None
    state = run_state.load_json(KIS_TREND_STATE_FILE)
    if not isinstance((state or {}).get("weights"), dict):
        return None
    return state


def apply_trend_weights(assets, trend_state):
    if not trend_state:
        return assets
    weights = trend_state["weights"]
    return [
        {
            **asset,
            "target_weight_pct": weights.get(asset.get("symbol", "").split(".")[0], asset.get("target_weight_pct")),
        }
        for asset in assets
    ]


def compute_weights(quotes):
    """KIS 국내주식 잔고의 현재가 기준으로 비중을 재계산한다."""
    values = []
    for q in quotes:
        shares = q.get("shares")
        if shares in (None, "") or float(shares) <= 0:
            values.append(None)
            continue
        values.append(float(shares) * q["price"])
    total = sum(v for v in values if v is not None)
    if not total:
        return
    for q, v in zip(quotes, values):
        if v is not None:
            q["weight_pct"] = round(v / total * 100, 2)


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


def format_krw_short(amount):
    return f"{round(float(amount) / 10000):,.0f}만"


def format_change_amount(item):
    amount = item.get("chg_amount", 0)
    return format_signed_amount(amount, item["currency"])


def format_position_effect(item):
    evaluation_profit_loss = item.get("evaluation_profit_loss_amount")
    if evaluation_profit_loss not in (None, ""):
        return f", 평가손익 {format_signed_amount(float(evaluation_profit_loss), item['currency'])}"

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


def format_average_price(item):
    average_price = item.get("average_price")
    if average_price in (None, "") or float(average_price) <= 0:
        return ""
    if item["currency"] == "KRW":
        return f"평단 ₩{float(average_price):,.0f}"
    return f"평단 ${float(average_price):,.2f}"


def movement_emoji(chg_pct):
    if chg_pct > 0:
        return "🔴"
    if chg_pct < 0:
        return "🔵"
    return "⚪"


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
    surges = [item for item in quotes if item["chg_pct"] >= SIGNIFICANT_MOVE_PCT]
    drops = [item for item in quotes if item["chg_pct"] <= -SIGNIFICANT_MOVE_PCT]

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


def build_alert_lines(quotes, errors):
    alerts = []
    for item in quotes:
        if item["chg_pct"] >= SIGNIFICANT_MOVE_PCT:
            alerts.append(f"급등: {item['ticker']} {item['chg_pct']:+.2f}%")
        elif item["chg_pct"] <= -SIGNIFICANT_MOVE_PCT:
            alerts.append(f"급락: {item['ticker']} {item['chg_pct']:+.2f}%")

    if errors:
        alerts.extend(f"데이터 확인: {error}" for error in errors)

    return alerts[:6] if alerts else ["특이사항 없음"]


def build_rebalancing_lines(quotes):
    """목표 비중과 현재 비중의 차이로 신규 매수 우선순위를 만든다."""
    rows = []
    for item in quotes:
        target = item.get("target_weight_pct")
        current = item.get("weight_pct")
        if target in (None, "") or current in (None, ""):
            continue
        gap = float(current) - float(target)
        if gap <= -1:
            action = "신규 매수 우선"
        elif gap >= 1:
            action = "신규 매수 보류"
        else:
            action = "목표 범위"
        rows.append((item["display"], float(target), float(current), action))
    return rows


def build_content(indexes, quotes, errors, account_summary=None, trend_state=None, market_notice=""):
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
    alert_lines = [f"  ▸ {line}" for line in build_alert_lines(quotes, errors)]
    surge_text = ", ".join(item["ticker"] for item in surges) if surges else "없음"
    drop_text = ", ".join(item["ticker"] for item in drops) if drops else "없음"
    rebalancing_rows = build_rebalancing_lines(quotes)

    usd_to_krw = next((q["usd_to_krw"] for q in quotes if q.get("usd_to_krw")), None)
    index_lines_list = [
        f"{item['display']} {format_price(item)} ({item['chg_pct']:+.2f}%)"
        for item in indexes
    ]
    if usd_to_krw:
        index_lines_list.append(f"환율 ₩{usd_to_krw:,.0f}")
    index_summary = "\n".join(index_lines_list)

    pos_count = sum(1 for q in quotes if q["chg_pct"] > 0)
    neg_count = sum(1 for q in quotes if q["chg_pct"] < 0)
    count_str = f"🔴{pos_count} 🔵{neg_count}"
    trend_labels = {"risk_on": "위험 선호", "neutral": "중립", "risk_off": "위험 회피"}
    trend_marks = {"risk_on": "↑", "neutral": "↔", "risk_off": "↓"}
    composite_signal_line = ""
    if trend_state and trend_state.get("components"):
        composite_signal_line = "신호: " + " · ".join(
            f"{component['label']}{trend_marks.get(component.get('state'), '↔')}"
            for component in trend_state["components"]
        )

    def price_row(item):
        alert = "🚨" if abs(item["chg_pct"]) >= CRITICAL_MOVE_PCT else ("⚠️" if abs(item["chg_pct"]) >= SIGNIFICANT_MOVE_PCT else "")
        rate = item.get("usd_to_krw")
        if item["currency"] == "USD" and rate:
            price_str = f"₩{item['price'] * rate:,.0f}"
        else:
            price_str = format_price(item)
        shares = item.get("shares")
        evaluation_profit_loss = item.get("evaluation_profit_loss_amount")
        if evaluation_profit_loss not in (None, ""):
            effect_str = format_signed_amount(float(evaluation_profit_loss), item["currency"])
        elif shares not in (None, "") and float(shares) > 0:
            effect = item.get("chg_amount", 0) * float(shares)
            effect_str = format_signed_amount(effect * rate, "KRW") if item["currency"] == "USD" and rate else format_signed_amount(effect, item["currency"])
        else:
            effect_str = ""
        weight = item.get("weight_pct")
        weight_str = f"비중 {float(weight):.1f}%" if weight not in (None, "") else ""
        details = " · ".join(x for x in [weight_str, effect_str] if x)
        first_line = f"{movement_emoji(item['chg_pct'])} {item['display']} {item['chg_pct']:+.2f}%{alert}"
        second_line = " · ".join(x for x in [price_str, details] if x)
        return f"{first_line}\n   {second_line}" if second_line else first_line

    compact_rows = [price_row(item) for item in quotes]

    claude_actions = {}

    alert_action_lines = []
    for item in quotes:
        if abs(item["chg_pct"]) >= SIGNIFICANT_MOVE_PCT:
            icon = "🚨" if abs(item["chg_pct"]) >= CRITICAL_MOVE_PCT else "⚠️"
            if item["ticker"] in claude_actions:
                action_text = claude_actions[item["ticker"]].split(": ", 1)[-1]
            else:
                action_text = action_for(item).split(": ", 1)[-1]
            alert_action_lines.append(f"{icon} {item['display']} {item['chg_pct']:+.2f}%\n{action_text}")

    telegram_lines = [f"📈 포트폴리오 {today_short} · 추세 {trend_labels.get(trend_state.get('state'), '중립') if trend_state else '중립'}"]
    if market_notice:
        telegram_lines.extend([market_notice, ""])
    else:
        telegram_lines.append("")

    if indexes:
        telegram_lines.append(index_summary)

    if account_summary:
        total = as_float(account_summary.get("tot_evlu_amt"))
        cash = as_float(account_summary.get("prvs_rcdl_excc_amt"))
        telegram_lines.append(f"자산 {format_krw_short(total)} · 예수금 {format_krw_short(cash)}")

    if composite_signal_line:
        telegram_lines.append(composite_signal_line)

    telegram_lines.extend([
        f"오늘 흐름: {pos_count} 상승 · {neg_count} 하락",
        "",
        "📍 보유",
        *compact_rows,
    ])

    if alert_action_lines:
        telegram_lines.extend(["", *alert_action_lines])

    if errors:
        telegram_lines.extend(["", "⚠️ 오류", *[f"  • {e}" for e in errors]])

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
        *([f"- {composite_signal_line}"] if composite_signal_line else []),
        "",
        *(["## 📌 시장 상태", "", market_notice, ""] if market_notice else []),
        "## ⚠️ 먼저 볼 것",
        "",
        *[f"- {line}" for line in build_alert_lines(quotes, errors)],
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
        if shares in (None, "") or float(shares) <= 0:
            effect = "-"
        elif item.get("evaluation_profit_loss_amount") not in (None, ""):
            effect = format_signed_amount(float(item["evaluation_profit_loss_amount"]), item["currency"])
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
            *[
                f"- {item['ticker']}: {claude_actions[item['ticker']].split(': ', 1)[-1]}"
                if item["ticker"] in claude_actions
                else f"- {action_for(item)}"
                for item in quotes
            ],
            "",
            "## 📊 변동성 체크",
            "",
            f"- 급등 종목: {surge_text}",
            f"- 급락 종목: {drop_text}",
            f"- 전체 분위기: {mood}",
        ]
    )

    if account_summary:
        total = as_float(account_summary.get("tot_evlu_amt"))
        cash = as_float(account_summary.get("prvs_rcdl_excc_amt"))
        md_lines.extend([
            "",
            "## 💳 계좌 요약",
            "",
            f"- 평가금액: ₩{total:,.0f}",
            f"- 출금가능 예수금: ₩{cash:,.0f}",
        ])

    if rebalancing_rows:
        md_lines.extend(["", "## 📊 리밸런싱", ""])
        md_lines.extend(
            f"- {display}: 목표 {target:.0f}% / 현재 {current:.1f}% → {action}"
            for display, target, current, action in rebalancing_rows
        )

    if errors:
        md_lines.extend(["", "## ⚠️ 데이터 확인 필요", "", *[f"- {error}" for error in errors]])

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

    MAX_LEN = 4096
    chunks = []
    remaining = message
    while len(remaining) > MAX_LEN:
        split_at = remaining.rfind("\n", 0, MAX_LEN)
        if split_at <= 0:
            split_at = MAX_LEN
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)

    session = get_http_session()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i, chunk in enumerate(chunks, 1):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        try:
            response = session.post(url, json=payload, timeout=20)
        except Exception as exc:
            print(f"Telegram failed: {exc}")
            return False

        if response.status_code != 200:
            print(f"Telegram failed: {response.status_code} - {response.text[:200]}")
            return False

        if len(chunks) > 1:
            print(f"Telegram sent ({i}/{len(chunks)}).")

    print("Telegram sent.")
    return True


def main():
    configure_console_output()
    print("=" * 50)
    print(f"Portfolio briefing - {now_kst()}")
    print("=" * 50)

    try:
        total_steps = 5

        print(f"[1/{total_steps}] Loading portfolio...")
        indexes_config, assets_config = load_portfolio()

        if not kis_enabled():
            raise ValueError("KIS_BALANCE_ENABLED=true 설정이 필요합니다.")

        print(f"[2/{total_steps}] Fetching KIS balance...")
        holdings, account_summary, access_token = fetch_kis_balance()
        save_kis_balance_snapshot(holdings, account_summary, access_token)
        print(f"[2/{total_steps}] Fetching KIS indexes...")
        indexes, index_errors = fetch_kis_indexes(indexes_config, access_token)
        market_quotes, market_quote_errors = fetch_kis_domestic_quotes(
            [str(holding.get("pdno", "")).strip() for holding in holdings if holding.get("hldg_qty")],
            access_token,
        )
        assets_config = assets_from_kis_balance(assets_config, holdings, market_quotes)
        trend_state = load_trend_state()
        assets_config = apply_trend_weights(assets_config, trend_state)
        quotes = assets_config
        quote_errors = []
        compute_weights(quotes)
        print(f"KIS 잔고조회 완료: 보유 {len(quotes)}종목")

        errors = index_errors + market_quote_errors + quote_errors

        next_step = 3
        print(f"[{next_step}/{total_steps}] Building rule-based briefing...")
        telegram_msg, md_content = build_content(
            indexes, quotes, errors, account_summary, trend_state, KRX_MARKET_NOTICE
        )

        next_step += 1
        print(f"[{next_step}/{total_steps}] Saving markdown...")
        save_markdown(md_content)

        next_step += 1
        print(f"[{next_step}/{total_steps}] Sending Telegram...")
        print(telegram_msg)
        if TELEGRAM_MESSAGE_FILE:
            with open(TELEGRAM_MESSAGE_FILE, "w", encoding="utf-8") as file:
                file.write(telegram_msg)
            print(f"Telegram message saved: {TELEGRAM_MESSAGE_FILE}")
        elif not send_telegram(telegram_msg):
            raise RuntimeError("Telegram message was not sent.")

        print("Done.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
