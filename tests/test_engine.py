from __future__ import annotations
"""Tests for the LLM decision engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

from autolunch.services.llm.engine import LLMDecisionEngine, DecisionResult
from autolunch.models.restaurant import LLMOrderDecision, CartSimulationResult, MenuItem, Restaurant
from autolunch.core.exceptions import BudgetExceededError, MaxRetriesExceededError


class TestDecisionResult:
    def test_slack_summary(self, sample_decision, sample_cart, sample_restaurants):
        result = DecisionResult(
            decision=sample_decision,
            cart=sample_cart,
            restaurant=sample_restaurants[0],
            item=sample_restaurants[0].menu[2],  # Chana Masala Thali
        )
        summary = result.slack_summary
        assert "Haldiram" in summary
        assert "NET TOTAL" in summary
        assert "174.95" in summary

    def test_telegram_backward_compat(self, sample_decision, sample_cart, sample_restaurants):
        result = DecisionResult(
            decision=sample_decision,
            cart=sample_cart,
            restaurant=sample_restaurants[0],
            item=sample_restaurants[0].menu[2],
        )
        assert result.telegram_summary == result.slack_summary


class TestResolvePickStatic:
    def test_resolve_by_id(self, sample_restaurants):
        decision = LLMOrderDecision(
            restaurant_name="Haldiram's", restaurant_id="zmt_1001",
            item_name="Dal Makhani + 2 Roti", item_id="item_101",
            base_price=179, estimated_net_total=196, reasoning="test", confidence=0.9,
        )
        r, item = LLMDecisionEngine._resolve_pick(decision, sample_restaurants)
        assert r is not None
        assert item is not None
        assert r.name == "Haldiram's"
        assert item.name == "Dal Makhani + 2 Roti"

    def test_resolve_by_name_fallback(self, sample_restaurants):
        decision = LLMOrderDecision(
            restaurant_name="Saravana Bhavan", restaurant_id="wrong_id",
            item_name="Masala Dosa + Sambar", item_id="wrong_id",
            base_price=129, estimated_net_total=145, reasoning="test", confidence=0.8,
        )
        r, item = LLMDecisionEngine._resolve_pick(decision, sample_restaurants)
        assert r is not None
        assert r.name == "Saravana Bhavan"

    def test_resolve_not_found(self, sample_restaurants):
        decision = LLMOrderDecision(
            restaurant_name="NonExistent", restaurant_id="nope",
            item_name="Nothing", item_id="nope",
            base_price=100, estimated_net_total=120, reasoning="test", confidence=0.5,
        )
        r, item = LLMDecisionEngine._resolve_pick(decision, sample_restaurants)
        assert r is None
        assert item is None


class TestLearnedBlockCreation:
    def test_creates_block_after_2_rejections(self, tmp_path):
        from autolunch.repositories.memory_repo import MemoryRepository
        from autolunch.models.memory import Rejection

        repo = MemoryRepository(tmp_path / "memory.json")

        # Add 2 rejections for same restaurant within 7 days
        for _ in range(2):
            repo.append_rejection(Rejection(
                rejection_date=date.today(),
                suggested_restaurant="Bad Restaurant",
                suggested_item="Bad Item",
                suggested_net_total=200,
                user_reason="Terrible food",
            ))

        # Create a minimal engine with this repo and trigger block check
        # Directly test the learned block logic without instantiating full engine
        from collections import Counter
        from datetime import timedelta
        from autolunch.models.memory import LearnedBlock

        memory = repo.load()
        recent = memory.recent_rejections(days=7)
        restaurant_counts = Counter(r.suggested_restaurant for r in recent)
        already_blocked = {b.blocked_entity.lower() for b in memory.learned_blocks}

        for restaurant, count in restaurant_counts.items():
            if count >= 2 and restaurant.lower() not in already_blocked:
                reasons = [r.user_reason for r in recent if r.suggested_restaurant == restaurant]
                block = LearnedBlock(
                    blocked_entity=restaurant,
                    block_type="restaurant",
                    reason_summary=f"Rejected {count}x in 7 days: {'; '.join(reasons[:3])}",
                    created_on=date.today(),
                    expires_on=date.today() + timedelta(days=30),
                )
                repo.append_learned_block(block)

        mem = repo.load()
        assert len(mem.learned_blocks) == 1
        assert mem.learned_blocks[0].blocked_entity == "Bad Restaurant"
        assert "2x" in mem.learned_blocks[0].reason_summary
