from __future__ import annotations
"""Tests for the FastAPI endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from autolunch.api import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["service"] == "autolunch-api"


@pytest.mark.asyncio
class TestHistoryEndpoint:
    async def test_history_memory(self, client):
        r = await client.get("/history?source=memory")
        assert r.status_code == 200
        data = r.json()
        assert "orders" in data
        assert "count" in data

    async def test_history_sheet(self, client):
        r = await client.get("/history?source=sheet")
        assert r.status_code == 200


@pytest.mark.asyncio
class TestSlackInteract:
    async def test_approve_action(self, client):
        payload = '{"actions":[{"value":"approve|cart123|RestName|rid1|ItemName|iid1|149|175"}],"user":{"id":"U1"},"channel":{"id":"C1"},"message":{"ts":"1"}}'
        import urllib.parse
        encoded = urllib.parse.quote(payload)
        r = await client.post("/slack/interact", content=f"payload={encoded}",
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 200

    async def test_reject_action(self, client):
        payload = '{"actions":[{"value":"reject|cart123|RestName|rid1|ItemName|iid1|149|175"}],"user":{"id":"U1"},"channel":{"id":"C1"},"message":{"ts":"1"}}'
        import urllib.parse
        encoded = urllib.parse.quote(payload)
        r = await client.post("/slack/interact", content=f"payload={encoded}",
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 200
