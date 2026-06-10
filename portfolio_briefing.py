#!/usr/bin/env python3
"""
Daily portfolio briefing for GitHub Actions.

This version does not call an AI API. It fetches prices directly from Yahoo
Finance, applies simple rule-based guidance, sends Telegram, and saves markdown.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, unquote
import xml.etree.ElementTree as ET

import pytz
import requests


KST = pytz.timezone("Asia/Seoul")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SEND_TELEGRAM = os.getenv("SEND_TELEGRAM", "true").strip().lower()
TOSS_CLIENT_ID = (os.getenv("TOSS_CLIENT_ID") or os.getenv("TOSS_API_KEY", "")).strip()
TOSS_CLIENT_SECRET = (os.getenv("TOSS_CLIENT_SECRET") or os.getenv("TOSS_API_SECRET", "")).strip()
TOSS_BASE_URL = os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com").strip().rstrip("/")
TOSS_TOKEN_URL = os.getenv("TOSS_TOKEN_URL", "").strip()
TOSS_QUOTE_URL_TEMPLATE = os.getenv("TOSS_QUOTE_URL_TEMPLATE", "").strip()
TOSS_CANDLE_URL_TEMPLATE = os.getenv("TOSS_CANDLE_URL_TEMPLATE", "").strip()
TOSS_ACCESS_TOKEN = os.getenv("TOSS_ACCESS_TOKEN", "").strip()
TOSS_ACCOUNT_SEQ = os.getenv("TOSS_ACCOUNT_SEQ", "").strip()
TOSS_ENABLE_LIVE_ORDERS = os.getenv("TOSS_ENABLE_LIVE_ORDERS", "false").strip().lower()
TOSS_LIVE_ORDER_CONFIRM = os.getenv("TOSS_LIVE_ORDER_CONFIRM", "").strip()
TOSS_LIVE_ORDER_CONFIRM_PHRASE = "LIVE_ORDER_APPROVED"

PORTFOLIO_FILE = "portfolio.json"
_TOSS_ACCESS_TOKEN_CACHE = None


class TossApiError(RuntimeError):
    def __init__(self, status_code, code, message, request_id=None):
        parts = [f"Toss API {status_code}"]
        if code:
            parts.append(str(code))
        if message:
            parts.append(str(message))
        if request_id:
            parts.append(f"requestId={request_id}")
        super().__init__(" - ".join(parts))
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id


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


def pick_first_value(data, keys):
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = pick_first_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = pick_first_value(value, keys)
            if found not in (None, ""):
                return found
    return None


def toss_market_code(asset):
    if asset.get("toss_market"):
        return asset["toss_market"]
    if asset.get("currency") == "KRW":
        return "KR"
    if asset.get("currency") == "USD":
        return "US"
    return asset.get("currency", "")


def toss_symbol(asset):
    return asset.get("toss_symbol") or asset.get("symbol", "").replace(".KS", "")


def toss_token_url():
    if TOSS_TOKEN_URL:
        return TOSS_TOKEN_URL
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/oauth2/token"
    return ""


def toss_quote_url():
    if TOSS_QUOTE_URL_TEMPLATE:
        return TOSS_QUOTE_URL_TEMPLATE
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/prices"
    return ""


def toss_candle_url():
    if TOSS_CANDLE_URL_TEMPLATE:
        return TOSS_CANDLE_URL_TEMPLATE
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/candles"
    return ""


def toss_accounts_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/accounts"
    return ""


def toss_holdings_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/holdings"
    return ""


def toss_buying_power_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/buying-power"
    return ""


def toss_sellable_quantity_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/sellable-quantity"
    return ""


def toss_commissions_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/commissions"
    return ""


def toss_orders_url(order_id=None):
    if not TOSS_BASE_URL:
        return ""
    if order_id:
        return f"{TOSS_BASE_URL}/api/v1/orders/{quote_plus(str(order_id))}"
    return f"{TOSS_BASE_URL}/api/v1/orders"


def toss_order_modify_url(order_id):
    if not TOSS_BASE_URL:
        return ""
    return f"{TOSS_BASE_URL}/api/v1/orders/{quote_plus(str(order_id))}/modify"


def toss_order_cancel_url(order_id):
    if not TOSS_BASE_URL:
        return ""
    return f"{TOSS_BASE_URL}/api/v1/orders/{quote_plus(str(order_id))}/cancel"


def toss_exchange_rate_url():
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/exchange-rate"
    return ""


def toss_market_calendar_url(market_country):
    if TOSS_BASE_URL:
        return f"{TOSS_BASE_URL}/api/v1/market-calendar/{quote_plus(str(market_country).upper())}"
    return ""


def toss_is_configured():
    return bool(
        toss_quote_url()
        and toss_candle_url()
        and (TOSS_ACCESS_TOKEN or (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET and toss_token_url()))
    )


def toss_account_is_configured():
    return bool(toss_holdings_url() and (TOSS_ACCESS_TOKEN or (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET and toss_token_url())))


def toss_orders_are_configured():
    return bool(toss_orders_url() and (TOSS_ACCESS_TOKEN or (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET and toss_token_url())))


def live_toss_orders_enabled():
    return TOSS_ENABLE_LIVE_ORDERS == "true" and TOSS_LIVE_ORDER_CONFIRM == TOSS_LIVE_ORDER_CONFIRM_PHRASE


def toss_error_from_response(response):
    request_id = response.headers.get("X-Request-Id") or response.headers.get("cf-ray")
    code = None
    message = None

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        request_id = error.get("requestId") or request_id
        code = error.get("code")
        message = error.get("message")

    if not message:
        message = response.text[:200].strip()

    return TossApiError(response.status_code, code, message, request_id)


def toss_request(method, url, **kwargs):
    for attempt in range(3):
        response = requests.request(method, url, timeout=20, **kwargs)
        if response.status_code < 400:
            return response
        if response.status_code != 429:
            raise toss_error_from_response(response)

        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = float(retry_after) if retry_after else 2**attempt
        except ValueError:
            wait_seconds = 2**attempt
        time.sleep(min(wait_seconds, 8))

    raise toss_error_from_response(response)


def fetch_toss_access_token():
    global _TOSS_ACCESS_TOKEN_CACHE

    if TOSS_ACCESS_TOKEN:
        return TOSS_ACCESS_TOKEN
    if _TOSS_ACCESS_TOKEN_CACHE:
        return _TOSS_ACCESS_TOKEN_CACHE
    token_url = toss_token_url()
    if not (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET and token_url):
        raise ValueError("Toss API token settings are missing.")

    response = toss_request(
        "POST",
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": TOSS_CLIENT_ID,
            "client_secret": TOSS_CLIENT_SECRET,
        },
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    data = response.json()
    token = pick_first_value(data, ["access_token", "accessToken", "token"])
    if not token:
        raise ValueError("Toss access token was not found in response.")

    _TOSS_ACCESS_TOKEN_CACHE = str(token)
    return _TOSS_ACCESS_TOKEN_CACHE


def authorized_toss_headers(token, account_seq=None):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if account_seq not in (None, ""):
        headers["X-Tossinvest-Account"] = str(account_seq)
    return headers


def fetch_toss_account_seq(token):
    if TOSS_ACCOUNT_SEQ:
        return TOSS_ACCOUNT_SEQ
    if not toss_accounts_url():
        raise ValueError("Toss accounts URL is not configured.")

    response = toss_request(
        "GET",
        toss_accounts_url(),
        headers=authorized_toss_headers(token),
    )
    result = response.json().get("result", [])
    if not isinstance(result, list) or not result:
        raise ValueError("Toss account list is empty.")

    account_seq = result[0].get("accountSeq")
    if account_seq in (None, ""):
        raise ValueError("Toss accountSeq was not found.")
    return str(account_seq)


def fetch_toss_holdings():
    if not toss_account_is_configured():
        raise ValueError("Toss holdings API is not configured.")

    token = fetch_toss_access_token()
    account_seq = fetch_toss_account_seq(token)
    response = toss_request(
        "GET",
        toss_holdings_url(),
        headers=authorized_toss_headers(token, account_seq),
    )
    result = response.json().get("result", {})
    items = result.get("items", []) if isinstance(result, dict) else []
    return items if isinstance(items, list) else []


def fetch_toss_account_resource(url, params=None):
    if not toss_account_is_configured():
        raise ValueError("Toss account API is not configured.")
    if not url:
        raise ValueError("Toss account resource URL is not configured.")

    token = fetch_toss_access_token()
    account_seq = fetch_toss_account_seq(token)
    response = toss_request(
        "GET",
        url,
        params=params,
        headers=authorized_toss_headers(token, account_seq),
    )
    return response.json().get("result")


def fetch_toss_buying_power(currency):
    if currency not in ("KRW", "USD"):
        raise ValueError("currency must be KRW or USD.")
    return fetch_toss_account_resource(
        toss_buying_power_url(),
        params={"currency": currency},
    )


def fetch_toss_sellable_quantity(symbol):
    if not symbol:
        raise ValueError("symbol is required.")
    return fetch_toss_account_resource(
        toss_sellable_quantity_url(),
        params={"symbol": str(symbol)},
    )


def fetch_toss_commissions():
    result = fetch_toss_account_resource(toss_commissions_url())
    return result if isinstance(result, list) else []


def fetch_toss_open_orders():
    result = fetch_toss_account_resource(toss_orders_url(), params={"status": "OPEN"})
    return result if isinstance(result, list) else []


def fetch_toss_order_detail(order_id):
    if not order_id:
        raise ValueError("order_id is required.")
    return fetch_toss_account_resource(toss_orders_url(order_id))


def fetch_toss_exchange_rate():
    if not toss_is_configured():
        raise ValueError("Toss API is not configured.")
    response = toss_request(
        "GET",
        toss_exchange_rate_url(),
        headers=authorized_toss_headers(fetch_toss_access_token()),
    )
    return response.json().get("result")


def fetch_toss_market_calendar(market_country):
    if market_country not in ("KR", "US"):
        raise ValueError("market_country must be KR or US.")
    if not toss_is_configured():
        raise ValueError("Toss API is not configured.")
    response = toss_request(
        "GET",
        toss_market_calendar_url(market_country),
        headers=authorized_toss_headers(fetch_toss_access_token()),
    )
    return response.json().get("result")


def build_toss_order_payload(
    symbol,
    side,
    order_type,
    quantity=None,
    order_amount=None,
    price=None,
    client_order_id=None,
    confirm_high_value_order=False,
    time_in_force=None,
):
    if not symbol:
        raise ValueError("symbol is required.")
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL.")
    if order_type not in ("LIMIT", "MARKET"):
        raise ValueError("order_type must be LIMIT or MARKET.")
    if (quantity is None) == (order_amount is None):
        raise ValueError("Set exactly one of quantity or order_amount.")
    if order_type == "LIMIT" and price in (None, ""):
        raise ValueError("LIMIT orders require price.")
    if order_type == "MARKET" and price not in (None, ""):
        raise ValueError("MARKET orders must not include price.")

    payload = {
        "symbol": str(symbol),
        "side": side,
        "orderType": order_type,
    }
    if quantity is not None:
        payload["quantity"] = str(quantity)
    if order_amount is not None:
        payload["orderAmount"] = str(order_amount)
    if price not in (None, ""):
        payload["price"] = str(price)
    if client_order_id:
        payload["clientOrderId"] = str(client_order_id)
    if confirm_high_value_order:
        payload["confirmHighValueOrder"] = True
    if time_in_force:
        payload["timeInForce"] = str(time_in_force)
    return payload


def submit_toss_order(order_payload, dry_run=True):
    return send_toss_order_request("POST", toss_orders_url(), order_payload, dry_run=dry_run)


def modify_toss_order(order_id, order_payload, dry_run=True):
    if not order_id:
        raise ValueError("order_id is required.")
    return send_toss_order_request("POST", toss_order_modify_url(order_id), order_payload, dry_run=dry_run)


def cancel_toss_order(order_id, dry_run=True):
    if not order_id:
        raise ValueError("order_id is required.")
    return send_toss_order_request("POST", toss_order_cancel_url(order_id), {}, dry_run=dry_run)


def send_toss_order_request(method, url, payload, dry_run=True):
    if not toss_orders_are_configured():
        raise ValueError("Toss order API is not configured.")
    if dry_run or not live_toss_orders_enabled():
        return {
            "dryRun": True,
            "method": method,
            "url": url,
            "payload": payload,
            "liveOrderRequiredEnv": {
                "TOSS_ENABLE_LIVE_ORDERS": "true",
                "TOSS_LIVE_ORDER_CONFIRM": TOSS_LIVE_ORDER_CONFIRM_PHRASE,
            },
        }

    token = fetch_toss_access_token()
    account_seq = fetch_toss_account_seq(token)
    response = toss_request(
        method,
        url,
        json=payload,
        headers=authorized_toss_headers(token, account_seq),
    )
    return response.json().get("result")


def holding_map_key(symbol):
    return str(symbol or "").casefold()


def normalize_holding_item(item):
    return {
        "shares": item.get("quantity"),
        "average_purchase_price": item.get("averagePurchasePrice"),
        "holding_market_value": pick_first_value(item.get("marketValue", {}), ["amount"]),
        "holding_profit_loss_amount": pick_first_value(item.get("profitLoss", {}), ["amount"]),
        "holding_profit_loss_rate": pick_first_value(item.get("profitLoss", {}), ["rate"]),
        "daily_profit_loss_amount": pick_first_value(item.get("dailyProfitLoss", {}), ["amount"]),
        "daily_profit_loss_rate": pick_first_value(item.get("dailyProfitLoss", {}), ["rate"]),
    }


def apply_toss_holdings_to_assets(assets):
    if not toss_account_is_configured():
        return assets, []

    try:
        holdings = fetch_toss_holdings()
    except Exception as exc:
        return assets, [f"Toss 보유자산: {exc}"]

    holdings_by_symbol = {
        holding_map_key(item.get("symbol")): normalize_holding_item(item)
        for item in holdings
        if isinstance(item, dict)
    }
    enriched_assets = []
    for asset in assets:
        holding = holdings_by_symbol.get(holding_map_key(toss_symbol(asset)))
        enriched_assets.append({**asset, **holding} if holding else asset)
    return enriched_assets, []


def to_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_toss_management_snapshot():
    if not toss_account_is_configured():
        return {}, []

    snapshot = {"buying_power": {}, "open_orders": []}
    errors = []

    for currency in ("KRW", "USD"):
        try:
            result = fetch_toss_buying_power(currency)
            if isinstance(result, dict):
                snapshot["buying_power"][currency] = result.get("cashBuyingPower")
        except Exception as exc:
            errors.append(f"Toss 매수가능금액 {currency}: {exc}")

    try:
        snapshot["open_orders"] = fetch_toss_open_orders()
    except Exception as exc:
        errors.append(f"Toss 대기주문: {exc}")

    return snapshot, errors


def account_totals_from_quotes(quotes):
    totals = {}
    for item in quotes:
        currency = item.get("currency", "")
        if currency not in totals:
            totals[currency] = {
                "market_value": 0.0,
                "daily_profit_loss": 0.0,
                "profit_loss": 0.0,
                "holding_count": 0,
            }

        shares = item.get("shares")
        if shares not in (None, ""):
            totals[currency]["holding_count"] += 1

        market_value = item.get("holding_market_value")
        if market_value in (None, "") and shares not in (None, ""):
            market_value = to_float(item.get("price")) * to_float(shares)

        totals[currency]["market_value"] += to_float(market_value)
        totals[currency]["daily_profit_loss"] += to_float(item.get("daily_profit_loss_amount"))
        totals[currency]["profit_loss"] += to_float(item.get("holding_profit_loss_amount"))

    return totals


def account_summary_lines(quotes, account_snapshot=None):
    account_snapshot = account_snapshot or {}
    totals = account_totals_from_quotes(quotes)
    lines = []

    for currency in ("KRW", "USD"):
        total = totals.get(currency)
        if not total or total["holding_count"] == 0:
            continue
        line = (
            f"{currency}: 보유 {total['holding_count']}개, 평가금액 "
            f"{format_signed_amount(total['market_value'], currency).lstrip('+')}, "
            f"당일손익 {format_signed_amount(total['daily_profit_loss'], currency)}"
        )
        if total["profit_loss"]:
            line += f", 누적손익 {format_signed_amount(total['profit_loss'], currency)}"
        lines.append(line)

    buying_power = account_snapshot.get("buying_power", {})
    for currency in ("KRW", "USD"):
        amount = buying_power.get(currency)
        if amount not in (None, ""):
            lines.append(f"{currency} 매수가능금액: {format_signed_amount(to_float(amount), currency).lstrip('+')}")

    open_orders = account_snapshot.get("open_orders")
    if isinstance(open_orders, list):
        lines.append(f"대기 주문: {len(open_orders)}건")

    return lines or ["계좌 데이터 없음"]


def select_toss_price_item(asset, data):
    result = data.get("result") if isinstance(data, dict) else data
    items = result if isinstance(result, list) else [result]
    symbol = toss_symbol(asset).casefold()

    for item in items:
        if not isinstance(item, dict):
            continue
        item_symbol = str(item.get("symbol", "")).casefold()
        if item_symbol == symbol:
            return item

    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def toss_previous_close_from_candles(candle_data):
    result = candle_data.get("result") if isinstance(candle_data, dict) else candle_data
    candles = result.get("candles") if isinstance(result, dict) else result
    if not isinstance(candles, list) or len(candles) < 2:
        raise ValueError("Toss candle response does not include previous close.")

    previous_close = candles[1].get("closePrice") if isinstance(candles[1], dict) else None
    if previous_close in (None, ""):
        raise ValueError("Toss previous close was not found in candle response.")
    return previous_close


def parse_toss_quote(asset, price_data, candle_data=None):
    price_item = select_toss_price_item(asset, price_data)
    price = pick_first_value(price_item, ["lastPrice", "price", "currentPrice"])
    previous_close = (
        toss_previous_close_from_candles(candle_data)
        if candle_data is not None
        else pick_first_value(price_item, ["previousClose", "prevClose", "basePrice", "priorClose"])
    )
    return quote_from_price(asset, price, previous_close, "Toss")


def format_toss_url(url_template, asset):
    if "{" not in url_template:
        return url_template
    return url_template.format(
        symbol=quote_plus(toss_symbol(asset)),
        ticker=quote_plus(asset["ticker"]),
        market=quote_plus(toss_market_code(asset)),
    )


def fetch_toss_quote(asset):
    if not toss_is_configured():
        raise ValueError("Toss API is not configured.")

    token = fetch_toss_access_token()
    quote_url = toss_quote_url()
    if "{" in quote_url:
        url = format_toss_url(quote_url, asset)
        params = None
    else:
        url = quote_url
        params = {
            "symbols": toss_symbol(asset),
        }
    response = toss_request(
        "GET",
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    price_data = response.json()

    candle_url = toss_candle_url()
    if "{" in candle_url:
        url = format_toss_url(candle_url, asset)
        params = None
    else:
        url = candle_url
        params = {
            "symbol": toss_symbol(asset),
            "interval": "1d",
            "count": 2,
            "adjusted": "true",
        }
    candle_response = toss_request(
        "GET",
        url,
        params=params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    return parse_toss_quote(asset, price_data, candle_response.json())


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


def fetch_quote(asset):
    if toss_is_configured():
        try:
            return fetch_toss_quote(asset)
        except Exception as exc:
            print(f"TOSS FALLBACK {asset['ticker']}: {exc}")

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


def translate_to_korean(text):
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

        if not news.get(item["ticker"]):
            alerts.append(f"뉴스 없음: {item['ticker']}")

    if errors:
        alerts.extend(f"데이터 확인: {error}" for error in errors)

    return alerts[:6] if alerts else ["특이사항 없음"]


def build_content(indexes, quotes, news, errors, account_snapshot=None):
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
    account_lines = account_summary_lines(quotes, account_snapshot)
    surge_text = ", ".join(item["ticker"] for item in surges) if surges else "없음"
    drop_text = ", ".join(item["ticker"] for item in drops) if drops else "없음"
    news_sections = []
    for item in quotes:
        titles = news.get(item["ticker"], [])
        if not titles:
            continue
        news_sections.append((item["display"], titles))

    telegram_lines = [
        f"📈 포트폴리오 브리핑 {today_short}",
        "",
        "🔎 한 줄 판단",
        f"{headline_text}",
        f"분위기: {mood}",
        f"가격 출처: {provider_text}",
        "",
        "⚠️ 먼저 볼 것",
        *alert_lines,
        "",
        "💰 가격",
        *index_lines,
        "",
        *price_lines,
        "",
        "📌 계좌 현황",
        *[f"  ▸ {line}" for line in account_lines],
        "",
        "🎯 오늘의 대응",
        *action_lines,
        "",
        "📊 변동성 체크",
        f"  ▸ 급등 종목: {surge_text}",
        f"  ▸ 급락 종목: {drop_text}",
        f"  ▸ 전체 분위기: {mood}",
    ]

    if news_sections:
        telegram_lines.extend(["", "📰 참고 뉴스"])
        for display, titles in news_sections:
            telegram_lines.extend(["", f"[{display}]"])
            telegram_lines.extend(f"  ▸ {title}" for title in titles)

    if errors:
        telegram_lines.extend(["", "⚠️ 데이터 확인 필요", *[f"  ▸ {error}" for error in errors]])

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
            "## 📌 계좌 현황",
            "",
            *[f"- {line}" for line in account_lines],
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
    print("=" * 50)
    print(f"Portfolio briefing - {now_kst()}")
    print("=" * 50)

    try:
        print("[1/6] Fetching prices...")
        indexes_config, assets_config = load_portfolio()
        assets_config, holding_errors = apply_toss_holdings_to_assets(assets_config)
        indexes, index_errors = fetch_prices(indexes_config, require_any=False)
        quotes, quote_errors = fetch_prices(assets_config)

        print("[2/6] Fetching account status...")
        account_snapshot, account_errors = fetch_toss_management_snapshot()

        print("[3/6] Fetching news titles...")
        news, news_errors = fetch_news(assets_config)
        errors = holding_errors + account_errors + index_errors + quote_errors + news_errors

        print("[4/6] Building rule-based briefing...")
        telegram_msg, md_content = build_content(indexes, quotes, news, errors, account_snapshot)

        print("[5/6] Saving markdown...")
        save_markdown(md_content)

        print("[6/6] Sending Telegram...")
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
