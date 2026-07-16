#!/usr/bin/env python3
"""Backward-compatible entry point for the ETF trading executor."""

from trading_execution import *  # noqa: F401,F403
from trading_execution import main


if __name__ == "__main__":
    main()
