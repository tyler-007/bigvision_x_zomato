from __future__ import annotations
"""Tests for Zomato mock client against mock server."""
import pytest
import asyncio

from autolunch.services.zomato.client import ZomatoMCPClient
from autolunch.core.exceptions import BudgetExceededError, ZomatoNoResultsError


@pytest.fixture
def mock_server_running():
    """Check if mock server is running on port 3000."""
    import httpx
    try:
        r = httpx.get("http://localhost:3000/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.asyncio
class TestZomatoMockClient:
    async def test_search_restaurants(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            assert len(restaurants) >= 1
            # All should pass filters
            for r in restaurants:
                assert r.rating >= sample_prefs.min_restaurant_rating
                assert r.review_count >= sample_prefs.min_review_count
                assert r.distance_km <= sample_prefs.max_distance_km

    async def test_filters_low_reviews(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            names = [r.name for r in restaurants]
            assert "New Sketch Restaurant" not in names  # 450 reviews

    async def test_filters_far_restaurants(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            names = [r.name for r in restaurants]
            assert "Faraway Place" not in names  # 9.5km

    async def test_get_menu(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            r = await client.get_menu(restaurants[0])
            assert len(r.menu) >= 1
            assert r.menu[0].name  # Has a name
            assert r.menu[0].base_price > 0

    async def test_cart_simulation(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            r = await client.get_menu(restaurants[0])
            cart = await client.simulate_cart(r, r.menu[0])
            assert cart.net_total > cart.base_price  # Fees added
            assert cart.cart_id
            assert cart.within_budget

    async def test_checkout(self, sample_prefs, mock_server_running):
        if not mock_server_running:
            pytest.skip("Mock server not running on :3000")
        async with ZomatoMCPClient() as client:
            restaurants = await client.search_restaurants(sample_prefs)
            r = await client.get_menu(restaurants[0])
            cart = await client.simulate_cart(r, r.menu[0])
            result = await client.checkout(cart.cart_id)
            assert result.order_id
            assert result.upi_payment_link
            assert result.amount_payable > 0
