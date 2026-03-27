from runtime.agent.core.modes import (  # type: ignore
    AgentMode,
    ModeConfig,
    ModeTransitionGuard,
    TransitionResult,
)


def test_mode_config():
    assert ModeConfig.should_send_order(AgentMode.LIVE) is True
    assert ModeConfig.should_send_order(AgentMode.PAPER) is False
    assert ModeConfig.use_real_money(AgentMode.LIVE) is True
    assert ModeConfig.use_real_money(AgentMode.SHADOW) is False
    assert ModeConfig.use_testnet(AgentMode.SHADOW) is True
    assert ModeConfig.use_testnet(AgentMode.LIVE) is False
    assert ModeConfig.block_on_risk_violation(AgentMode.LIVE) is True
    assert ModeConfig.require_sl(AgentMode.LIVE) is True


def test_transition_guard_allowed():
    state = {"open_positions_count": 0, "pending_orders_count": 0}
    res = ModeTransitionGuard.validate_transition(AgentMode.PAPER, AgentMode.SHADOW, state)
    assert res == TransitionResult.ALLOWED


def test_transition_guard_forbidden():
    state = {"open_positions_count": 0, "pending_orders_count": 0}
    res = ModeTransitionGuard.validate_transition(AgentMode.LIVE, AgentMode.PAPER, state)
    assert res == TransitionResult.FORBIDDEN


class MockState:
    def __init__(self, open_pos, pending_orders):
        self.open_positions_count = open_pos
        self.pending_orders_count = pending_orders


def test_transition_guard_has_positions():
    state = MockState(1, 0)
    res = ModeTransitionGuard.validate_transition(AgentMode.PAPER, AgentMode.SHADOW, state)
    assert res == TransitionResult.HAS_OPEN_POSITIONS

    state_obj = MockState(0, 1)
    res2 = ModeTransitionGuard.validate_transition(AgentMode.PAPER, AgentMode.SHADOW, state_obj)
    assert res2 == TransitionResult.HAS_PENDING_ORDERS
