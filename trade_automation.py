#!/usr/bin/env python3
"""Build a guarded daily ETF trading plan from a KIS account balance.

This module intentionally creates plans only. Live order placement remains disabled
until the ISA account is verified and a separate explicit activation is made.
"""

import argparse
import json
import math
import os
from pathlib import Path

import portfolio_briefing as briefing


CONFIG_FILE = Path("trading_config.json")


def load_config(path=CONFIG_FILE):
    with open(path, encoding="utf-8") as file:
        config = json.load(file)

    weights = config.get("target_weights", {})
    if not weights or abs(sum(float(value) for value in weights.values()) - 100) > 0.01:
        raise ValueError("trading_config.json의 target_weights 합계는 100이어야 합니다.")
    if config.get("mode") != "dry-run":
        raise ValueError("현재 자동매매는 dry-run만 허용합니다.")
    if config.get("live_orders_enabled", False):
        raise ValueError("실주문 활성화는 아직 지원하지 않습니다.")
    daily_buy_limit = float(config.get("daily_buy_limit_krw", 0))
    if daily_buy_limit <= 0:
        raise ValueError("daily_buy_limit_krw는 0보다 커야 합니다.")
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
    for field in ("dnca_tot_amt", "nxdy_excc_amt", "prvs_rcdl_excc_amt"):
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
        token_response = session.post(
            f"{base_url}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
            timeout=20,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ValueError("KIS 접근 토큰을 받지 못했습니다.")

    return {
        "account_no": account_no,
        "product_code": product_code,
        "base_url": base_url,
        "session": session,
        "headers": {
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "custtype": "P",
        },
    }


def fetch_kis_prices(codes, context):
    prices = {}
    for code in codes:
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


def fetch_kis_best_ask(code, context):
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
    price = briefing.as_float((payload.get("output1") or {}).get("askp1"), None)
    if price is None or price <= 0:
        raise ValueError(f"KIS 최우선 매도호가가 없습니다: {code}")
    return price


def submit_cash_buy(code, quantity, price, context):
    payload = {
        "CANO": context["account_no"],
        "ACNT_PRDT_CD": context["product_code"],
        "PDNO": code,
        "ORD_DVSN": "00",
        "ORD_QTY": str(quantity),
        "ORD_UNPR": str(round(price)),
        "EXCG_ID_DVSN_CD": "KRX",
    }
    response = context["session"].post(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/order-cash",
        headers={**context["headers"], "tr_id": "TTTC0012U"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("rt_cd") != "0":
        raise ValueError(f"KIS 매수주문 실패({code}): {result.get('msg1', '알 수 없는 오류')}")
    return result.get("output") or {}


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

    response = context["session"].get(
        f"{context['base_url']}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        headers={
            **context["headers"],
            "tr_id": "TTTC8908R",
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


def plan_orders(config, positions, prices, cash, orderable_cash=None):
    """Return sell-first and cash-funded buy plans without placing an order."""
    targets = {code: float(weight) / 100 for code, weight in config["target_weights"].items()}
    values = {
        code: positions[code]["quantity"] * prices.get(code, 0)
        for code in targets
    }
    total = cash + sum(values.values())
    if total <= 0:
        return {"total_value": 0, "cash": cash, "sells": [], "buys": [], "unallocated_cash": cash}

    sells = []
    for code, target_weight in targets.items():
        current_weight = values[code] / total
        if current_weight <= config["sell_trigger_weight_pct"] / 100:
            continue
        price = prices.get(code, 0)
        if price <= 0:
            continue
        sell_value = min(
            values[code] - total * config["sell_target_weight_pct"] / 100,
            float(config["daily_sell_limit_per_asset_krw"]),
        )
        quantity = min(positions[code]["quantity"], math.floor(sell_value / price))
        if quantity >= 1:
            sells.append({"code": code, "quantity": int(quantity), "price": price, "value": quantity * price})

    deficits = {
        code: max(total * target_weight - values[code], 0)
        for code, target_weight in targets.items()
        if prices.get(code, 0) > 0
    }
    buyable_cash = cash if orderable_cash is None else min(cash, max(orderable_cash, 0))
    daily_buy_limit = float(config["daily_buy_limit_krw"])
    budget = min(buyable_cash, daily_buy_limit, sum(deficits.values()))
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
        "daily_buy_limit": daily_buy_limit,
        "sells": sells,
        "buys": buys,
        "unallocated_cash": buyable_cash - sum(order["value"] for order in buys),
    }


def format_plan(plan):
    lines = [
        "자동매매 dry-run",
        f"총 자산(예수금 포함): {plan['total_value']:,.0f}원",
        f"주문 전 예수금: {plan['cash']:,.0f}원",
        f"주문가능금액: {plan.get('orderable_cash', plan['cash']):,.0f}원",
        f"일일 매수 limit: {plan.get('daily_buy_limit', 0):,.0f}원",
    ]
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
    lines.append("실제 주문은 전송하지 않았습니다.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-test-buy", nargs="+", metavar="CODE")
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
