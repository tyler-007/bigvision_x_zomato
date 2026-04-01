from __future__ import annotations
"""Tests for LLM prompt generation."""
import pytest

from autolunch.services.llm.prompts import build_system_prompt, build_user_prompt


class TestSystemPrompt:
    def test_contains_schema(self):
        prompt = build_system_prompt()
        assert "restaurant_name" in prompt
        assert "item_name" in prompt
        assert "confidence" in prompt
        assert "JSON" in prompt

    def test_contains_rules(self):
        prompt = build_system_prompt()
        assert "NEVER" in prompt
        assert "budget" in prompt.lower()


class TestUserPrompt:
    def test_contains_preferences(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants)
        assert "VEGETARIAN" in prompt
        assert "250" in prompt
        assert "north_indian" in prompt or "south_indian" in prompt

    def test_contains_restaurants(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants)
        assert "Haldiram" in prompt
        assert "Saravana Bhavan" in prompt
        assert "Dal Makhani" in prompt

    def test_contains_recent_orders(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants)
        assert "Haldiram" in prompt  # From past orders

    def test_contains_rejections(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants)
        assert "Bikanervala" in prompt or "heavy" in prompt.lower()

    def test_extra_constraints_injected(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants, ["NO RICE DISHES"])
        assert "NO RICE DISHES" in prompt

    def test_blocked_restaurants_shown(self, sample_prefs, sample_memory, sample_restaurants):
        prompt = build_user_prompt(sample_prefs, sample_memory, sample_restaurants)
        assert "Bad Place" in prompt  # From guardrails.blocked_restaurants
