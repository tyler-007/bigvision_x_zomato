"""
AutoLunch — Pydantic Data Models: Restaurant & Order

These are the intermediate data structures passed between
the Zomato client, the LLM decision engine, and n8n.
"""
from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    """A single item on a restaurant's menu."""

    item_id: str
    name: str
    description: str = ""
    base_price: float          # Price on the menu — NOT the checkout total
    is_veg: bool
    category: str = ""         # e.g. "Main Course", "Beverages"
    rating: float | None = None
    cuisine_tags: list[str] = Field(default_factory=list)


class Restaurant(BaseModel):
    """A restaurant returned from Zomato MCP searchRestaurants."""

    restaurant_id: str
    name: str
    cuisine_types: list[str]
    rating: float
    distance_km: float
    delivery_time_minutes: int
    menu: list[MenuItem] = Field(default_factory=list)

    def affordable_items(self, max_base_price: float) -> list[MenuItem]:
        """
        Pre-filter menu items by base price.
        Note: This is NOT the final budget check — cart simulation is required
        to get the true net total (base + GST + platform fee + delivery).
        Used to reduce the candidate list before LLM processing.
        """
        return [item for item in self.menu if item.base_price <= max_base_price]


class CartSimulationResult(BaseModel):
    """Result from Zomato MCP addToCart simulation."""

    cart_id: str
    restaurant_id: str
    item_id: str
    base_price: float
    delivery_fee: float
    platform_fee: float
    gst: float
    net_total: float           # The only number that matters for the ₹250 check
    within_budget: bool        # net_total <= settings.zomato.max_budget_inr


class LLMOrderDecision(BaseModel):
    """
    Structured output from the LLM decision engine.
    The LLM must return valid JSON matching this schema.
    """

    restaurant_name: str
    restaurant_id: str
    item_name: str
    item_id: str
    base_price: float
    estimated_net_total: float    # LLM's estimate — always verified by cart simulation
    reasoning: str                # LLM's explanation (logged, shown in Telegram preview)
    confidence: float = Field(ge=0.0, le=1.0, description="LLM confidence score 0–1")


class CheckoutResult(BaseModel):
    """Result from Zomato MCP checkout call."""

    order_id: str
    upi_payment_link: str
    upi_qr_code_url: str | None = None
    amount_payable: float          # Final confirmed net total
    estimated_delivery_minutes: int
