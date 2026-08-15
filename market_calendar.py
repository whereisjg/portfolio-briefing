"""Small KRX trading-day calendar used before KIS trading calls."""

from datetime import date, datetime, time

import pytz

import kis_client


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
STATIC_CALENDAR_YEARS = {day.year for day in KRX_HOLIDAYS}


def today_kst():
    return datetime.now(KST).date()


def krx_market_status(day=None):
    day = day or today_kst()
    if day.weekday() >= 5:
        return {"open": False, "date": day, "reason": "주말"}
    if day in KRX_HOLIDAYS:
        return {"open": False, "date": day, "reason": KRX_HOLIDAYS[day]}
    if day.year not in STATIC_CALENDAR_YEARS:
        return {"open": False, "date": day, "reason": "KRX 휴장일 정보 미등록"}
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


def fetch_kis_krx_market_status(day=None, session_factory=None):
    """Read the official KRX open flag from KIS without using account data."""
    day = day or today_kst()
    if day.weekday() >= 5:
        return {"open": False, "date": day, "reason": "주말"}
    if day in KRX_HOLIDAYS:
        return {"open": False, "date": day, "reason": KRX_HOLIDAYS[day]}

    app_key = kis_client.required("KIS_APP_KEY")
    app_secret = kis_client.required("KIS_APP_SECRET")
    base_url = kis_client.env_value("KIS_API_BASE_URL", kis_client.DEFAULT_BASE_URL)
    cache_file = kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE")
    session_factory = session_factory or kis_client.get_http_session
    access_token = kis_client.get_access_token(
        app_key,
        app_secret,
        base_url,
        cache_file,
        session_factory,
    )
    response = session_factory(retries=1).get(
        f"{base_url}/uapi/domestic-stock/v1/quotations/chk-holiday",
        headers={
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "CTCA0903R",
            "custtype": "P",
        },
        params={
            "BASS_DT": day.strftime("%Y%m%d"),
            "CTX_AREA_FK": "",
            "CTX_AREA_NK": "",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise ValueError(payload.get("msg1") or "KIS 거래일 조회 실패")
    rows = payload.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    requested = day.strftime("%Y%m%d")
    row = next((item for item in rows if str(item.get("bass_dt", "")) == requested), None)
    if row is None:
        raise ValueError(f"KIS 거래일 응답에 {requested} 정보가 없습니다.")
    is_open = str(row.get("opnd_yn", "")).upper() == "Y"
    return {
        "open": is_open,
        "date": day,
        "reason": "KIS 거래일" if is_open else "KIS 휴장일",
    }


def kis_krx_order_status(now=None, market_status_fetcher=fetch_kis_krx_market_status):
    """Fail closed unless KIS confirms that the KRX market is open."""
    now = now or datetime.now(KST)
    try:
        day_status = market_status_fetcher(now.date())
    except Exception as error:
        day_status = {
            "open": False,
            "date": now.date(),
            "reason": f"KIS 거래일 확인 실패: {error}",
        }
    if not day_status["open"]:
        return {**day_status, "orderable": False}
    if not KRX_ORDER_START <= now.time() < KRX_ORDER_CUTOFF:
        return {
            **day_status,
            "orderable": False,
            "reason": "정규장 주문 가능 시간 아님 (09:00~15:20 KST)",
        }
    return {**day_status, "orderable": True, "reason": "정규장 주문 가능 시간"}
