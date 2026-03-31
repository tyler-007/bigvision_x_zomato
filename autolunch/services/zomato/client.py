from __future__ import annotations
"""
AutoLunch — Zomato MCP Client

This is the core service that communicates with the Zomato MCP Node.js server.
It exposes clean async Python methods for each MCP tool call, with:
  - 7km radius filtering (Zomato Gold free delivery constraint)
  - ≥1000 review count filter (social proof / quality signal)
  - ≥4.0 rating filter
  - Cart simulation with real net total (the only number used for ₹250 check)
  - Retry logic via tenacity for transient API errors

Architecture note: This client talks to the Zomato MCP server over HTTP
(the MCP server runs locally as a Node.js process on port 3000).
For testing / dry-runs, set ZOMATO_MCP_SERVER_URL to the mock server.
"""
import httpx
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from autolunch.config.settings import settings
from autolunch.core.exceptions import (
    ZomatoAuthError,
    ZomatoServerError,
    ZomatoNoResultsError,
    BudgetExceededError,
)
from autolunch.models.preferences import UserPreferences
from autolunch.models.restaurant import (
    Restaurant,
    MenuItem,
    CartSimulationResult,
    CheckoutResult,
)
from autolunch.services.zomato.models import (
    ZomatoRestaurantDTO,
    ZomatoMenuItemDTO,
    ZomatoCartDTO,
    ZomatoCheckoutDTO,
    SearchRestaurantsParams,
    GetMenuParams,
    AddToCartParams,
    CheckoutParams,
)


# ── Retry config (used on all MCP HTTP calls) ────────────────────────────────
_RETRY_POLICY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.TransportError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class ZomatoMCPClient:
    """
    Async client for the Zomato MCP Node.js server.

    Usage:
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(prefs)
    """

    def __init__(self) -> None:
        self._base_url = settings.zomato.mcp_server_url
        self._auth_token = settings.zomato.auth_token
        self._lat = settings.zomato.delivery_latitude
        self._lng = settings.zomato.delivery_longitude
        self._max_km = settings.zomato.max_distance_km
        self._budget = settings.zomato.max_budget_inr
        self._min_rating = settings.zomato.min_restaurant_rating
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ZomatoMCPClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._auth_token}",
                "Content-Type": "application/json",
                "X-Client": "autolunch/1.0",
            },
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    # ── Internal HTTP helper ─────────────────────────────────────────────────

    @retry(**_RETRY_POLICY)
    async def _call(self, tool: str, params: dict) -> dict:
        """
        Generic MCP tool call — wraps the HTTP POST and handles error codes.
        Zomato MCP server expects: POST /tool/<tool_name> with JSON body.
        """
        logger.debug(f"MCP call", tool=tool, params=params)
        try:
            response = await self._client.post(f"/tool/{tool}", json=params)
        except httpx.TransportError as e:
            raise ZomatoServerError(f"Network error calling Zomato MCP: {e}") from e

        if response.status_code == 401:
            raise ZomatoAuthError(
                "Zomato auth token is invalid or expired. Re-authenticate.",
                context={"status": 401},
            )
        if response.status_code >= 500:
            raise ZomatoServerError(
                f"Zomato MCP server error: {response.status_code}",
                context={"body": response.text[:200]},
            )
        if response.status_code >= 400:
            raise ZomatoServerError(
                f"Zomato MCP client error: {response.status_code}",
                context={"body": response.text[:200]},
            )

        return response.json()

    # ── Public API ───────────────────────────────────────────────────────────

    async def search_restaurants(self, prefs: UserPreferences) -> list[Restaurant]:
        """
        Step 1: Fetch restaurants near office and apply all hard filters.

        Filters applied (in order):
          1. ≤ 7km radius (Zomato Gold free delivery)
          2. Restaurant must be open
          3. Rating ≥ min_restaurant_rating (from prefs, default 4.0)
          4. Review count ≥ min_review_count (from prefs, default 1000)
          5. Cuisine blocklist from guardrails

        Returns sorted list (best rating first), ready for LLM processing.
        """
        params = SearchRestaurantsParams(
            latitude=self._lat,
            longitude=self._lng,
            radius_km=float(prefs.max_distance_km),
        )
        raw = await self._call("searchRestaurants", params.model_dump())
        restaurants_raw: list[dict] = raw.get("restaurants", [])

        results: list[Restaurant] = []
        blocked = {c.lower() for c in prefs.guardrails.blocked_cuisines}
        blocked_names = {r.lower() for r in prefs.guardrails.blocked_restaurants}

        for r in restaurants_raw:
            dto = ZomatoRestaurantDTO.model_validate(r)

            # ── Hard filters ──────────────────────────────────────────────
            if not dto.is_open:
                continue
            if dto.distance > prefs.max_distance_km:
                continue
            if dto.avg_rating < prefs.min_restaurant_rating:
                logger.debug("Filtered: low rating", name=dto.name, rating=dto.avg_rating)
                continue
            if dto.total_ratings < prefs.min_review_count:
                logger.debug("Filtered: too few reviews", name=dto.name, reviews=dto.total_ratings)
                continue
            if dto.name.lower() in blocked_names:
                logger.debug("Filtered: blocklisted restaurant", name=dto.name)
                continue
            if any(c.lower() in blocked for c in dto.cuisines):
                logger.debug("Filtered: blocked cuisine", name=dto.name, cuisines=dto.cuisines)
                continue

            results.append(Restaurant(
                restaurant_id=dto.id,
                name=dto.name,
                cuisine_types=dto.cuisines,
                rating=dto.avg_rating,
                review_count=dto.total_ratings,
                distance_km=dto.distance,
                delivery_time_minutes=dto.delivery_time,
            ))

        # Sort: preferred restaurants first, then by rating desc
        preferred = {r.lower() for r in prefs.guardrails.preferred_restaurants}
        results.sort(key=lambda r: (
            r.name.lower() not in preferred,   # preferred = False → sorts first
            -r.rating,
        ))

        logger.info(
            "Restaurant search complete",
            total_from_api=len(restaurants_raw),
            after_filters=len(results),
        )

        if not results:
            raise ZomatoNoResultsError(
                "No restaurants match your filters (distance, rating, reviews, blocklist).",
                context={
                    "radius_km": prefs.max_distance_km,
                    "min_rating": prefs.min_restaurant_rating,
                    "min_reviews": prefs.min_review_count,
                },
            )

        return results

    async def get_menu(self, restaurant: Restaurant) -> Restaurant:
        """
        Step 2: Fetch the full menu for a restaurant and attach it.
        Returns the same Restaurant object with menu populated.
        """
        raw = await self._call("getMenu", GetMenuParams(restaurant_id=restaurant.restaurant_id).model_dump())
        items_raw: list[dict] = raw.get("menu", [])

        menu: list[MenuItem] = [
            MenuItem(
                item_id=dto.id,
                name=dto.name,
                description=dto.description,
                base_price=dto.price,
                is_veg=dto.is_veg,
                category=dto.category,
                rating=dto.avg_rating,
                cuisine_tags=dto.tags,
            )
            for dto in (ZomatoMenuItemDTO.model_validate(i) for i in items_raw)
        ]

        restaurant.menu = menu
        logger.info("Menu fetched", restaurant=restaurant.name, item_count=len(menu))
        return restaurant

    async def simulate_cart(
        self,
        restaurant: Restaurant,
        item: MenuItem,
    ) -> CartSimulationResult:
        """
        Step 3: Add item to cart and get the REAL net total.

        This is the critical budget check — we do NOT trust the menu price.
        The cart simulation includes GST (5%), platform fee, and delivery fee.

        Raises BudgetExceededError if net_total > configured max budget.
        The LLM decision engine catches this and picks a different item.
        """
        raw = await self._call(
            "addToCart",
            AddToCartParams(
                restaurant_id=restaurant.restaurant_id,
                item_id=item.item_id,
            ).model_dump(),
        )
        dto = ZomatoCartDTO.model_validate(raw)

        within_budget = dto.grand_total <= self._budget

        result = CartSimulationResult(
            cart_id=dto.cart_id,
            restaurant_id=dto.restaurant_id,
            item_id=dto.item_id,
            base_price=dto.item_price,
            delivery_fee=dto.delivery_fee,
            platform_fee=dto.platform_fee,
            gst=dto.gst,
            net_total=dto.grand_total,
            within_budget=within_budget,
        )

        logger.info(
            "Cart simulated",
            restaurant=restaurant.name,
            item=item.name,
            base=dto.item_price,
            net_total=dto.grand_total,
            within_budget=within_budget,
            budget_cap=self._budget,
        )

        if not within_budget:
            raise BudgetExceededError(net_total=dto.grand_total, budget=self._budget)

        return result

    async def checkout(self, cart_id: str) -> CheckoutResult:
        """
        Step 4: Initiate checkout and get the UPI payment link.
        Called ONLY after user confirms 'Yes' on Telegram.
        Returns the payment URL to send to the user.
        """
        raw = await self._call(
            "checkout",
            CheckoutParams(cart_id=cart_id).model_dump(),
        )
        dto = ZomatoCheckoutDTO.model_validate(raw)

        result = CheckoutResult(
            order_id=dto.order_id,
            upi_payment_link=dto.payment_url,
            upi_qr_code_url=dto.qr_code_url,
            amount_payable=dto.amount,
            estimated_delivery_minutes=dto.estimated_delivery_minutes,
        )

        logger.info(
            "Checkout initiated",
            order_id=dto.order_id,
            amount=dto.amount,
            delivery_minutes=dto.estimated_delivery_minutes,
        )
        return result
