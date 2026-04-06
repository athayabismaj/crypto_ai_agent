"""
report.py — Generate Laporan Backtest
Output JSON + human-readable summary.
Termasuk perbandingan In-Sample vs Out-of-Sample.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone

from research.backtest.metrics import BacktestMetrics

log = logging.getLogger(__name__)


def generate_report(
    metrics: BacktestMetrics,
    model_id: str = "unknown",
    symbol: str = "unknown",
    timeframe: str = "unknown",
    period: str = "unknown",
    output_dir: str = "research/results",
) -> dict:
    """
    Generate laporan JSON dari metrics.
    Return dict report.
    """
    os.makedirs(output_dir, exist_ok=True)

    report = {
        "report_id": f"{model_id}_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": model_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "metrics": asdict(metrics),
        "deploy_ready": metrics.deploy_ready,
        "deploy_failures": metrics.deploy_failures,
    }

    # Save JSON
    filename = f"{report['report_id']}.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w") as f:
        json.dump(report, f, indent=2)

    log.info(f"Report saved: {path}")
    return report


def compare_is_oos(
    is_metrics: BacktestMetrics,
    oos_metrics: BacktestMetrics,
    max_degradation_pct: float = 40.0,
) -> dict:
    """
    Bandingkan In-Sample vs Out-of-Sample.
    Degradasi > max_degradation_pct = overfitting.
    """
    def _degrade(is_val: float, oos_val: float) -> float:
        if is_val == 0:
            return 0.0
        return ((is_val - oos_val) / abs(is_val)) * 100

    comparison = {
        "sharpe": {
            "in_sample": is_metrics.sharpe_ratio,
            "out_of_sample": oos_metrics.sharpe_ratio,
            "degradation_pct": _degrade(is_metrics.sharpe_ratio, oos_metrics.sharpe_ratio),
        },
        "win_rate": {
            "in_sample": is_metrics.win_rate,
            "out_of_sample": oos_metrics.win_rate,
            "degradation_pct": _degrade(is_metrics.win_rate, oos_metrics.win_rate),
        },
        "profit_factor": {
            "in_sample": is_metrics.profit_factor,
            "out_of_sample": oos_metrics.profit_factor,
            "degradation_pct": _degrade(is_metrics.profit_factor, oos_metrics.profit_factor),
        },
        "max_drawdown": {
            "in_sample": is_metrics.max_drawdown_pct,
            "out_of_sample": oos_metrics.max_drawdown_pct,
            "worsened": oos_metrics.max_drawdown_pct > is_metrics.max_drawdown_pct,
        },
    }

    # Overall verdict
    sharpe_degrade = comparison["sharpe"]["degradation_pct"]
    is_overfit = sharpe_degrade > max_degradation_pct

    comparison["verdict"] = {
        "is_overfit": is_overfit,
        "sharpe_degradation_pct": sharpe_degrade,
        "threshold": max_degradation_pct,
        "recommendation": (
            "REJECT — Model overfitting. Kembali ke feature engineering."
            if is_overfit
            else "ACCEPT — Degradasi dalam toleransi. Lanjut deploy."
        ),
    }

    if is_overfit:
        log.warning(
            f"⚠️ OVERFITTING: Sharpe degradasi {sharpe_degrade:.1f}% "
            f"(>{max_degradation_pct}%)"
        )
    else:
        log.info(
            f"✅ IS vs OOS comparison OK: Sharpe degradasi {sharpe_degrade:.1f}%"
        )

    return comparison


def format_summary(metrics: BacktestMetrics, title: str = "Backtest") -> str:
    """Format ringkasan human-readable untuk console/notifikasi."""
    status = "✅ DEPLOY READY" if metrics.deploy_ready else "❌ NOT READY"
    lines = [
        f"╔══════════════════════════════════════╗",
        f"║ {title:^36} ║",
        f"╠══════════════════════════════════════╣",
        f"║ ROI          : {metrics.total_roi_pct:>+8.2f}%            ║",
        f"║ CAGR         : {metrics.cagr_pct:>+8.2f}%            ║",
        f"║ Max Drawdown : {metrics.max_drawdown_pct:>8.2f}%            ║",
        f"║ Sharpe       : {metrics.sharpe_ratio:>8.2f}             ║",
        f"║ Sortino      : {metrics.sortino_ratio:>8.2f}             ║",
        f"║ Calmar       : {metrics.calmar_ratio:>8.2f}             ║",
        f"║ Win Rate     : {metrics.win_rate:>8.1f}%            ║",
        f"║ Profit Factor: {metrics.profit_factor:>8.2f}             ║",
        f"║ Avg R:R      : {metrics.avg_rr:>8.2f}             ║",
        f"║ Total Trades : {metrics.total_trades:>8d}             ║",
        f"║ Max Consec L : {metrics.max_consecutive_loss:>8d}             ║",
        f"╠══════════════════════════════════════╣",
        f"║ {status:^36} ║",
        f"╚══════════════════════════════════════╝",
    ]
    return "\n".join(lines)
