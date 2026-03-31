from __future__ import annotations
"""
AutoLunch — Pydantic Data Models: Episodic Memory

The memory file acts as the agent's long-term memory:
- Past successful orders (for repeat-aversion logic)
- User rejections with reasons (so the LLM learns your real-time mood)
- Learned blocks (cumulative patterns the LLM should internalize)
"""
from datetime import date
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum): pass
from pydantic import BaseModel, Field


class OrderStatus(StrEnum):
    PLACED = "placed"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    MANUAL = "manual"          # User chose to order manually


class PastOrder(BaseModel):
    """A single successfully placed order."""

    order_date: date
    restaurant_name: str
    restaurant_id: str
    item_name: str
    item_id: str
    base_price: float
    net_total: float           # Actual charged amount (incl. all fees)
    status: OrderStatus = OrderStatus.PLACED
    receipt_drive_url: str | None = None
    sheet_row_id: int | None = None    # Row index in Google Sheet for deduplication


class Rejection(BaseModel):
    """A single user rejection during HITL approval."""

    rejection_date: date
    suggested_restaurant: str
    suggested_item: str
    suggested_net_total: float
    user_reason: str           # Raw text reason typed by user
    llm_extracted_constraint: str | None = None  # LLM-parsed constraint from reason


class LearnedBlock(BaseModel):
    """
    A persistent block derived from repeated rejections.
    Written by the decision engine when the same restaurant/item is
    rejected 2+ times within 7 days.
    """

    blocked_entity: str        # Could be restaurant name, item keyword, or cuisine
    block_type: str            # "restaurant" | "item_keyword" | "cuisine"
    reason_summary: str
    created_on: date
    expires_on: date | None = None   # None = permanent


class AgentMemory(BaseModel):
    """
    Root memory structure — serialized to data/memory.json.
    Append-only log of orders, rejections, and learned blocks.
    """

    past_orders: list[PastOrder] = Field(default_factory=list)
    rejections: list[Rejection] = Field(default_factory=list)
    learned_blocks: list[LearnedBlock] = Field(default_factory=list)

    def recent_orders(self, days: int = 14) -> list[PastOrder]:
        """Return past orders within the last N days (for repeat-aversion)."""
        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=days)
        return [o for o in self.past_orders if o.order_date >= cutoff]

    def recent_rejections(self, days: int = 7) -> list[Rejection]:
        """Return rejections within the last N days (fed to LLM as constraints)."""
        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=days)
        return [r for r in self.rejections if r.rejection_date >= cutoff]

    def todays_rejection_count(self) -> int:
        """How many times the user has rejected suggestions today."""
        today = date.today()
        return sum(1 for r in self.rejections if r.rejection_date == today)
