from __future__ import annotations
"""
AutoLunch — Local HTTP API Server

Self-contained server handling:
  - LLM decision engine
  - Slack HITL (interactive buttons for approve/reject)
  - Zomato checkout
  - Full approval/rejection loop

Runs on: http://localhost:8100
Start: source .venv/bin/activate && uvicorn autolunch.api:app --port 8100

Endpoints:
  POST /decide           → Run LLM decision engine
  POST /checkout         → Trigger Zomato checkout
  POST /reject           → Record rejection + re-decide
  POST /slack/interact   → Slack interactive button handler
  POST /trigger          → Manually trigger the full flow (decide → Slack)
  GET  /health           → Health check
"""
import asyncio
import json
from urllib.parse import parse_qs, unquote
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
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

# Track rejection count per day (in-memory, resets on restart)
_daily_state: dict = {"date": "", "rejections": 0}
MAX_REJECTIONS = 2


class RejectRequest(BaseModel):
    restaurant_name: str
    item_name: str
    cart_id: str
    net_total: float
    reason: str


class CheckoutRequest(BaseModel):
    cart_id: str
    # Order context for memory logging (sent by n8n from the decision result)
    restaurant_name: str = ""
    restaurant_id: str = ""
    item_name: str = ""
    item_id: str = ""
    base_price: float = 0


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
    """Trigger Zomato MCP checkout for a given cart ID and log order to memory."""
    from autolunch.config.settings import settings
    from autolunch.services.zomato import get_zomato_client
    from autolunch.repositories import get_memory_repository
    from autolunch.models.memory import PastOrder, OrderStatus
    from datetime import date

    async with get_zomato_client() as zomato:
        result = await zomato.checkout(body.cart_id)

        # Record the order in memory so repeat-aversion works
        if body.restaurant_name and body.item_name:
            memory_repo = get_memory_repository(settings.data_dir)
            memory_repo.append_order(PastOrder(
                order_date=date.today(),
                restaurant_name=body.restaurant_name,
                restaurant_id=body.restaurant_id or "",
                item_name=body.item_name,
                item_id=body.item_id or "",
                base_price=body.base_price or 0,
                net_total=result.amount_payable,
                status=OrderStatus.PLACED,
            ))
            logger.info("Order recorded to memory", restaurant=body.restaurant_name, item=body.item_name)

        return {
            "status": "ok",
            "order_id": result.order_id,
            "upi_payment_link": result.upi_payment_link,
            "upi_qr_code_url": result.upi_qr_code_url,
            "amount": result.amount_payable,
            "estimated_delivery_minutes": result.estimated_delivery_minutes,
        }


# ── Slack HITL Endpoints ─────────────────────────────────────────────────────

async def _send_slack_suggestion(decision_data: dict) -> None:
    """Send a Block Kit suggestion to Slack with Approve/Reject buttons."""
    import httpx
    from autolunch.config.settings import settings
    if not settings.slack:
        logger.warning("Slack not configured, skipping message")
        return

    d = decision_data
    net = round(d['net_total'], 2)
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🍱 AutoLunch — Today's Suggestion"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Item*\n{d['item_name']}"},
            {"type": "mrkdwn", "text": f"*Restaurant*\n{d['restaurant_name']}"},
            {"type": "mrkdwn", "text": f"*Rating*\n{d['rating']}⭐ ({d['review_count']:,} reviews)"},
            {"type": "mrkdwn", "text": f"*Distance*\n{d['distance_km']}km · ~{d['delivery_minutes']}min"},
        ]},
        {"type": "divider"},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Base Price*\n₹{d['base_price']:.2f}"},
            {"type": "mrkdwn", "text": f"*Delivery*\n₹{d['delivery_fee']:.2f}"},
            {"type": "mrkdwn", "text": f"*Platform + GST*\n₹{d['platform_fee'] + d['gst']:.2f}"},
            {"type": "mrkdwn", "text": f"*NET TOTAL*\n*₹{net:.2f}* ✅"},
        ]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"🤖 _{d['reasoning']}_"}
        ]},
        {"type": "divider"},
        {"type": "actions", "block_id": "lunch_decision", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "✅ Yes, Order This!"},
             "style": "primary",
             "value": f"approve|{d['cart_id']}|{d['restaurant_name']}|{d['restaurant_id']}|{d['item_name']}|{d['item_id']}|{d['base_price']}|{d['net_total']}",
             "action_id": "autolunch_approve"},
            {"type": "button", "text": {"type": "plain_text", "text": "❌ No, Suggest Again"},
             "style": "danger",
             "value": f"reject|{d['cart_id']}|{d['restaurant_name']}|{d['restaurant_id']}|{d['item_name']}|{d['item_id']}|{d['base_price']}|{d['net_total']}",
             "action_id": "autolunch_reject"},
        ]}
    ]

    async with httpx.AsyncClient() as client:
        r = await client.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack.bot_token}", "Content-Type": "application/json"},
            json={"channel": settings.slack.channel_id, "blocks": blocks,
                  "text": f"🍱 {d['item_name']} from {d['restaurant_name']} — ₹{net:.2f}"})
        result = r.json()
        if result.get("ok"):
            logger.info("Slack suggestion sent", ts=result["ts"])
        else:
            logger.error("Slack send failed", error=result.get("error"))


async def _send_slack_message(text: str, blocks: list | None = None) -> None:
    """Send a simple message to the Slack channel."""
    import httpx
    from autolunch.config.settings import settings
    if not settings.slack:
        return
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack.bot_token}", "Content-Type": "application/json"},
            json={"channel": settings.slack.channel_id, "text": text, **({"blocks": blocks} if blocks else {})})


@app.post("/trigger")
async def trigger():
    """Manually trigger the full flow: decide → send to Slack."""
    result = await decide()
    if isinstance(result, JSONResponse):
        return result
    if result.get("status") == "ok":
        await _send_slack_suggestion(result)
        return {"status": "ok", "message": "Suggestion sent to Slack", **result}
    return result


@app.post("/slack/interact")
async def slack_interact(request: Request):
    """
    Handle Slack interactive button clicks (Approve/Reject).
    Slack posts form-encoded payload to this endpoint.
    Set this URL in Slack App → Interactivity → Request URL.
    """
    from datetime import date

    # Slack sends form-encoded body with 'payload' field containing JSON
    body = await request.body()
    body_str = body.decode()
    if body_str.startswith("payload="):
        payload_str = unquote(body_str.replace("payload=", "", 1))
        payload = json.loads(payload_str)
    else:
        payload = json.loads(body_str)

    action = payload["actions"][0]
    parts = action["value"].split("|")
    # Format: action_type|cart_id|restaurant_name|restaurant_id|item_name|item_id|base_price|net_total
    action_type = parts[0]
    cart_id = parts[1] if len(parts) > 1 else ""
    restaurant_name = parts[2] if len(parts) > 2 else ""
    restaurant_id = parts[3] if len(parts) > 3 else ""
    item_name = parts[4] if len(parts) > 4 else ""
    item_id = parts[5] if len(parts) > 5 else ""
    base_price = float(parts[6]) if len(parts) > 6 else 0
    net_total = float(parts[7]) if len(parts) > 7 else 0

    logger.info(f"Slack action: {action_type}", item=item_name, restaurant=restaurant_name)

    # Track daily rejections
    today = date.today().isoformat()
    if _daily_state["date"] != today:
        _daily_state["date"] = today
        _daily_state["rejections"] = 0

    if action_type == "approve":
        # Checkout and send UPI link
        asyncio.create_task(_handle_approve(cart_id, restaurant_name, restaurant_id, item_name, item_id, base_price))
        return Response(status_code=200)

    elif action_type == "reject":
        _daily_state["rejections"] += 1

        if _daily_state["rejections"] >= MAX_REJECTIONS:
            asyncio.create_task(_send_slack_message(
                "✋ *AutoLunch: Order Manually Today*\n\n"
                "You've passed on 2 suggestions. No problem — today's your call!\n"
                "Tap here to open Zomato: https://www.zomato.com/"
            ))
            return Response(status_code=200)

        # Reject and re-suggest
        asyncio.create_task(_handle_reject(restaurant_name, item_name, cart_id, net_total))
        return Response(status_code=200)

    return Response(status_code=200)


async def _handle_approve(cart_id: str, restaurant_name: str, restaurant_id: str, item_name: str, item_id: str, base_price: float) -> None:
    """Process approval: checkout + send UPI link to Slack."""
    try:
        result = await checkout(CheckoutRequest(
            cart_id=cart_id, restaurant_name=restaurant_name,
            restaurant_id=restaurant_id, item_name=item_name,
            item_id=item_id, base_price=base_price,
        ))
        data = result if isinstance(result, dict) else result.body
        if isinstance(data, bytes):
            data = json.loads(data)

        order_id = data.get("order_id", "")
        amount = data.get("amount", 0)
        upi_link = data.get("upi_payment_link", "https://www.zomato.com/")

        if order_id.startswith("pending_"):
            # Checkout couldn't complete — cart is created, user pays via shareable link
            cart_link = upi_link  # This is the shareable_link from Zomato
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "🍱 Cart Ready — Tap to Pay!"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"Your cart is ready with the best promo applied!\n\n"
                            f"Tap the button to open your cart in Zomato and complete payment:"},
                 "accessory": {"type": "button", "text": {"type": "plain_text", "text": "🛒 Open My Cart"},
                               "url": cart_link, "style": "primary", "action_id": "open_cart"}},
            ]
            await _send_slack_message(f"🍱 Cart ready — tap to open in Zomato!", blocks)
        else:
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": "💳 Complete Your Payment"}},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"*Order ID:* `{order_id}`\n"
                            f"*Amount:* ₹{amount}\n"
                            f"*Estimated delivery:* ~{data.get('estimated_delivery_minutes', 30)} minutes\n\n"
                            f"Tap below to pay via UPI:"},
                 "accessory": {"type": "button", "text": {"type": "plain_text", "text": "💰 Pay Now"},
                               "url": upi_link, "style": "primary", "action_id": "pay_upi"}},
                {"type": "context", "elements": [
                    {"type": "mrkdwn", "text": "🔒 Secure UPI payment — your PIN is never shared with AutoLunch"}
                ]},
            ]
            await _send_slack_message(f"💳 Pay ₹{amount} for your AutoLunch order", blocks)
    except Exception as e:
        logger.error(f"Checkout failed: {e}")
        await _send_slack_message(f"⚠️ *Checkout failed:* {e}\n\nPlease order manually: https://www.zomato.com/")


async def _handle_reject(restaurant_name: str, item_name: str, cart_id: str, net_total: float) -> None:
    """Process rejection: record + re-decide + send new suggestion."""
    try:
        await _send_slack_message("Got it! 👎 Finding something else...")
        result = await reject(RejectRequest(
            restaurant_name=restaurant_name, item_name=item_name,
            cart_id=cart_id, net_total=net_total,
            reason="User declined this suggestion",
        ))
        data = result if isinstance(result, dict) else json.loads(result.body)
        if data.get("status") == "ok":
            await _send_slack_suggestion(data)
        else:
            await _send_slack_message("⚠️ Couldn't find another option. Please order manually: https://www.zomato.com/")
    except Exception as e:
        logger.error(f"Re-decide failed: {e}")
        await _send_slack_message(f"⚠️ *Error finding alternative:* {e}")
