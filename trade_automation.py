#!/usr/bin/env python3
"""Build and, when explicitly enabled, execute a guarded KIS ETF rebalance."""

import argparse
from copy import deepcopy
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

import portfolio_briefing as briefing


CONFIG_FILE = Path("trading_config.json")


def load_config(path=CONFIG_FILE):
    with open(path, encoding="utf-8") as file:
        config = json.load(file)

    weights = config.get("target_weights", {})
    if not weights or abs(sum(float(value) for value in weights.values()) - 100) > 0.01:
        raise ValueError("trading_config.json의 target_weights 합계는 100이어야 합니다.")
    mode = config.get("mode")
    if mode not in {"dry-run", "live"}:
        raise ValueError("mode는 dry-run 또는 live여야 합니다.")
    if mode == "live" and not config.get("live_orders_enabled", False):
        raise ValueError("live 모드에는 live_orders_enabled: true가 필요합니다.")
    daily_turnover_limit_pct = float(config.get("daily_turnover_limit_pct", 100))
    if not 0 < daily_turnover_limit_pct <= 100:
        raise ValueError("daily_turnover_limit_pct는 0 초과 100 이하여야 합니다.")
    paper_test_order_limit = float(config.get("paper_test_order_limit_krw", 0))
    if paper_test_order_limit <= 0:
        raise ValueError("paper_test_order_limit_krw는 0보다 커야 합니다.")
    return config


def load_balance_snapshot():
    path = os.getenv("KIS_BALANCE_SNAPSHOT_FILE", "").strip()
    if not path:
        return None
    with open(path, encoding="utf-8") as file:
        snapshot = json.load(file)
    return snapshot.get("holdings", []), snapshot.get("summary", {}), snapshot.get("access_token")


def code_from_asset(asset):
    return str(asset.get("symbol", "")).split(".")[0]


def cash_from_balance(summary):
    for field in ("prvs_rcdl_excc_amt", "nxdy_excc_amt", "dnca_tot_amt"):
        amount = briefing.as_float(summary.get(field), None)
        if amount is not None:
            return max(amount, 0)
    return 0.0


def get_kis_context(access_token=None):
    app_key = briefing.kis_required("KIS_APP_KEY")
    app_secret = briefing.kis_required("KIS_APP_SECRET")
    account_no = briefing.kis_required("KIS_ACCOUNT_NO")
    product_code = briefing.kis_required("KIS_PRODUCT_CODE")
    base_url = briefing.env_value("KIS_API_BASE_URL", "https://openapi.koreainvestment.com:9443")
    session = briefing.get_http_session(retries=1)
    if not access_token:
        access_token = briefing.get_kis_access_token(app_key, app_secret, base_url)

    return {
        "account_no": account_no,
        "product_code": product_code,
        "base_url": base_url,
        "is_paper": briefing.kis_is_paper(),
        "session": session,
        # The briefing may have just used the same token. Keep order-related
        # calls below KIS's per-second request limit.
        "next_kis_request_at": time.monotonic() + 1.1,
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
    context["next_kis_request_at"] = time.monotonic() + 1.1


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
        price = briefing.as_float((payload.get("output") or {}).get("stck_prpr"), None)
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
    price = briefing.as_float((payload.get("output1") or {}).get(field), None)
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
    if response.status_code != 200:
        raise ValueError(
            f"KIS {side} 주문 HTTP {response.status_code}: {response.text[:500]}"
        )
    result = response.json()
    if result.get("rt_cd") != "0":
        raise ValueError(f"KIS {side} 주문 실패({code}): {result.get('msg1', '알 수 없는 오류')}")
    return result.get("output") or {}


def submit_cash_buy(code, quantity, price, context):
    return submit_cash_order(code, quantity, price, "buy", context)


def submit_cash_sell(code, quantity, price, context):
    return submit_cash_order(code, quantity, price, "sell", context)


def execute_confirmed_test_buys(codes, context):
    if os.getenv("CONFIRM_LIVE_TEST_BUY") != "CONFIRM":
        raise ValueError("실주문 확인값이 없습니다.")
    asks = {code: fetch_kis_best_ask(code, context) for code in codes}
    required_cash = sum(asks.values())
    orderable_cash = fetch_kis_orderable_cash(asks, context)
    if required_cash > orderable_cash:
        raise ValueError(f"주문가능금액 부족: 필요 {required_cash:,.0f}원 / 가능 {orderable_cash:,.0f}원")

    results = []
    for code in codes:
        price = asks[code]
        result = submit_cash_buy(code, 1, price, context)
        results.append({"code": code, "price": price, "order_no": result.get("ODNO", "")})
    return results


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
    amount = briefing.as_float((payload.get("output") or {}).get("nrcvb_buy_amt"), None)
    if amount is None:
        raise ValueError("KIS 주문가능금액이 없습니다.")
    return max(amount, 0)


def fetch_today_orders(context):
    """Read today's cash orders before placing anything to prevent duplicate runs."""
    today = datetime.now(briefing.KST).strftime("%Y%m%d")
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
    quantity = int(briefing.as_float(row.get("psbl_qty"), 0))
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
        quantity = int(briefing.as_float(row.get("rmn_qty"), 0))
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
        value = briefing.as_float(row.get("tot_ccld_amt"), 0)
        if value <= 0:
            value = briefing.as_float(row.get("tot_ccld_qty"), 0) * briefing.as_float(row.get("avg_prvs"), 0)
        if value <= 0:
            continue
        values["orders"].add(order["order_no"])
        if order["side"] == "buy":
            values["buy"] += value
        else:
            values["sell"][order["code"]] = values["sell"].get(order["code"], 0) + value
    return values


def filled_turnover_for_codes(today_orders, target_codes):
    """Return today's actual filled turnover for the managed ETF set."""
    turnover = 0.0
    for row in today_orders:
        if str(row.get("pdno", "")).strip() not in target_codes:
            continue
        value = briefing.as_float(row.get("tot_ccld_amt"), 0)
        if value <= 0:
            value = briefing.as_float(row.get("tot_ccld_qty"), 0) * briefing.as_float(row.get("avg_prvs"), 0)
        turnover += max(value, 0)
    return turnover


def has_open_target_order(today_orders, target_codes):
    return any(
        str(row.get("pdno", "")).strip() in target_codes
        and briefing.as_float(row.get("rmn_qty"), 0) > 0
        for row in today_orders
    )


def daily_turnover_budget(config, total_assets, today_orders, target_codes, context):
    """Keep real-account daily turnover bounded while leaving paper tests unrestricted."""
    if context.get("is_paper"):
        return None, 0.0, float(config["paper_test_order_limit_krw"])

    cap = total_assets * float(config["daily_turnover_limit_pct"]) / 100
    used = filled_turnover_for_codes(today_orders, target_codes)
    return cap, used, max(cap - used, 0)


def format_execution_report(today_orders, submitted, cancelled):
    """Format KIS order status into a compact Telegram-friendly execution report."""
    if not submitted:
        return []

    rows_by_order_no = {str(row.get("odno", "")).strip(): row for row in today_orders}
    cancelled_order_nos = {str(item.get("order_no", "")).strip() for item in cancelled}
    lines = ["📋 체결 품질"]
    for order in submitted:
        order_no = str(order.get("order_no", "")).strip()
        row = rows_by_order_no.get(order_no)
        side = "매수" if order["side"] == "buy" else "매도"
        requested = int(order["quantity"])
        if row is None:
            lines.append(
                f"{side} {order['code']} · 지정가 {order['price']:,.0f}원 · 상태 조회 대기"
            )
            continue

        filled = int(briefing.as_float(row.get("tot_ccld_qty"), 0))
        remaining = int(briefing.as_float(row.get("rmn_qty"), 0))
        average_price = briefing.as_float(row.get("avg_prvs"), 0)
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
        lines.append(f"{side} {order['code']} · 지정가 {order['price']:,.0f}원 · {details} · {status}")
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
    today_orders = fetch_today_orders(context)
    if has_open_target_order(today_orders, target_codes):
        return {
            "status": "skipped",
            "reason": "대상 ETF의 미체결 주문이 남아 있어 추가 주문을 보류했습니다.",
            "plan": None,
            "orders": [],
        }

    positions = positions_from_holdings(holdings, config["target_weights"])
    market_prices = fetch_kis_prices(config["target_weights"], context)
    cash = cash_from_balance(summary)
    orderable_cash = fetch_kis_orderable_cash(market_prices, context)
    total_assets = cash + sum(
        positions[code]["quantity"] * market_prices[code]
        for code in config["target_weights"]
    )
    daily_turnover_cap, daily_turnover_used, remaining_turnover = daily_turnover_budget(
        config, total_assets, today_orders, target_codes, context
    )
    plan = plan_orders(
        config,
        positions,
        market_prices,
        cash,
        orderable_cash,
        turnover_limit=remaining_turnover,
    )
    plan["daily_turnover_cap"] = daily_turnover_cap
    plan["daily_turnover_used"] = daily_turnover_used

    sell_orders, buy_orders = live_orders_for_plan(plan, config, context)
    submitted = submit_live_orders(sell_orders, buy_orders, context)
    if not submitted:
        return {"status": "submitted", "plan": plan, "orders": [], "reason": ""}

    time.sleep(int(config["order_policy"]["first_order_check_minutes"]) * 60)
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
    fresh_holdings, fresh_summary, _token = briefing.fetch_kis_balance()
    context["next_kis_request_at"] = time.monotonic() + 1.1
    fresh_positions = positions_from_holdings(fresh_holdings, config["target_weights"])
    fresh_prices = fetch_kis_prices(config["target_weights"], context)
    fresh_orderable_cash = fetch_kis_orderable_cash(fresh_prices, context)
    filled = filled_values_for_orders(fetch_today_orders(context), submitted)
    retry_config = deepcopy(config)
    retry_sell_limits = {
        code: max(float(config["daily_sell_limit_per_asset_krw"]) - filled["sell"].get(code, 0), 0)
        for code in config["target_weights"]
    }
    filled_turnover = filled["buy"] + sum(filled["sell"].values())
    retry_plan = plan_orders(
        retry_config,
        fresh_positions,
        fresh_prices,
        cash_from_balance(fresh_summary),
        fresh_orderable_cash,
        retry_sell_limits,
        max(plan["daily_turnover_limit"] - filled_turnover, 0),
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
        "execution_report": format_execution_report(final_today_orders, all_orders, cancelled),
        "reason": "",
    }


def positions_from_holdings(holdings, target_codes):
    positions = {code: {"quantity": 0.0, "price": 0.0} for code in target_codes}
    for holding in holdings:
        code = str(holding.get("pdno", "")).strip()
        if code not in positions:
            continue
        positions[code] = {
            "quantity": max(briefing.as_float(holding.get("hldg_qty")), 0),
            "price": max(briefing.as_float(holding.get("prpr")), 0),
        }
    return positions


def target_prices(configured_assets, positions, kis_prices):
    del configured_assets
    prices = {}
    for code in positions:
        price = kis_prices.get(code)
        if price is None or price <= 0:
            raise ValueError(f"KIS 현재가를 찾지 못했습니다: {code}")
        prices[code] = price
    return prices


def plan_orders(config, positions, prices, cash, orderable_cash=None, sell_limits=None, turnover_limit=None):
    """Return sell-first and cash-funded buy plans without placing an order."""
    targets = {code: float(weight) / 100 for code, weight in config["target_weights"].items()}
    values = {
        code: positions[code]["quantity"] * prices.get(code, 0)
        for code in targets
    }
    total = cash + sum(values.values())
    if total <= 0:
        return {"total_value": 0, "cash": cash, "sells": [], "buys": [], "unallocated_cash": cash}

    daily_turnover_limit = (
        float(turnover_limit)
        if turnover_limit is not None
        else total * float(config.get("daily_turnover_limit_pct", 100)) / 100
    )
    remaining_turnover = daily_turnover_limit
    sells = []
    for code, target_weight in targets.items():
        current_weight = values[code] / total
        if current_weight <= config["sell_trigger_weight_pct"] / 100:
            continue
        price = prices.get(code, 0)
        if price <= 0:
            continue
        sell_limit = (
            float(sell_limits.get(code, 0))
            if sell_limits is not None
            else float(config["daily_sell_limit_per_asset_krw"])
        )
        sell_value = min(
            values[code] - total * config["sell_target_weight_pct"] / 100,
            sell_limit,
            remaining_turnover,
        )
        quantity = min(positions[code]["quantity"], math.floor(sell_value / price))
        if quantity >= 1:
            value = quantity * price
            sells.append({"code": code, "quantity": int(quantity), "price": price, "value": value})
            remaining_turnover -= value

    deficits = {
        code: max(total * target_weight - values[code], 0)
        for code, target_weight in targets.items()
        if prices.get(code, 0) > 0
    }
    buyable_cash = cash if orderable_cash is None else min(cash, max(orderable_cash, 0))
    budget = min(buyable_cash, remaining_turnover, sum(deficits.values()))
    buys = []
    if budget > 0 and deficits:
        total_deficit = sum(deficits.values())
        remaining = budget
        for code, deficit in sorted(deficits.items(), key=lambda item: item[1], reverse=True):
            price = prices[code]
            allocation = budget * deficit / total_deficit
            quantity = min(math.floor(allocation / price), math.floor(remaining / price))
            if quantity >= 1:
                value = quantity * price
                buys.append({"code": code, "quantity": int(quantity), "price": price, "value": value})
                remaining -= value

        # Reuse share-price rounding leftovers for the most underweight asset.
        ordered_deficits = sorted(deficits.items(), key=lambda item: item[1], reverse=True)
        while True:
            candidate = next(
                ((code, prices[code]) for code, _deficit in ordered_deficits if prices[code] <= remaining),
                None,
            )
            if candidate is None:
                break
            code, price = candidate
            existing = next((order for order in buys if order["code"] == code), None)
            if existing:
                existing["quantity"] += 1
                existing["value"] += price
            else:
                buys.append({"code": code, "quantity": 1, "price": price, "value": price})
            remaining -= price

    return {
        "total_value": total,
        "cash": cash,
        "orderable_cash": buyable_cash,
        "daily_turnover_limit": daily_turnover_limit,
        "sells": sells,
        "buys": buys,
        "unallocated_cash": buyable_cash - sum(order["value"] for order in buys),
    }


def format_plan(plan, live=False):
    lines = [
        "자동매매 실주문" if live else "자동매매 dry-run",
        f"총 자산(예수금 포함): {plan['total_value']:,.0f}원",
        f"주문 전 예수금: {plan['cash']:,.0f}원",
        f"주문가능금액: {plan.get('orderable_cash', plan['cash']):,.0f}원",
    ]
    if plan.get("daily_turnover_cap") is None:
        lines.append(f"모의투자 실행당 매매 한도: {plan['daily_turnover_limit']:,.0f}원")
    else:
        lines.extend([
            f"일일 총 매매 한도: {plan['daily_turnover_cap']:,.0f}원",
            f"오늘 체결: {plan.get('daily_turnover_used', 0):,.0f}원 · 잔여: {plan.get('daily_turnover_limit', 0):,.0f}원",
        ])
    for label, orders in (("매도", plan["sells"]), ("매수", plan["buys"])):
        lines.append(label + ":")
        if not orders:
            lines.append("  없음")
        for order in orders:
            lines.append(
                f"  {order['code']} {order['quantity']}주 / 기준가 {order['price']:,.0f}원 / {order['value']:,.0f}원"
            )
    for warning in plan.get("warnings", []):
        lines.append(f"주의: {warning}")
    lines.append(f"주문 후 남는 주문가능금액(추정): {plan['unallocated_cash']:,.0f}원")
    lines.append("지정가 주문을 전송합니다." if live else "실제 주문은 전송하지 않았습니다.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-test-buy", nargs="+", metavar="CODE")
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()

    if args.execute_test_buy:
        snapshot = load_balance_snapshot()
        access_token = snapshot[2] if snapshot else None
        context = get_kis_context(access_token)
        results = execute_confirmed_test_buys(args.execute_test_buy, context)
        for result in results:
            print(f"실주문 접수: {result['code']} 1주 / 지정가 {result['price']:,.0f}원 / 주문번호 {result['order_no']}")
        return

    config = load_config()
    _indexes, assets = briefing.load_portfolio()
    snapshot = load_balance_snapshot()
    if snapshot is None:
        holdings, summary, access_token = briefing.fetch_kis_balance()
    else:
        holdings, summary, access_token = snapshot
    positions = positions_from_holdings(holdings, config["target_weights"])
    kis_context = get_kis_context(access_token)
    if args.execute_live:
        if config.get("mode") != "live" or not config.get("live_orders_enabled"):
            raise ValueError("실주문은 live 설정이 활성화된 경우에만 실행할 수 있습니다.")
        execution = execute_live_rebalance(config, holdings, summary, kis_context)
        if execution["status"] == "skipped":
            print("자동매매 실주문\n" + execution["reason"])
            return
        print(format_plan(execution["plan"], live=True))
        if not execution["orders"]:
            print("주문 없음: 현재 목표 비중과 예수금 조건상 실행할 주문이 없습니다.")
            return
        for order in execution["orders"]:
            direction = "매수" if order["side"] == "buy" else "매도"
            print(
                f"실주문 접수: {direction} {order['code']} {order['quantity']}주 / "
                f"지정가 {order['price']:,.0f}원 / 주문번호 {order['order_no']}"
            )
        for line in execution.get("execution_report", []):
            print(line)
        return

    kis_prices = fetch_kis_prices(config["target_weights"], kis_context)
    prices = target_prices(assets, positions, kis_prices)
    cash = cash_from_balance(summary)
    warnings = []
    try:
        orderable_cash = fetch_kis_orderable_cash(prices, kis_context)
    except Exception as exc:
        orderable_cash = 0
        warnings.append(f"KIS 주문가능금액 조회 실패로 매수 계획을 만들지 않았습니다: {exc}")
    plan = plan_orders(config, positions, prices, cash, orderable_cash)
    plan["warnings"] = warnings
    print(format_plan(plan))


if __name__ == "__main__":
    main()
