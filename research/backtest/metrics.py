"""
metrics.py — Kalkulasi Metrik Performa Backtest
Semua metrik dihitung secara konsisten dari satu sumber kebenaran.

Deploy threshold:
  Sharpe ≥ 1.0 | Max DD < 20% | Win Rate > 45% | PF > 1.3 | Trades > 30
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)


DEPLOY_THRESHOLDS = {
    "sharpe": 1.0,
    "sortino": 1.5,
    "calmar": 1.0,
    "max_dd_pct": 20.0,
    "win_rate": 45.0,
    "profit_factor": 1.3,
    "avg_rr": 1.2,
    "min_trades": 30,
    "cagr_pct": 20.0,
}


@dataclass
class BacktestMetrics:
    total_roi_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    win_rate: float
    profit_factor: float
    avg_rr: float              # avg win / avg loss
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    max_consecutive_loss: int
    avg_holding_bars: float
    deploy_ready: bool
    deploy_failures: list[str]


def calc_max_drawdown(equity_curve: list[float] | np.ndarray) -> float:
    """Max drawdown dari equity curve dalam persen."""
    eq = np.array(equity_curve)
    if len(eq) < 2:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    return float(np.max(dd))


def calc_sharpe(daily_returns: np.ndarray, risk_free: float = 0.0) -> float:
    """Annualized Sharpe Ratio dari daily returns. RF=0 (karena holding USDT)."""
    if len(daily_returns) < 2 or np.std(daily_returns) == 0:
        return 0.0
    excess = daily_returns - risk_free
    return float(np.mean(excess) / np.std(excess) * np.sqrt(365))


def calc_sortino(daily_returns: np.ndarray, risk_free: float = 0.0) -> float:
    """Annualized Sortino Ratio — hanya downside deviation."""
    if len(daily_returns) < 2:
        return 0.0
    excess = daily_returns - risk_free
    downside = excess[excess < 0]
    if len(downside) == 0 or np.std(downside) == 0:
        return 0.0
    return float(np.mean(excess) / np.std(downside) * np.sqrt(365))


def _equity_to_daily_returns(equity_curve: list[float]) -> np.ndarray:
    """Convert equity curve ke daily returns (persentase)."""
    eq = np.array(equity_curve)
    if len(eq) < 2:
        return np.array([])
    returns = np.diff(eq) / eq[:-1]
    return returns


def calculate_metrics(
    trades: list,  # list[Trade] dari engine.py
    equity_curve: list[float],
    initial_capital: float,
    n_days: int = 0,
) -> BacktestMetrics:
    """
    Hitung semua metrik dari hasil backtest.
    """
    total_trades = len(trades)
    final_equity = equity_curve[-1] if equity_curve else initial_capital

    # ROI
    total_roi = ((final_equity - initial_capital) / initial_capital) * 100

    # CAGR
    if n_days > 0 and final_equity > 0:
        years = n_days / 365
        cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100 if years > 0 else 0
    else:
        cagr = 0.0

    # Max Drawdown
    max_dd = calc_max_drawdown(equity_curve)

    # Daily returns → Sharpe, Sortino
    daily_ret = _equity_to_daily_returns(equity_curve)
    sharpe = calc_sharpe(daily_ret)
    sortino = calc_sortino(daily_ret)

    # Calmar
    calmar = cagr / max_dd if max_dd > 0 else 0.0

    # Win/Loss analysis
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    winning = len(wins)
    losing = len(losses)
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    avg_win_pct = float(np.mean([t.pnl_pct for t in wins])) if wins else 0
    avg_loss_pct = float(np.mean([abs(t.pnl_pct) for t in losses])) if losses else 0
    avg_rr = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0

    # Max consecutive losses
    max_consec = 0
    current_consec = 0
    for t in trades:
        if t.pnl <= 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0

    # Avg holding
    holding_bars = [t.exit_bar - t.entry_bar for t in trades]
    avg_holding = float(np.mean(holding_bars)) if holding_bars else 0

    # Deploy readiness check
    failures: list[str] = []
    if sharpe < DEPLOY_THRESHOLDS["sharpe"]:
        failures.append(f"Sharpe={sharpe:.2f} < {DEPLOY_THRESHOLDS['sharpe']}")
    if max_dd > DEPLOY_THRESHOLDS["max_dd_pct"]:
        failures.append(f"MaxDD={max_dd:.1f}% > {DEPLOY_THRESHOLDS['max_dd_pct']}%")
    if win_rate < DEPLOY_THRESHOLDS["win_rate"]:
        failures.append(f"WinRate={win_rate:.1f}% < {DEPLOY_THRESHOLDS['win_rate']}%")
    if profit_factor < DEPLOY_THRESHOLDS["profit_factor"]:
        failures.append(f"PF={profit_factor:.2f} < {DEPLOY_THRESHOLDS['profit_factor']}")
    if total_trades < DEPLOY_THRESHOLDS["min_trades"]:
        failures.append(f"Trades={total_trades} < {DEPLOY_THRESHOLDS['min_trades']}")

    deploy_ready = len(failures) == 0

    metrics = BacktestMetrics(
        total_roi_pct=total_roi,
        cagr_pct=cagr,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_rr=avg_rr,
        total_trades=total_trades,
        winning_trades=winning,
        losing_trades=losing,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        max_consecutive_loss=max_consec,
        avg_holding_bars=avg_holding,
        deploy_ready=deploy_ready,
        deploy_failures=failures,
    )

    if deploy_ready:
        log.info(f"✅ Backtest LULUS deploy threshold: Sharpe={sharpe:.2f}, MaxDD={max_dd:.1f}%")
    else:
        log.warning(f"❌ Backtest GAGAL deploy: {failures}")

    return metrics
