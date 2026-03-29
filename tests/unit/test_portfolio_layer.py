"""
Test untuk Portfolio Layer
"""

from runtime.agent.portfolio_layer.allocator import PortfolioAllocator
from runtime.agent.portfolio_layer.capital_manager import CapitalManager, PaperEquityTracker
from runtime.agent.portfolio_layer.correlation import CorrelationController
from runtime.agent.portfolio_layer.risk_budget import RiskBudgetManager


class DummyConfig:
    pass


def test_allocator_can_open():
    allocator = PortfolioAllocator()

    # 1. Test ketersediaan equity $10k
    equity = 10000.0
    positions = {}

    # Strat minta $1,500
    ok, reason = allocator.can_open("spot_strat", "BTCUSDT", 1500.0, equity, positions)
    assert ok is True

    # Strat minta $5000 (Limit strategy adalah 40% = $4000)
    ok_limit, reason_limit = allocator.can_open("spot_strat", "BTCUSDT", 5000.0, equity, positions)
    assert ok_limit is False
    assert "membebani max limit" in reason_limit

    # Test Reserve Limit (10% cash)
    # Mocking usage of $8000
    allocator.record_opened("other_strat", "ETHUSDT", 8000.0)
    # Total allocations = 8000. Equity 10000. Reserve 1000. Sisa avaliable = 1000.

    ok_mem, reason_mem = allocator.can_open("spot_strat", "BTCUSDT", 1500.0, equity, positions)
    assert ok_mem is False  # Karena 1500 > 1000 sisa bebas kas


def test_capital_manager_drawdown():
    cap = CapitalManager(initial_equity=10000.0)

    # Menang $500 -> Peak = $10500
    cap.update(realized_equity=10500.0, open_positions={}, current_prices={})
    status_win = cap.get_status()
    assert status_win.current_equity == 10500.0
    assert status_win.peak_equity == 10500.0
    assert status_win.safe_to_trade is True

    # Drop ke $9000 (Loss $1500 dari peak = ~ -14.2%)
    cap.update(realized_equity=9000.0, open_positions={}, current_prices={})
    status_drop = cap.get_status()
    assert status_drop.safe_to_trade is False  # Karena -14.2% > MAX_DRAWDOWN (-10%)


def test_correlation_blocks():
    corr = CorrelationController()
    equity = 10000.0

    class FakePos:
        def __init__(self, qty, px, side):
            self.filled_qty = qty
            self.avg_fill_price = px
            self.side = side

    positions = {
        "BTCUSDT": FakePos(1.0, 2000.0, "BUY"),  # $2000
    }

    # 1. Coba open ETHUSDT senilai $2000 => Total exposure grup (BTC+ETH) = $4000 (40%)
    # Limit grup btc_eth adalah 35% equity.
    ok, reason = corr.check("ETHUSDT", "BUY", 2000.0, positions, equity)
    assert ok is False
    assert "btc_eth" in reason

    # 2. Tapi kalau SHORT ETH, beda side -> diijinkan sebagai hedge natural
    ok_hedge, _ = corr.check("ETHUSDT", "SELL", 2000.0, positions, equity)
    assert ok_hedge is True


def test_paper_equity():
    paper = PaperEquityTracker(1000.0)
    paper.apply_trade(pnl_usd=100.0, commission=1.0)
    assert paper.equity == 1099.0
    assert paper.peak == 1099.0

    paper.apply_trade(pnl_usd=-50.0, commission=1.0)
    assert paper.equity == 1048.0
    assert paper.peak == 1099.0


def test_risk_budget():
    cfg = DummyConfig()
    setattr(cfg, "max_daily_loss_pct", 0.05)  # Max loss sistem = 5%
    setattr(cfg, "strategy_weights", {"MyStrat": 1.0})

    rm = RiskBudgetManager(cfg)
    equity = 10000.0  # Bgt sistem = $500

    ok, _ = rm.can_take_risk("MyStrat", 100.0, equity)
    assert ok is True

    # Kita kunci resiko -$400
    rm.record_risk_taken("MyStrat", 400.0)

    # Coba minta risk $150 -> Sisa cuma $100 ($500 - $400)
    ok_fail, reason = rm.can_take_risk("MyStrat", 150.0, equity)
    assert ok_fail is False
    assert "Butuh=150.00" in reason
