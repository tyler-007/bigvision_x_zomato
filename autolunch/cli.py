"""
AutoLunch — CLI Entrypoint

Called by n8n's "Execute Command" node at 12:45 PM.
Also usable for dry-run testing from the terminal.

Usage:
  # Normal run (called by n8n)
  python -m autolunch.cli decide

  # Dry run with mock constraints (for testing)
  python -m autolunch.cli decide --dry-run

  # Record a rejection (called by n8n on "No" webhook)
  python -m autolunch.cli reject --reason "Too heavy today"

Output is JSON on stdout — n8n parses this to build the Slack message.
"""
import asyncio
import json
import sys
from pathlib import Path

import argparse
from dotenv import load_dotenv

load_dotenv()

from loguru import logger
from autolunch.core.logging import setup_logging
from autolunch.core.exceptions import (
    AutoLunchError,
    MaxRetriesExceededError,
    ZomatoNoResultsError,
)
from autolunch.services.llm.engine import LLMDecisionEngine

setup_logging()


async def cmd_decide(dry_run: bool = False, constraints: list[str] | None = None) -> None:
    """
    Pick a lunch item. Outputs JSON to stdout for n8n to consume.
    Exit code 0 = success, 1 = budget/retry failure, 2 = no restaurants found.
    """
    engine = LLMDecisionEngine()

    # Dry-run: inject a mock constraint to test the retry path
    if dry_run:
        constraints = (constraints or []) + [
            "[DRY RUN] Prefer the cheapest available option for testing"
        ]
        logger.info("Running in DRY RUN mode")

    try:
        result = await engine.decide(extra_constraints=constraints)

        # Output: clean JSON that n8n reads to build the Slack message
        output = {
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
            "slack_message": result.slack_summary,
        }
        print(json.dumps(output, indent=2))
        sys.exit(0)

    except MaxRetriesExceededError as e:
        error_output = {
            "status": "error",
            "error_type": "budget_retry_exceeded",
            "message": str(e),
            "slack_message": (
                "⚠️ *AutoLunch Warning*\n\n"
                "Couldn't find a suitable meal within ₹250 after multiple attempts.\n"
                "Please order manually today."
            ),
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)

    except ZomatoNoResultsError as e:
        error_output = {
            "status": "error",
            "error_type": "no_restaurants",
            "message": str(e),
            "slack_message": (
                "⚠️ *AutoLunch Warning*\n\n"
                "No restaurants found matching your filters near Miraya Rose right now.\n"
                "Please order manually today."
            ),
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(2)

    except AutoLunchError as e:
        error_output = {
            "status": "error",
            "error_type": type(e).__name__,
            "message": str(e),
            "slack_message": (
                "⚠️ *AutoLunch Error*\n\n"
                f"Something went wrong: {e.message}\n"
                "Please order manually today."
            ),
        }
        print(json.dumps(error_output, indent=2))
        sys.exit(1)


async def cmd_reject(restaurant_name: str, item_name: str, cart_id: str, net_total: float, reason: str) -> None:
    """
    Record a user rejection. Called by n8n after user replies 'No' on Slack.
    Returns JSON with the new suggestion (re-runs decide with rejection constraint).
    """
    from autolunch.models.restaurant import LLMOrderDecision, CartSimulationResult, Restaurant, MenuItem
    from autolunch.services.llm.engine import DecisionResult

    engine = LLMDecisionEngine()

    # Build a minimal DecisionResult just for rejection recording
    # (we only need restaurant_name + item_name + net_total for memory)
    mock_decision = LLMOrderDecision(
        restaurant_name=restaurant_name,
        restaurant_id="",
        item_name=item_name,
        item_id="",
        base_price=0,
        estimated_net_total=net_total,
        reasoning="",
        confidence=0,
    )
    mock_cart = CartSimulationResult(
        cart_id=cart_id,
        restaurant_id="",
        item_id="",
        base_price=0,
        delivery_fee=0,
        platform_fee=0,
        gst=0,
        net_total=net_total,
        within_budget=True,
    )
    mock_result = DecisionResult(
        decision=mock_decision,
        cart=mock_cart,
        restaurant=Restaurant(restaurant_id="", name=restaurant_name, cuisine_types=[], rating=0, distance_km=0, delivery_time_minutes=0),
        item=MenuItem(item_id="", name=item_name, base_price=0, is_veg=True),
    )

    # Record rejection (also extracts LLM constraint from reason)
    await engine.record_rejection(mock_result, reason)

    # Re-run decide with the extracted constraint
    extracted_constraint = f"User just rejected '{item_name}' from '{restaurant_name}': \"{reason}\". Do not suggest this again."
    await cmd_decide(constraints=[extracted_constraint])


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoLunch CLI")
    subparsers = parser.add_subparsers(dest="command")

    # decide
    decide_parser = subparsers.add_parser("decide", help="Pick today's lunch")
    decide_parser.add_argument("--dry-run", action="store_true", help="Test mode with mock constraints")
    decide_parser.add_argument("--constraint", action="append", default=[], help="Inject extra constraint")

    # reject
    reject_parser = subparsers.add_parser("reject", help="Record a user rejection and get new suggestion")
    reject_parser.add_argument("--restaurant", required=True)
    reject_parser.add_argument("--item", required=True)
    reject_parser.add_argument("--cart-id", required=True)
    reject_parser.add_argument("--net-total", type=float, required=True)
    reject_parser.add_argument("--reason", required=True)

    args = parser.parse_args()

    if args.command == "decide":
        asyncio.run(cmd_decide(dry_run=args.dry_run, constraints=args.constraint or None))
    elif args.command == "reject":
        asyncio.run(cmd_reject(args.restaurant, args.item, args.cart_id, args.net_total, args.reason))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
