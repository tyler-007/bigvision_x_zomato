from __future__ import annotations
"""Tests for Pydantic data models."""
import pytest
from datetime import date, timedelta

from autolunch.models.preferences import UserPreferences, DietType, MealStyle, Guardrails
from autolunch.models.memory import AgentMemory, PastOrder, Rejection, LearnedBlock, OrderStatus
from autolunch.models.restaurant import Restaurant, MenuItem, CartSimulationResult, LLMOrderDecision


class TestUserPreferences:
    def test_valid_prefs(self, sample_prefs):
        assert sample_prefs.diet_type == DietType.VEGETARIAN
        assert sample_prefs.spice_tolerance == 3
        assert sample_prefs.max_net_budget_inr == 250

    def test_spice_bounds(self):
        with pytest.raises(Exception):
            UserPreferences(diet_type="vegetarian", spice_tolerance=6, preferred_meal_styles=["rice_bowl"])
        with pytest.raises(Exception):
            UserPreferences(diet_type="vegetarian", spice_tolerance=0, preferred_meal_styles=["rice_bowl"])

    def test_budget_bounds(self):
        p = UserPreferences(diet_type="vegetarian", spice_tolerance=3, preferred_meal_styles=["rice_bowl"], max_net_budget_inr=500)
        assert p.max_net_budget_inr == 500
        with pytest.raises(Exception):
            UserPreferences(diet_type="vegetarian", spice_tolerance=3, preferred_meal_styles=["rice_bowl"], max_net_budget_inr=10)

    def test_guardrails_defaults(self):
        p = UserPreferences(diet_type="vegetarian", spice_tolerance=3, preferred_meal_styles=["rice_bowl"])
        assert p.guardrails.blocked_restaurants == []
        assert p.guardrails.blocked_cuisines == []


class TestAgentMemory:
    def test_empty_memory(self):
        m = AgentMemory()
        assert m.past_orders == []
        assert m.rejections == []
        assert m.learned_blocks == []

    def test_recent_orders(self, sample_memory):
        recent = sample_memory.recent_orders(days=7)
        assert len(recent) == 1
        assert recent[0].restaurant_name == "Haldiram's"

    def test_recent_orders_expired(self, sample_memory):
        # Order from 30 days ago shouldn't show up in 7-day window
        sample_memory.past_orders[0].order_date = date.today() - timedelta(days=30)
        assert len(sample_memory.recent_orders(days=7)) == 0

    def test_recent_rejections(self, sample_memory):
        recent = sample_memory.recent_rejections(days=7)
        assert len(recent) == 1
        assert recent[0].suggested_restaurant == "Bikanervala"

    def test_todays_rejection_count(self):
        m = AgentMemory(rejections=[
            Rejection(rejection_date=date.today(), suggested_restaurant="R1", suggested_item="I1", suggested_net_total=100, user_reason="no"),
            Rejection(rejection_date=date.today(), suggested_restaurant="R2", suggested_item="I2", suggested_net_total=100, user_reason="no"),
            Rejection(rejection_date=date.today() - timedelta(days=1), suggested_restaurant="R3", suggested_item="I3", suggested_net_total=100, user_reason="no"),
        ])
        assert m.todays_rejection_count() == 2


class TestRestaurant:
    def test_is_trustworthy(self):
        r = Restaurant(restaurant_id="1", name="Test", cuisine_types=[], rating=4.5, review_count=5000, distance_km=2, delivery_time_minutes=30)
        assert r.is_trustworthy(min_reviews=1000) is True
        assert r.is_trustworthy(min_reviews=10000) is False

    def test_affordable_items(self, sample_restaurants):
        r = sample_restaurants[0]  # Haldiram's
        affordable = r.affordable_items(max_base_price=170)
        assert len(affordable) == 1
        assert affordable[0].name == "Chana Masala Thali"

    def test_affordable_items_all(self, sample_restaurants):
        r = sample_restaurants[0]
        affordable = r.affordable_items(max_base_price=300)
        assert len(affordable) == 3


class TestCartSimulationResult:
    def test_within_budget(self, sample_cart):
        assert sample_cart.within_budget is True
        assert sample_cart.net_total == 174.95

    def test_promo_fields(self, sample_cart):
        assert sample_cart.promo_code == "TESTPROMO"
        assert sample_cart.promo_discount == 20.0
        assert sample_cart.shareable_link.startswith("https://")


class TestLLMOrderDecision:
    def test_valid_decision(self, sample_decision):
        assert sample_decision.confidence == 0.85
        assert sample_decision.restaurant_name == "Haldiram's"

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            LLMOrderDecision(
                restaurant_name="X", restaurant_id="1", item_name="Y", item_id="2",
                base_price=100, estimated_net_total=120, reasoning="test", confidence=1.5,
            )
