"""Portfolio target-weight and rebalance planning rules.

This module intentionally has no KIS or order-submission dependency. It turns a
portfolio snapshot into a plan; the execution layer decides how to submit it.
"""

import json
import math
from pathlib import Path


CONFIG_FILE = Path("trading_config.json")


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def load_config(path=CONFIG_FILE):
    with open(path, encoding="utf-8") as file:
        config = json.load(file)

    weights = config.get("target_weights", {})
    if not weights or abs(sum(float(value) for value in weights.values()) - 100) > 0.01:
        raise ValueError("trading_config.json의 target_weights 합계는 100이어야 합니다.")
    liquidation_codes = config.get("liquidation_codes", [])
    if not isinstance(liquidation_codes, list) or any(not isinstance(code, str) or not code for code in liquidation_codes):
        raise ValueError("liquidation_codes는 종목코드 목록이어야 합니다.")
    if set(liquidation_codes) & set(weights):
        raise ValueError("liquidation_codes는 target_weights 종목과 겹칠 수 없습니다.")
    mode = config.get("mode")
    if mode not in {"dry-run", "live"}:
        raise ValueError("mode는 dry-run 또는 live여야 합니다.")
    if mode == "live" and not config.get("live_orders_enabled", False):
        raise ValueError("live 모드에는 live_orders_enabled: true가 필요합니다.")
    for side in ("buy", "sell"):
        limit_pct = float(config.get(f"daily_{side}_limit_pct", config.get("daily_turnover_limit_pct", 100)))
        if not 0 < limit_pct <= 100:
            raise ValueError(f"daily_{side}_limit_pct는 0 초과 100 이하여야 합니다.")
    paper_test_order_limit = float(config.get("paper_test_order_limit_krw", 0))
    if paper_test_order_limit <= 0:
        raise ValueError("paper_test_order_limit_krw는 0보다 커야 합니다.")
    rebalance_band_pct = float(config.get("rebalance_band_pct", 0))
    if not 0 <= rebalance_band_pct < 100:
        raise ValueError("rebalance_band_pct는 0 이상 100 미만이어야 합니다.")
    trend = config.get("trend_strategy", {})
    if trend.get("enabled"):
        if trend.get("signal_code") not in weights:
            raise ValueError("trend_strategy.signal_code는 target_weights에 있어야 합니다.")
        for state in ("risk_on", "neutral", "risk_off"):
            state_weights = trend.get("weights", {}).get(state, {})
            if set(state_weights) != set(weights) or abs(sum(float(value) for value in state_weights.values()) - 100) > 0.01:
                raise ValueError(f"trend_strategy.weights.{state}는 대상 ETF 전체 합계 100이어야 합니다.")
    return config


def cash_from_balance(summary):
    for field in ("prvs_rcdl_excc_amt", "nxdy_excc_amt", "dnca_tot_amt"):
        amount = as_float(summary.get(field), None)
        if amount is not None:
            return max(amount, 0)
    return 0.0


def calculate_trend_state(closes, short_window, long_window, confirmation_days):
    """Classify a confirmed moving-average regime from completed closing prices."""
    required = long_window + confirmation_days - 1
    if len(closes) < required:
        raise ValueError(f"추세 판단에 필요한 일봉이 부족합니다: {len(closes)}/{required}")

    signals = []
    latest_short_average = None
    latest_long_average = None
    for index in range(len(closes) - confirmation_days, len(closes)):
        price = closes[index][1]
        short_average = sum(close for _date, close in closes[index - short_window + 1:index + 1]) / short_window
        long_average = sum(close for _date, close in closes[index - long_window + 1:index + 1]) / long_window
        latest_short_average = short_average
        latest_long_average = long_average
        if price > long_average and short_average > long_average:
            signals.append("risk_on")
        elif price < long_average and short_average < long_average:
            signals.append("risk_off")
        else:
            signals.append("neutral")

    state = signals[0] if len(set(signals)) == 1 else "neutral"
    latest_date, latest_close = closes[-1]
    return {
        "state": state,
        "latest_date": latest_date,
        "latest_close": latest_close,
        "short_average": latest_short_average,
        "long_average": latest_long_average,
        "confirmation_days": confirmation_days,
        "signals": signals,
    }


def positions_from_holdings(holdings, target_codes):
    positions = {code: {"quantity": 0.0, "price": 0.0} for code in target_codes}
    for holding in holdings:
        code = str(holding.get("pdno", "")).strip()
        if code not in positions:
            continue
        positions[code] = {
            "quantity": max(as_float(holding.get("hldg_qty")), 0),
            "price": max(as_float(holding.get("prpr")), 0),
        }
    return positions


def plan_orders(
    config,
    positions,
    prices,
    cash,
    orderable_cash=None,
    sell_limits=None,
    turnover_limit=None,
    buy_limit=None,
    sell_turnover_limit=None,
):
    """Return sell-first and cash-funded buy plans without placing an order."""
    targets = {code: float(weight) / 100 for code, weight in config["target_weights"].items()}
    liquidation_codes = config.get("liquidation_codes", [])
    managed_codes = [*targets, *liquidation_codes]
    values = {
        code: positions.get(code, {"quantity": 0})["quantity"] * prices.get(code, 0)
        for code in managed_codes
    }
    total = cash + sum(values.values())
    if total <= 0:
        return {"total_value": 0, "cash": cash, "sells": [], "buys": [], "unallocated_cash": cash}

    default_buy_limit = total * float(
        config.get("daily_buy_limit_pct", config.get("daily_turnover_limit_pct", 100))
    ) / 100
    default_sell_limit = total * float(
        config.get("daily_sell_limit_pct", config.get("daily_turnover_limit_pct", 100))
    ) / 100
    if turnover_limit is not None:
        default_buy_limit = float(turnover_limit)
        default_sell_limit = float(turnover_limit)
    daily_buy_limit = float(buy_limit) if buy_limit is not None else default_buy_limit
    daily_sell_limit = float(sell_turnover_limit) if sell_turnover_limit is not None else default_sell_limit
    remaining_sell_limit = daily_sell_limit
    rebalance_band = float(config.get("rebalance_band_pct", 0)) / 100
    sells = []
    for code in liquidation_codes:
        price = prices.get(code, 0)
        if price <= 0:
            continue
        sell_limit = float(sell_limits.get(code, 0)) if sell_limits is not None else float(config["daily_sell_limit_per_asset_krw"])
        sell_value = min(values[code], sell_limit, remaining_sell_limit)
        quantity = min(positions.get(code, {"quantity": 0})["quantity"], math.floor(sell_value / price))
        if quantity >= 1:
            value = quantity * price
            sells.append({"code": code, "quantity": int(quantity), "price": price, "value": value})
            remaining_sell_limit -= value

    for code, target_weight in targets.items():
        current_weight = values[code] / total
        if current_weight <= target_weight + rebalance_band:
            continue
        price = prices.get(code, 0)
        if price <= 0:
            continue
        sell_limit = float(sell_limits.get(code, 0)) if sell_limits is not None else float(config["daily_sell_limit_per_asset_krw"])
        sell_value = min(values[code] - total * target_weight, sell_limit, remaining_sell_limit)
        quantity = min(positions[code]["quantity"], math.floor(sell_value / price))
        if quantity >= 1:
            value = quantity * price
            sells.append({"code": code, "quantity": int(quantity), "price": price, "value": value})
            remaining_sell_limit -= value

    deficits = {
        code: max(total * target_weight - values[code], 0)
        for code, target_weight in targets.items()
        if prices.get(code, 0) > 0
    }
    buyable_cash = cash if orderable_cash is None else min(cash, max(orderable_cash, 0))
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

        ordered_deficits = sorted(deficits.items(), key=lambda item: item[1], reverse=True)
        while True:
            candidate = next(((code, prices[code]) for code, _deficit in ordered_deficits if prices[code] <= remaining), None)
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
        "daily_sell_limit": daily_sell_limit,
        "daily_turnover_limit": daily_buy_limit,
        "sells": sells,
        "buys": buys,
        "unallocated_cash": buyable_cash - sum(order["value"] for order in buys),
    }


def format_plan(plan, live=False, asset_labels=None):
    asset_labels = asset_labels or {}

    def label_for(code):
        return asset_labels.get(code, code)

    def short_amount(amount):
        value = float(amount) / 10000
        return f"{value:,.1f}".rstrip("0").rstrip(".") + "만"

    lines = ["🤖 자동매매 실주문" if live else "🤖 자동매매 dry-run"]
    legacy_limit = plan.get("daily_turnover_limit", 0)
    buy_limit = plan.get("daily_buy_limit", legacy_limit)
    sell_limit = plan.get("daily_sell_limit", legacy_limit)
    if plan.get("daily_buy_cap") is None:
        lines[0] += f" · 매수 {short_amount(buy_limit)} · 매도 {short_amount(sell_limit)}"
    else:
        lines[0] += f" · 잔여 매수 {short_amount(buy_limit)} · 매도 {short_amount(sell_limit)}"
    for label, orders in (("매도", plan["sells"]), ("매수", plan["buys"])):
        if orders:
            lines.append(f"{label} {'주문' if live else '예정'}")
            lines.extend(
                f"  {label_for(order['code'])} {order['quantity']}주 · {short_amount(order['value'])}"
                for order in orders
            )
    for warning in plan.get("warnings", []):
        lines.append(f"주의: {warning}")
    if not plan["sells"] and not plan["buys"]:
        lines.append("주문 없음")
    return "\n".join(lines)
