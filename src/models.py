from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class StrategySignal(BaseModel):
    """The master signal returned by a Sizing Strategy to the OMS."""
    execute_buy: bool
    trade_value: float
    grid_step: float
    profit_target: float

class DashboardLot(BaseModel):
    """Strictly typed schema for an active inventory lot."""
    lot_id: str
    buy_price: float
    target_sell_price: float
    shares: float
    timestamp: Optional[datetime] = None

class DashboardStatePayload(BaseModel):
    """The master data contract ensuring absolute UI and Backend parity."""
    symbol: str
    current_price: float
    last_buy_price: float
    grid_step: float
    open_lots: List[DashboardLot] = Field(default_factory=list)
    closed_lots_count: int
    realized_profit: float
    timestamp: datetime
