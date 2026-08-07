"""Shared KIS Open API authentication, account context, and balance access."""

import json
import os
import sys
import time

import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


KST = pytz.timezone("Asia/Seoul")
TOKEN_MAX_AGE_SECONDS = 6 * 60 * 60
DEFAULT_BASE_URL = "https://openapi.koreainvestment.com:9443"
BALANCE_MCI_RETRY_DELAYS_SECONDS = (3, 7, 15, 30)


def env_value(name, default=""):
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def as_float(value, default=0.0):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def get_http_session(retries=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 504)):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def is_paper():
    return env_value("KIS_ACCOUNT_MODE", "live").lower() == "paper"


def transaction_id(real, paper):
    return paper if is_paper() else real


def required(name):
    value = env_value(name)
    if not value:
        raise ValueError(f"KIS 환경변수가 없습니다: {name}")
    return value


def load_cached_access_token(cache_file, max_age_seconds=TOKEN_MAX_AGE_SECONDS):
    if not cache_file:
        return None
    try:
        with open(cache_file, encoding="utf-8") as file:
            cached = json.load(file)
        issued_at = float(cached.get("issued_at", 0))
        token = str(cached.get("access_token", "")).strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if not token or time.time() - issued_at >= max_age_seconds:
        return None
    print("KIS access token cache hit (issued within 6 hours)", file=sys.stderr)
    return token


def save_access_token(cache_file, access_token):
    if not cache_file:
        return
    payload = {"access_token": access_token, "issued_at": time.time()}
    with open(cache_file, "w", encoding="utf-8") as file:
        json.dump(payload, file)
    with open(f"{cache_file}.updated", "w", encoding="utf-8") as file:
        file.write("issued\n")


def get_access_token(app_key, app_secret, base_url, cache_file="", session_factory=get_http_session):
    access_token = load_cached_access_token(cache_file)
    if access_token:
        return access_token

    token_response = session_factory(retries=1).post(
        f"{base_url}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
        timeout=20,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise ValueError("KIS 접근 토큰을 받지 못했습니다.")
    save_access_token(cache_file, access_token)
    print("KIS access token issued", file=sys.stderr)
    return access_token


def fetch_balance(session_factory=get_http_session, cache_file=""):
    """Read the domestic-stock account balance without placing an order."""
    app_key = required("KIS_APP_KEY")
    app_secret = required("KIS_APP_SECRET")
    account_no = required("KIS_ACCOUNT_NO")
    product_code = required("KIS_PRODUCT_CODE")
    base_url = env_value("KIS_API_BASE_URL", DEFAULT_BASE_URL)
    tr_id = env_value("KIS_BALANCE_TR_ID", transaction_id("TTTC8434R", "VTTC8434R"))
    access_token = get_access_token(app_key, app_secret, base_url, cache_file, session_factory)
    session = session_factory(retries=1)
    headers = {
        "authorization": f"Bearer {access_token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }
    params = {
        "CANO": account_no,
        "ACNT_PRDT_CD": product_code,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "N",
        "INQR_DVSN": "01",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    for attempt, delay in enumerate((*BALANCE_MCI_RETRY_DELAYS_SECONDS, None)):
        response = session.get(
            f"{base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        message = str(payload.get("msg1", ""))
        if payload.get("rt_cd") == "0":
            break
        if "MCI전송" not in message or delay is None:
            raise ValueError(f"KIS 잔고조회 실패: {message or '알 수 없는 오류'}")
        print(
            f"KIS 잔고조회 MCI 오류, {delay}초 후 재시도합니다. "
            f"({attempt + 1}/{len(BALANCE_MCI_RETRY_DELAYS_SECONDS)})",
            file=sys.stderr,
        )
        time.sleep(delay)

    holdings = payload.get("output1") or payload.get("output") or []
    summary_rows = payload.get("output2") or [{}]
    summary = summary_rows[0] if isinstance(summary_rows, list) else summary_rows
    return holdings, summary, access_token
