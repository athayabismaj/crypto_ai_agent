"""
fetch_data.py — Akuisisi Data Exchange
Mengambil OHLCV, funding rate, dan orderbook snapshot via ccxt.
Output: file Parquet immutable di research/data/raw/
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class FetchConfig:
    exchange: str = "binance"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    timeframes: list[str] = field(default_factory=lambda: ["1h", "4h"])
    start_date: str = "2023-01-01"
    end_date: str = ""  # kosong = sampai sekarang
    rate_limit_sleep: float = 0.3
    max_retry: int = 3
    raw_dir: str = "research/data/raw"


class FetchError(Exception):
    pass


class RateLimitError(FetchError):
    pass


def _get_exchange(name: str) -> Any:
    """Inisialisasi ccxt exchange instance."""
    try:
        import ccxt
    except ImportError:
        raise ImportError("Install ccxt: pip install ccxt")

    exchange_class = getattr(ccxt, name, None)
    if exchange_class is None:
        raise ValueError(f"Exchange '{name}' tidak dikenali oleh ccxt.")
    return exchange_class({"enableRateLimit": True})


def fetch_ohlcv(
    symbol: str,
    tf: str,
    start: datetime,
    end: datetime,
    exchange: str = "binance",
    config: FetchConfig | None = None,
) -> pd.DataFrame:
    """
    Ambil data OHLCV dari exchange via ccxt (synchronous).
    Return DataFrame [timestamp, open, high, low, close, volume].
    """
    cfg = config or FetchConfig()
    ex = _get_exchange(exchange)

    all_data: list[list] = []
    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    limit = 1000  # max per request (Binance)

    import time

    for attempt in range(cfg.max_retry):
        try:
            while since_ms < end_ms:
                ohlcv = ex.fetch_ohlcv(symbol, tf, since=since_ms, limit=limit)
                if not ohlcv:
                    break
                all_data.extend(ohlcv)
                since_ms = ohlcv[-1][0] + 1  # next candle
                time.sleep(cfg.rate_limit_sleep)
            break
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower():
                wait = cfg.rate_limit_sleep * (2**attempt)
                log.warning(f"Rate limit hit, menunggu {wait}s...")
                time.sleep(wait)
                continue
            raise FetchError(f"Gagal fetch {symbol} {tf}: {e}") from e

    if not all_data:
        raise FetchError(f"Tidak ada data untuk {symbol} {tf}")

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    # Filter in range
    df = df[(df["timestamp"] >= start.replace(tzinfo=timezone.utc)) & (df["timestamp"] <= end.replace(tzinfo=timezone.utc))]
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def fetch_funding_rate(
    symbol: str,
    start: datetime,
    end: datetime,
    exchange: str = "binance",
) -> pd.DataFrame:
    """Ambil funding rate history (Futures)."""
    ex = _get_exchange(exchange)
    try:
        if not hasattr(ex, "fetch_funding_rate_history"):
            log.warning(f"Exchange {exchange} tidak support funding rate history.")
            return pd.DataFrame(columns=["timestamp", "rate", "next_time"])

        data = ex.fetch_funding_rate_history(symbol, since=int(start.timestamp() * 1000), limit=500)
        rows = []
        for entry in data:
            rows.append({
                "timestamp": pd.to_datetime(entry.get("timestamp", 0), unit="ms", utc=True),
                "rate": entry.get("fundingRate", 0.0),
                "next_time": pd.to_datetime(entry.get("nextFundingTimestamp", 0), unit="ms", utc=True),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        log.error(f"Gagal fetch funding rate {symbol}: {e}")
        return pd.DataFrame(columns=["timestamp", "rate", "next_time"])


def fetch_orderbook_snap(
    symbol: str,
    depth: int = 20,
    n_samples: int = 1,
    exchange: str = "binance",
) -> list[dict]:
    """Ambil orderbook depth snapshot (untuk simulasi fill di backtest)."""
    ex = _get_exchange(exchange)
    try:
        ob = ex.fetch_order_book(symbol, limit=depth)
        return [{"bids": ob["bids"][:depth], "asks": ob["asks"][:depth], "timestamp": ob.get("timestamp")}]
    except Exception as e:
        raise FetchError(f"Gagal fetch orderbook {symbol}: {e}") from e


def save_raw(df: pd.DataFrame, symbol: str, tf: str, config: FetchConfig | None = None) -> Path:
    """Simpan DataFrame ke Parquet di raw_dir."""
    cfg = config or FetchConfig()
    os.makedirs(cfg.raw_dir, exist_ok=True)

    if df.empty:
        raise ValueError("DataFrame kosong, tidak bisa disimpan.")

    start_str = df["timestamp"].min().strftime("%Y%m%d")
    end_str = df["timestamp"].max().strftime("%Y%m%d")
    filename = f"{symbol}_{tf}_{start_str}_{end_str}.parquet"
    path = Path(cfg.raw_dir) / filename

    df.to_parquet(path, engine="pyarrow", index=False)
    log.info(f"Saved raw data: {path} ({len(df)} rows)")
    return path


def run_fetch_pipeline(config: FetchConfig | None = None) -> dict[str, Path]:
    """
    Jalankan seluruh pipeline fetch untuk semua simbol dan timeframe.
    Return mapping dari key "SYMBOL_TF" ke path file Parquet.
    """
    cfg = config or FetchConfig()
    end_dt = datetime.fromisoformat(cfg.end_date) if cfg.end_date else datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(cfg.start_date)

    results: dict[str, Path] = {}
    for symbol in cfg.symbols:
        for tf in cfg.timeframes:
            log.info(f"Fetching {symbol} {tf} dari {start_dt} sampai {end_dt}...")
            try:
                df = fetch_ohlcv(symbol, tf, start_dt, end_dt, cfg.exchange, cfg)
                path = save_raw(df, symbol, tf, cfg)
                results[f"{symbol}_{tf}"] = path
            except FetchError as e:
                log.error(f"SKIP {symbol} {tf}: {e}")
    return results
