from __future__ import annotations
"""
AutoLunch — Real Zomato MCP Client

Connects to the real Zomato MCP server at https://mcp-server.zomato.com/mcp
using the MCP protocol with OAuth authentication.

Setup (one-time):
  1. python scripts/setup_zomato_auth.py   → generates auth URL
  2. Open URL in browser, log in to Zomato
  3. python scripts/exchange_token.py 'callback_url'  → saves tokens

After auth, this client auto-selects when data/zomato_oauth.json exists.
"""
import asyncio
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from autolunch.config.settings import settings
from autolunch.models.restaurant import (
    CartSimulationResult,
    CheckoutResult,
    MenuItem,
    Restaurant,
)
from autolunch.models.preferences import UserPreferences
from autolunch.core.exceptions import (
    ZomatoAuthError,
    ZomatoNoResultsError,
    ZomatoServerError,
    BudgetExceededError,
)

ZOMATO_MCP_URL = "https://mcp-server.zomato.com/mcp"
TOKEN_FILE = Path("data/zomato_oauth.json")

# Miraya Rose default address ID (from user's Zomato account)
DEFAULT_ADDRESS_ID = "877033185"


def _load_tokens() -> dict:
    """Load saved OAuth tokens from disk."""
    if not TOKEN_FILE.exists():
        raise ZomatoAuthError(
            "Zomato OAuth tokens not found. Run: python scripts/exchange_token.py"
        )
    data = json.loads(TOKEN_FILE.read_text())
    tokens = data.get("tokens")
    if not tokens or not tokens.get("access_token"):
        raise ZomatoAuthError("Invalid token file. Re-run auth flow.")
    return tokens


class RealZomatoMCPClient:
    """
    Connects to the real Zomato MCP server and exposes the same interface
    as the mock ZomatoMCPClient for seamless integration.

    Zomato MCP tools used:
      - get_restaurants_for_keyword  → search_restaurants()
      - get_menu_items_listing       → get_menu() step 1: discover categories
      - get_restaurant_menu_by_categories → get_menu() step 2: fetch items
      - create_cart                  → simulate_cart()
      - checkout_cart                → checkout()
      - get_cart_offers              → (bonus) apply best promo
      - get_saved_addresses_for_user → resolve delivery address
    """

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._address_id: str = DEFAULT_ADDRESS_ID
        self._ctx_stack: list = []

    async def connect(self) -> None:
        """Establish MCP connection with saved OAuth token."""
        tokens = _load_tokens()
        http_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            timeout=60.0,
        )
        self._ctx = streamable_http_client(ZOMATO_MCP_URL, http_client=http_client)
        read, write, _ = await self._ctx.__aenter__()
        self._session_ctx = ClientSession(read, write)
        self._session = await self._session_ctx.__aenter__()
        await self._session.initialize()

        tools = await self._session.list_tools()
        tool_names = [t.name for t in tools.tools]
        logger.info(f"Connected to real Zomato MCP. Tools: {tool_names}")

    async def disconnect(self) -> None:
        try:
            if self._session and hasattr(self, '_session_ctx'):
                await self._session_ctx.__aexit__(None, None, None)
            if hasattr(self, '_ctx'):
                await self._ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"Disconnect cleanup: {e}")

    async def __aenter__(self) -> RealZomatoMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ── MCP Tool Call Helper ─────────────────────────────────────────────────

    async def _call(self, tool: str, args: dict) -> Any:
        """Call an MCP tool and return parsed JSON result."""
        if not self._session:
            raise ZomatoServerError("Not connected to Zomato MCP")
        logger.debug(f"MCP call: {tool}", args=args)
        result = await self._session.call_tool(tool, arguments=args)
        for content in result.content:
            if hasattr(content, 'text'):
                text = content.text
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown code blocks or mixed text
                    import re
                    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
                    if json_match:
                        try:
                            return json.loads(json_match.group(1))
                        except json.JSONDecodeError:
                            pass
                    # Try to find any JSON object in the text
                    brace_start = text.find('{')
                    if brace_start >= 0:
                        # Find matching closing brace
                        depth = 0
                        for i, c in enumerate(text[brace_start:], brace_start):
                            if c == '{': depth += 1
                            elif c == '}': depth -= 1
                            if depth == 0:
                                try:
                                    return json.loads(text[brace_start:i+1])
                                except json.JSONDecodeError:
                                    break
                    logger.warning(f"MCP {tool} returned non-JSON text ({len(text)} chars): {text[:500]}")
                    return {"raw_text": text}
        return {}

    # ── Public API (matches mock client interface) ───────────────────────────

    async def search_restaurants(self, prefs: UserPreferences) -> list[Restaurant]:
        """
        Search real Zomato restaurants near the delivery address.
        Applies all preference filters (rating, reviews, distance, blocklist).
        """
        # Run multiple searches to get a wider restaurant pool
        diet_keyword = "vegetarian" if str(prefs.diet_type) == "vegetarian" else ""
        search_queries = [
            f"{diet_keyword} lunch thali",
            f"{diet_keyword} meals",
            f"{diet_keyword} north indian",
            f"{diet_keyword} south indian",
        ]
        # Add preferred cuisines as searches
        for cuisine in prefs.guardrails.preferred_cuisines[:2]:
            search_queries.append(f"{diet_keyword} {cuisine}")

        seen_ids: set[str] = set()
        restaurants_raw: list[dict] = []
        for query in search_queries:
            try:
                raw = await self._call("get_restaurants_for_keyword", {
                    "address_id": self._address_id,
                    "keyword": query.strip(),
                })
                for r in raw.get("results", []):
                    rid = str(r.get("res_id", ""))
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        restaurants_raw.append(r)
            except Exception as e:
                logger.debug(f"Search '{query}' failed: {e}")
        blocked_names = {r.lower() for r in prefs.guardrails.blocked_restaurants}
        blocked_cuisines = {c.lower() for c in prefs.guardrails.blocked_cuisines}

        results: list[Restaurant] = []
        for r in restaurants_raw:
            name = r.get("name", "")
            rating = float(r.get("rating", 0))
            votes = int(r.get("votes", 0))
            distance = float(r.get("distance", 99))

            # Apply hard filters
            if r.get("serviceability_status") != "serviceable":
                continue
            if distance > prefs.max_distance_km:
                continue
            if rating < prefs.min_restaurant_rating:
                continue
            if votes < prefs.min_review_count:
                continue
            if name.lower() in blocked_names:
                continue

            # Parse inline menu items (Zomato returns top items with search)
            menu_items: list[MenuItem] = []
            for item in r.get("items", []):
                # Use variant_id as primary ID (required by create_cart)
                # Fall back to catalogue_id if variant_id not available
                item_id = str(item.get("variant_id", item.get("catalogue_id", "")))
                menu_items.append(MenuItem(
                    item_id=item_id,
                    name=item.get("name", ""),
                    base_price=float(item.get("price", item.get("min_price", 0))),
                    is_veg=item.get("is_veg", True),
                    category="",
                    cuisine_tags=[],
                ))

            restaurant = Restaurant(
                restaurant_id=str(r.get("res_id", "")),
                name=name,
                cuisine_types=[],
                rating=rating,
                review_count=votes,
                distance_km=distance,
                delivery_time_minutes=self._parse_eta(r.get("eta", "30")),
                menu=menu_items,
            )
            results.append(restaurant)

        # Sort: preferred restaurants first, then by rating
        preferred = {r.lower() for r in prefs.guardrails.preferred_restaurants}
        results.sort(key=lambda r: (r.name.lower() not in preferred, -r.rating))

        logger.info(f"Real search: {len(restaurants_raw)} from Zomato → {len(results)} after filters")

        if not results:
            raise ZomatoNoResultsError(
                "No restaurants match your filters on Zomato right now.",
                context={"keyword": keyword, "address": self._address_id},
            )
        return results

    async def get_menu(self, restaurant: Restaurant) -> Restaurant:
        """
        Fetch full menu for a restaurant. Two-step:
        1. get_menu_items_listing → discover categories
        2. get_restaurant_menu_by_categories → fetch items per category
        """
        # If we already have items from search results, return as-is
        if restaurant.menu and len(restaurant.menu) >= 3:
            logger.info(f"Using inline menu for {restaurant.name}: {len(restaurant.menu)} items")
            return restaurant

        try:
            # Step 1: Get categories
            listing = await self._call("get_menu_items_listing", {
                "res_id": int(restaurant.restaurant_id),
                "address_id": self._address_id,
            })

            categories = []
            if isinstance(listing, dict):
                # Extract category names from the listing
                for key, val in listing.items():
                    if isinstance(val, str) and val not in categories:
                        categories.append(val)
                    elif isinstance(val, list):
                        categories.extend(val)
            categories = list(set(categories))[:5]  # Top 5 categories

            if not categories:
                logger.warning(f"No categories found for {restaurant.name}")
                return restaurant

            # Step 2: Get menu items
            menu_data = await self._call("get_restaurant_menu_by_categories", {
                "res_id": int(restaurant.restaurant_id),
                "categories": categories,
                "address_id": self._address_id,
            })

            menu_items: list[MenuItem] = []
            items_raw = menu_data if isinstance(menu_data, list) else menu_data.get("items", menu_data.get("menu", []))

            if isinstance(items_raw, list):
                for item in items_raw:
                    if isinstance(item, dict):
                        menu_items.append(MenuItem(
                            item_id=str(item.get("catalogue_id", item.get("id", ""))),
                            name=item.get("name", "Unknown"),
                            description=item.get("description", ""),
                            base_price=float(item.get("price", item.get("min_price", 0))),
                            is_veg=item.get("is_veg", True),
                            category=item.get("category", ""),
                            cuisine_tags=item.get("tags", []),
                        ))

            if menu_items:
                restaurant.menu = menu_items
            logger.info(f"Full menu for {restaurant.name}: {len(restaurant.menu)} items")

        except Exception as e:
            logger.warning(f"Failed to fetch full menu for {restaurant.name}: {e}")

        return restaurant

    async def simulate_cart(self, restaurant: Restaurant, item: MenuItem) -> CartSimulationResult:
        """
        Create a real cart on Zomato and get actual pricing.
        Uses UPI as payment method for exact net total.
        """
        budget = settings.zomato.max_budget_inr if settings.zomato else 250

        # Build item payload — Zomato requires variant_id
        # The search results give us catalogue_id; we store variant_id when available
        variant_id = item.item_id
        # If we have a catalogue_id, we need the variant_id from search results
        # The search API returns both — variant_id is what create_cart needs

        try:
            raw = await self._call("create_cart", {
                "res_id": int(restaurant.restaurant_id),
                "items": [{"variant_id": variant_id, "quantity": 1}],
                "address_id": self._address_id,
                "payment_type": "upi",
            })

            logger.info(f"[CART RAW] create_cart response keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}")
            logger.info(f"[CART RAW] Full response: {json.dumps(raw, default=str)[:2000]}")

            # Parse cart response — Zomato returns nested structure
            cart_data = raw.get("cart", raw)

            # Handle raw_text responses — parse cart details from plain text
            if list(cart_data.keys()) == ["raw_text"]:
                raw_text = cart_data["raw_text"]
                logger.info(f"[CART] Raw text from Zomato ({len(raw_text)} chars): {raw_text[:1000]}")
                # Try to extract key-value pairs from the text
                import re
                def _extract(pattern, text, default=None):
                    m = re.search(pattern, text, re.IGNORECASE)
                    return m.group(1).strip() if m else default

                cart_id = _extract(r'cart[_\s]?id[:\s]+["\']?(\S+?)["\']?(?:\s|,|$)', raw_text) or f"cart_{restaurant.restaurant_id}_{item.item_id}"
                shareable_link = _extract(r'(?:shareable[_\s]?link|cart[_\s]?link|share[_\s]?url)[:\s]+["\']?(https?\S+)["\']?', raw_text) or ""
                # Try to find total/amount
                total_str = _extract(r'(?:grand[_\s]?total|total|final[_\s]?amount|net[_\s]?total|amount)[:\s]+[₹]?(\d+\.?\d*)', raw_text)
                delivery_str = _extract(r'(?:delivery[_\s]?(?:fee|charge))[:\s]+[₹]?(\d+\.?\d*)', raw_text)
                platform_str = _extract(r'(?:platform[_\s]?(?:fee|charge))[:\s]+[₹]?(\d+\.?\d*)', raw_text)
                gst_str = _extract(r'(?:gst|tax)[:\s]+[₹]?(\d+\.?\d*)', raw_text)
                promo_code = _extract(r'(?:promo|coupon)[_\s]?(?:code)?[:\s]+["\']?(\S+?)["\']?(?:\s|,|$)', raw_text)
                promo_disc_str = _extract(r'(?:promo[_\s]?discount|discount)[:\s]+[₹]?(\d+\.?\d*)', raw_text)

                net_total = float(total_str) if total_str else round(item.base_price * 1.05 + 8, 2)
                delivery_fee = float(delivery_str) if delivery_str else 0.0
                platform_fee = float(platform_str) if platform_str else 0.0
                gst = float(gst_str) if gst_str else 0.0
                base_price = item.base_price
                _shareable = shareable_link
                _promo = promo_code or ""
                _promo_disc = float(promo_disc_str) if promo_disc_str else 0.0

                logger.info(f"[CART] Parsed from raw text: cart_id={cart_id}, net={net_total}, link={bool(shareable_link)}, promo={_promo}")
            else:
                cart_id = str(cart_data.get("cart_id", f"cart_{restaurant.restaurant_id}_{item.item_id}"))
                logger.info(f"[CART] Parsed cart_id={cart_id}, cart_data keys={list(cart_data.keys()) if isinstance(cart_data, dict) else 'N/A'}")

            # Parse charge breakdown
            charges = cart_data.get("charge_breakdown", {})
            base_charges = charges.get("base_charges", [])
            taxes = charges.get("taxes", [])

            platform_fee = 0.0
            delivery_fee = 0.0
            for charge in base_charges:
                ctype = charge.get("charge_type", "")
                if "PLATFORM" in ctype:
                    platform_fee = float(charge.get("amount", 0))
                elif "DELIVERY" in ctype:
                    delivery_fee = float(charge.get("amount", 0))

            gst = sum(float(t.get("tax_amount", 0)) for t in taxes)
            base_price = float(cart_data.get("item_total", item.base_price))

            # Use Zomato's final_amount if available (includes auto-applied promos)
            final_amount = cart_data.get("final_amount")
            promo = cart_data.get("promo_code")
            promo_discount = float(cart_data.get("promo_discount_amount") or 0)
            shareable_link = cart_data.get("shareable_link") or ""
            if final_amount is not None:
                net_total = round(float(final_amount), 2)
                if promo:
                    logger.info(f"Promo auto-applied: {promo} (-₹{promo_discount})")
            else:
                net_total = round(base_price + delivery_fee + platform_fee + gst, 2)

            # These will be included in the CartSimulationResult
            _shareable = shareable_link
            _promo = promo or ""
            _promo_disc = promo_discount

        except Exception as e:
            logger.warning(f"Cart creation failed, estimating: {e}")
            cart_id = f"cart_{restaurant.restaurant_id}_{item.item_id}"
            delivery_fee = 0.0 if item.base_price >= 149 else 30.0
            platform_fee = 8.0
            gst = round(item.base_price * 0.05, 2)
            net_total = round(item.base_price + delivery_fee + platform_fee + gst, 2)
            base_price = item.base_price
            _shareable = ""
            _promo = ""
            _promo_disc = 0.0

        within_budget = net_total <= budget
        result = CartSimulationResult(
            cart_id=cart_id,
            restaurant_id=restaurant.restaurant_id,
            item_id=item.item_id,
            base_price=base_price,
            delivery_fee=delivery_fee,
            platform_fee=platform_fee,
            gst=gst,
            net_total=net_total,
            within_budget=within_budget,
            shareable_link=_shareable,
            promo_code=_promo,
            promo_discount=_promo_disc,
        )

        logger.info(f"Real cart: {item.name} from {restaurant.name} → ₹{net_total} (budget_ok={within_budget})")

        if not within_budget:
            raise BudgetExceededError(net_total=net_total, budget=budget)
        return result

    async def checkout(self, cart_id: str) -> CheckoutResult:
        """
        Place a real order on Zomato.
        If checkout_cart fails (e.g. AmountMismatchError), we still return
        the cart details so the user can complete payment in the Zomato app.
        """
        raw = await self._call("checkout_cart", {"cart_id": cart_id})

        # Check for error response
        error_text = raw.get("raw_text", "") if isinstance(raw, dict) else str(raw)
        if "Error" in error_text:
            logger.warning(f"Zomato checkout returned error: {error_text}")
            # Use shareable link so user can complete in Zomato app with their cart
            link = getattr(self, "_last_shareable_link", "") or "https://www.zomato.com/"
            return CheckoutResult(
                order_id=f"pending_{cart_id[:8]}",
                upi_payment_link=link,
                upi_qr_code_url=None,
                amount_payable=0,
                estimated_delivery_minutes=30,
            )

        return CheckoutResult(
            order_id=str(raw.get("order_id", raw.get("id", f"ord_{cart_id[-8:]}"))),
            upi_payment_link=raw.get("payment_url", raw.get("upi_link", "")),
            upi_qr_code_url=raw.get("qr_code_url"),
            amount_payable=float(raw.get("amount", raw.get("total", 0))),
            estimated_delivery_minutes=int(raw.get("eta_minutes", raw.get("estimated_delivery_minutes", 30))),
        )

    async def get_offers(self, cart_id: str) -> list[dict]:
        """Get available promos for a cart."""
        try:
            raw = await self._call("get_cart_offers", {
                "cart_id": cart_id,
                "address_id": self._address_id,
            })
            return raw if isinstance(raw, list) else raw.get("promos", [])
        except Exception as e:
            logger.debug(f"Offers fetch failed: {e}")
            return []

    async def list_available_tools(self) -> list[dict]:
        """Return all available MCP tools (for debugging)."""
        if not self._session:
            return []
        tools = await self._session.list_tools()
        return [
            {"name": t.name, "description": (t.description or "")[:100]}
            for t in tools.tools
        ]

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_eta(eta_str: str) -> int:
        """Parse '35–40 min' → 37 (average)."""
        import re
        nums = re.findall(r'\d+', str(eta_str))
        if len(nums) >= 2:
            return (int(nums[0]) + int(nums[1])) // 2
        elif nums:
            return int(nums[0])
        return 30
