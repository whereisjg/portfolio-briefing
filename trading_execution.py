#!/usr/bin/env python3
"""Build and, when explicitly enabled, execute a guarded KIS ETF rebalance."""

import argparse
from copy import deepcopy
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import kis_client
import run_state
from trading_strategy import (
    calculate_composite_trend_state,
    calculate_trend_state,
    cash_from_balance,
    format_plan,
    load_config,
    plan_orders,
    positions_from_holdings,
    weighted_close_series,
)


KIS_REQUEST_MIN_INTERVAL_SECONDS = 2.0
TREND_STATE_FILE = os.getenv("KIS_TREND_STATE_FILE", "").strip()
PORTFOLIO_FILE = Path("portfolio.json")


def load_balance_snapshot():
    path = os.getenv("KIS_BALANCE_SNAPSHOT_FILE", "").strip()
    if not path:
        return None
    return run_state.load_balance_snapshot(path)


def load_asset_labels(path=PORTFOLIO_FILE):
    """Map KIS ETF codes to the short labels used in user-facing reports."""
    try:
        with open(path, encoding="utf-8") as file:
            assets = json.load(file).get("assets", [])
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(asset.get("symbol", "")).split(".")[0]: (
            asset.get("display") or asset.get("name") or asset.get("ticker")
        )
        for asset in assets
        if asset.get("symbol")
    }


def get_kis_context(access_token=None):
    app_key = kis_client.required("KIS_APP_KEY")
    app_secret = kis_client.required("KIS_APP_SECRET")
    account_no = kis_client.required("KIS_ACCOUNT_NO")
    product_code = kis_client.required("KIS_PRODUCT_CODE")
    base_url = kis_client.env_value("KIS_API_BASE_URL", kis_client.DEFAULT_BASE_URL)
    session = kis_client.get_http_session(retries=1)
    if not access_token:
        access_token = kis_client.get_access_token(
            app_key,
            app_secret,
            base_url,
            kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE"),
        )

    return {
        "account_no": account_no,
        "product_code": product_code,
        "base_url": base_url,
        "is_paper": kis_client.is_paper(),
        "session": session,
        # The briefing may have just used the same token. Keep order-related
        # calls below KIS's per-second request limit.
        "next_kis_request_at": time.monotonic() + KIS_REQUEST_MIN_INTERVAL_SECONDS,
        "headers": {
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "custtype": "P",
        },
    }


def kis_tr_id(context, real, paper):
    return paper if context.get("is_paper") else real


def wait_for_kis_request_slot(context):
    """Serialize KIS calls so a quote lookup cannot immediately block an order."""
    wait_seconds = context.get("next_kis_request_at", 0) - time.monotonic()
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    context["next_kis_request_at"] = time.monotonic() + KIS_REQUEST_MIN_INTERVAL_SECONDS


def fetch_kis_daily_closes(code, context):
    """Fetch completed daily closes for a domestic ETF trend signal."""
    now = datetime.now(kis_client.KST)
    start = (now - timedelta(days=180)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers={**context["headers"], "tr_id": "FHKST03010100"},
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 일봉조회 실패({code}): {payload.get('msg1', '알 수 없는 오류')}")

    closes = []
    today = now.strftime("%Y%m%d")
    for row in payload.get("output2") or []:
        date = str(row.get("stck_bsop_date", "")).strip()
        close = kis_client.as_float(row.get("stck_clpr"), 0)
        if date and date < today and close > 0:
            closes.append((date, close))
    closes.sort(key=lambda item: item[0])
    return closes


def fetch_kis_index_daily_closes(symbol, context):
    """Fetch completed Nasdaq100 or S&P500 closes from KIS's overseas index API."""
    now = datetime.now(kis_client.KST)
    start = (now - timedelta(days=140)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice",
        headers={**context["headers"], "tr_id": "FHKST03030100"},
        params={
            "FID_COND_MRKT_DIV_CODE": "N",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 지수 일봉조회 실패({symbol}): {payload.get('msg1', '알 수 없는 오류')}")

    today = now.strftime("%Y%m%d")
    closes = []
    for row in payload.get("output2") or []:
        date = str(row.get("stck_bsop_date", "")).strip()
        close = kis_client.as_float(row.get("ovrs_nmix_prpr"), 0)
        if date and date < today and close > 0:
            closes.append((date, close))
    closes.sort(key=lambda item: item[0])
    return closes


def resolve_trend_strategy(config, context):
    """Resolve effective target weights and persist them for the post-trade briefing."""
    trend = config.get("trend_strategy", {})
    default_weights = config["target_weights"]
    if not trend.get("enabled"):
        result = {"state": "neutral", "weights": default_weights, "enabled": False}
    else:
        try:
            if trend.get("signals"):
                result = resolve_composite_trend_strategy(trend, context)
            else:
                closes = fetch_kis_daily_closes(trend["signal_code"], context)
                result = calculate_trend_state(
                    closes,
                    int(trend["short_window_days"]),
                    int(trend["long_window_days"]),
                    int(trend["confirmation_days"]),
                )
            result.update({
                "enabled": True,
                "weights": trend["weights"][result["state"]],
            })
            if trend.get("signal_code"):
                result["signal_code"] = trend["signal_code"]
        except Exception as exc:
            result = {
                "state": "neutral",
                "weights": trend.get("weights", {}).get("neutral", default_weights),
                "enabled": True,
                "error": str(exc),
            }

    if TREND_STATE_FILE:
        run_state.save_json(TREND_STATE_FILE, result)
    return result


def resolve_composite_trend_strategy(trend, context):
    """Resolve a portfolio, Nasdaq100, and S&P500 composite trend signal."""
    short_window = int(trend["short_window_days"])
    long_window = int(trend["long_window_days"])
    confirmation_days = int(trend["confirmation_days"])
    components = []
    for signal in trend["signals"]:
        if signal["kind"] == "portfolio":
            closes = weighted_close_series({
                code: fetch_kis_daily_closes(code, context)
                for code in signal["codes"]
            })
        else:
            closes = fetch_kis_index_daily_closes(signal["symbol"], context)
        state = calculate_trend_state(closes, short_window, long_window, confirmation_days)
        components.append({
            "label": signal["label"],
            "weight_pct": float(signal["weight_pct"]),
            "kind": signal["kind"],
            "state": state["daily_states"][-1]["state"],
            "latest_close": state["latest_close"],
            "short_average": state["short_average"],
            "long_average": state["long_average"],
            "daily_states": state["daily_states"],
        })
    result = calculate_composite_trend_state(
        components,
        confirmation_days,
        float(trend.get("composite_threshold", 0.5)),
    )
    result.update({"signal_type": "composite", "components": components})
    return result


def fetch_kis_prices(codes, context):
    prices = {}
    for code in codes:
        wait_for_kis_request_slot(context)
        response = context["session"].get(
            f"{context['base_url']}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={**context["headers"], "tr_id": "FHKST01010100"},
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") != "0":
            raise ValueError(f"KIS 현재가조회 실패({code}): {payload.get('msg1', '알 수 없는 오류')}")
        price = kis_client.as_float((payload.get("output") or {}).get("stck_prpr"), None)
        if price is None or price <= 0:
            raise ValueError(f"KIS 현재가가 없습니다: {code}")
        prices[code] = price
    return prices


def fetch_kis_best_quote(code, context, field, label):
    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
        headers={**context["headers"], "tr_id": "FHKST01010200"},
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 최우선 매도호가 조회 실패({code}): {payload.get('msg1', '알 수 없는 오류')}")
    price = kis_client.as_float((payload.get("output1") or {}).get(field), None)
    if price is None or price <= 0:
        raise ValueError(f"KIS 최우선 {label}호가가 없습니다: {code}")
    return price


def fetch_kis_best_ask(code, context):
    return fetch_kis_best_quote(code, context, "askp1", "매도")


def fetch_kis_best_bid(code, context):
    return fetch_kis_best_quote(code, context, "bidp1", "매수")


def submit_cash_order(code, quantity, price, side, context):
    if side not in {"buy", "sell"}:
        raise ValueError("주문 방향은 buy 또는 sell이어야 합니다.")
    payload = {
        "CANO": context["account_no"],
        "ACNT_PRDT_CD": context["product_code"],
        "PDNO": code,
        "ORD_DVSN": "00",
        "ORD_QTY": str(quantity),
        "ORD_UNPR": str(round(price)),
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "01" if side == "sell" else "",
        "CNDT_PRIC": "",
    }
    for attempt in range(2):
        wait_for_kis_request_slot(context)
        response = context["session"].post(
            f"{context['base_url']}/uapi/domestic-stock/v1/trading/order-cash",
            headers={
                **context["headers"],
                "tr_id": kis_tr_id(
                    context,
                    "TTTC0012U" if side == "buy" else "TTTC0011U",
                    "VTTC0012U" if side == "buy" else "VTTC0011U",
                ),
                "content-type": "application/json; charset=utf-8",
            },
            json=payload,
            timeout=20,
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("rt_cd") == "0":
                return result.get("output") or {}
            message = result.get("msg1", "알 수 없는 오류")
        else:
            message = response.text[:500]

        if "EGW002" in message and attempt == 0:
            time.sleep(KIS_REQUEST_MIN_INTERVAL_SECONDS)
            context["next_kis_request_at"] = time.monotonic() + KIS_REQUEST_MIN_INTERVAL_SECONDS
            continue
        if response.status_code != 200:
            raise ValueError(f"KIS {side} 주문 HTTP {response.status_code}: {message}")
        raise ValueError(f"KIS {side} 주문 실패({code}): {message}")


def submit_cash_buy(code, quantity, price, context):
    return submit_cash_order(code, quantity, price, "buy", context)


def submit_cash_sell(code, quantity, price, context):
    return submit_cash_order(code, quantity, price, "sell", context)


def fetch_kis_orderable_cash(prices, context):
    """Read the cash amount that can actually be used for a limit buy order."""
    code, price = next(((code, price) for code, price in prices.items() if price > 0), (None, None))
    if code is None:
        return 0.0

    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        headers={
            **context["headers"],
            "tr_id": kis_tr_id(context, "TTTC8908R", "VTTC8908R"),
        },
        params={
            "CANO": context["account_no"],
            "ACNT_PRDT_CD": context["product_code"],
            "PDNO": code,
            "ORD_UNPR": str(round(price)),
            "ORD_DVSN": "00",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 주문가능조회 실패: {payload.get('msg1', '알 수 없는 오류')}")
    if kis_client.env_value("KIS_DEBUG_ORDERABLE_CASH").lower() == "true":
        print(f"KIS 주문가능조회 응답: {json.dumps(payload.get('output') or {}, ensure_ascii=False)}")
    amount = kis_client.as_float((payload.get("output") or {}).get("nrcvb_buy_amt"), None)
    if amount is None:
        raise ValueError("KIS 주문가능금액이 없습니다.")
    return max(amount, 0)


def fetch_today_orders(context):
    """Read today's cash orders before placing anything to prevent duplicate runs."""
    today = datetime.now(kis_client.KST).strftime("%Y%m%d")
    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
        headers={**context["headers"], "tr_id": kis_tr_id(context, "TTTC0081R", "VTTC0081R")},
        params={
            "CANO": context["account_no"],
            "ACNT_PRDT_CD": context["product_code"],
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 당일 주문조회 실패: {payload.get('msg1', '알 수 없는 오류')}")
    return payload.get("output1") or []


def fetch_cancelable_orders(context):
    if context.get("is_paper"):
        return []
    wait_for_kis_request_slot(context)
    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        headers={**context["headers"], "tr_id": "TTTC0084R"},
        params={
            "CANO": context["account_no"],
            "ACNT_PRDT_CD": context["product_code"],
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(f"KIS 취소가능주문조회 실패: {payload.get('msg1', '알 수 없는 오류')}")
    return payload.get("output") or []


def cancel_unfilled_order(order, cancelable, context):
    order_no = str(order.get("order_no", ""))
    row = next((item for item in cancelable if str(item.get("odno", "")) == order_no), None)
    if not row:
        return None
    quantity = int(kis_client.as_float(row.get("psbl_qty"), 0))
    if quantity < 1:
        return None
    org_no = str(row.get("ord_gno_brno", "")).strip()
    if not org_no:
        raise ValueError(f"KIS 취소가능주문에 주문조직번호가 없습니다: {order_no}")
    payload = {
        "CANO": context["account_no"],
        "ACNT_PRDT_CD": context["product_code"],
        "KRX_FWDG_ORD_ORGNO": org_no,
        "ORGN_ODNO": order_no,
        "ORD_DVSN": str(row.get("ord_dvsn", "00")),
        "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": str(quantity),
        "ORD_UNPR": str(row.get("ord_unpr") or order["price"]),
        "QTY_ALL_ORD_YN": "Y",
        "EXCG_ID_DVSN_CD": "KRX",
    }
    wait_for_kis_request_slot(context)
    response = context["session"].post(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/order-rvsecncl",
        headers={
            **context["headers"],
            "tr_id": kis_tr_id(context, "TTTC0013U", "VTTC0013U"),
            "content-type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=20,
    )
    if response.status_code != 200:
        raise ValueError(f"KIS 주문취소 HTTP {response.status_code}: {response.text[:500]}")
    result = response.json()
    if result.get("rt_cd") != "0":
        raise ValueError(f"KIS 주문취소 실패({order_no}): {result.get('msg1', '알 수 없는 오류')}")
    return {"order_no": order_no, "quantity": quantity}


def cancel_order_by_receipt(order, context):
    """Paper trading has no cancelable-order inquiry; cancel the remaining receipt directly."""
    order_no = str(order.get("order_no", "")).strip()
    org_no = str(order.get("order_org_no", "")).strip()
    if not order_no or not org_no:
        raise ValueError("모의투자 주문취소에 필요한 주문번호 또는 조직번호가 없습니다.")
    payload = {
        "CANO": context["account_no"],
        "ACNT_PRDT_CD": context["product_code"],
        "KRX_FWDG_ORD_ORGNO": org_no,
        "ORGN_ODNO": order_no,
        "ORD_DVSN": "00",
        "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": str(order["quantity"]),
        "ORD_UNPR": str(order["price"]),
        "QTY_ALL_ORD_YN": "Y",
        "EXCG_ID_DVSN_CD": "KRX",
    }
    wait_for_kis_request_slot(context)
    response = context["session"].post(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/order-rvsecncl",
        headers={
            **context["headers"],
            "tr_id": "VTTC0013U",
            "content-type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=20,
    )
    if response.status_code != 200:
        raise ValueError(f"KIS 모의 주문취소 HTTP {response.status_code}: {response.text[:500]}")
    result = response.json()
    if result.get("rt_cd") != "0":
        raise ValueError(f"KIS 모의 주문취소 실패({order_no}): {result.get('msg1', '알 수 없는 오류')}")
    return {"order_no": order_no, "quantity": order["quantity"]}


def paper_unfilled_orders(today_orders, submitted):
    submitted_by_no = {str(order.get("order_no", "")): order for order in submitted}
    unfilled = []
    for row in today_orders:
        order = submitted_by_no.get(str(row.get("odno", "")))
        quantity = int(kis_client.as_float(row.get("rmn_qty"), 0))
        if order and quantity >= 1:
            unfilled.append({**order, "quantity": quantity})
    return unfilled


def reprice_orders(orders, prices, total_limit, per_order_limits=None):
    """Keep quantity plans within real limit prices and the applicable cash cap."""
    remaining = max(float(total_limit), 0)
    repriced = []
    for order in orders:
        code = order["code"]
        price = prices[code]
        max_value = remaining
        if per_order_limits is not None:
            max_value = min(max_value, per_order_limits.get(code, max_value))
        quantity = min(int(order["quantity"]), math.floor(max_value / price))
        if quantity < 1:
            continue
        value = quantity * price
        repriced.append({"code": code, "quantity": quantity, "price": price, "value": value})
        remaining -= value
    return repriced


def filled_values_for_orders(today_orders, submitted):
    submitted_by_no = {str(order.get("order_no", "")): order for order in submitted}
    values = {"buy": 0.0, "sell": {}, "orders": set()}
    for row in today_orders:
        order = submitted_by_no.get(str(row.get("odno", "")))
        if not order:
            continue
        value = kis_client.as_float(row.get("tot_ccld_amt"), 0)
        if value <= 0:
            value = kis_client.as_float(row.get("tot_ccld_qty"), 0) * kis_client.as_float(row.get("avg_prvs"), 0)
        if value <= 0:
            continue
        values["orders"].add(order["order_no"])
        if order["side"] == "buy":
            values["buy"] += value
        else:
            values["sell"][order["code"]] = values["sell"].get(order["code"], 0) + value
    return values


def filled_trade_values_for_codes(today_orders, target_codes):
    """Return today's actual filled buy and sell values for the managed ETF set."""
    values = {"buy": 0.0, "sell": 0.0}
    for row in today_orders:
        if str(row.get("pdno", "")).strip() not in target_codes:
            continue
        value = kis_client.as_float(row.get("tot_ccld_amt"), 0)
        if value <= 0:
            value = kis_client.as_float(row.get("tot_ccld_qty"), 0) * kis_client.as_float(row.get("avg_prvs"), 0)
        side = "sell" if str(row.get("sll_buy_dvsn_cd", "")).strip() == "01" else "buy"
        values[side] += max(value, 0)
    return values


def has_open_target_order(today_orders, target_codes):
    return any(
        str(row.get("pdno", "")).strip() in target_codes
        and kis_client.as_float(row.get("rmn_qty"), 0) > 0
        for row in today_orders
    )


def daily_trade_budgets(config, total_assets, today_orders, target_codes, context):
    """Keep real-account buy and sell values independently bounded."""
    if context.get("is_paper"):
        limit = float(config["paper_test_order_limit_krw"])
        return {
            "buy_cap": None,
            "buy_used": 0.0,
            "buy_remaining": limit,
            "sell_cap": None,
            "sell_used": 0.0,
            "sell_remaining": limit,
        }

    buy_cap = total_assets * float(
        config.get("daily_buy_limit_pct", config.get("daily_turnover_limit_pct", 100))
    ) / 100
    sell_cap = total_assets * float(
        config.get("daily_sell_limit_pct", config.get("daily_turnover_limit_pct", 100))
    ) / 100
    used = filled_trade_values_for_codes(today_orders, target_codes)
    return {
        "buy_cap": buy_cap,
        "buy_used": used["buy"],
        "buy_remaining": max(buy_cap - used["buy"], 0),
        "sell_cap": sell_cap,
        "sell_used": used["sell"],
        "sell_remaining": max(sell_cap - used["sell"], 0),
    }


def format_execution_report(today_orders, submitted, cancelled, asset_labels=None):
    """Format KIS order status into a compact Telegram-friendly execution report."""
    if not submitted:
        return []

    asset_labels = asset_labels or {}
    rows_by_order_no = {str(row.get("odno", "")).strip(): row for row in today_orders}
    cancelled_order_nos = {str(item.get("order_no", "")).strip() for item in cancelled}
    lines = ["📋 체결 품질"]
    for order in submitted:
        order_no = str(order.get("order_no", "")).strip()
        row = rows_by_order_no.get(order_no)
        side = "매수" if order["side"] == "buy" else "매도"
        label = asset_labels.get(order["code"], order["code"])
        requested = int(order["quantity"])
        if row is None:
            lines.append(
                f"{side} {label} · 지정가 {order['price']:,.0f}원 · 상태 조회 대기"
            )
            continue

        filled = int(kis_client.as_float(row.get("tot_ccld_qty"), 0))
        remaining = int(kis_client.as_float(row.get("rmn_qty"), 0))
        average_price = kis_client.as_float(row.get("avg_prvs"), 0)
        if filled >= requested:
            status = "전량 체결"
        elif filled > 0:
            status = f"부분 체결 · 잔량 {remaining}주"
        elif order_no in cancelled_order_nos:
            status = "미체결 취소"
        elif remaining > 0:
            status = f"미체결 · 잔량 {remaining}주"
        else:
            status = "미체결"

        details = f"체결 {filled}/{requested}주"
        if filled > 0 and average_price > 0:
            difference = average_price - order["price"]
            difference_pct = difference / order["price"] * 100 if order["price"] else 0
            details += (
                f" @ {average_price:,.0f}원 · 주문 대비 {difference:+,.0f}원"
                f" ({difference_pct:+.2f}%)"
            )
        lines.append(f"{side} {label} · 지정가 {order['price']:,.0f}원 · {details} · {status}")
    return lines


def live_orders_for_plan(plan, config, context, first_buy_prices=None):
    bid_prices = {order["code"]: fetch_kis_best_bid(order["code"], context) for order in plan["sells"]}
    sell_limits = {
        order["code"]: order["value"]
        for order in plan["sells"]
    }
    sell_orders = reprice_orders(plan["sells"], bid_prices, sum(sell_limits.values()), sell_limits)

    ask_prices = {order["code"]: fetch_kis_best_ask(order["code"], context) for order in plan["buys"]}
    if first_buy_prices is not None:
        max_increase = float(config["order_policy"]["max_buy_price_increase_pct"]) / 100
        ask_prices = {
            code: price for code, price in ask_prices.items()
            if code in first_buy_prices and price <= first_buy_prices[code] * (1 + max_increase)
        }
    buy_orders = reprice_orders(
        [order for order in plan["buys"] if order["code"] in ask_prices],
        ask_prices,
        min(plan["orderable_cash"], sum(order["value"] for order in plan["buys"])),
    )
    return sell_orders, buy_orders


def submit_live_orders(sell_orders, buy_orders, context):
    submitted = []
    for side, orders in (("sell", sell_orders), ("buy", buy_orders)):
        for order in orders:
            result = submit_cash_order(order["code"], order["quantity"], order["price"], side, context)
            submitted.append({
                **order,
                "side": side,
                "order_no": result.get("ODNO", ""),
                "order_org_no": result.get("KRX_FWDG_ORD_ORGNO", ""),
            })
    return submitted


def execute_live_rebalance(config, holdings, summary, context):
    """Submit one guarded live rebalance pass using limit orders only."""
    target_codes = set(config["target_weights"])
    managed_codes = target_codes | set(config.get("liquidation_codes", []))
    trend = resolve_trend_strategy(config, context)
    effective_config = deepcopy(config)
    effective_config["target_weights"] = trend["weights"]
    today_orders = fetch_today_orders(context)
    if has_open_target_order(today_orders, managed_codes):
        return {
            "status": "skipped",
            "reason": "대상 ETF의 미체결 주문이 남아 있어 추가 주문을 보류했습니다.",
            "plan": None,
            "orders": [],
            "trend": trend,
        }

    positions = positions_from_holdings(holdings, managed_codes)
    market_prices = fetch_kis_prices(managed_codes, context)
    cash = cash_from_balance(summary)
    orderable_cash = fetch_kis_orderable_cash(market_prices, context)
    total_assets = cash + sum(
        positions[code]["quantity"] * market_prices[code]
        for code in managed_codes
    )
    daily_budgets = daily_trade_budgets(
        effective_config, total_assets, today_orders, managed_codes, context
    )
    plan = plan_orders(
        effective_config,
        positions,
        market_prices,
        cash,
        orderable_cash,
        buy_limit=daily_budgets["buy_remaining"],
        sell_turnover_limit=daily_budgets["sell_remaining"],
    )
    plan["daily_buy_cap"] = daily_budgets["buy_cap"]
    plan["daily_sell_cap"] = daily_budgets["sell_cap"]
    plan["trend"] = trend

    sell_orders, buy_orders = live_orders_for_plan(plan, effective_config, context)
    submitted = submit_live_orders(sell_orders, buy_orders, context)
    if not submitted:
        return {"status": "submitted", "plan": plan, "orders": [], "trend": trend, "reason": ""}

    time.sleep(int(effective_config["order_policy"]["first_order_check_minutes"]) * 60)
    if context.get("is_paper"):
        cancelled = [
            cancel_order_by_receipt(order, context)
            for order in paper_unfilled_orders(fetch_today_orders(context), submitted)
        ]
    else:
        cancelable = fetch_cancelable_orders(context)
        cancelled = [
            result for result in (cancel_unfilled_order(order, cancelable, context) for order in submitted)
            if result is not None
        ]

    wait_for_kis_request_slot(context)
    fresh_holdings, fresh_summary, _token = kis_client.fetch_balance(
        cache_file=kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE"),
    )
    context["next_kis_request_at"] = time.monotonic() + KIS_REQUEST_MIN_INTERVAL_SECONDS
    fresh_positions = positions_from_holdings(fresh_holdings, managed_codes)
    fresh_prices = fetch_kis_prices(managed_codes, context)
    fresh_orderable_cash = fetch_kis_orderable_cash(fresh_prices, context)
    filled = filled_values_for_orders(fetch_today_orders(context), submitted)
    retry_config = deepcopy(effective_config)
    retry_sell_limits = {
        code: max(float(config["daily_sell_limit_per_asset_krw"]) - filled["sell"].get(code, 0), 0)
        for code in managed_codes
    }
    retry_plan = plan_orders(
        retry_config,
        fresh_positions,
        fresh_prices,
        cash_from_balance(fresh_summary),
        fresh_orderable_cash,
        retry_sell_limits,
        buy_limit=max(plan["daily_buy_limit"] - filled["buy"], 0),
        sell_turnover_limit=max(plan["daily_sell_limit"] - sum(filled["sell"].values()), 0),
    )
    first_buy_prices = {order["code"]: order["price"] for order in submitted if order["side"] == "buy"}
    retry_sells, retry_buys = live_orders_for_plan(retry_plan, retry_config, context, first_buy_prices)
    retried = submit_live_orders(retry_sells, retry_buys, context)
    all_orders = submitted + retried
    final_today_orders = fetch_today_orders(context)
    return {
        "status": "submitted",
        "plan": plan,
        "orders": all_orders,
        "cancelled": cancelled,
        "execution_report": format_execution_report(
            final_today_orders, all_orders, cancelled, load_asset_labels()
        ),
        "trend": trend,
        "reason": "",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()

    config = load_config()
    snapshot = load_balance_snapshot()
    if snapshot is None:
        holdings, summary, access_token = kis_client.fetch_balance(
            cache_file=kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE"),
        )
    else:
        holdings, summary, access_token = snapshot
    managed_codes = set(config["target_weights"]) | set(config.get("liquidation_codes", []))
    positions = positions_from_holdings(holdings, managed_codes)
    kis_context = get_kis_context(access_token)
    if args.execute_live:
        if config.get("mode") != "live" or not config.get("live_orders_enabled"):
            raise ValueError("실주문은 live 설정이 활성화된 경우에만 실행할 수 있습니다.")
        execution = execute_live_rebalance(config, holdings, summary, kis_context)
        if execution["status"] == "skipped":
            print("자동매매 실주문\n" + execution["reason"])
            return
        asset_labels = load_asset_labels()
        print(format_plan(execution["plan"], live=True, asset_labels=asset_labels))
        if not execution["orders"]:
            print("주문 없음: 현재 목표 비중과 예수금 조건상 실행할 주문이 없습니다.")
            return
        for order in execution["orders"]:
            direction = "매수" if order["side"] == "buy" else "매도"
            label = asset_labels.get(order["code"], order["code"])
            print(
                f"실주문 접수: {direction} {label} {order['quantity']}주 / "
                f"지정가 {order['price']:,.0f}원 / 주문번호 {order['order_no']}"
            )
        for line in execution.get("execution_report", []):
            print(line)
        return

    trend = resolve_trend_strategy(config, kis_context)
    effective_config = deepcopy(config)
    effective_config["target_weights"] = trend["weights"]
    positions = positions_from_holdings(holdings, managed_codes)
    prices = fetch_kis_prices(managed_codes, kis_context)
    cash = cash_from_balance(summary)
    warnings = []
    try:
        orderable_cash = fetch_kis_orderable_cash(prices, kis_context)
    except Exception as exc:
        orderable_cash = 0
        warnings.append(f"KIS 주문가능금액 조회 실패로 매수 계획을 만들지 않았습니다: {exc}")
    total_assets = cash + sum(
        positions[code]["quantity"] * prices[code]
        for code in managed_codes
    )
    daily_budgets = daily_trade_budgets(
        effective_config,
        total_assets,
        [],
        managed_codes,
        kis_context,
    )
    plan = plan_orders(
        effective_config,
        positions,
        prices,
        cash,
        orderable_cash,
        buy_limit=daily_budgets["buy_remaining"],
        sell_turnover_limit=daily_budgets["sell_remaining"],
    )
    plan["daily_buy_cap"] = daily_budgets["buy_cap"]
    plan["daily_sell_cap"] = daily_budgets["sell_cap"]
    plan["trend"] = trend
    plan["warnings"] = warnings
    print(format_plan(plan, asset_labels=load_asset_labels()))


if __name__ == "__main__":
    main()
