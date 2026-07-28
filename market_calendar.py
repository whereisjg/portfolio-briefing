"""Small KRX trading-day calendar used before KIS trading calls."""

from datetime import date, datetime, time

import pytz


KST = pytz.timezone("Asia/Seoul")

KRX_HOLIDAYS = {
    date(2026, 1, 1): "신정",
    date(2026, 2, 16): "설날 연휴",
    date(2026, 2, 17): "설날",
    date(2026, 2, 18): "설날 연휴",
    date(2026, 3, 2): "삼일절 대체공휴일",
    date(2026, 5, 1): "근로자의 날",
    date(2026, 5, 5): "어린이날",
    date(2026, 5, 25): "부처님오신날 대체공휴일",
    date(2026, 6, 3): "전국동시지방선거일",
    date(2026, 7, 17): "제헌절",
    date(2026, 8, 17): "광복절 대체공휴일",
    date(2026, 9, 24): "추석 연휴",
    date(2026, 9, 25): "추석",
    date(2026, 10, 5): "개천절 대체공휴일",
    date(2026, 10, 9): "한글날",
    date(2026, 12, 25): "성탄절",
}

KRX_ORDER_START = time(9, 0)
KRX_ORDER_CUTOFF = time(15, 20)


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


def krx_order_status(now=None):
    """Return whether a cash ETF order may be sent during the regular KRX session."""
    now = now or datetime.now(KST)
    day_status = krx_market_status(now.date())
    if not day_status["open"]:
        return {**day_status, "orderable": False}
    if not KRX_ORDER_START <= now.time() < KRX_ORDER_CUTOFF:
        return {
            **day_status,
            "orderable": False,
            "reason": "정규장 주문 가능 시간 아님 (09:00~15:20 KST)",
        }
    return {**day_status, "orderable": True, "reason": "정규장 주문 가능 시간"}
