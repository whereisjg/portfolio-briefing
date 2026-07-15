"""Read-only KIS domestic stock balance check for GitHub Actions."""

import os
import sys

import requests


BASE_URL = "https://openapi.koreainvestment.com:9443"


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    app_key = required("KIS_APP_KEY")
    app_secret = required("KIS_APP_SECRET")
    cano = required("KIS_ACCOUNT_NO")
    acnt_prdt_cd = required("KIS_PRODUCT_CODE")

    token_response = requests.post(
        f"{BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        timeout=20,
    )
    token_response.raise_for_status()
    access_token = token_response.json().get("access_token")
    if not access_token:
        raise RuntimeError("KIS response did not contain an access token")

    response = requests.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers={
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": "TTTC8434R",
            "custtype": "P",
        },
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "01",
            "UNPR": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise RuntimeError(f"KIS balance query failed: {payload.get('msg1', 'unknown error')}")

    output = payload.get("output", [])
    summary = payload.get("output2", [{}])[0]
    print(f"KIS balance query succeeded: {len(output)} holding(s)")
    print(f"evaluated amount: {summary.get('tot_evlu_amt', 'N/A')}")
    print(f"cash available: {summary.get('prvs_rcdl_excc_amt', 'N/A')}")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"KIS balance query failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
