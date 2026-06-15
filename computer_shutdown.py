#!/usr/bin/env python3
"""
PC를 종료하기 전에 Telegram 알림을 전송합니다.

사용법:
  python computer_shutdown.py             # 즉시 종료
  python computer_shutdown.py --delay 60  # 60초 후 종료
  python computer_shutdown.py --dry-run   # 종료 없이 테스트
"""

import argparse
import os
import platform
import subprocess
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def env_value(name, default=""):
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def get_http_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.3, status_forcelist=(429, 500, 502, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def send_telegram_notification(message, token, chat_id):
    if not token or not chat_id:
        print("Telegram 설정 없음 — 알림 건너뜀.")
        return False
    try:
        session = get_http_session()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = session.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        resp.raise_for_status()
        print("Telegram 알림 전송 완료.")
        return True
    except Exception as exc:
        print(f"Telegram 전송 실패: {exc}")
        return False


def build_shutdown_command():
    system = platform.system()
    if system == "Windows":
        return ["shutdown", "/s", "/t", "0"]
    if system in ("Linux", "Darwin"):
        return ["sudo", "shutdown", "-h", "now"]
    raise RuntimeError(f"지원하지 않는 운영체제: {system}")


def run_shutdown(dry_run=False):
    cmd = build_shutdown_command()
    if dry_run:
        print(f"[dry-run] 종료 명령: {' '.join(cmd)}")
        return
    print(f"종료 명령 실행: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="PC 종료 + Telegram 알림")
    parser.add_argument("--delay", type=int, default=0, metavar="초",
                        help="종료 전 대기 시간(초, 기본값: 0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="종료 명령을 실행하지 않고 테스트")
    args = parser.parse_args(argv)

    token = env_value("TELEGRAM_BOT_TOKEN")
    chat_id = env_value("TELEGRAM_CHAT_ID")

    notice = "🖥️ 컴퓨터를 종료합니다"
    if args.delay:
        notice += f" ({args.delay}초 후)"
    notice += "."

    send_telegram_notification(notice, token, chat_id)

    if args.delay > 0:
        print(f"{args.delay}초 대기 중...")
        time.sleep(args.delay)

    try:
        run_shutdown(dry_run=args.dry_run)
    except subprocess.CalledProcessError as exc:
        print(f"종료 명령 실패: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
