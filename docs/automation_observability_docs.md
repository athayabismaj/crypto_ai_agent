# crypto_ai_agent — Automation · Observability · Audit · Docs

*retrain · deploy · scheduler · health_restart*
*prometheus_metrics · grafana_dashboard · alert_rules*
*trade_history · tax_report · pnl_tracker*
*architecture · agent_flow · risk_management · deployment_guide · rollback_guide*

**Version 1.0** | Final Document — Closing the crypto_ai_agent Series

---

## 1. Overview — Three Operational Support Layers

These three components ensure the system can operate continuously: **Automation** runs routine tasks without human intervention, **Observability** provides full visibility into system health, and **Docs** serves as the single source of truth for all operations.

| Component | Purpose | Who Needs It |
|---|---|---|
| **automation/** | Run routine tasks: retrain, deploy, health restart | DevOps, the system itself |
| **observability/** | Monitor system performance & health in real-time | Quant, DevOps, operator |
| **audit/** | Store permanent history for compliance & analysis | Quant, accountant, audit |
| **docs/** | Operational documentation — deploy, rollback, emergency | All team members |

---

## PART A — automation/

---

## 2. retrain.py — Auto-Retrain Model

Called automatically when `drift_detection.py` detects significant drift, or on a weekly schedule. Coordinates the entire research pipeline to produce a new model.

### 2.1 Trigger Conditions

| Trigger | Condition | Action | Priority |
|---|---|---|---|
| **Critical drift** | DriftReport.severity = critical | Retrain immediately, suspend trading until complete | 1 (highest) |
| **Major drift** | DriftReport.severity = major | Schedule retrain for tonight | 2 |
| **Weekly schedule** | Every Sunday 02:00 UTC | Retrain with latest data | 3 |
| **Manual trigger** | Via gateway endpoint or CLI | Retrain immediately | Manual |

---

### 2.2 Public Interface

```python
class RetrainPipeline:

    async def run(
        self,
        strategy_id:     str,
        force:           bool = False,    # True = skip drift check
        suspend_trading: bool = False,    # True = halt trading during retrain
    ) -> 'RetrainReport':
        """
        Retrain pipeline sequence:
        1. Fetch latest data (fetch_data.py)
        2. Clean & feature engineering
        3. Leakage check — fail → abort
        4. Train model
        5. Walk-forward validation
        6. Evaluate — fail threshold → abort, do not deploy
        7. Backtest OOS — fail threshold → abort
        8. Deploy via deploy.py
        9. Send result notification
        """

    def get_last_report(self, strategy_id: str) -> 'RetrainReport | None':
        ...

@dataclass
class RetrainReport:
    strategy_id:  str
    trigger:      str        # 'drift' | 'scheduled' | 'manual'
    started_at:   datetime
    finished_at:  datetime
    success:      bool
    deployed:     bool
    new_version:  str        # e.g. '2.4.0'
    old_version:  str        # e.g. '2.3.1'
    metrics_new:  dict       # IC, Sharpe, win rate of new model
    metrics_old:  dict       # IC, Sharpe, win rate of old model
    abort_reason: str        # filled if success=False
    duration_s:   float
```

---

### 2.3 Safeguard — Do Not Deploy If Worse

```python
def _should_deploy(
    self,
    new_metrics: dict,
    old_metrics: dict,
) -> tuple[bool, str]:
    """
    New model is only deployed if it is BETTER than the old model.
    At minimum, maintain performance within tolerance.

    Checks:
    1. New Sharpe >= Old Sharpe × 0.90   (max 10% drop)
    2. New IC     >= Old IC × 0.85       (max 15% drop)
    3. Backtest OOS still passes all minimum thresholds
    """
    if new_metrics['sharpe'] < old_metrics['sharpe'] * 0.90:
        return False, f'Sharpe dropped too much: {new_metrics["sharpe"]:.2f} vs {old_metrics["sharpe"]:.2f}'

    if new_metrics['ic_mean'] < old_metrics['ic_mean'] * 0.85:
        return False, f'IC dropped too much: {new_metrics["ic_mean"]:.3f} vs {old_metrics["ic_mean"]:.3f}'

    return True, ''
```

---

## 3. deploy.py — Deploy Artifacts to Runtime

Moves models and research parameters to runtime atomically. Can be called automatically by `retrain.py` or manually by an operator.

### 3.1 Public Interface

```python
class DeployManager:

    async def deploy_model(
        self,
        new_model_path: Path,
        new_meta_path:  Path,
        market_type:    str,    # 'spot' | 'futures'
        notify:         bool = True,
    ) -> 'DeployReport':
        """
        Atomic deploy — see models/deploy flow in notification_utils_models_docs.md
        """

    def deploy_params(
        self,
        strategy_id:     str,
        new_params_path: Path,
    ) -> bool:
        """
        Deploy best_params.json from optimization to runtime config.
        Save backup of old params before replacing.
        """

    def rollback_model(
        self,
        market_type: str,
        version:     str = None,    # None = previous version
    ) -> bool:
        """Roll back to a previous model version from archive/."""

    def list_available_versions(self, market_type: str) -> list[str]:
        """List all versions available in archive/ for rollback."""

@dataclass
class DeployReport:
    market_type:   str
    old_version:   str
    new_version:   str
    success:       bool
    deployed_at:   datetime
    rollback_path: str    # backup path in case rollback is needed
    error:         str    # filled if success=False
```

---

### 3.2 Automated Deploy Checklist

| Step | Action | On Failure |
|---|---|---|
| 1 | Validate new model via ModelLoader (feature_names, status, mode compat) | Abort deploy, send error notification |
| 2 | Backup old model to `archive/{model_id}_{timestamp}.pkl` | Abort if backup fails |
| 3 | Backup old metadata to `archive/{model_id}_{timestamp}_metadata.json` | Abort if backup fails |
| 4 | Copy new model to `runtime/agent/models/{type}_model.pkl` | Rollback to backup, abort |
| 5 | Update `metadata.json` with `deployed_at = utcnow()` | Rollback to backup, abort |
| 6 | Re-validate with ModelLoader (smoke test) | Auto rollback + CRITICAL notification |
| 7 | Send successful deploy notification to Telegram | Log error, continue (non-fatal) |

---

## 4. scheduler.py & health_restart.py — Automation Tasks

### 4.1 automation/scheduler.py — Cron Jobs

Not the same scheduler as `core/scheduler.py` inside the agent. This file is an external cron runner that can run even when the agent is down.

```python
# automation/scheduler.py
# Runs as a separate process (cron or systemd timer)

SCHEDULE = [
    # Format: (cron_expression, task_function, description)
    ('0 2 * * 0',  run_weekly_retrain,  'Weekly retrain — Sunday 02:00 UTC'),
    ('0 1 * * *',  rotate_logs,         'Daily log rotation — 01:00 UTC'),
    ('0 3 * * 0',  vacuum_databases,    'VACUUM SQLite — Sunday 03:00 UTC'),
    ('0 4 * * *',  backup_databases,    'Daily DB backup — 04:00 UTC'),
    ('0 6 * * 1',  run_full_backtest,   'Weekly backtest — Monday 06:00 UTC'),
]

async def run_weekly_retrain():
    """
    Run retrain if model has not been updated in > 7 days
    and no retrain is currently running.
    """

async def rotate_logs():
    """
    Compress logs > 7 days old to .gz.
    Delete logs > 30 days old (except errors.log = 90 days).
    """

async def vacuum_databases():
    """
    VACUUM all SQLite databases to reclaim disk space.
    Run when agent is idle (no active trades).
    """

async def backup_databases():
    """
    Copy all .db files to backup/ with timestamp.
    Compress with gzip.
    Delete backups > 7 days old.
    """
```

---

### 4.2 health_restart.py — Auto Restart

Monitors the agent heartbeat from outside the process. If the agent dies or becomes unresponsive, automatically restarts it.

```python
# automation/health_restart.py
# Runs as a Docker sidecar or separate systemd service

HEARTBEAT_DB     = 'runtime/agent/memory/heartbeat.db'
CHECK_INTERVAL_S = 60      # check every 60 seconds
DEAD_THRESHOLD_S = 180     # agent considered dead after 3 minutes
MAX_RESTART      = 3       # max restarts within 1 hour
COOLDOWN_S       = 300     # wait 5 minutes between restarts

async def main():
    restart_count = 0
    last_restart  = None

    while True:
        await asyncio.sleep(CHECK_INTERVAL_S)
        status = read_heartbeat(HEARTBEAT_DB)

        if status == HeartbeatStatus.DEAD:
            # Check if still in cooldown
            if last_restart and (utcnow() - last_restart).seconds < COOLDOWN_S:
                continue

            if restart_count >= MAX_RESTART:
                await send_alert_critical('Agent dead, max restarts reached. Manual intervention required.')
                break

            log.critical('Agent DEAD — restarting', count=restart_count + 1)
            await send_alert('Agent unresponsive, restarting...')
            await restart_agent()

            restart_count += 1
            last_restart   = utcnow()

        elif status == HeartbeatStatus.STALE:
            await send_alert_warning('Agent pulse is slow — monitoring')

async def restart_agent():
    """Restart via Docker or systemd."""
    import subprocess
    result = subprocess.run(
        ['docker', 'restart', 'crypto_ai_agent'],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        log.error('Docker restart failed', stderr=result.stderr)
    await asyncio.sleep(30)    # wait for agent startup
```

---

## PART B — observability/

---

## 5. prometheus_metrics.py — Metrics Endpoint

Exposes all important system metrics in Prometheus format. Grafana reads from this endpoint to build real-time dashboards.

### 5.1 All Exposed Metrics

| Metric Name | Type | Label | Description |
|---|---|---|---|
| `agent_alive` | Gauge | mode | 1 = alive, 0 = dead. Alert if 0 > 90 seconds |
| `equity_usd` | Gauge | mode | Current equity in USDT |
| `daily_pnl_usd` | Gauge | mode, strategy | Today's PnL per strategy |
| `open_positions_total` | Gauge | symbol, side | Number of open positions per symbol |
| `trade_opened_total` | Counter | symbol, strategy | Total trades opened (cumulative) |
| `trade_closed_total` | Counter | symbol, exit_reason | Total trades closed per reason |
| `win_rate_30d` | Gauge | strategy | 30-day win rate per strategy |
| `order_latency_ms` | Histogram | exchange, endpoint | Order latency to exchange (P50/P95/P99) |
| `circuit_breaker_state` | Gauge | mode | 0=normal, 1=warned, 2=halted |
| `drawdown_pct` | Gauge | mode | Drawdown from peak in percent |
| `ws_last_message_age_s` | Gauge | symbol, stream | Seconds since last WebSocket message |
| `api_weight_used` | Gauge | exchange | API weight used (out of 1200) |
| `db_query_duration_ms` | Histogram | db, operation | Database query latency |
| `llm_cost_usd_total` | Counter | model | Total LLM cost incurred |
| `signal_generated_total` | Counter | symbol, strategy | Total signals generated |
| `signal_blocked_total` | Counter | symbol, reason | Total signals blocked per reason |

---

### 5.2 Public Interface

```python
from prometheus_client import (Counter, Gauge, Histogram,
                               start_http_server, REGISTRY)

class AgentMetrics:
    """
    Singleton — initialized once in main.py.
    All layers access via metrics.gauge(...), metrics.counter(...), etc.
    """

    _instance: 'AgentMetrics' = None

    @classmethod
    def get(cls) -> 'AgentMetrics':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_server(self, port: int = 8090) -> None:
        """Start HTTP server on port 8090 for Prometheus scraping."""
        start_http_server(port, registry=REGISTRY)
        log.info('Metrics server started', port=port)

    def gauge(self, name: str) -> Gauge:
        """Return or create a Gauge metric."""

    def counter(self, name: str) -> Counter:
        """Return or create a Counter metric."""

    def histogram(self, name: str) -> Histogram:
        """Return or create a Histogram metric."""

    def update_all(self, portfolio_state: dict, system_state: dict) -> None:
        """
        Update all gauges at once.
        Called by scheduler every 30 seconds.
        """

# Usage in other layers:
# from observability.prometheus_metrics import AgentMetrics
# metrics = AgentMetrics.get()
# metrics.gauge('equity_usd').set(10234.50)
# metrics.counter('trade_opened_total').labels(symbol='BTCUSDT', strategy='spot_v1').inc()
# metrics.histogram('order_latency_ms').labels(exchange='binance').observe(123.4)
```

---

## 6. grafana_dashboard.json — Visualization Dashboard

A Grafana JSON file that can be imported directly. Contains all panels for real-time trading agent monitoring.

### 6.1 Required Panels

| Panel | Metric Source | Visualization | Alert Threshold |
|---|---|---|---|
| Agent Status | `agent_alive` | Stat (green/red) | < 1 for 90s |
| Equity Curve | `equity_usd` | Time series | < 95% initial |
| Daily PnL | `daily_pnl_usd` | Bar chart per day | < -5% equity |
| Drawdown | `drawdown_pct` | Time series + threshold | Red line at 10% |
| Open Positions | `open_positions_total` | Table | > max_positions |
| Win Rate 30d | `win_rate_30d` | Gauge (0–100%) | < 40% |
| Order Latency P95 | `order_latency_ms` | Time series | > 2000ms |
| Circuit Breaker State | `circuit_breaker_state` | Stat (color per state) | = 2 (HALTED) |
| API Weight Usage | `api_weight_used` | Gauge (0–1200) | > 900 |
| Signal vs Blocked | signal_generated / blocked | Stacked bar | — |
| LLM Cost Daily | `llm_cost_usd_total` | Stat | > daily budget |
| WS Staleness | `ws_last_message_age_s` | Stat per stream | > 30s |

---

### 6.2 How to Import the Dashboard

```bash
# 1. Open Grafana: http://localhost:3000
# 2. Menu: Dashboards → Import
# 3. Upload observability/grafana_dashboard.json
# 4. Select Prometheus data source
# 5. Click Import

# Prometheus scrape setup in prometheus.yml:
scrape_configs:
  - job_name: crypto_ai_agent
    static_configs:
      - targets: ['localhost:8090']
    scrape_interval: 15s

# Docker Compose snippet for monitoring stack:
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml
    ports: ['9090:9090']

  grafana:
    image: grafana/grafana:latest
    ports: ['3000:3000']
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=your_password
```

---

## 7. alert_rules.yml — Automated Alert Rules

Prometheus alerting rules that trigger alerts to Alertmanager (which can forward to Telegram, Discord, or email). This is the last alert layer outside the agent system itself.

### 7.1 Format & All Rules

```yaml
# observability/alert_rules.yml
groups:
  - name: crypto_ai_agent_critical
    interval: 30s
    rules:

      # Agent dead
      - alert: AgentDead
        expr: agent_alive == 0
        for: 2m
        labels: { severity: critical }
        annotations:
          summary: 'Agent unresponsive for 2 minutes'
          action:  'Check health_restart.py, check Docker logs'

      # Circuit breaker active
      - alert: CircuitBreakerHalted
        expr: circuit_breaker_state == 2
        for: 0m
        labels: { severity: critical }
        annotations:
          summary: 'Circuit breaker HALTED — trading stopped'
          action:  'Check daily PnL and drawdown. Review logs.'

      # Critical drawdown
      - alert: DrawdownCritical
        expr: drawdown_pct > 8
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: 'Drawdown {{ $value }}% approaching 10% limit'

  - name: crypto_ai_agent_warning
    interval: 60s
    rules:

      # WebSocket stale
      - alert: WebSocketStale
        expr: ws_last_message_age_s > 60
        for: 2m
        labels: { severity: warning }
        annotations:
          summary: 'WebSocket has not received data for {{ $value }}s'

      # High API weight
      - alert: APIWeightHigh
        expr: api_weight_used > 900
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: 'API weight {{ $value }}/1200 — approaching rate limit'

      # Low win rate
      - alert: WinRateLow
        expr: win_rate_30d < 0.40
        for: 0m
        labels: { severity: warning }
        annotations:
          summary: '30d win rate {{ $value }}% is below 40%'

      # Negative daily PnL
      - alert: DailyPnLNegative
        expr: daily_pnl_usd < -(equity_usd * 0.03)
        for: 0m
        labels: { severity: warning }
        annotations:
          summary: 'Daily PnL is -{{ $value }} USD (>3% equity)'

      # LLM budget nearly exhausted
      - alert: LLMBudgetLow
        expr: llm_cost_usd_total > (llm_daily_budget * 0.80)
        for: 0m
        labels: { severity: info }
        annotations:
          summary: 'LLM budget is 80% consumed today'
```

---

## PART C — audit/

---

## 8. trade_history.db — Permanent Audit Database

An immutable database that stores all trades permanently for compliance, tax reporting, and long-term historical analysis. Different from `experience.db` which can be archived.

### 8.1 Schema

```sql
-- Database: audit/trade_history.db
-- NO UPDATE or DELETE allowed
-- INSERT only

CREATE TABLE all_trades (
    -- Identity
    trade_id          TEXT PRIMARY KEY,
    client_order_id   TEXT UNIQUE NOT NULL,
    exchange_order_id TEXT,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,

    -- Execution
    entry_price       REAL NOT NULL,
    exit_price        REAL,
    qty               REAL NOT NULL,
    commission_usd    REAL DEFAULT 0,

    -- PnL
    pnl_usd           REAL,
    pnl_pct           REAL,
    net_pnl_usd       REAL,    -- pnl_usd - commission_usd

    -- Context
    strategy_id       TEXT,
    exit_reason       TEXT,
    regime_at_entry   TEXT,
    mode              TEXT NOT NULL,

    -- Timestamps
    opened_at         TEXT NOT NULL,
    closed_at         TEXT,

    -- Audit metadata
    recorded_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE reconciliation_reports (
    date        TEXT PRIMARY KEY,    -- YYYY-MM-DD
    payload     TEXT NOT NULL,       -- JSON report
    recorded_at TEXT NOT NULL
);

CREATE TABLE daily_summaries (
    date         TEXT NOT NULL,
    strategy_id  TEXT NOT NULL,
    total_trades INTEGER,
    win_rate     REAL,
    pnl_usd      REAL,
    equity_end   REAL,
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (date, strategy_id)
);

-- Indexes for tax report queries
CREATE INDEX idx_closed_at ON all_trades(closed_at);
CREATE INDEX idx_symbol    ON all_trades(symbol, closed_at);
```

---

## 9. tax_report.py & pnl_tracker.py

### 9.1 tax_report.py — Tax Report Generator

Generates a report of all trades within a given period in a format usable for tax calculation. Output format: CSV and a JSON summary.

```python
class TaxReporter:

    def generate(
        self,
        year:       int,
        output_dir: Path = Path('audit/reports/'),
    ) -> 'TaxReport':
        """
        Generate tax report for one year.
        Output:
        1. audit/reports/tax_{year}.csv — detailed trade list
        2. audit/reports/tax_{year}_summary.json — monthly summary
        """

    def generate_period(
        self,
        start: datetime,
        end:   datetime,
    ) -> 'TaxReport':
        """Generate for a custom period."""

@dataclass
class TaxReport:
    period:               str      # '2024' or '2024-Q1', etc.
    total_trades:         int
    winning_trades:       int
    losing_trades:        int
    gross_profit:         float
    gross_loss:           float
    net_pnl:              float
    total_commission:     float
    net_after_commission: float
    by_symbol:            dict     # {symbol: {pnl, trades, commission}}
    by_month:             dict     # {month: {pnl, trades}}
    csv_path:             Path
    json_path:            Path
    generated_at:         datetime

# CSV header format:
# date, symbol, side, entry_price, exit_price, qty, pnl_usd, commission, net_pnl, hold_days
```

---

### 9.2 pnl_tracker.py — Real-time PnL Tracking

```python
class PnLTracker:

    def get_current_pnl(self) -> 'PnLSnapshot':
        """
        Current PnL snapshot:
        - Realized: from closed trades
        - Unrealized: estimate from open positions
        - Total: realized + unrealized
        """

    def get_daily_pnl(self, days: int = 30) -> list[dict]:
        """Return list of daily PnL for the last N days."""

    def get_monthly_summary(self, year: int) -> dict:
        """Return monthly PnL summary for one year."""

@dataclass
class PnLSnapshot:
    timestamp:           datetime
    realized_pnl_usd:    float    # total from closed trades
    unrealized_pnl_usd:  float    # estimate from open positions
    total_pnl_usd:       float
    total_commission:    float
    net_pnl_usd:         float    # total - commission
    roi_pct:             float    # net_pnl / initial_equity × 100
    daily_pnl:           float    # today's PnL only
    weekly_pnl:          float
    monthly_pnl:         float
```

---

## PART D — docs/ — Operational Documentation

Files in the `docs/` folder are operational guides read by humans — not code documentation (that lives in the docx series).

| File | Contents | Primary Reader | Update When |
|---|---|---|---|
| `architecture.md` | Layer architecture diagram & data flow | New developer | When architecture changes |
| `agent_flow.md` | Flowchart: signal → risk → trade → close | Developer, Quant | When flow changes |
| `risk_management.md` | Full explanation of all risk parameters | Quant, operator | When thresholds change |
| `deployment_guide.md` | Step-by-step deploy guide: paper → shadow → live | DevOps, operator | When procedure changes |
| `rollback_guide.md` | Emergency procedures: shutdown, rollback, recovery | Everyone (emergency) | When procedure changes |

---

## 10. deployment_guide.md — Deploy Guide

Content that must exist in this file. Not a complete technical guide — that is in the docx series — but an operational quick-reference readable under pressure.

### 10.1 Document Structure

```markdown
# deployment_guide.md

## Prerequisites
- Python 3.11+
- Docker & Docker Compose
- Binance account with API key (spot + futures)
- Telegram Bot Token + Chat ID
- Server with minimum 2GB RAM, 20GB disk

## Step-by-Step: Paper Mode (Start Here)
1. Clone repository
2. Copy .env.example to .env, fill all fields
3. Set AGENT_MODE=paper
4. docker-compose up -d
5. Verify: docker logs crypto_ai_agent | tail -50
6. Check Telegram — should receive 'Agent started' message
7. Monitor Grafana dashboard

## Transition to Shadow Mode
Requirements:
- Paper mode has been running for 2 weeks
- 2-week Sharpe > 1.0
- No BLOCKED orders that should have executed

Steps:
1. Ensure no open positions
2. Set AGENT_MODE=shadow in .env
3. docker-compose restart

## Transition to Live Mode
Requirements:
- Shadow mode ran for 1 week without critical errors
- Circuit breaker never triggered in shadow
- All integration tests pass
- Checklist in runtime_layer_docs.md Section 16 complete

Steps:
1. Ensure no open positions
2. Backup all databases
3. Set AGENT_MODE=live in .env
4. docker-compose restart
5. Monitor closely for the first 24 hours
```

---

## 11. rollback_guide.md — Emergency Procedures

The most important document to read before going live. Contains procedures for emergency situations.

### 11.1 Emergency Situations & Responses

| Situation | Urgency Level | First Action | Follow-up Action |
|---|---|---|---|
| Circuit breaker HALTED | MEDIUM | Monitor only — auto-resume in 60 minutes | Investigate logs, check daily PnL |
| Agent unresponsive (heartbeat) | HIGH | health_restart.py will auto-restart | Check Docker logs, don't panic |
| Open position without monitoring | CRITICAL | Login to Binance web, close manually | Investigate why agent didn't close |
| Model generating strange signals | HIGH | Manual halt via Telegram command | Rollback model to previous version |
| Equity dropping drastically | CRITICAL | Emergency stop via gateway | Close all positions, investigate |
| DB corrupt | CRITICAL | Stop agent, restore from backup | Run recovery, reconcile |
| API key suspected leaked | VERY CRITICAL | Revoke key on Binance web IMMEDIATELY | Create new key, update .env, restart |

---

### 11.2 Emergency Stop — Quick Methods

```bash
# Method 1: Via Telegram Bot Command
# Send to bot: /halt
# Agent will enter safe_mode.emergency_stop()

# Method 2: Via Gateway API
curl -X POST http://localhost:8080/api/v1/control/halt \
     -H 'Authorization: Bearer YOUR_GATEWAY_KEY' \
     -H 'Content-Type: application/json' \
     -d '{"reason": "manual halt"}'

# Method 3: Docker stop (most abrupt — agent has no time to clean up)
docker stop crypto_ai_agent
# WARNING: This does NOT cancel open orders!
# After docker stop, check Binance web for open orders and cancel manually.

# Method 4: Kill process (ONLY if other methods fail)
kill -SIGTERM $(pgrep -f 'python main.py')
# Agent will catch SIGTERM and run graceful_shutdown()
```

---

### 11.3 Model Rollback — Procedure

```bash
# Roll back to a previous model version

# View available versions:
ls runtime/agent/models/archive/
# Output: spot_model_v2.2.1.pkl  spot_model_v2.2.1_metadata.json  ...

# Method 1: Via CLI script
python automation/deploy.py rollback --market spot --version 2.2.1

# Method 2: Manual (if CLI is unavailable)
cp runtime/agent/models/archive/spot_model_v2.2.1.pkl \
   runtime/agent/models/spot_model.pkl

cp runtime/agent/models/archive/spot_model_v2.2.1_metadata.json \
   runtime/agent/models/metadata.json

# After rollback:
# 1. If HOT_RELOAD_MODEL=True: model auto-reloads within 5 minutes
# 2. Otherwise: docker-compose restart crypto_ai_agent

# Verify rollback succeeded:
python -c "
import json
meta = json.load(open('runtime/agent/models/metadata.json'))
print('Model version:', meta['version'])
print('Status:', meta['status'])
"
```

---

### 11.4 Database Recovery — Procedure

```bash
# If state.db is corrupt or missing

# Step 1: Stop agent
docker stop crypto_ai_agent

# Step 2: Check latest backup
ls -lt runtime/agent/memory/backups/ | head -5

# Step 3: Restore from backup
cp runtime/agent/memory/backups/state_20241115_000000.db \
   runtime/agent/memory/state.db

# Step 4: Restart agent — recovery.py will auto-reconcile
docker start crypto_ai_agent

# Step 5: Monitor recovery log
docker logs -f crypto_ai_agent | grep -i recovery
# Expected: 'Recovery complete, 0 discrepancies'

# IMPORTANT: After restoring from an old backup,
# some trades may be missing. Check Binance web
# for positions that may not exist in the DB.
```

---

## 12. docker-compose.yml — Container Orchestration

Docker Compose configuration to run the full stack: agent, monitoring, database backup, and health restart.

```yaml
# runtime/docker-compose.yml
version: '3.8'

services:

  # ── Main agent ────────────────────────────────────────────
  agent:
    build: .
    container_name: crypto_ai_agent
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./agent:/app/agent
      - ./logs:/app/logs
    ports:
      - '8080:8080'    # gateway API
      - '8090:8090'    # prometheus metrics
    depends_on:
      - prometheus
    healthcheck:
      test: ['CMD', 'python', '-c',
             'from monitoring.heartbeat import check_alive; check_alive()']
      interval: 30s
      timeout: 10s
      retries: 3

  # ── Health restart sidecar ────────────────────────────────
  health_restart:
    build: .
    container_name: crypto_health_restart
    restart: always
    command: python automation/health_restart.py
    env_file: .env
    volumes:
      - ./agent/memory:/app/agent/memory:ro    # read-only heartbeat DB
    depends_on:
      - agent

  # ── Prometheus ────────────────────────────────────────────
  prometheus:
    image: prom/prometheus:latest
    container_name: crypto_prometheus
    restart: unless-stopped
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./observability/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
    ports: ['9090:9090']

  # ── Grafana ───────────────────────────────────────────────
  grafana:
    image: grafana/grafana:latest
    container_name: crypto_grafana
    restart: unless-stopped
    volumes:
      - grafana_data:/var/lib/grafana
    ports: ['3000:3000']
    environment:
      GF_SECURITY_ADMIN_PASSWORD: '${GRAFANA_PASSWORD}'

volumes:
  grafana_data:
```

---

## 13. Final Checklist & Series Summary

### 13.1 Automation, Observability & Audit Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | retrain.py aborts if new model is worse than old model | Test: inject lower metrics → should_deploy=False | ☐ |
| 2 | deploy.py is atomic: backup first, replace, re-validate | Simulate crash after replace → backup still exists | ☐ |
| 3 | health_restart.py does not restart more than MAX_RESTART in 1 hour | Test: mock agent dead 5x → only 3x restarts, then alert | ☐ |
| 4 | All Prometheus metrics exposed on port 8090 | `curl localhost:8090/metrics \| grep agent_alive` → found | ☐ |
| 5 | alert_rules.yml loaded by Prometheus without errors | `promtool check rules alert_rules.yml` → OK | ☐ |
| 6 | Grafana dashboard can be imported without errors | Import JSON to Grafana → all panels open | ☐ |
| 7 | audit/trade_history.db cannot be UPDATE or DELETE | Try UPDATE → SQLite trigger or application error | ☐ |
| 8 | tax_report.py produces valid CSV | Open CSV in Excel → all columns correct, no NaN | ☐ |
| 9 | rollback_guide.md has been read by everyone going live | Team sign-off on document | ☐ |
| 10 | docker-compose.yml can be brought up without errors | `docker-compose up -d` → all services running | ☐ |

---

### 13.2 Full Series Summary — 13 Documents Complete

| No | Document | Layer | File |
|---|---|---|---|
| 1 | Final Project Structure | All layers (overview) | crypto_ai_agent_structure.docx |
| 2 | Research Layer | pipeline, validation, modeling, backtest | research_layer_docs.docx |
| 3 | Runtime Layer | security, gateway, overview deployment | runtime_layer_docs.docx |
| 4 | Agent Core | main, config, modes, scheduler, event bus, memory | agent_core_docs.docx |
| 5 | Data & Intelligence Layer | market, orderbook, websocket, regime, volatility | data_intelligence_docs.docx |
| 6 | Strategy & Portfolio Layer | base/spot/futures strategy, allocator, risk budget | strategy_portfolio_docs.docx |
| 7 | Risk Layer | risk_manager, circuit_breaker, position_size, SL | risk_layer_docs.docx |
| 8 | Trade & Execution Layer | trade, manager, idempotency, recovery, binance | trade_execution_docs.docx |
| 9 | Exit, Monitoring & Sync Layer | exit_manager, trailing, heartbeat, sync | exit_monitoring_sync_docs.docx |
| 10 | Learning & LLM Layer | drift, reflection, adaptation, strategy_killer | learning_llm_docs.docx |
| 11 | Notification, Utils & Models | telegram, discord, helpers, time_utils, metadata | notification_utils_models_docs.docx |
| 12 | Tests Layer | unit, integration, mocks, conftest, coverage | tests_layer_docs.docx |
| 13 | Automation, Observability & Docs | retrain, deploy, prometheus, grafana, rollback | automation_observability_docs.docx |

> **HOW TO USE THIS SERIES:**
> When implementing a layer, upload the relevant document to a new session along with skeleton code.
> The AI can generate an implementation consistent with the entire architecture without needing re-explanation.

---

> **The crypto_ai_agent documentation series is complete.**
>
> **13 documents · all layers covered · ready for implementation.**
>
> Start from Research Layer → implement one layer per session → test → deploy incrementally.
>
> *Any changes to interfaces, config key defaults, or operational procedures MUST be updated in the relevant document.*
