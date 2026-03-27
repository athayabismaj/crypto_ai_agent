from unittest.mock import MagicMock, patch

import pytest  # type: ignore

from runtime.agent.core.event_bus import EventBus  # type: ignore
from runtime.agent.core.safe_mode import SafeMode  # type: ignore


@pytest.mark.asyncio
async def test_emergency_stop():
    event_bus = EventBus()
    scheduler = MagicMock()

    # Mock stop to return a future
    async def mock_stop():
        pass

    scheduler.stop = mock_stop

    sm = SafeMode(event_bus=event_bus, scheduler=scheduler)

    with patch("sys.exit") as mock_exit:
        await sm.emergency_stop("TEST_REASON", close_positions=True, exit_code=99)
        mock_exit.assert_called_once_with(99)
        assert sm.is_halted() is True


@pytest.mark.asyncio
async def test_graceful_shutdown():
    scheduler = MagicMock()

    async def mock_stop():
        pass

    scheduler.stop = mock_stop

    sm = SafeMode(scheduler=scheduler)

    with patch("sys.exit") as mock_exit:
        await sm.graceful_shutdown()
        mock_exit.assert_called_once_with(0)
        assert sm.is_halted() is True
