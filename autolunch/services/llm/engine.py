from __future__ import annotations
"""
AutoLunch — LLM Decision Engine

The core intelligence of the system. Called by n8n at 12:45 PM and
again on each user rejection. Produces a single validated lunch pick.

Decision flow:
  1. Load preferences + memory from repositories
  2. Search restaurants (already filtered: 7km, rating, reviews, blocklist)
  3. Fetch menus for top restaurants (parallelized)
  4. Call OpenRouter LLM → get structured LLMOrderDecision JSON
  5. Simulate cart → verify net total ≤ ₹250
     - If over budget: inject constraint, re-call LLM (max 3 attempts)
  6. Return validated DecisionResult

Rejection flow (called from n8n on user "No"):
  1. Save rejection to memory (reason + what was suggested)
  2. Derive a new constraint from the reason (via LLM extraction)
  3. Re-run decision engine with the new constraint injected
"""
import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from loguru import logger
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from autolunch.config.settings import settings
from autolunch.core.exceptions import (
    BudgetExceededError,
    LLMResponseParseError,
    MaxRetriesExceededError,
    ZomatoNoResultsError,
)
from autolunch.models.memory import AgentMemory, LearnedBlock, Rejection
from autolunch.models.preferences import UserPreferences
from autolunch.models.restaurant import (
    CartSimulationResult,
    LLMOrderDecision,
    MenuItem,
    Restaurant,
)
from autolunch.repositories import get_memory_repository, get_preferences_repository
from autolunch.services.llm.prompts import build_system_prompt, build_user_prompt
from autolunch.services.zomato.client import ZomatoMCPClient


@dataclass
class DecisionResult:
    """
    Final output of the decision engine — everything n8n needs
    to send the Telegram approval message and execute the order.
    """
    decision: LLMOrderDecision
    cart: CartSimulationResult
    restaurant: Restaurant
    item: MenuItem

    @property
    def slack_summary(self) -> str:
        """Pre-formatted message for the Slack HITL approval notification."""
        return (
            f"🍱 *AutoLunch Suggestion*\n\n"
            f"*{self.decision.item_name}*\n"
            f"📍 {self.decision.restaurant_name}\n"
            f"⭐ {self.restaurant.rating} ({self.restaurant.review_count:,} reviews)\n"
            f"📦 {self.restaurant.distance_km}km · ~{self.restaurant.delivery_time_minutes}min delivery\n\n"
            f"💰 Breakdown:\n"
            f"  Base: ₹{self.cart.base_price}\n"
            f"  Delivery: ₹{self.cart.delivery_fee}\n"
            f"  Platform fee: ₹{self.cart.platform_fee}\n"
            f"  GST: ₹{self.cart.gst}\n"
            f"  *NET TOTAL: ₹{self.cart.net_total}* ✅\n\n"
            f"🤖 _{self.decision.reasoning}_\n\n"
            f"Approve this order?"
        )

    @property
    def telegram_summary(self) -> str:
        """Backward-compatible alias."""
        return self.slack_summary


class LLMDecisionEngine:
    """
    Main orchestrator — loads context, calls LLM, validates budget,
    handles retry loops, and writes rejections back to memory.
    """

    def __init__(self) -> None:
        self._openai = AsyncOpenAI(
            api_key=settings.openrouter.api_key,
            base_url=settings.openrouter.base_url,
        )
        self._model = settings.openrouter.model
        self._max_llm_retries = settings.max_llm_retry_attempts
        self._prefs_repo = get_preferences_repository(settings.data_dir)
        self._memory_repo = get_memory_repository(settings.data_dir)

    # ── Public API ────────────────────────────────────────────────────────────

    async def decide(
        self,
        extra_constraints: list[str] | None = None,
    ) -> DecisionResult:
        """
        Main entry point — called by n8n at 12:45 PM and on each rejection.

        Args:
            extra_constraints: Injected by n8n on rejection (e.g. "User said too spicy")

        Returns:
            DecisionResult with fully validated pick (net ≤ ₹250 guaranteed)

        Raises:
            MaxRetriesExceededError: LLM couldn't find a within-budget item in N attempts
            ZomatoNoResultsError: No restaurants pass the filter chain
        """
        prefs = self._prefs_repo.load()
        memory = self._memory_repo.load()

        async with ZomatoMCPClient() as zomato:
            # Step 1: Get filtered restaurants
            restaurants = await zomato.search_restaurants(prefs)

            # Step 2: Fetch menus concurrently (top 5 to keep prompt manageable)
            restaurants = restaurants[:5]
            restaurants = await asyncio.gather(
                *[zomato.get_menu(r) for r in restaurants]
            )

            # Step 3: Decision + validation loop
            constraints = list(extra_constraints or [])
            last_error: str | None = None

            for attempt in range(1, self._max_llm_retries + 1):
                logger.info(f"LLM decision attempt {attempt}/{self._max_llm_retries}", constraints=constraints)

                # Inject budget-exceeded feedback from previous attempt
                if last_error:
                    constraints.append(last_error)

                decision = await self._call_llm(prefs, memory, list(restaurants), constraints)

                # Find the restaurant and item objects from the decision
                restaurant, item = self._resolve_pick(decision, list(restaurants))
                if not restaurant or not item:
                    last_error = (
                        f"Item '{decision.item_name}' from '{decision.restaurant_name}' "
                        f"was not found in the menu data. Pick a different item."
                    )
                    logger.warning("LLM returned unknown item", attempt=attempt)
                    continue

                # Simulate cart → get real net total
                try:
                    cart = await zomato.simulate_cart(restaurant, item)
                    logger.info(
                        "Decision validated",
                        item=decision.item_name,
                        net_total=cart.net_total,
                        attempt=attempt,
                    )
                    return DecisionResult(
                        decision=decision,
                        cart=cart,
                        restaurant=restaurant,
                        item=item,
                    )
                except BudgetExceededError as e:
                    last_error = (
                        f"'{decision.item_name}' from '{decision.restaurant_name}' "
                        f"has a net total of ₹{e.net_total:.2f} — this EXCEEDS the ₹{e.budget} "
                        f"budget. Pick a cheaper item."
                    )
                    logger.warning(
                        "Budget exceeded, retrying",
                        net_total=e.net_total,
                        budget=e.budget,
                        attempt=attempt,
                    )

            raise MaxRetriesExceededError(
                f"LLM failed to find a within-budget item after {self._max_llm_retries} attempts. "
                "Trigger manual order fallback.",
                context={"last_error": last_error, "constraints": constraints},
            )

    async def record_rejection(
        self,
        result: DecisionResult,
        user_reason: str,
    ) -> None:
        """
        Persist a user rejection to memory and extract a reusable constraint.
        Called by n8n when the user clicks "No" on Telegram.
        """
        # Extract a clean constraint from the free-text reason using LLM
        extracted = await self._extract_constraint_from_reason(
            suggested_item=result.decision.item_name,
            suggested_restaurant=result.decision.restaurant_name,
            user_reason=user_reason,
        )

        rejection = Rejection(
            rejection_date=date.today(),
            suggested_restaurant=result.decision.restaurant_name,
            suggested_item=result.decision.item_name,
            suggested_net_total=result.cart.net_total,
            user_reason=user_reason,
            llm_extracted_constraint=extracted,
        )
        self._memory_repo.append_rejection(rejection)
        logger.info("Rejection recorded", reason=user_reason, constraint=extracted)

        # Auto-derive learned blocks from repeated rejections
        self._check_and_create_learned_blocks()

    # ── Private helpers ───────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    async def _call_llm(
        self,
        prefs: UserPreferences,
        memory: AgentMemory,
        restaurants: list[Restaurant],
        constraints: list[str],
    ) -> LLMOrderDecision:
        """
        Single OpenRouter API call → parse + validate JSON response.
        Retried automatically on transient errors.
        """
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(prefs, memory, restaurants, constraints)

        response = await self._openai.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,    # Low temp: consistent, predictable picks
            max_tokens=500,
        )

        raw = response.choices[0].message.content
        logger.debug("LLM raw response", raw=raw[:300])

        try:
            parsed = json.loads(raw)
            return LLMOrderDecision.model_validate(parsed)
        except Exception as e:
            raise LLMResponseParseError(
                f"LLM returned malformed JSON: {e}",
                context={"raw_response": raw[:500]},
            ) from e

    async def _extract_constraint_from_reason(
        self,
        suggested_item: str,
        suggested_restaurant: str,
        user_reason: str,
    ) -> str:
        """
        Use LLM to turn a free-text rejection reason into a reusable constraint
        that can be injected into future prompts.

        e.g. "too oily today" → "Avoid heavy/oily dishes for the next few days"
        """
        prompt = (
            f"The user rejected '{suggested_item}' from '{suggested_restaurant}'.\n"
            f"Their reason: \"{user_reason}\"\n\n"
            f"Write a single clear constraint sentence (max 20 words) that captures "
            f"what to avoid in future lunch suggestions. "
            f"Respond with ONLY the constraint sentence, nothing else."
        )
        response = await self._openai.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()

    def _check_and_create_learned_blocks(self) -> None:
        """
        Auto-derive learned blocks when the same restaurant is rejected
        2+ times within 7 days. Prevents the LLM from repeatedly suggesting
        restaurants the user clearly dislikes.
        """
        memory = self._memory_repo.load()
        recent = memory.recent_rejections(days=7)

        # Count rejections per restaurant
        restaurant_counts = Counter(r.suggested_restaurant for r in recent)

        # Existing blocked entities (avoid duplicates)
        already_blocked = {b.blocked_entity.lower() for b in memory.learned_blocks}

        for restaurant, count in restaurant_counts.items():
            if count >= 2 and restaurant.lower() not in already_blocked:
                reasons = [
                    r.user_reason for r in recent
                    if r.suggested_restaurant == restaurant
                ]
                reason_summary = "; ".join(reasons[:3])

                block = LearnedBlock(
                    blocked_entity=restaurant,
                    block_type="restaurant",
                    reason_summary=f"Rejected {count}x in 7 days: {reason_summary}",
                    created_on=date.today(),
                    expires_on=date.today() + timedelta(days=30),
                )
                self._memory_repo.append_learned_block(block)
                logger.info(
                    "Auto-created learned block",
                    restaurant=restaurant,
                    rejections=count,
                )

    @staticmethod
    def _resolve_pick(
        decision: LLMOrderDecision,
        restaurants: list[Restaurant],
    ) -> tuple[Restaurant | None, MenuItem | None]:
        """
        Find the Restaurant and MenuItem objects that match the LLM's decision
        by looking up restaurant_id and item_id from the loaded menu data.
        Falls back to name matching if IDs don't match (LLM hallucination guard).
        """
        for restaurant in restaurants:
            if (
                restaurant.restaurant_id == decision.restaurant_id
                or restaurant.name.lower() == decision.restaurant_name.lower()
            ):
                for item in restaurant.menu:
                    if (
                        item.item_id == decision.item_id
                        or item.name.lower() == decision.item_name.lower()
                    ):
                        return restaurant, item
        return None, None
