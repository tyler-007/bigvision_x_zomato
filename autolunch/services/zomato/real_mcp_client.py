from __future__ import annotations
"""
AutoLunch — Real Zomato MCP Client

Connects to the real Zomato MCP server at https://mcp-server.zomato.com/mcp
using the MCP protocol with OAuth authentication.

OAuth flow (one-time):
  1. Run: python -m autolunch.services.zomato.real_mcp_client --auth
  2. Browser opens → log in to Zomato → authorize
  3. Paste the callback URL → tokens saved to data/zomato_oauth.json
  4. Subsequent calls use the saved tokens automatically

After auth, this client replaces the mock server for real restaurant data.
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
from mcp.client.auth.oauth2 import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthClientProvider,
    OAuthToken,
    TokenStorage,
)
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
OAUTH_REDIRECT_URI = "https://oauth.pstmn.io/v1/callback"
TOKEN_FILE = Path("data/zomato_oauth.json")


# ── Token Storage ────────────────────────────────────────────────────────────

class FileTokenStorage:
    """Stores OAuth tokens and client info to a JSON file."""

    def __init__(self, path: Path = TOKEN_FILE) -> None:
        self._path = path
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except Exception:
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, default=str))

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._data.get("tokens")
        if raw:
            return OAuthToken.model_validate(raw)
        return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._data["tokens"] = tokens.model_dump()
        self._save()
        logger.info("OAuth tokens saved")

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._data.get("client_info")
        if raw:
            return OAuthClientInformationFull.model_validate(raw)
        return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._data["client_info"] = client_info.model_dump()
        self._save()
        logger.info("OAuth client info saved")


# ── Real MCP Client ─────────────────────────────────────────────────────────

class RealZomatoMCPClient:
    """
    Connects to the real Zomato MCP server and exposes the same interface
    as the existing ZomatoMCPClient (search, menu, cart, checkout).
    """

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._storage = FileTokenStorage()
        self._tools: dict[str, Any] = {}

    async def connect(self) -> None:
        """Establish MCP connection with OAuth."""
        client_metadata = OAuthClientMetadata(
            redirect_uris=[OAUTH_REDIRECT_URI],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name="AutoLunch",
            client_uri="https://github.com/tyler-007/bigvision_x_zomato",
        )

        auth = OAuthClientProvider(
            server_url=ZOMATO_MCP_URL,
            client_metadata=client_metadata,
            storage=self._storage,
            redirect_handler=self._handle_redirect,
            callback_handler=self._handle_callback,
        )

        http_client = httpx.AsyncClient(auth=auth, timeout=30.0)

        self._ctx = streamable_http_client(ZOMATO_MCP_URL, http_client=http_client)
        read, write, _ = await self._ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

        # Cache available tools
        tools_resp = await self._session.list_tools()
        self._tools = {t.name: t for t in tools_resp.tools}
        logger.info(f"Connected to Zomato MCP. Tools: {list(self._tools.keys())}")

    async def disconnect(self) -> None:
        if self._session:
            await self._session.__aexit__(None, None, None)
        if hasattr(self, '_ctx'):
            await self._ctx.__aexit__(None, None, None)

    async def __aenter__(self) -> RealZomatoMCPClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ── OAuth handlers ───────────────────────────────────────────────────────

    _auth_url: str | None = None
    _auth_event: asyncio.Event | None = None
    _callback_url: str | None = None

    async def _handle_redirect(self, url: str) -> None:
        """Called when the OAuth flow needs the user to visit a URL."""
        self._auth_url = url
        print(f"\n{'='*60}")
        print("ZOMATO LOGIN REQUIRED")
        print(f"{'='*60}")
        print(f"\nOpen this URL in your browser:\n\n{url}\n")
        try:
            webbrowser.open(url)
            print("(Browser should open automatically)")
        except Exception:
            print("(Copy and paste the URL manually)")
        print(f"\nAfter logging in, you'll be redirected to a URL starting with:")
        print(f"  {OAUTH_REDIRECT_URI}?code=...")
        print(f"\nPaste the FULL redirect URL here:")

    async def _handle_callback(self) -> tuple[str, str | None]:
        """Called to get the authorization code from the callback URL."""
        callback_url = input("> ").strip()
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        state = params.get("state", [None])[0]
        if not code:
            raise ZomatoAuthError("No authorization code found in URL")
        return code, state

    # ── MCP Tool Calls ───────────────────────────────────────────────────────

    async def _call_tool(self, name: str, args: dict) -> Any:
        """Call an MCP tool and return parsed result."""
        if not self._session:
            raise ZomatoServerError("Not connected to Zomato MCP")
        logger.debug(f"MCP tool call: {name}", args=args)
        result = await self._session.call_tool(name, arguments=args)
        # Parse text content from result
        for content in result.content:
            if hasattr(content, 'text'):
                try:
                    return json.loads(content.text)
                except json.JSONDecodeError:
                    return {"raw": content.text}
        return {}

    async def search_restaurants(self, prefs: UserPreferences) -> list[Restaurant]:
        """Search real restaurants near the configured location."""
        lat = settings.zomato.delivery_latitude if settings.zomato else 12.957175
        lng = settings.zomato.delivery_longitude if settings.zomato else 77.732161

        # Try different tool names — we don't know exact Zomato tool names yet
        tool_name = None
        for candidate in ["searchRestaurants", "search_restaurants", "search", "find_restaurants"]:
            if candidate in self._tools:
                tool_name = candidate
                break

        if not tool_name:
            # If no exact match, use the first tool that looks like search
            for name, tool in self._tools.items():
                desc = (tool.description or "").lower()
                if "restaurant" in desc and ("search" in desc or "find" in desc or "discover" in desc):
                    tool_name = name
                    break

        if not tool_name:
            logger.warning(f"No search tool found. Available: {list(self._tools.keys())}")
            raise ZomatoNoResultsError(
                f"No restaurant search tool found. Available tools: {list(self._tools.keys())}"
            )

        raw = await self._call_tool(tool_name, {
            "latitude": lat,
            "longitude": lng,
            "radius_km": float(prefs.max_distance_km),
        })

        restaurants_raw = raw.get("restaurants", []) if isinstance(raw, dict) else []
        results: list[Restaurant] = []
        blocked = {c.lower() for c in prefs.guardrails.blocked_cuisines}
        blocked_names = {r.lower() for r in prefs.guardrails.blocked_restaurants}

        for r in restaurants_raw:
            name = r.get("name", "")
            rating = float(r.get("avg_rating", r.get("rating", 0)))
            reviews = int(r.get("total_ratings", r.get("review_count", 0)))
            distance = float(r.get("distance", r.get("distance_km", 99)))
            cuisines = r.get("cuisines", r.get("cuisine_types", []))
            if isinstance(cuisines, str):
                cuisines = [c.strip() for c in cuisines.split(",")]

            # Apply filters
            if distance > prefs.max_distance_km:
                continue
            if rating < prefs.min_restaurant_rating:
                continue
            if reviews < prefs.min_review_count:
                continue
            if name.lower() in blocked_names:
                continue
            if any(c.lower() in blocked for c in cuisines):
                continue

            results.append(Restaurant(
                restaurant_id=str(r.get("id", r.get("restaurant_id", ""))),
                name=name,
                cuisine_types=cuisines,
                rating=rating,
                review_count=reviews,
                distance_km=distance,
                delivery_time_minutes=int(r.get("delivery_time", r.get("delivery_time_minutes", 30))),
            ))

        results.sort(key=lambda r: -r.rating)
        logger.info(f"Real search: {len(results)} restaurants after filters")

        if not results:
            raise ZomatoNoResultsError("No restaurants match your filters.")
        return results

    async def get_menu(self, restaurant: Restaurant) -> Restaurant:
        """Fetch menu for a restaurant."""
        tool_name = None
        for candidate in ["getMenu", "get_menu", "menu", "get_restaurant_menu"]:
            if candidate in self._tools:
                tool_name = candidate
                break
        if not tool_name:
            for name, tool in self._tools.items():
                if "menu" in (tool.description or "").lower():
                    tool_name = name
                    break

        if not tool_name:
            logger.warning(f"No menu tool found. Available: {list(self._tools.keys())}")
            return restaurant

        raw = await self._call_tool(tool_name, {"restaurant_id": restaurant.restaurant_id})
        items_raw = raw.get("menu", raw.get("items", [])) if isinstance(raw, dict) else []

        menu = []
        for item in items_raw:
            menu.append(MenuItem(
                item_id=str(item.get("id", item.get("item_id", ""))),
                name=item.get("name", "Unknown"),
                description=item.get("description", ""),
                base_price=float(item.get("price", item.get("base_price", 0))),
                is_veg=item.get("is_veg", True),
                category=item.get("category", ""),
                cuisine_tags=item.get("tags", item.get("cuisine_tags", [])),
            ))
        restaurant.menu = menu
        logger.info(f"Menu for {restaurant.name}: {len(menu)} items")
        return restaurant

    async def simulate_cart(self, restaurant: Restaurant, item: MenuItem) -> CartSimulationResult:
        """Add to cart and get real pricing."""
        tool_name = None
        for candidate in ["addToCart", "add_to_cart", "cart", "create_cart"]:
            if candidate in self._tools:
                tool_name = candidate
                break
        if not tool_name:
            for name, tool in self._tools.items():
                if "cart" in (tool.description or "").lower():
                    tool_name = name
                    break

        budget = settings.zomato.max_budget_inr if settings.zomato else 250

        if tool_name:
            raw = await self._call_tool(tool_name, {
                "restaurant_id": restaurant.restaurant_id,
                "item_id": item.item_id,
            })
            net_total = float(raw.get("grand_total", raw.get("net_total", item.base_price * 1.13)))
            delivery_fee = float(raw.get("delivery_fee", 0))
            platform_fee = float(raw.get("platform_fee", 8))
            gst = float(raw.get("gst", round(item.base_price * 0.05, 2)))
            cart_id = str(raw.get("cart_id", f"cart_{restaurant.restaurant_id}_{item.item_id}"))
        else:
            # Estimate fees if no cart tool
            delivery_fee = 0.0 if item.base_price >= 149 else 30.0
            platform_fee = 8.0
            gst = round(item.base_price * 0.05, 2)
            net_total = round(item.base_price + delivery_fee + platform_fee + gst, 2)
            cart_id = f"cart_{restaurant.restaurant_id}_{item.item_id}"

        within_budget = net_total <= budget
        result = CartSimulationResult(
            cart_id=cart_id,
            restaurant_id=restaurant.restaurant_id,
            item_id=item.item_id,
            base_price=item.base_price,
            delivery_fee=delivery_fee,
            platform_fee=platform_fee,
            gst=gst,
            net_total=net_total,
            within_budget=within_budget,
        )

        if not within_budget:
            raise BudgetExceededError(net_total=net_total, budget=budget)
        return result

    async def checkout(self, cart_id: str) -> CheckoutResult:
        """Initiate checkout."""
        tool_name = None
        for candidate in ["checkout", "place_order", "order"]:
            if candidate in self._tools:
                tool_name = candidate
                break

        if tool_name:
            raw = await self._call_tool(tool_name, {"cart_id": cart_id})
            return CheckoutResult(
                order_id=str(raw.get("order_id", f"ord_{cart_id[-6:]}")),
                upi_payment_link=raw.get("payment_url", raw.get("upi_payment_link", "")),
                upi_qr_code_url=raw.get("qr_code_url"),
                amount_payable=float(raw.get("amount", raw.get("amount_payable", 0))),
                estimated_delivery_minutes=int(raw.get("estimated_delivery_minutes", 30)),
            )
        else:
            raise ZomatoServerError("No checkout tool available on Zomato MCP")

    async def list_available_tools(self) -> list[dict]:
        """Return all available MCP tools (for debugging)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "params": list((t.inputSchema or {}).get("properties", {}).keys()),
            }
            for t in self._tools.values()
        ]


# ── CLI: One-time auth + tool discovery ──────────────────────────────────────

async def _run_auth():
    """Interactive OAuth flow — run once to save tokens."""
    print("Connecting to Zomato MCP server for authentication...")
    async with RealZomatoMCPClient() as client:
        tools = await client.list_available_tools()
        print(f"\nAuthenticated! Available tools ({len(tools)}):")
        for t in tools:
            print(f"  {t['name']}: {t['description']}")
            if t['params']:
                print(f"    params: {t['params']}")
        print(f"\nTokens saved to {TOKEN_FILE}")


if __name__ == "__main__":
    if "--auth" in sys.argv:
        asyncio.run(_run_auth())
    else:
        print("Usage: python -m autolunch.services.zomato.real_mcp_client --auth")
        print("  Run with --auth to complete Zomato OAuth login")
