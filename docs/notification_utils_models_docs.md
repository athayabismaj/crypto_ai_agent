# crypto_ai_agent — Notification · Utils · Models Layer Documentation

*telegram · discord · notifier*
*logger · structured_logger · time_utils · helpers*
*spot_model · futures_model · metadata.json*

**Version 1.0** | Reference: agent_core_docs.md · runtime_layer_docs.md

---

## 1. Overview

These three supporting components serve the entire system without owning any business logic of their own. Notification sends outgoing messages. Utils provides functions used by all layers. Models stores the ML artifacts that power the strategies.

| Component | Purpose | Used By |
|---|---|---|
| **notification/** | Send messages to operators via Telegram & Discord | monitoring, trade_layer, learning_layer, main_loop |
| **utils/** | Stateless functions used by all layers | All layers — free to import |
| **models/** | Store ML models & metadata used by strategy_layer | strategy_layer, main.py (validation at startup) |

---

## PART A — Notification Layer

---

## 2. notifier.py — Centralized Router

The single entry point for all notifications. Other layers must not import `telegram.py` or `discord.py` directly — everything goes through `Notifier`.

### 2.1 EventType → Channel Mapping

| Event | Telegram | Discord | Log Level | Send Condition |
|---|---|---|---|---|
| `trade_opened` | Yes | No | INFO | notify_trade_open = True in config |
| `trade_closed_win` | Yes | No | INFO | notify_trade_close = True |
| `trade_closed_loss` | Yes | Yes | INFO | notify_trade_close = True |
| `circuit_breaker` | Yes | Yes | ERROR | Always — cannot be disabled |
| `strategy_disabled` | Yes | Yes | ERROR | Always |
| `system_error` | Yes | Yes | CRITICAL | Always |
| `drift_major` | Yes | No | WARNING | Drift severity = major or critical |
| `daily_summary` | Yes | No | INFO | notify_daily_summary = True |
| `low_budget_llm` | Yes | No | WARNING | LLM budget < 20% remaining |
| `reconcile_issue` | Yes | Yes | WARNING | Daily reconciliation found an issue |

---

### 2.2 Public Interface

```python
class Notifier:

    def __init__(
        self,
        telegram: 'TelegramNotifier',
        discord:  'DiscordNotifier',
        config:   AgentConfig,
    ): ...

    async def notify(
        self,
        event_type: str,
        data:       dict,
        severity:   str = 'info',   # info | warning | critical
    ) -> None:
        """
        Main entry point. Routing is performed here.
        A failed notification MUST NOT crash the main loop.
        Wrap all send() calls with try/except.
        """

    async def send_emergency(
        self,
        title:   str,
        message: str,
        data:    dict = None,
    ) -> None:
        """
        Send to all channels with aggressive retry.
        Called by safe_mode.emergency_stop().
        Must not fail silently — raise if all channels fail.
        """

    def format_message(
        self,
        event_type: str,
        data:       dict,
    ) -> str:
        """Delegate to a formatter per event type."""
```

> **RULE:** All notifications must be non-blocking to the main loop. Use `asyncio.create_task()` for fire-and-forget. If delivery fails after retries, log to `errors.log` and continue.

---

## 3. telegram.py — Telegram Delivery

Implementation of message delivery to the Telegram Bot API. Supports HTML formatting, automatic retry, and splitting of long messages.

### 3.1 Configuration

```bash
# .env
TELEGRAM_BOT_TOKEN = '123456789:ABCdef...'
TELEGRAM_CHAT_ID   = '-100123456789'   # group or channel

# Optional: separate chat IDs per severity
TELEGRAM_ALERT_CHAT_ID = '-100000000001'   # for CRITICAL only
TELEGRAM_TRADE_CHAT_ID = '-100000000002'   # for trade updates only
```

---

### 3.2 Public Interface

```python
class TelegramNotifier:
    MAX_MESSAGE_LEN = 4096    # Telegram API hard limit
    MAX_RETRY       = 3
    RETRY_DELAY_S   = [2, 5, 10]

    async def send(
        self,
        message:    str,
        chat_id:    str  = None,    # None = TELEGRAM_CHAT_ID
        parse_mode: str  = 'HTML',
        silent:     bool = False,   # True = no notification sound
    ) -> bool:
        """
        Send message. Returns True if successful.
        If message > MAX_MESSAGE_LEN: auto-split per line.
        """

    async def send_with_retry(
        self,
        message: str,
        **kwargs,
    ) -> bool:
        """Wrapper with exponential backoff retry."""

    def is_configured(self) -> bool:
        """True if BOT_TOKEN and CHAT_ID are available."""
```

---

### 3.3 Message Templates — All Events

| Event | HTML Format | Required Data Fields |
|---|---|---|
| `trade_opened` | 🟢/🔴 `<b>Trade Opened</b>` — Symbol Side Qty @ Price \| SL TP \| Risk \| Mode | symbol, side, qty, avg_fill_price, sl_price, tp_price, risk_usd, mode |
| `trade_closed_win` | ✅ `<b>Trade Closed</b>` — Symbol \| +$PnL (+pct%) \| Exit reason \| Equity | symbol, pnl_usd, pnl_pct, exit_reason, equity |
| `trade_closed_loss` | ❌ `<b>Trade Closed</b>` — Symbol \| -$PnL (-pct%) \| Exit reason \| Equity | symbol, pnl_usd, pnl_pct, exit_reason, equity |
| `circuit_breaker` | 🚨 `<b>CIRCUIT BREAKER</b>` — State \| Reason \| Daily PnL \| Equity \| Halt X min | state, reasons, daily_pnl, equity, halt_duration |
| `strategy_disabled` | ⚠️ `<b>Strategy Disabled</b>` — ID \| Reason \| Last stats | strategy_id, reasons, win_rate, sharpe |
| `system_error` | 🔴 `<b>SYSTEM ERROR</b>` — Component \| Message \| Time | component, message, timestamp |
| `daily_summary` | 📊 `<b>Daily Summary</b>` — PnL \| Trades \| WR \| Equity \| Open positions | daily_pnl, total_trades, win_rate, equity, open_count |

---

### 3.4 Full Message Examples

```
# trade_opened — BUY
🟢 <b>Trade Opened</b>
Symbol : <code>BTCUSDT</code>
Side   : BUY  |  Qty: 0.001
Entry  : 50,000.00 USDT
SL     : 49,000.00  |  TP: 52,000.00
Risk   : $50.00  |  R:R = 1:2
Mode   : [PAPER]

# circuit_breaker — HALTED
🚨 <b>CIRCUIT BREAKER HALTED</b>
Reason     : Daily loss 5.2% > limit 5.0%
Today's PnL: -$520.00 (-5.20%)
Equity     : $9,480.00
Auto-resume: 60 minutes

# daily_summary
📊 <b>Daily Summary — 2024-11-15</b>
PnL     : +$234.50 (+2.35%)
Trades  : 6  |  Won: 4  |  Lost: 2
Win Rate: 66.7%
Equity  : $10,234.50
Open positions: 0
```

---

## 4. discord.py — Discord Delivery

Discord webhook implementation for serious alerts. Discord is used as a secondary channel — it only receives WARNING and CRITICAL alerts.

### 4.1 Configuration

```bash
# .env
DISCORD_WEBHOOK_URL = 'https://discord.com/api/webhooks/...'

# Optional: separate webhooks per channel
DISCORD_ALERT_WEBHOOK = 'https://discord.com/api/webhooks/.../alerts'
DISCORD_TRADE_WEBHOOK = 'https://discord.com/api/webhooks/.../trades'
```

---

### 4.2 Discord Embed Format

```python
class DiscordNotifier:
    MAX_EMBED_DESCRIPTION = 4096

    EMBED_COLORS = {
        'info':     0x2980B9,   # blue
        'warning':  0xF39C12,   # orange
        'critical': 0xE74C3C,   # red
        'success':  0x27AE60,   # green
    }

    async def send(
        self,
        title:       str,
        description: str,
        severity:    str        = 'info',
        fields:      list[dict] = None,
        webhook_url: str        = None,
    ) -> bool:
        """
        Send a Discord embed.
        fields = [{'name': str, 'value': str, 'inline': bool}]
        """
        payload = {
            'embeds': [{
                'title':       title,
                'description': description,
                'color':       self.EMBED_COLORS.get(severity, 0x95A5A6),
                'fields':      fields or [],
                'timestamp':   utcnow().isoformat(),
                'footer':      {'text': 'crypto_ai_agent'},
            }]
        }
        ...

    def is_configured(self) -> bool:
        """True if DISCORD_WEBHOOK_URL is available."""
```

---

## PART B — Utils

---

## 5. logger.py — Standard Logger

A thin wrapper on top of Python's standard logging that automatically adds context (mode, layer name) to every log entry.

### 5.1 Setup & Usage

```python
# In every module — import like this:
from utils.logger import get_logger

log = get_logger(__name__)

# Usage:
log.debug('Debug detail', value=123)
log.info('Trade opened', trade_id='uuid', symbol='BTCUSDT')
log.warning('High spread', spread_pct=1.5)
log.error('Order failed', error=str(e), retry_count=3)
log.critical('Circuit breaker triggered', reason='daily_loss')

# All calls accept keyword arguments for structured context
# Auto-added context: timestamp, level, logger_name, mode
```

---

### 5.2 Public Interface

```python
def get_logger(name: str) -> 'AgentLogger':
    """
    Factory function. Call once per module at module level.
    Not inside a function — avoids creating duplicate loggers.
    """

class AgentLogger:
    """
    Wrapper on top of standard logging.Logger.
    All methods accept **kwargs for structured context.
    Context is serialized to JSON in StructuredLogger,
    or to key=value strings in StandardLogger.
    """

    def debug(self, msg: str, **ctx) -> None: ...
    def info(self, msg: str, **ctx) -> None: ...
    def warning(self, msg: str, **ctx) -> None: ...
    def error(self, msg: str, **ctx) -> None: ...
    def critical(self, msg: str, **ctx) -> None: ...

    def bind(self, **ctx) -> 'AgentLogger':
        """
        Return a new logger with pre-bound context.
        Useful for adding trade_id to all logs in one function.
        Example:
            log = log.bind(trade_id=trade.trade_id, symbol=trade.symbol)
            log.info('Trade opened')   # trade_id is automatically included
            log.info('Order sent')     # trade_id is automatically included too
        """
```

---

### 5.3 Log Level Convention

| Level | When Used | Sent to Error Log? | Sent to Alert? |
|---|---|---|---|
| **DEBUG** | Internal details for debugging. DISABLED in production. | No | No |
| **INFO** | Important normal events: trade opened, sync complete, config loaded. | No | No |
| **WARNING** | Suboptimal conditions but system still running: high spread, WS reconnect. | No | No |
| **ERROR** | Failures requiring investigation: order rejected, sync failed. | Yes | No |
| **CRITICAL** | Fatal failures: safe mode, circuit breaker, DB corrupt. | Yes | Yes (via alerts.py) |

---

## 6. structured_logger.py — JSON Logging

Writes logs in JSON format with one object per line. This format can be parsed directly by Prometheus, Grafana Loki, or other log analysis tools.

### 6.1 Standard JSON Format

```json
// Each log line = one JSON object
{
  "timestamp":   "2024-11-15T08:30:00.123Z",
  "level":       "INFO",
  "logger":      "trade_layer.manager",
  "message":     "Trade opened",
  "mode":        "paper",
  "trade_id":    "uuid-...",
  "symbol":      "BTCUSDT",
  "side":        "BUY",
  "qty":         0.001,
  "price":       50000.0,
  "equity":      10000.0
}

// REQUIRED fields in every entry:
// timestamp (ISO 8601 UTC), level, logger, message, mode

// CONDITIONAL fields (add when relevant):
// trade_id, symbol, strategy_id, equity, error, latency_ms
```

---

### 6.2 Public Interface

```python
class StructuredLogger:
    """
    AgentLogger implementation that outputs JSON.
    Activated via LOG_FORMAT=json in config.
    Falls back to plain text if LOG_FORMAT=text (default for development).
    """

    def __init__(
        self,
        name:       str,
        log_file:   str = 'logs/agent.log',
        log_format: str = 'json',    # 'json' | 'text'
        level:      str = 'INFO',
    ): ...

    def _serialize(self, msg: str, level: str, **ctx) -> str:
        """
        Serialize to JSON string.
        Handle types that are not JSON-serializable:
        - datetime  → isoformat()
        - Exception → str()
        - dataclass → dataclasses.asdict()
        - object    → repr()
        """
```

---

### 6.3 Consistency with Prometheus

```python
# structured_logger.py and prometheus_metrics.py must agree
# on the same field names for consistent queries.

# Example: latency
# In structured_logger:
log.info("Order placed", latency_ms=123.4)

# In prometheus_metrics.py:
metrics.histogram("order_latency_ms").observe(123.4)

# In Grafana query:
# sum(rate(order_latency_ms_bucket[5m])) by (symbol)

# Log field name = Prometheus metric name MUST be the same.
# This makes it easy to correlate logs and metrics when debugging.
```

---

### 6.4 Logger Config Keys

| Key | Default | Description |
|---|---|---|
| `LOG_FORMAT` | json | json = structured JSON. text = human-readable for development. |
| `LOG_LEVEL` | INFO | DEBUG for development only. Production = INFO. |
| `LOG_DIR` | logs/ | Output directory for all log files. |
| `LOG_ROTATION` | daily | Rotate daily. Old files are compressed and kept for RETENTION days. |
| `LOG_RETENTION` | 30 | Days to retain logs before deletion (except errors.log = 90 days). |
| `LOG_MAX_SIZE_MB` | 100 | Max file size per log before forced rotation. |

---

## 7. time_utils.py — Time Utilities

All time operations must use functions from this file. There must be no `datetime.now()` or `datetime.utcnow()` calls directly in code — always via `time_utils` for consistency and testability.

> **RULE:** Use `utcnow()` from `time_utils`, NOT `datetime.utcnow()` directly. The reason: `time_utils` can be mocked in unit tests to simulate a specific point in time.

### 7.1 Public Functions

| Function | Return | Description |
|---|---|---|
| `utcnow()` | datetime (UTC, aware) | Replaces datetime.utcnow(). Always timezone-aware. |
| `utcnow_ms()` | int | Unix timestamp in milliseconds. Used for client_order_id. |
| `tf_to_seconds(tf)` | int | '1h'→3600, '4h'→14400, '1d'→86400, '1m'→60, etc. |
| `tf_to_ms(tf)` | int | tf_to_seconds × 1000. For Binance API timestamps. |
| `candle_open_time(ts,tf)` | datetime | Open time of the candle containing timestamp ts. |
| `candle_close_time(ts,tf)` | datetime | Close time of the candle (candle_open + tf - 1ms). |
| `is_new_candle(ts,tf,prev_ts)` | bool | True if ts and prev_ts are in different candles. |
| `time_until_candle_close(tf)` | float (seconds) | Seconds remaining until the current candle closes. |
| `format_duration(seconds)` | str | 123 → '2m 3s', 3700 → '1h 1m 40s'. For logging. |
| `is_daily_reset_due(last_reset)` | bool | True if UTC 00:00 has passed since last_reset. |

---

### 7.2 Implementation & Mock Support

```python
from datetime import datetime, timezone
from typing  import Optional

# Internal clock — can be overridden for testing
_mock_time: Optional[datetime] = None

def utcnow() -> datetime:
    """Always return a timezone-aware UTC datetime."""
    if _mock_time is not None:
        return _mock_time
    return datetime.now(timezone.utc)

def mock_time(dt: datetime) -> None:
    """Override for unit testing. ONLY in test code."""
    global _mock_time
    _mock_time = dt

def reset_mock_time() -> None:
    """Reset to real time after test completes."""
    global _mock_time
    _mock_time = None

# Example usage in test:
def test_daily_reset():
    from utils.time_utils import mock_time, reset_mock_time, utcnow
    mock_time(datetime(2024, 11, 15, 23, 59, 59, tzinfo=timezone.utc))
    assert not scheduler.is_due('daily_reset')

    mock_time(datetime(2024, 11, 16, 0, 0, 1, tzinfo=timezone.utc))
    assert scheduler.is_due('daily_reset')
    reset_mock_time()

VALID_TIMEFRAMES = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d'}

def tf_to_seconds(tf: str) -> int:
    mapping = {
        '1m':60, '3m':180, '5m':300, '15m':900, '30m':1800,
        '1h':3600, '2h':7200, '4h':14400, '6h':21600,
        '1d':86400
    }
    if tf not in mapping:
        raise ValueError(f'Invalid timeframe: {tf}. Valid: {VALID_TIMEFRAMES}')
    return mapping[tf]
```

---

## 8. helpers.py — General-Purpose Functions

A collection of stateless functions used throughout the codebase. No state, no I/O — all pure functions that are easy to test.

### 8.1 Quantity & Price Rounding

```python
import math

def round_qty(qty: float, step_size: float) -> float:
    """
    Round quantity to step_size using FLOOR.
    NOT regular rounding — floor to avoid over-ordering.
    Examples:
        round_qty(0.1235, 0.001) → 0.123  (not 0.124)
        round_qty(0.009, 0.01)  → 0.00   (if below step)
    """
    if step_size <= 0:
        raise ValueError(f'step_size must be > 0, got: {step_size}')
    precision = max(0, round(-math.log10(step_size)))
    return round(math.floor(qty / step_size) * step_size, precision)

def round_price(price: float, tick_size: float) -> float:
    """
    Round price to tick_size using regular ROUND.
    (Different from round_qty which uses floor)
    """
    if tick_size <= 0:
        raise ValueError(f'tick_size must be > 0, got: {tick_size}')
    precision = max(0, round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)

def clamp(value: float, min_val: float, max_val: float) -> float:
    """Ensure value is within [min_val, max_val]."""
    return max(min_val, min(max_val, value))

def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Safe division — returns default if b = 0."""
    return a / b if b != 0 else default

def pct_change(old: float, new: float) -> float:
    """Return percentage change from old to new."""
    return safe_div(new - old, abs(old)) * 100
```

---

### 8.2 Retry & Error Handling

```python
import asyncio
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar('T')

async def retry_async(
    coro_fn:      Callable,
    max_retry:    int   = 3,
    base_delay_s: float = 1.0,
    max_delay_s:  float = 60.0,
    backoff:      float = 2.0,
    exceptions:   tuple = (Exception,),
) -> any:
    """
    Retry a coroutine with exponential backoff.
    Example: max_retry=3, base=1, backoff=2 → delays 1s, 2s, 4s
    """
    last_exc = None
    delay    = base_delay_s

    for attempt in range(max_retry + 1):
        try:
            return await coro_fn()
        except exceptions as e:
            last_exc = e
            if attempt == max_retry:
                break
            await asyncio.sleep(min(delay, max_delay_s))
            delay *= backoff

    raise last_exc

def retry_sync(
    max_retry:    int   = 3,
    base_delay_s: float = 0.5,
    exceptions:   tuple = (Exception,),
):
    """Decorator for synchronous functions."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            import time
            last_exc = None
            for attempt in range(max_retry + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retry:
                        time.sleep(base_delay_s * (2 ** attempt))
            raise last_exc
        return wrapper
    return decorator
```

---

### 8.3 Order ID & String Utilities

```python
import re, uuid
from utils.time_utils import utcnow_ms

ORDER_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{1,36}$')

def generate_order_id(strategy_id: str, symbol: str) -> str:
    """
    Generate a unique and deterministic client_order_id.
    Format: {strategy_prefix}_{symbol_lower}_{timestamp_ms}
    Example: spot001_btcusdt_1731658200000
    Automatically truncated to 36 characters if strategy_id is too long.
    """
    prefix = strategy_id[:8].lower().replace(' ', '_')
    sym    = symbol[:8].lower()
    ts     = utcnow_ms()
    raw    = f'{prefix}_{sym}_{ts}'
    return raw[:36]

def validate_order_id(cid: str) -> tuple[bool, str]:
    """
    Return (True, '') if valid.
    Return (False, error_message) if not.
    """
    if not cid:
        return False, 'client_order_id is empty'
    if len(cid) > 36:
        return False, f'Too long: {len(cid)} > 36'
    if not ORDER_ID_PATTERN.match(cid):
        return False, f'Invalid characters: {cid}'
    return True, ''

def truncate(text: str, max_len: int, suffix: str = '...') -> str:
    """Truncate string with suffix if too long."""
    if len(text) <= max_len: return text
    return text[:max_len - len(suffix)] + suffix

def mask_key(key: str, visible_chars: int = 6) -> str:
    """
    Mask API key for safe logging.
    'abc123xyz789' → 'abc123...789'
    """
    if len(key) <= visible_chars * 2: return '***'
    return key[:visible_chars] + '...' + key[-visible_chars:]
```

---

### 8.4 Financial Calculations

```python
def calc_pnl(
    side:        str,
    entry_price: float,
    exit_price:  float,
    qty:         float,
    commission:  float = 0.0,
) -> tuple[float, float]:
    """
    Return (pnl_usd, pnl_pct).
    BUY:  pnl = (exit - entry) × qty - commission
    SELL: pnl = (entry - exit) × qty - commission
    """
    gross = (exit_price - entry_price) * qty
    if side == 'SELL': gross = -gross
    pnl_usd = gross - commission
    cost    = entry_price * qty
    pnl_pct = safe_div(pnl_usd, cost) * 100
    return pnl_usd, pnl_pct

def calc_risk_reward(entry: float, sl: float, tp: float) -> float:
    """
    Return risk:reward ratio.
    Example: entry=50000, sl=49000, tp=52000 → RR = 2.0
    """
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    return safe_div(reward, risk)

def calc_position_value(
    qty:      float,
    price:    float,
    leverage: int = 1,
) -> dict:
    """
    Return dict: notional, margin, leverage_ratio
    notional = qty × price
    margin   = notional / leverage
    """
    notional = qty * price
    margin   = safe_div(notional, leverage)
    return {'notional': notional, 'margin': margin, 'leverage': leverage}

def annualize_return(period_return: float, days: int) -> float:
    """
    Annualized return from a period return.
    Example: 5% in 30 days → ~73% annualized.
    Formula: (1 + r)^(365/days) - 1
    """
    if days <= 0: return 0.0
    return (1 + period_return) ** (365 / days) - 1
```

---

### 8.5 Validation Helpers

```python
def is_valid_symbol(symbol: str) -> bool:
    """
    Validate Binance symbol format.
    Valid:   BTCUSDT, ETHUSDT, SOLUSDT
    Invalid: btcusdt (lowercase), BTC/USDT (with slash), empty
    """
    if not symbol or len(symbol) > 20: return False
    return symbol.isupper() and symbol.isalnum()

def is_valid_side(side: str) -> bool:
    return side in ('BUY', 'SELL')

def is_valid_timeframe(tf: str) -> bool:
    from utils.time_utils import VALID_TIMEFRAMES
    return tf in VALID_TIMEFRAMES

def validate_config_range(
    value:   float,
    min_val: float,
    max_val: float,
    name:    str,
) -> None:
    """
    Raise ValueError if value is out of range.
    Used by AgentConfig validators.
    """
    if not (min_val <= value <= max_val):
        raise ValueError(
            f'{name} must be in range [{min_val}, {max_val}], '
            f'got: {value}'
        )
```

---

## PART C — Models

---

## 9. models/ Folder Structure

```
agent/models/
├── spot_model.pkl          ← active spot model
├── futures_model.pkl       ← active futures model
├── metadata.json           ← interface contract (CRITICAL)
└── archive/
    ├── spot_model_v2.2.1.pkl
    ├── spot_model_v2.2.1_metadata.json
    └── ...                 ← keep at least 3 previous versions
```

> **ROLLBACK RULE:** A minimum of 3 previous model versions must exist in `archive/`. When a new model causes problems in production, rollback must be achievable in < 5 minutes by simply replacing the `.pkl` file and `metadata.json`.

---

## 10. metadata.json — ML Interface Contract

This file is the contract between the Research Layer (which trains the model) and the Runtime Layer (which uses the model). Every field is required and must be consistent.

### 10.1 Full Format

```json
{
  "_version":          "1.0",
  "_doc":              "Interface contract between research and runtime",
  "model_id":          "spot_lgbm_v2_3_0",
  "version":           "2.3.0",
  "created_at":        "2024-11-10T08:00:00Z",
  "deployed_at":       "2024-11-15T00:00:00Z",
  "deployed_by":       "automation/deploy.py",
  "model_type":        "lightgbm",
  "task":              "regression",
  "symbol":            "BTCUSDT",
  "timeframe":         "1h",
  "train_period":      ["2020-01-01", "2024-10-31"],
  "test_period":       ["2024-11-01", "2024-11-10"],
  "feature_names": [
    "rsi_7", "rsi_14", "rsi_21",
    "ema_ratio_9_21", "ema_ratio_21_50", "ema_ratio_50_200",
    "atr_14", "atr_pct_14", "atr_percentile",
    "bb_width_20", "bb_pct_20",
    "volume_ratio_20", "obv_norm",
    "log_return_1", "log_return_5", "log_return_20",
    "adx_14", "trend_strength",
    "funding_rate", "funding_cumulative_8h"
  ],
  "feature_count":     20,
  "feature_config": {
    "rsi_periods": [7, 14, 21],
    "ema_periods": [9, 21, 50, 200],
    "atr_period":  14,
    "bb_period":   20,
    "include_funding": false
  },
  "target_col":        "target_return_4h",
  "target_horizon":    4,
  "thresholds": {
    "entry_long":      0.003,
    "entry_short":    -0.003,
    "min_confidence":  0.60
  },
  "metrics": {
    "ic_mean":         0.078,
    "ic_std":          0.031,
    "icir":            2.52,
    "dir_accuracy":    0.543,
    "sharpe_signal":   1.12,
    "backtest_sharpe": 1.34,
    "backtest_maxdd":  0.162,
    "backtest_winrate":0.551
  },
  "status":            "production",
  "replaces":          "spot_lgbm_v2_2_1",
  "compatible_modes":  ["paper", "shadow", "live"],
  "notes":             "Added funding rate feature. IC improved from 0.062."
}
```

---

### 10.2 Required vs Optional Fields

| Field | Required? | Used By | If Changed |
|---|---|---|---|
| `feature_names` | Yes | strategy_layer, main.py (validation) | Major version bump required |
| `feature_count` | Yes | main.py (fast validation) | Major version bump required |
| `feature_config` | Yes | RuntimeFeatureEngine | Major version bump required |
| `thresholds` | Yes | spot_strategy / futures_strategy | Patch version bump |
| `target_col` | Yes | research (documentation) | Minor version bump |
| `metrics` | Yes | model_registry, monitoring | Does not affect version |
| `status` | Yes | main.py (status validation) | Does not bump version |
| `notes` | No | Documentation only | — |

---

### 10.3 Versioning Rules

| Change | Version Bump | Example |
|---|---|---|
| Add / remove / reorder feature_names | MAJOR (2.x.x → 3.0.0) | Adding 'obv_norm' to feature list |
| Change feature_config (add new feature) | MAJOR | include_funding: false → true |
| Change target_col or target_horizon | MINOR (2.3.x → 2.4.0) | target_return_4h → target_return_8h |
| Retrain with newer data, same features | MINOR (2.3.x → 2.4.0) | Update train_period only |
| Change thresholds (entry_long/short) | PATCH (2.3.0 → 2.3.1) | entry_long: 0.003 → 0.004 |
| Update metrics after re-evaluation | No version bump | Update metrics field only |

---

## 11. Model Loader — Validation at Startup

Every time the agent starts, the model must be validated to ensure `feature_names` matches the runtime feature pipeline. Mismatches must be detected before the agent begins trading.

### 11.1 ModelLoader Implementation

```python
import joblib, json
from pathlib import Path

class ModelCompatibilityError(Exception): pass
class ModelStatusError(Exception): pass

class ModelLoader:
    VALID_STATUSES = ('production', 'validated')

    def load(
        self,
        model_path: str | Path,
        meta_path:  str | Path,
        config:     AgentConfig,
    ) -> 'TrainedModel':
        """
        Load model + metadata, validate compatibility.
        Raise if incompatible — do not let a broken model run.
        """
        model    = joblib.load(model_path)
        metadata = json.loads(Path(meta_path).read_text())

        self._validate_status(metadata)
        self._validate_features(metadata, config)
        self._validate_model_api(model)
        self._validate_mode_compat(metadata, config)

        return TrainedModel(model=model, metadata=metadata)

    def _validate_status(self, metadata: dict) -> None:
        status = metadata.get('status', '')
        if status not in self.VALID_STATUSES:
            raise ModelStatusError(
                f'Model status "{status}" is not allowed for deployment. '
                f'Valid: {self.VALID_STATUSES}'
            )

    def _validate_features(self, metadata: dict, config: AgentConfig) -> None:
        expected = metadata.get('feature_names', [])
        runtime  = RuntimeFeatureEngine(metadata).feature_names

        if expected != runtime:
            # Find specific differences
            missing = set(expected) - set(runtime)
            extra   = set(runtime)  - set(expected)
            raise ModelCompatibilityError(
                f'Feature mismatch!\n'
                f'  Missing in runtime: {missing}\n'
                f'  Extra in runtime:   {extra}\n'
                f'  Update FeatureConfig or retrain the model.'
            )

    def _validate_model_api(self, model) -> None:
        if not hasattr(model, 'predict'):
            raise ModelCompatibilityError('Model does not have a predict() method')

    def _validate_mode_compat(
        self, metadata: dict, config: AgentConfig
    ) -> None:
        compat = metadata.get('compatible_modes', ['paper', 'shadow', 'live'])
        if config.mode not in compat:
            raise ModelCompatibilityError(
                f'Model is not compatible with mode {config.mode}. '
                f'Compatible: {compat}'
            )
```

---

### 11.2 TrainedModel Dataclass

```python
@dataclass
class TrainedModel:
    model:    any    # LightGBM / XGBoost / sklearn estimator
    metadata: dict

    # Shortcut properties (derived from metadata)
    @property
    def feature_names(self) -> list[str]:
        return self.metadata['feature_names']

    @property
    def version(self) -> str:
        return self.metadata['version']

    @property
    def thresholds(self) -> dict:
        return self.metadata.get('thresholds', {})

    @property
    def entry_threshold_long(self) -> float:
        return self.thresholds.get('entry_long', 0.003)

    @property
    def entry_threshold_short(self) -> float:
        return self.thresholds.get('entry_short', -0.003)

    def predict(self, X) -> float:
        """
        Predict wrapper that ensures correct input shape.
        X can be: pd.Series, np.ndarray, or list.
        """
        import numpy as np
        if hasattr(X, 'values'): X = X.values
        X = np.array(X).reshape(1, -1)
        return float(self.model.predict(X)[0])
```

---

## 12. Model Deploy — automation/deploy.py

The process of moving a new model from `research/` to `runtime/agent/models/`. Must be done atomically — there must never be a state where the old model has been deleted but the new one is not yet available.

### 12.1 Deploy Flow

```python
# automation/deploy.py — called after model passes all checklists

async def deploy_model(
    new_model_path: Path,
    new_meta_path:  Path,
    market_type:    str,    # 'spot' | 'futures'
    config:         AgentConfig,
) -> DeployReport:
    """
    Atomic deploy: backup first, then replace.
    Sequence:
    1. Validate new model via ModelLoader (fail → abort)
    2. Archive old model to models/archive/ with timestamp
    3. Rename old metadata.json to archive/{id}_metadata.json
    4. Copy new model to models/{market_type}_model.pkl
    5. Update metadata.json with deployed_at = now()
    6. Validate again with ModelLoader (fail → rollback)
    7. Send successful deploy notification
    """

def rollback_model(
    market_type: str,
    version:     str = None,   # None = previous version
) -> bool:
    """
    Rollback to the previous version from archive/.
    No agent restart required — the agent will reload the model
    on the next tick (if hot-reload is enabled).
    """
```

---

### 12.2 Hot-Reload Model (Optional)

```python
# If HOT_RELOAD_MODEL = True in config:
# Scheduler checks every 5 minutes if metadata.json has changed.
# If changed (deployed_at is newer) → reload model without restart.

class ModelHotReloader:

    async def check_and_reload(
        self,
        strategy:   BaseStrategy,
        model_path: Path,
        meta_path:  Path,
    ) -> bool:
        """
        Returns True if model was reloaded.
        Reload is performed when there are no open trades for that symbol.
        """
        current_meta_ts = self._last_meta_ts.get(model_path)
        new_meta_ts     = meta_path.stat().st_mtime

        if current_meta_ts == new_meta_ts:
            return False    # no changes

        # Wait until there are no open positions
        if self._store.count_open(strategy.symbol) > 0:
            return False    # defer reload

        new_model         = self._loader.load(model_path, meta_path, self._config)
        strategy.model    = new_model
        self._last_meta_ts[model_path] = new_meta_ts

        log.info('Model hot-reloaded', version=new_model.version)
        return True
```

---

## 13. Dependency Map & Checklist

### 13.1 Dependency Map

| File | Imports From | Used By |
|---|---|---|
| `notifier.py` | telegram.py, discord.py | monitoring, trade_layer, learning_layer, main_loop |
| `telegram.py` | aiohttp, utils/helpers (truncate) | notifier.py |
| `discord.py` | aiohttp | notifier.py |
| `logger.py` | Python logging stdlib | Every file (get_logger(\_\_name\_\_)) |
| `structured_logger.py` | logger.py | When LOG_FORMAT=json |
| `time_utils.py` | datetime stdlib | All layers that need time |
| `helpers.py` | math, re, time_utils | risk_layer (round_qty), trade_layer (generate_order_id), all layers |
| `metadata.json` | — (static file) | main.py (validation), strategy_layer, ModelLoader |
| `*.pkl` | joblib | ModelLoader → TrainedModel → strategy_layer |

---

### 13.2 Implementation Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | All notifications wrapped in try/except — do not crash main loop | Test: mock Telegram error → main loop continues | ☐ |
| 2 | `send_emergency()` raises if all channels fail | Test: mock all channel errors → Exception raised | ☐ |
| 3 | Messages > 4096 characters are auto-split | Test: send 5000-character string → 2 separate messages | ☐ |
| 4 | `utcnow()` from time_utils, not `datetime.utcnow()` directly | `grep -rn 'datetime.utcnow()' --include='*.py'` → empty | ☐ |
| 5 | `round_qty()` uses floor, `round_price()` uses round | Test: round_qty(0.1235, 0.001) = 0.123, not 0.124 | ☐ |
| 6 | `retry_async()` stops after max_retry, raises last exception | Test: inject continuous error → raise after N retries | ☐ |
| 7 | `mask_key()` never exposes full API key in logs | `grep -rn 'api_key' logs/` after run → all masked | ☐ |
| 8 | `ModelLoader` fails loudly if feature_names do not match | Test: change one feature_name → ModelCompatibilityError | ☐ |
| 9 | Deploy is atomic: backup first, then replace | Test: simulate crash mid-deploy → old model still present | ☐ |
| 10 | metadata.json always has feature_names, version, status fields | JSON schema validation in test suite | ☐ |
| 11 | LOG_FORMAT=json produces valid JSON on every line | `jq '.' logs/agent.log` → no parse errors | ☐ |
| 12 | structured_logger field names are consistent with Prometheus metric names | Create mapping table in tests/ | ☐ |

---

> **This document closes the crypto_ai_agent documentation series.**
> Any changes to message formats, the helpers API, or the metadata.json schema
> **MUST** be updated here before merging to main.
>
> *References: agent_core_docs.md · research_layer_docs.md · learning_llm_docs.md*
