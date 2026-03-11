"""
AutoLunch — Local HTTP API Server

n8n v2 removed the executeCommand node for security reasons.
This FastAPI server acts as a local bridge — n8n calls it via
HTTP Request nodes instead of shell commands.

Runs on: http://localhost:8100
Start: source .venv/bin/activate && uvicorn autolunch.api:app --port 8100

Endpoints:
  POST /decide           → Run LLM decision engine
  POST /checkout         → Trigger Zomato checkout
  POST /reject           → Record rejection + re-decide
  GET  /health           → Health check
"""
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

from autolunch.core.logging import setup_logging
from autolunch.core.exceptions import (
    MaxRetriesExceededError,
    ZomatoNoResultsError,
    AutoLunchError,
)
from autolunch.services.llm.engine import LLMDecisionEngine

setup_logging()
app = FastAPI(title="AutoLunch API", version="1.0")


class RejectRequest(BaseModel):
    restaurant_name: str
    item_name: str
    cart_id: str
    net_total: float
    reason: str


class CheckoutRequest(BaseModel):
    cart_id: str


@app.get("/health")
async def health():
    return {"status": "ok", "service": "autolunch-api"}


@app.post("/decide")
async def decide(constraints: list[str] | None = None):
    """Run LLM decision engine → return validated lunch pick."""
    engine = LLMDecisionEngine()
    try:
        result = await engine.decide(extra_constraints=constraints)
        return {
            "status": "ok",
            "restaurant_name": result.decision.restaurant_name,
            "restaurant_id": result.decision.restaurant_id,
            "item_name": result.decision.item_name,
            "item_id": result.decision.item_id,
            "base_price": result.cart.base_price,
            "net_total": result.cart.net_total,
            "delivery_fee": result.cart.delivery_fee,
            "platform_fee": result.cart.platform_fee,
            "gst": result.cart.gst,
            "cart_id": result.cart.cart_id,
            "reasoning": result.decision.reasoning,
            "confidence": result.decision.confidence,
            "distance_km": result.restaurant.distance_km,
            "delivery_minutes": result.restaurant.delivery_time_minutes,
            "rating": result.restaurant.rating,
            "review_count": result.restaurant.review_count,
        }
    except MaxRetriesExceededError:
        return JSONResponse(status_code=422, content={
            "status": "error",
            "error_type": "budget_retry_exceeded",
            "message": "Couldn't find a within-budget meal after 3 attempts. Order manually today.",
        })
    except ZomatoNoResultsError:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "error_type": "no_restaurants",
            "message": "No restaurants found matching your filters near Miraya Rose.",
        })
    except AutoLunchError as e:
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error_type": type(e).__name__,
            "message": e.message,
        })


@app.post("/reject")
async def reject(body: RejectRequest):
    """Record rejection + immediately decide again with constraint."""
    from autolunch.models.restaurant import LLMOrderDecision, CartSimulationResult, Restaurant, MenuItem
    from autolunch.services.llm.engine import DecisionResult

    engine = LLMDecisionEngine()

    # Build minimal mock result for rejection recording
    mock_result = DecisionResult(
        decision=LLMOrderDecision(
            restaurant_name=body.restaurant_name, restaurant_id="",
            item_name=body.item_name, item_id="",
            base_price=0, estimated_net_total=body.net_total,
            reasoning="", confidence=0,
        ),
        cart=CartSimulationResult(
            cart_id=body.cart_id, restaurant_id="", item_id="",
            base_price=0, delivery_fee=0, platform_fee=0,
            gst=0, net_total=body.net_total, within_budget=True,
        ),
        restaurant=Restaurant(restaurant_id="", name=body.restaurant_name, cuisine_types=[], rating=0, distance_km=0, delivery_time_minutes=0),
        item=MenuItem(item_id="", name=body.item_name, base_price=0, is_veg=True),
    )
    await engine.record_rejection(mock_result, body.reason)

    # Re-decide with rejection constraint injected
    constraint = f"User just rejected '{body.item_name}' from '{body.restaurant_name}': \"{body.reason}\". Do not suggest this again today."
    return await decide(constraints=[constraint])


@app.post("/checkout")
async def checkout(body: CheckoutRequest):
    """Trigger Zomato MCP checkout for a given cart ID."""
    from autolunch.config.settings import settings
    from autolunch.services.zomato.client import ZomatoMCPClient

    async with ZomatoMCPClient() as zomato:
        result = await zomato.checkout(body.cart_id)
        return {
            "status": "ok",
            "order_id": result.order_id,
            "upi_payment_link": result.upi_payment_link,
            "upi_qr_code_url": result.upi_qr_code_url,
            "amount": result.amount_payable,
            "estimated_delivery_minutes": result.estimated_delivery_minutes,
        }
