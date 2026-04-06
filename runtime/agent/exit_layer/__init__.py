"""
runtime.agent.exit_layer — Exit Layer.

Menentukan kapan dan bagaimana keluar dari posisi aktif.
Pure function — tidak ada I/O.
"""

from runtime.agent.exit_layer.break_even import BreakEvenManager  # type: ignore
from runtime.agent.exit_layer.exit_manager import ExitManager  # type: ignore
from runtime.agent.exit_layer.take_profit import TakeProfitManager  # type: ignore
from runtime.agent.exit_layer.trailing import TrailingStopManager  # type: ignore

__all__ = [
    "ExitManager",
    "TrailingStopManager",
    "TakeProfitManager",
    "BreakEvenManager",
]
