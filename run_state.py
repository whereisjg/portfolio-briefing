"""Short-lived workflow state shared between portfolio steps."""

import json
from pathlib import Path


def load_json(path):
    try:
        with open(Path(path), encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def save_json(path, value):
    with open(Path(path), "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False)


def load_balance_snapshot(path):
    snapshot = load_json(path)
    if snapshot is None:
        return None
    return snapshot.get("holdings", []), snapshot.get("summary", {}), snapshot.get("access_token")


def save_balance_snapshot(path, holdings, summary, access_token):
    save_json(path, {
        "holdings": holdings,
        "summary": summary,
        "access_token": access_token,
    })
