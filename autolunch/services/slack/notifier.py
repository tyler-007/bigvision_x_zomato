"""
AutoLunch — Slack Notification Service

Sends interactive Slack messages with Yes/No buttons for HITL approval.
Uses Slack's Block Kit for rich message formatting.

How it works:
  1. `send_suggestion()` posts a rich Block Kit message to your DM
     with ✅ Approve and ❌ Reject buttons
  2. When you click a button, Slack sends an interactive payload to
     the n8n webhook URL (configured in your Slack App's Interactivity settings)
  3. n8n routes YES → checkout, NO → record rejection → re-suggest

Setup required (one-time):
  1. Create Slack App at https://api.slack.com/apps
  2. Add Bot Token Scopes: chat:write, im:write, im:history
  3. Enable Interactivity → set Request URL to your n8n webhook
  4. Install app to workspace → copy Bot Token (xoxb-...) to .env
  5. Get your DM Channel ID (open your DM with the bot → copy ID from URL)
"""
import hashlib
import hmac
import time

import httpx
from loguru import logger

from autolunch.config.settings import settings
from autolunch.core.exceptions import AutoLunchError
from autolunch.services.llm.engine import DecisionResult


class SlackError(AutoLunchError):
    """Raised on Slack API errors."""


class SlackNotifier:
    """
    Async Slack client for sending rich Block Kit messages with
    interactive Yes/No approval buttons.
    """

    API_BASE = "https://slack.com/api"

    def __init__(self) -> None:
        self._token = settings.slack.bot_token
        self._channel = settings.slack.channel_id
        self._signing_secret = settings.slack.signing_secret
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def send_suggestion(self, result: DecisionResult, callback_id: str) -> str:
        """
        Post an approval message to your Slack DM with ✅/❌ buttons.

        Args:
            result: The validated decision result from the LLM engine
            callback_id: Unique ID for this decision (used to correlate n8n webhook response)

        Returns:
            Slack message timestamp (ts) — used to update/delete the message later
        """
        blocks = self._build_suggestion_blocks(result, callback_id)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE}/chat.postMessage",
                headers=self._headers,
                json={
                    "channel": self._channel,
                    "blocks": blocks,
                    "text": f"🍱 AutoLunch: {result.decision.item_name} from {result.decision.restaurant_name} — ₹{result.cart.net_total}",
                },
                timeout=10.0,
            )

        data = response.json()
        if not data.get("ok"):
            raise SlackError(
                f"Slack API error: {data.get('error', 'unknown')}",
                context={"response": data},
            )

        ts = data["ts"]
        logger.info("Slack suggestion sent", channel=self._channel, ts=ts)
        return ts

    async def send_upi_link(self, order_id: str, upi_link: str, amount: float, delivery_minutes: int) -> None:
        """Send the UPI payment link after user approves."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "💳 Complete Your Payment"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Order ID:* `{order_id}`\n"
                        f"*Amount:* ₹{amount}\n"
                        f"*Estimated delivery:* ~{delivery_minutes} minutes\n\n"
                        f"Tap the button below to pay via UPI:"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "💰 Pay Now via UPI"},
                    "url": upi_link,
                    "style": "primary",
                    "action_id": "pay_upi",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "🔒 Secure UPI payment — your PIN is never shared with AutoLunch",
                    }
                ],
            },
        ]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.API_BASE}/chat.postMessage",
                headers=self._headers,
                json={"channel": self._channel, "blocks": blocks, "text": f"Pay ₹{amount} for your AutoLunch order"},
                timeout=10.0,
            )
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack UPI message error: {data.get('error')}", context={"response": data})
        logger.info("Slack UPI link sent", order_id=order_id, amount=amount)

    async def send_error_alert(self, message: str) -> None:
        """Send a plain error/warning DM — used by n8n's global error handler."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.API_BASE}/chat.postMessage",
                headers=self._headers,
                json={
                    "channel": self._channel,
                    "text": f"⚠️ *AutoLunch Alert*\n\n{message}",
                },
                timeout=10.0,
            )
        logger.warning("Slack error alert sent", message=message)

    async def send_manual_order_notice(self, reason: str = "Max rejections reached") -> None:
        """Send the fallback 'order manually' message."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "✋ *AutoLunch: Order Manually Today*\n\n"
                        f"_{reason}_\n\n"
                        "No more suggestions for today. Tap below to open Zomato:"
                    ),
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Zomato 🍱"},
                    "url": "https://www.zomato.com/",
                    "action_id": "open_zomato",
                },
            }
        ]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.API_BASE}/chat.postMessage",
                headers=self._headers,
                json={"channel": self._channel, "blocks": blocks, "text": "Order manually today"},
                timeout=10.0,
            )
        logger.info("Manual order notice sent")

    async def ask_rejection_reason(self, ts: str) -> None:
        """
        Follow up after a rejection — ask user to type why.
        Slack's response goes back to the n8n webhook as a plain message.
        """
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.API_BASE}/chat.postMessage",
                headers=self._headers,
                json={
                    "channel": self._channel,
                    "thread_ts": ts,
                    "text": "Got it! 👎 Why didn't you want that? _(type your reason — I'll remember it for future orders)_",
                },
                timeout=10.0,
            )

    def verify_slack_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        """
        Verify that an incoming interactive payload actually came from Slack.
        Should be called in the n8n webhook node's pre-processing step.
        """
        if abs(time.time() - int(timestamp)) > 300:
            return False   # Replay attack protection
        base = f"v0:{timestamp}:{body.decode()}"
        expected = "v0=" + hmac.new(
            self._signing_secret.encode(),
            base.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Block Kit builder ─────────────────────────────────────────────────────

    @staticmethod
    def _build_suggestion_blocks(result: DecisionResult, callback_id: str) -> list[dict]:
        """Build a rich Slack Block Kit message for the approval prompt."""
        d = result.decision
        c = result.cart
        r = result.restaurant

        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🍱 AutoLunch — Today's Suggestion"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Item*\n{d.item_name}"},
                    {"type": "mrkdwn", "text": f"*Restaurant*\n{d.restaurant_name}"},
                    {"type": "mrkdwn", "text": f"*Rating*\n{r.rating}⭐ ({r.review_count:,} reviews)"},
                    {"type": "mrkdwn", "text": f"*Distance*\n{r.distance_km}km · ~{r.delivery_time_minutes}min"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Base Price*\n₹{c.base_price}"},
                    {"type": "mrkdwn", "text": f"*Delivery*\n₹{c.delivery_fee} (Gold 🥇)"},
                    {"type": "mrkdwn", "text": f"*Platform + GST*\n₹{c.platform_fee + c.gst:.2f}"},
                    {"type": "mrkdwn", "text": f"*NET TOTAL*\n*₹{c.net_total}* ✅"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"🤖 _{d.reasoning}_"},
                ],
            },
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": callback_id,
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Yes, Order This!"},
                        "style": "primary",
                        "value": f"approve|{callback_id}",
                        "action_id": "autolunch_approve",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ No, Suggest Again"},
                        "style": "danger",
                        "value": f"reject|{callback_id}",
                        "action_id": "autolunch_reject",
                    },
                ],
            },
        ]
