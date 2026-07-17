"""Small KRX trading-day calendar used before KIS trading calls."""

from datetime import date, datetime

import pytz


KST = pytz.timezone("Asia/Seoul")

KRX_HOLIDAYS = {
    date(2026, 7, 17): "제헌절",
}


def today_kst():
    return datetime.now(KST).date()


def krx_market_status(day=None):
    day = day or today_kst()
    if day.weekday() >= 5:
        return {"open": False, "date": day, "reason": "주말"}
    if day in KRX_HOLIDAYS:
        return {"open": False, "date": day, "reason": KRX_HOLIDAYS[day]}
    return {"open": True, "date": day, "reason": "거래일"}


def is_krx_trading_day(day=None):
    return krx_market_status(day)["open"]
