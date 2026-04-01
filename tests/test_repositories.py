from __future__ import annotations
"""Tests for repository layer."""
import json
import pytest
from pathlib import Path
from datetime import date

from autolunch.models.preferences import UserPreferences
from autolunch.models.memory import AgentMemory, PastOrder, Rejection, LearnedBlock, OrderStatus
from autolunch.repositories.preferences_repo import PreferencesRepository
from autolunch.repositories.memory_repo import MemoryRepository
from autolunch.core.exceptions import ConfigurationError


class TestPreferencesRepository:
    def test_load_valid(self, tmp_path, sample_prefs):
        f = tmp_path / "preferences.json"
        f.write_text(sample_prefs.model_dump_json(indent=2))
        repo = PreferencesRepository(f)
        loaded = repo.load()
        assert loaded.diet_type == sample_prefs.diet_type
        assert loaded.max_net_budget_inr == 250

    def test_load_missing_file(self, tmp_path):
        repo = PreferencesRepository(tmp_path / "nonexistent.json")
        with pytest.raises(ConfigurationError):
            repo.load()

    def test_load_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json at all")
        repo = PreferencesRepository(f)
        with pytest.raises(ConfigurationError):
            repo.load()

    def test_save_and_reload(self, tmp_path, sample_prefs):
        f = tmp_path / "prefs.json"
        repo = PreferencesRepository(f)
        repo.save(sample_prefs)
        loaded = repo.load()
        assert loaded.spice_tolerance == sample_prefs.spice_tolerance


class TestMemoryRepository:
    def test_load_empty(self, tmp_path):
        repo = MemoryRepository(tmp_path / "memory.json")
        mem = repo.load()
        assert mem.past_orders == []
        assert mem.rejections == []

    def test_append_order(self, tmp_path):
        repo = MemoryRepository(tmp_path / "memory.json")
        order = PastOrder(
            order_date=date.today(),
            restaurant_name="Test",
            restaurant_id="1",
            item_name="Test Item",
            item_id="i1",
            base_price=100,
            net_total=120,
            status=OrderStatus.PLACED,
        )
        repo.append_order(order)
        mem = repo.load()
        assert len(mem.past_orders) == 1
        assert mem.past_orders[0].restaurant_name == "Test"

    def test_append_rejection(self, tmp_path):
        repo = MemoryRepository(tmp_path / "memory.json")
        rejection = Rejection(
            rejection_date=date.today(),
            suggested_restaurant="R1",
            suggested_item="I1",
            suggested_net_total=150,
            user_reason="Not hungry",
        )
        repo.append_rejection(rejection)
        mem = repo.load()
        assert len(mem.rejections) == 1
        assert mem.rejections[0].user_reason == "Not hungry"

    def test_append_learned_block(self, tmp_path):
        repo = MemoryRepository(tmp_path / "memory.json")
        block = LearnedBlock(
            blocked_entity="Bad Restaurant",
            block_type="restaurant",
            reason_summary="Rejected 3x",
            created_on=date.today(),
        )
        repo.append_learned_block(block)
        mem = repo.load()
        assert len(mem.learned_blocks) == 1
        assert mem.learned_blocks[0].blocked_entity == "Bad Restaurant"

    def test_corrupted_file_soft_fail(self, tmp_path):
        f = tmp_path / "memory.json"
        f.write_text("corrupted garbage")
        repo = MemoryRepository(f)
        mem = repo.load()  # Should not raise
        assert mem.past_orders == []
