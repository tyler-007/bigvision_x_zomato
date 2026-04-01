from __future__ import annotations
"""Shared fixtures for all tests."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolunch.models.preferences import UserPreferences
from autolunch.models.memory import AgentMemory, Rejection, PastOrder, OrderStatus
from autolunch.models.restaurant import (
    Restaurant, MenuItem, CartSimulationResult, LLMOrderDecision, CheckoutResult,
)


@pytest.fixture
def sample_prefs() -> UserPreferences:
    return UserPreferences(
        diet_type="vegetarian",
        spice_tolerance=3,
        preferred_meal_styles=["roti_based", "rice_bowl"],
        avoid_repeat_days=3,
        min_restaurant_rating=4.0,
        min_review_count=1000,
        max_net_budget_inr=250,
        max_distance_km=7,
        guardrails={
            "blocked_restaurants": ["Bad Place"],
            "blocked_cuisines": ["fast_food"],
            "preferred_cuisines": ["north_indian", "south_indian"],
        },
    )


@pytest.fixture
def sample_memory() -> AgentMemory:
    return AgentMemory(
        past_orders=[
            PastOrder(
                order_date="2026-03-30",
                restaurant_name="Haldiram's",
                restaurant_id="zmt_1001",
                item_name="Dal Makhani",
                item_id="item_101",
                base_price=179.0,
                net_total=195.95,
                status=OrderStatus.PLACED,
            )
        ],
        rejections=[
            Rejection(
                rejection_date="2026-03-31",
                suggested_restaurant="Bikanervala",
                suggested_item="Rajma Chawal",
                suggested_net_total=165.0,
                user_reason="Too heavy today",
                llm_extracted_constraint="Avoid heavy dishes",
            )
        ],
    )


@pytest.fixture
def sample_restaurants() -> list[Restaurant]:
    return [
        Restaurant(
            restaurant_id="zmt_1001",
            name="Haldiram's",
            cuisine_types=["north_indian"],
            rating=4.3,
            review_count=8200,
            distance_km=1.8,
            delivery_time_minutes=28,
            menu=[
                MenuItem(item_id="item_101", name="Dal Makhani + 2 Roti", base_price=179.0, is_veg=True, category="Main Course"),
                MenuItem(item_id="item_102", name="Paneer Butter Masala + Rice", base_price=209.0, is_veg=True, category="Main Course"),
                MenuItem(item_id="item_103", name="Chana Masala Thali", base_price=159.0, is_veg=True, category="Thali"),
            ],
        ),
        Restaurant(
            restaurant_id="zmt_1002",
            name="Saravana Bhavan",
            cuisine_types=["south_indian"],
            rating=4.5,
            review_count=12400,
            distance_km=4.2,
            delivery_time_minutes=35,
            menu=[
                MenuItem(item_id="item_201", name="Meals (Full South Indian Thali)", base_price=189.0, is_veg=True, category="Meals"),
                MenuItem(item_id="item_202", name="Masala Dosa + Sambar", base_price=129.0, is_veg=True, category="Dosa"),
            ],
        ),
    ]


@pytest.fixture
def sample_cart() -> CartSimulationResult:
    return CartSimulationResult(
        cart_id="cart_zmt_1001_item_103",
        restaurant_id="zmt_1001",
        item_id="item_103",
        base_price=159.0,
        delivery_fee=0.0,
        platform_fee=8.0,
        gst=7.95,
        net_total=174.95,
        within_budget=True,
        shareable_link="https://link.zomato.com/xqzv/carts?id=test123",
        promo_code="TESTPROMO",
        promo_discount=20.0,
    )


@pytest.fixture
def sample_decision() -> LLMOrderDecision:
    return LLMOrderDecision(
        restaurant_name="Haldiram's",
        restaurant_id="zmt_1001",
        item_name="Chana Masala Thali",
        item_id="item_103",
        base_price=159.0,
        estimated_net_total=175.0,
        reasoning="Affordable thali within budget",
        confidence=0.85,
    )
