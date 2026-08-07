"""Persistent cash-flow-neutral comparison of target allocation strategies."""

import json
from datetime import date
from pathlib import Path


SCHEMA_VERSION = 1
MAX_ABS_INTERVAL_RETURN = 0.5


def _validated_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"성과 기준일이 올바르지 않습니다: {value}") from exc


def _validated_prices(prices):
    result = {}
    for code, value in prices.items():
        price = float(value)
        if price <= 0:
            raise ValueError(f"성과 계산 가격이 올바르지 않습니다: {code}")
        result[str(code)] = price
    if not result:
        raise ValueError("성과 계산 대상 가격이 없습니다.")
    return result


def _validated_weights(weights, codes, label):
    result = {str(code): float(weight) for code, weight in weights.items()}
    if set(result) != set(codes):
        raise ValueError(f"{label} 비중 종목과 가격 종목이 일치하지 않습니다.")
    total = sum(result.values())
    if any(weight < 0 for weight in result.values()) or abs(total - 100) > 0.01:
        raise ValueError(f"{label} 비중 합계는 100이어야 합니다: {total:.2f}")
    return result


def load_history(path):
    history_path = Path(path)
    if not history_path.exists():
        return {"version": SCHEMA_VERSION, "records": []}
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"전략 성과 이력을 읽지 못했습니다: {exc}") from exc
    if payload.get("version") != SCHEMA_VERSION or not isinstance(payload.get("records"), list):
        raise ValueError("지원하지 않는 전략 성과 이력 형식입니다.")
    return payload


def save_history(path, payload):
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _strategy_return(previous, current, weight_key):
    interval_return = 0.0
    for code, weight_pct in previous[weight_key].items():
        if code not in current["prices"]:
            raise ValueError(f"성과 가격이 누락되었습니다: {code}")
        asset_return = current["prices"][code] / previous["prices"][code] - 1
        if abs(asset_return) > MAX_ABS_INTERVAL_RETURN:
            raise ValueError(
                f"성과 가격 변동이 비정상적으로 큽니다: {code} {asset_return * 100:+.2f}%"
            )
        interval_return += weight_pct / 100 * asset_return
    return interval_return


def calculate_summary(records):
    if not records:
        return None

    hma_index = 1.0
    fixed_index = 1.0
    hma_peak = 1.0
    fixed_peak = 1.0
    hma_mdd = 0.0
    fixed_mdd = 0.0
    hma_turnover = 0.0
    periods = 0

    for previous, current in zip(records, records[1:]):
        hma_return = _strategy_return(previous, current, "hma_weights")
        fixed_return = _strategy_return(previous, current, "fixed_weights")
        hma_index *= 1 + hma_return
        fixed_index *= 1 + fixed_return
        hma_peak = max(hma_peak, hma_index)
        fixed_peak = max(fixed_peak, fixed_index)
        hma_mdd = min(hma_mdd, hma_index / hma_peak - 1)
        fixed_mdd = min(fixed_mdd, fixed_index / fixed_peak - 1)
        hma_turnover += 0.5 * sum(
            abs(current["hma_weights"][code] - previous["hma_weights"][code])
            for code in previous["hma_weights"]
        )
        periods += 1

    hma_twr_pct = (hma_index - 1) * 100
    fixed_twr_pct = (fixed_index - 1) * 100
    return {
        "start_date": records[0]["date"],
        "end_date": records[-1]["date"],
        "observations": len(records),
        "periods": periods,
        "hma_twr_pct": hma_twr_pct,
        "fixed_twr_pct": fixed_twr_pct,
        "difference_pct_points": hma_twr_pct - fixed_twr_pct,
        "hma_mdd_pct": hma_mdd * 100,
        "fixed_mdd_pct": fixed_mdd * 100,
        "hma_turnover_pct": hma_turnover,
    }


def update_strategy_comparison(path, record_date, prices, hma_weights, fixed_weights):
    """Upsert one valuation and return geometrically linked strategy TWR metrics."""
    price_map = _validated_prices(prices)
    record = {
        "date": _validated_date(record_date),
        "prices": price_map,
        "hma_weights": _validated_weights(hma_weights, price_map, "HMA 전략"),
        "fixed_weights": _validated_weights(fixed_weights, price_map, "고정 전략"),
    }
    payload = load_history(path)
    records = [item for item in payload["records"] if item.get("date") != record["date"]]
    records.append(record)
    records.sort(key=lambda item: item["date"])

    if len(records) > 1 and any(
        set(item.get("prices", {})) != set(price_map)
        for item in records
    ):
        raise ValueError("전략 성과 비교 대상 종목이 변경되었습니다. 새 기준선이 필요합니다.")

    summary = calculate_summary(records)
    payload = {
        "version": SCHEMA_VERSION,
        "method": "cash-flow-neutral daily target-weight TWR",
        "records": records,
        "summary": summary,
    }
    save_history(path, payload)
    return summary
