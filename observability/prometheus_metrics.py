"""
prometheus_metrics.py — Metrics Endpoint untuk Grafana
Mengekspos semua metrik penting sistem dalam format Prometheus.
Grafana akan me-scrape endpoint ini.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Gunakan flag untuk deteksi ketersediaan prometheus_client
PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    pass


class AgentMetrics:
    """
    Singleton untuk mengelola Prometheus metrics.
    Jika prometheus_client tidak terinstall, metode akan bertindak sebagai no-op (silent stub)
    untuk menghindari crash di environment yang belum di-setup.
    """
    _instance: 'AgentMetrics' = None

    def __init__(self):
        self._gauges: dict[str, Any] = {}
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self.server_started = False
        
        # Pre-define some common gauges
        if PROMETHEUS_AVAILABLE:
             self._gauges["agent_alive"] = Gauge("agent_alive", "1 = hidup, 0 = mati", ["mode"])
             self._gauges["equity_usd"] = Gauge("equity_usd", "Equity saat ini dalam USDT", ["mode"])
             self._gauges["daily_pnl_usd"] = Gauge("daily_pnl_usd", "PnL hari ini per strategi", ["mode", "strategy"])
             self._gauges["open_positions_total"] = Gauge("open_positions_total", "Jumlah posisi terbuka", ["symbol", "side"])
             self._gauges["win_rate_30d"] = Gauge("win_rate_30d", "Win rate 30 hari terakhir per strategi", ["strategy"])
             self._gauges["circuit_breaker_state"] = Gauge("circuit_breaker_state", "0=normal, 1=warned, 2=halted", ["mode"])
             self._gauges["drawdown_pct"] = Gauge("drawdown_pct", "Drawdown dari peak dalam persen", ["mode"])
             self._gauges["ws_last_message_age_s"] = Gauge("ws_last_message_age_s", "Detik sejak pesan WebSocket terakhir", ["symbol", "stream"])
             self._gauges["api_weight_used"] = Gauge("api_weight_used", "API weight yang digunakan", ["exchange"])
             
             self._counters["trade_opened_total"] = Counter("trade_opened_total", "Total trade dibuka", ["symbol", "strategy"])
             self._counters["trade_closed_total"] = Counter("trade_closed_total", "Total trade ditutup", ["symbol", "exit_reason"])
             self._counters["llm_cost_usd_total"] = Counter("llm_cost_usd_total", "Total biaya LLM", ["model"])
             self._counters["signal_generated_total"] = Counter("signal_generated_total", "Total signal dihasilkan", ["symbol", "strategy"])
             self._counters["signal_blocked_total"] = Counter("signal_blocked_total", "Total signal diblokir", ["symbol", "reason"])
             
             self._histograms["order_latency_ms"] = Histogram("order_latency_ms", "Latency order ke exchange", ["exchange", "endpoint"])
             self._histograms["db_query_duration_ms"] = Histogram("db_query_duration_ms", "Latency query database", ["db", "operation"])

    @classmethod
    def get(cls) -> 'AgentMetrics':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_server(self, port: int = 8090) -> None:
        """Mulai HTTP server di thread terpisah untuk Prometheus scrape."""
        if not PROMETHEUS_AVAILABLE:
            log.warning("prometheus_client tidak terinstall. Metrics server dimatikan.")
            return

        if not self.server_started:
            try:
                start_http_server(port, registry=REGISTRY)
                self.server_started = True
                log.info(f"Prometheus metrics server started at port {port}")
            except Exception as e:
                log.error(f"Gagal memulai metrics server di port {port}: {e}")

    # ── Mocks/Wrappers untuk API yang aman ──
    
    class _MockMetric:
        def inc(self, amount=1): pass
        def set(self, val): pass
        def observe(self, val): pass
        def labels(self, **kwargs): return self

    def _get_metric(self, collection_dict, name, type_klass, create_fn, default_desc=""):
        if not PROMETHEUS_AVAILABLE:
             return self._MockMetric()
             
        if name not in collection_dict:
             try:
                 collection_dict[name] = create_fn()
             except Exception as e:
                 log.error(f"Gagal membuat metrik {name}: {e}")
                 return self._MockMetric()
                 
        return collection_dict[name]

    def gauge(self, name: str, desc: str = "") -> Any:
        def create(): return Gauge(name, desc or name)
        return self._get_metric(self._gauges, name, None, create)

    def counter(self, name: str, desc: str = "") -> Any:
        def create(): return Counter(name, desc or name)
        return self._get_metric(self._counters, name, None, create)
        
    def histogram(self, name: str, desc: str = "") -> Any:
        def create(): return Histogram(name, desc or name)
        return self._get_metric(self._histograms, name, None, create)

    def update_all(self, portfolio_state: dict, system_state: dict, mode: str = "live") -> None:
        """
        Daftarkan scheduler (setiap X detik) untuk push semua state sekaligus.
        """
        if not PROMETHEUS_AVAILABLE:
            return
            
        try:
            # Portfolio
            if "total_equity" in portfolio_state:
                self._gauges["equity_usd"].labels(mode=mode).set(portfolio_state["total_equity"])
            if "daily_pnl" in portfolio_state:
                self._gauges["daily_pnl_usd"].labels(mode=mode, strategy="all").set(portfolio_state["daily_pnl"])
            if "open_positions_count" in portfolio_state:
                # Disimplifikasi untuk summary, detailnya harus dikirim per symbol oleh komponen trading
                pass
            if "drawdown_pct" in portfolio_state:
                self._gauges["drawdown_pct"].labels(mode=mode).set(portfolio_state["drawdown_pct"])
                
            # System
            if "circuit_breaker" in system_state:
                state_map = {"NORMAL": 0, "WARNED": 1, "HALTED": 2}
                val = state_map.get(system_state["circuit_breaker"], 0)
                self._gauges["circuit_breaker_state"].labels(mode=mode).set(val)
                
            self._gauges["agent_alive"].labels(mode=mode).set(1)

        except Exception as e:
             log.error(f"Gagal update system metrics: {e}")

# Inisialisasi default / module-level instance exporter
metrics = AgentMetrics.get()
