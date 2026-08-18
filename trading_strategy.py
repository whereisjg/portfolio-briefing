"""Portfolio target-weight and rebalance planning rules.

This module intentionally has no KIS or order-submission dependency. It turns a
portfolio snapshot into a plan; the execution layer decides how to submit it.
"""

import json
import math
from pathlib import Path


CONFIG_FILE = Path("trading_config.json")
DEFAULT_COMPOSITE_TREND_THRESHOLD = 0.5


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
        if trend.get("average_type", "sma") not in {"sma", "hma"}:
            raise ValueError("trend_strategy.average_type은 sma 또는 hma여야 합니다.")
        long_filter_window = trend.get("long_filter_window_days")
        if long_filter_window is not None and int(long_filter_window) < int(trend["long_window_days"]):
            raise ValueError("long_filter_window_days는 long_window_days 이상이어야 합니다.")
        signals = trend.get("signals")
        if signals:
            if not isinstance(signals, list) or abs(sum(float(signal.get("weight_pct", 0)) for signal in signals) - 100) > 0.01:
                raise ValueError("trend_strategy.signals의 weight_pct 합계는 100이어야 합니다.")
            for signal in signals:
                if signal.get("kind") == "portfolio":
                    codes = signal.get("codes", [])
                    if not isinstance(codes, list) or not codes or not set(codes) <= set(weights):
                        raise ValueError("portfolio 추세 신호는 target_weights 종목만 사용해야 합니다.")
                elif signal.get("kind") == "index":
                    if not signal.get("symbol"):
                        raise ValueError("index 추세 신호에는 symbol이 필요합니다.")
                else:
                    raise ValueError("trend_strategy.signals.kind는 portfolio 또는 index여야 합니다.")
        elif trend.get("signal_code") not in weights:
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


def weighted_average(values):
    total_weight = len(values) * (len(values) + 1) / 2
    return sum(value * (index + 1) for index, value in enumerate(values)) / total_weight


def hull_average(closes, index, window):
    """Return the HMA at index using the supplied completed closing prices."""
    half_window = window // 2
    root_window = math.isqrt(window)
    raw_values = []
    for end in range(index - root_window + 1, index + 1):
        half = weighted_average([close for _day, close in closes[end - half_window + 1:end + 1]])
        full = weighted_average([close for _day, close in closes[end - window + 1:end + 1]])
        raw_values.append(2 * half - full)
    return weighted_average(raw_values)


def trend_signal_states(
    closes,
    short_window,
    long_window,
    confirmation_days,
    average_type="sma",
    long_filter_window=None,
    state_history_days=None,
):
    """Return recent daily moving-average states from completed closing prices."""
    state_history_days = max(int(state_history_days or confirmation_days), confirmation_days)
    windows = [short_window, long_window]
    if long_filter_window:
        windows.append(int(long_filter_window))
    required = max(windows) + state_history_days - 1
    if average_type == "hma":
        required += math.isqrt(max(windows)) - 1
    if len(closes) < required:
        raise ValueError(f"추세 판단에 필요한 일봉이 부족합니다: {len(closes)}/{required}")

    states = []
    for index in range(len(closes) - state_history_days, len(closes)):
        price = closes[index][1]
        if average_type == "hma":
            short_average = hull_average(closes, index, short_window)
            long_average = hull_average(closes, index, long_window)
        else:
            short_average = sum(close for _date, close in closes[index - short_window + 1:index + 1]) / short_window
            long_average = sum(close for _date, close in closes[index - long_window + 1:index + 1]) / long_window
        filter_average = (
            hull_average(closes, index, int(long_filter_window))
            if average_type == "hma" and long_filter_window
            else None
        )
        if price > long_average and short_average > long_average:
            state = "risk_on"
        elif price < long_average and short_average < long_average:
            state = "risk_off"
        else:
            state = "neutral"
        if filter_average is not None and state == "risk_on" and price <= filter_average:
            state = "neutral"
        states.append({
            "date": closes[index][0],
            "state": state,
            "close": price,
            "short_average": short_average,
            "long_average": long_average,
            "filter_average": filter_average,
        })
    return states


def calculate_trend_state(
    closes,
    short_window,
    long_window,
    confirmation_days,
    average_type="sma",
    long_filter_window=None,
    state_history_days=None,
):
    """Classify a confirmed moving-average regime from completed closing prices."""
    states = trend_signal_states(
        closes,
        short_window,
        long_window,
        confirmation_days,
        average_type,
        long_filter_window,
        state_history_days,
    )
    signals = [item["state"] for item in states[-confirmation_days:]]
    latest = states[-1]
    return {
        "state": signals[0] if len(set(signals)) == 1 else "neutral",
        "latest_date": latest["date"],
        "latest_close": latest["close"],
        "short_average": latest["short_average"],
        "long_average": latest["long_average"],
        "filter_average": latest["filter_average"],
        "long_filter_window": long_filter_window,
        "average_type": average_type,
        "confirmation_days": confirmation_days,
        "signals": signals,
        "daily_states": states,
    }


def weighted_close_series(component_closes, weights=None):
    """Build a weighted synthetic close series from matching component dates."""
    if not component_closes:
        raise ValueError("합성 추세 신호에 종목이 없습니다.")
    closes_by_code = {
        code: {date: close for date, close in closes if close > 0}
        for code, closes in component_closes.items()
    }
    common_dates = set.intersection(*(set(closes) for closes in closes_by_code.values()))
    if not common_dates:
        raise ValueError("합성 추세 신호의 공통 거래일이 없습니다.")
    ordered_dates = sorted(common_dates)
    base_prices = {code: closes[ordered_dates[0]] for code, closes in closes_by_code.items()}
    raw_weights = weights or {code: 1 for code in closes_by_code}
    if set(raw_weights) != set(closes_by_code) or any(float(weight) <= 0 for weight in raw_weights.values()):
        raise ValueError("합성 추세 신호의 비중이 올바르지 않습니다.")
    total_weight = sum(float(weight) for weight in raw_weights.values())
    return [
        (
            date,
            sum(
                closes[date] / base_prices[code] * float(raw_weights[code]) / total_weight
                for code, closes in closes_by_code.items()
            ) * 100,
        )
        for date in ordered_dates
    ]


def calculate_composite_trend_state(component_states, confirmation_days, threshold=DEFAULT_COMPOSITE_TREND_THRESHOLD):
    """Combine dated component states into one confirmed portfolio trend state."""
    if not component_states:
        raise ValueError("복합 추세 신호가 없습니다.")
    states_by_component = {
        component["label"]: {item["date"]: item for item in component["daily_states"]}
        for component in component_states
    }
    common_dates = set.intersection(*(set(states) for states in states_by_component.values()))
    if len(common_dates) < confirmation_days:
        raise ValueError("복합 추세 신호의 공통 거래일이 부족합니다.")

    score_for = {"risk_on": 1, "neutral": 0, "risk_off": -1}
    daily_states = []
    for date in sorted(common_dates)[-confirmation_days:]:
        score = sum(
            float(component["weight_pct"]) / 100
            * score_for[states_by_component[component["label"]][date]["state"]]
            for component in component_states
        )
        state = "risk_on" if score >= threshold else "risk_off" if score <= -threshold else "neutral"
        daily_states.append({"date": date, "state": state, "score": score})

    latest = daily_states[-1]
    return {
        "state": latest["state"] if len({item["state"] for item in daily_states}) == 1 else "neutral",
        "latest_date": latest["date"],
        "score": latest["score"],
        "confirmation_days": confirmation_days,
        "signals": [item["state"] for item in daily_states],
        "daily_states": daily_states,
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
        purchased = {code: 0.0 for code in deficits}
        for code, deficit in sorted(deficits.items(), key=lambda item: item[1], reverse=True):
            price = prices[code]
            allocation = budget * deficit / total_deficit
            quantity = min(math.floor(allocation / price), math.floor(remaining / price))
            if quantity >= 1:
                value = quantity * price
                buys.append({"code": code, "quantity": int(quantity), "price": price, "value": value})
                purchased[code] += value
                remaining -= value

        while True:
            remaining_deficits = {
                code: deficit - purchased[code]
                for code, deficit in deficits.items()
            }
            candidates = []
            for code, deficit in remaining_deficits.items():
                price = prices[code]
                improvement = abs(deficit) - abs(deficit - price)
                if price <= remaining and improvement > 0:
                    candidates.append((improvement, deficit, code, price))
            candidate = max(candidates, default=None)
            if candidate is None:
                break
            _improvement, _deficit, code, price = candidate
            existing = next((order for order in buys if order["code"] == code), None)
            if existing:
                existing["quantity"] += 1
                existing["value"] += price
            else:
                buys.append({"code": code, "quantity": 1, "price": price, "value": price})
            purchased[code] += price
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
