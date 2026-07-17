"""Compatibility exports for the shared volatility bridge models.

The canonical Pydantic contracts live in the ``volatility-bridge`` package.
Keeping these re-exports lets existing ``src.models`` imports continue to work
while ensuring this UI consumes the same schemas as the backend.
"""

from volatility_bridge.volatile_models import (
    DashboardLot,
    DashboardStatePayload,
    SellInstruction,
    StrategySignal,
    UICommandEmergencyHalt,
    UICommandLiquidateAll,
    UICommandMessage,
    UICommandResumeTrading,
    UICommandUpdateConfig,
)

__all__ = [
    "DashboardLot",
    "DashboardStatePayload",
    "SellInstruction",
    "StrategySignal",
    "UICommandEmergencyHalt",
    "UICommandLiquidateAll",
    "UICommandMessage",
    "UICommandResumeTrading",
    "UICommandUpdateConfig",
]
