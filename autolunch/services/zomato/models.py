"""
AutoLunch — Zomato MCP API Models

These are the raw DTOs returned by the Zomato MCP server before
they are mapped to our internal Restaurant/MenuItem domain models.
Keeping them separate means a Zomato API change only affects this file.
"""
from pydantic import BaseModel, Field


# ── Raw MCP Response DTOs ─────────────────────────────────────────────────────


class ZomatoMenuItemDTO(BaseModel):
    """Raw menu item as returned by Zomato MCP getMenu."""

    id: str
    name: str
    description: str = ""
    price: float                        # Base price in INR (pre-tax, pre-fee)
    is_veg: bool
    category: str = ""
    avg_rating: float | None = None
    tags: list[str] = Field(default_factory=list)


class ZomatoRestaurantDTO(BaseModel):
    """Raw restaurant record as returned by Zomato MCP searchRestaurants."""

    id: str
    name: str
    cuisines: list[str]
    avg_rating: float
    total_ratings_string: str = ""      # e.g. "1.2K ratings" — parsed for review_count
    total_ratings: int = 0              # Absolute review count (used for ≥1000 filter)
    distance: float                     # Distance in km from delivery coordinates
    delivery_time: int                  # Estimated delivery minutes
    is_open: bool = True


class ZomatoCartDTO(BaseModel):
    """Cart state returned by Zomato MCP addToCart."""

    cart_id: str
    restaurant_id: str
    item_id: str
    item_price: float
    delivery_fee: float
    platform_fee: float
    gst: float
    grand_total: float                  # Net total — the ₹250 check is against this


class ZomatoCheckoutDTO(BaseModel):
    """Checkout result returned by Zomato MCP checkout."""

    order_id: str
    payment_url: str                    # UPI deep-link
    qr_code_url: str | None = None
    amount: float
    estimated_delivery_minutes: int


# ── MCP Tool Call Schemas (sent TO the MCP server) ───────────────────────────


class SearchRestaurantsParams(BaseModel):
    latitude: float
    longitude: float
    radius_km: float = 7.0
    sort_by: str = "rating"             # "rating" | "delivery_time" | "distance"


class GetMenuParams(BaseModel):
    restaurant_id: str


class AddToCartParams(BaseModel):
    restaurant_id: str
    item_id: str
    quantity: int = 1


class CheckoutParams(BaseModel):
    cart_id: str
    delivery_address: str = "office"    # Pre-saved address key in Zomato account
