import pytest

from runtime.agent.utils.helpers import (
    annualize_return,
    calc_pnl,
    calc_position_value,
    calc_risk_reward,
    clamp,
    generate_order_id,
    is_valid_side,
    is_valid_symbol,
    is_valid_timeframe,
    mask_key,
    pct_change,
    round_price,
    round_qty,
    safe_div,
    truncate,
    validate_config_range,
    validate_order_id,
)


def test_round_qty_uses_floor():
    assert round_qty(0.1235, 0.001) == 0.123
    assert round_qty(0.009, 0.01) == 0.00
    assert round_qty(1.99, 1.0) == 1.0
    with pytest.raises(ValueError):
        round_qty(1.0, 0.0)


def test_round_price():
    assert round_price(0.1235, 0.001) == 0.124
    with pytest.raises(ValueError):
        round_price(1.0, -1.0)


def test_calc_pnl():
    # buy win
    usd, pct = calc_pnl("BUY", 100.0, 110.0, 2.0)
    assert usd == 20.0
    assert pct == 10.0

    # buy loss
    usd, pct = calc_pnl("BUY", 100.0, 90.0, 2.0)
    assert usd == -20.0
    assert pct == -10.0

    # sell win
    usd, pct = calc_pnl("SELL", 100.0, 90.0, 2.0)
    assert usd == 20.0
    assert pct == 10.0

    # sell with commission
    usd, pct = calc_pnl("SELL", 100.0, 90.0, 2.0, 1.5)
    assert usd == 18.5
    assert pct == 9.25


def test_generate_order_id():
    oid = generate_order_id("long_strategy_name_that_is_very_long", "BTCUSDT")
    assert len(oid) <= 36
    valid, err = validate_order_id(oid)
    assert valid is True
    assert err == ""


def test_validate_order_id_invalid():
    assert validate_order_id("")[0] is False
    assert validate_order_id("a" * 37)[0] is False
    assert validate_order_id("invalid@char")[0] is False


def test_other_helpers():
    assert clamp(5, 1, 10) == 5
    assert clamp(0, 1, 10) == 1
    assert clamp(11, 1, 10) == 10

    assert safe_div(10, 2) == 5.0
    assert safe_div(10, 0, default=-1) == -1

    assert pct_change(100, 110) == 10.0

    assert truncate("hello world", 5) == "he..."
    assert truncate("hi", 5) == "hi"

    assert mask_key("12345678901234567890") == "123456...567890"
    assert mask_key("short") == "***"

    assert calc_risk_reward(100, 90, 120) == 2.0
    v = calc_position_value(2.0, 50.0, 5)
    assert v["notional"] == 100.0
    assert v["margin"] == 20.0
    assert v["leverage"] == 5

    assert annualize_return(0.05, 30) > 0.7
    assert annualize_return(0.05, 0) == 0.0


def test_validators():
    assert is_valid_symbol("BTCUSDT") is True
    assert is_valid_symbol("btcusdt") is False
    assert is_valid_symbol("") is False

    assert is_valid_side("BUY") is True
    assert is_valid_side("HOLD") is False

    assert is_valid_timeframe("1h") is True
    assert is_valid_timeframe("invalid") is False

    validate_config_range(5.0, 1.0, 10.0, "test")
    with pytest.raises(ValueError):
        validate_config_range(0.0, 1.0, 10.0, "test")


@pytest.mark.asyncio
async def test_retry_async():
    from runtime.agent.utils.helpers import retry_async

    runs = 0

    async def failing_func():
        nonlocal runs
        runs += 1
        if runs < 3:
            raise ValueError("Fail")
        return "Success"

    res = await retry_async(failing_func, max_retry=3, base_delay_s=0.01)
    assert res == "Success"
    assert runs == 3

    # Test complete failure
    runs2 = 0

    async def always_fail():
        nonlocal runs2
        runs2 += 1
        raise ValueError("Fail")

    with pytest.raises(ValueError):
        await retry_async(always_fail, max_retry=2, base_delay_s=0.01)
    assert runs2 == 3  # initial + 2 retries


def test_retry_sync():
    from runtime.agent.utils.helpers import retry_sync

    runs = 0

    @retry_sync(max_retry=2, base_delay_s=0.01)
    def failing_func():
        nonlocal runs
        runs += 1
        if runs < 2:
            raise ValueError("Fail")
        return "Success"

    assert failing_func() == "Success"
    assert runs == 2

    runs2 = 0

    @retry_sync(max_retry=1, base_delay_s=0.01)
    def always_fail():
        nonlocal runs2
        runs2 += 1
        raise ValueError("Fail")

    with pytest.raises(ValueError):
        always_fail()
    assert runs2 == 2
