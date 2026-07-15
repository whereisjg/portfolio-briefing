#!/usr/bin/env python3
"""Build a guarded daily ETF trading plan from a KIS account balance.

This module intentionally creates plans only. Live order placement remains disabled
until the ISA account is verified and a separate explicit activation is made.
"""

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
    return config


def load_balance_snapshot():
    path = os.getenv("KIS_BALANCE_SNAPSHOT_FILE", "").strip()
    if not path:
        return None
    with open(path, encoding="utf-8") as file:
        snapshot = json.load(file)
    return snapshot.get("holdings", []), snapshot.get("summary", {})


def code_from_asset(asset):
    return str(asset.get("symbol", "")).split(".")[0]


def cash_from_balance(summary):
    for field in ("dnca_tot_amt", "nxdy_excc_amt", "prvs_rcdl_excc_amt"):
        amount = briefing.as_float(summary.get(field), None)
        if amount is not None:
            return max(amount, 0)
    return 0.0


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


def target_prices(configured_assets, positions):
    prices = {code: position["price"] for code, position in positions.items() if position["price"] > 0}
    for asset in configured_assets:
        code = code_from_asset(asset)
        if code not in positions or code in prices:
            continue
        quote = briefing.fetch_quote(asset)
        prices[code] = float(quote["price"])
    return prices


def plan_orders(config, positions, prices, cash):
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
        if current_weight < config["sell_trigger_weight_pct"] / 100:
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
    budget = min(cash, float(config["daily_buy_limit_krw"]), sum(deficits.values()))
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
        "sells": sells,
        "buys": buys,
        "unallocated_cash": cash - sum(order["value"] for order in buys),
    }


def format_plan(plan):
    lines = [
        "자동매매 dry-run",
        f"총 자산(예수금 포함): {plan['total_value']:,.0f}원",
        f"주문 전 예수금: {plan['cash']:,.0f}원",
    ]
    for label, orders in (("매도", plan["sells"]), ("매수", plan["buys"])):
        lines.append(label + ":")
        if not orders:
            lines.append("  없음")
        for order in orders:
            lines.append(
                f"  {order['code']} {order['quantity']}주 / 기준가 {order['price']:,.0f}원 / {order['value']:,.0f}원"
            )
    lines.append(f"주문 후 남는 예수금(추정): {plan['unallocated_cash']:,.0f}원")
    lines.append("실제 주문은 전송하지 않았습니다.")
    return "\n".join(lines)


def main():
    config = load_config()
    _indexes, assets = briefing.load_portfolio()
    snapshot = load_balance_snapshot()
    if snapshot is None:
        holdings, summary = briefing.fetch_kis_balance()
    else:
        holdings, summary = snapshot
    positions = positions_from_holdings(holdings, config["target_weights"])
    prices = target_prices(assets, positions)
    plan = plan_orders(config, positions, prices, cash_from_balance(summary))
    print(format_plan(plan))


if __name__ == "__main__":
    main()
