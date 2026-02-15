#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simmer FastLoop Trading Skill

Trades Polymarket BTC 5-minute fast markets using CEX price momentum.
Default signal: Binance BTCUSDT candles. Agents can customize signal source.

Usage:
    python fast_trader.py              # Dry run (show opportunities, no trades)
    python fast_trader.py --live       # Execute real trades
    python fast_trader.py --positions  # Show current fast market positions
    python fast_trader.py --quiet      # Only output on trades/errors

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import json
import math
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

# Force line-buffered stdout for non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

CONFIG_SCHEMA = {}
CONFIG_SCHEMA["entry_threshold"] = {"default": 0.05, "env": "SIMMER_SPRINT_ENTRY", "type": float, "help": "Min price divergence from 50c to trigger trade"}
CONFIG_SCHEMA["min_momentum_pct"] = {"default": 0.5, "env": "SIMMER_SPRINT_MOMENTUM", "type": float, "help": "Min BTC pct move in lookback window to trigger"}
CONFIG_SCHEMA["max_position"] = {"default": 5.0, "env": "SIMMER_SPRINT_MAX_POSITION", "type": float, "help": "Max USD per trade"}
CONFIG_SCHEMA["signal_source"] = {"default": "coinbase", "env": "SIMMER_SPRINT_SIGNAL", "type": str, "help": "Price feed source (coinbase, binance, coingecko)"}
CONFIG_SCHEMA["lookback_minutes"] = {"default": 5, "env": "SIMMER_SPRINT_LOOKBACK", "type": int, "help": "Minutes of price history for momentum calc"}
CONFIG_SCHEMA["min_time_remaining"] = {"default": 60, "env": "SIMMER_SPRINT_MIN_TIME", "type": int, "help": "Skip fast markets with less than this many seconds remaining"}
CONFIG_SCHEMA["asset"] = {"default": "BTC", "env": "SIMMER_SPRINT_ASSET", "type": str, "help": "Asset to trade (BTC, ETH, SOL)"}
CONFIG_SCHEMA["window"] = {"default": "5m", "env": "SIMMER_SPRINT_WINDOW", "type": str, "help": "Market window duration (5m or 15m)"}
CONFIG_SCHEMA["volume_confidence"] = {"default": True, "env": "SIMMER_SPRINT_VOL_CONF", "type": bool, "help": "Weight signal by volume (higher volume = more confident)"}

TRADE_SOURCE = "sdk:fastloop"
SMART_SIZING_PCT = 0.05  # 5% of balance per trade
MIN_SHARES_PER_ORDER = 5  # Polymarket minimum

# Asset -> Binance symbol mapping
ASSET_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

# Asset -> Gamma API search patterns
ASSET_PATTERNS = {
    "BTC": ["bitcoin up or down"],
    "ETH": ["ethereum up or down"],
    "SOL": ["solana up or down"],
}


def _load_config(schema, skill_file, config_filename="config.json"):
