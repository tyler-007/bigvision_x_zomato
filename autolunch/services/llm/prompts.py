"""
AutoLunch — LLM Prompt Templates

All prompts are functions, not strings, so they can be tested,
versioned, and composed cleanly without string manipulation littered
throughout the engine.

Design philosophy:
  - SYSTEM prompt: who the LLM is + output schema (never changes per-run)
  - USER prompt: all dynamic context (preferences, memory, menu, constraints)
  - Injected constraints: built up incrementally during retry/rejection loops
"""
from datetime import date

from autolunch.models.memory import AgentMemory
from autolunch.models.preferences import UserPreferences, DietType
from autolunch.models.restaurant import Restaurant


# ── Output schema injected into system prompt ─────────────────────────────────
OUTPUT_SCHEMA = """
{
  "restaurant_name": "string",
  "restaurant_id":   "string (exact ID from the menu data)",
  "item_name":       "string",
  "item_id":         "string (exact ID from the menu data)",
  "base_price":      number,
  "estimated_net_total": number  (your best estimate including ~5% GST + ₹8 platform fee),
  "reasoning":       "string (1–2 sentences explaining why this pick fits the preferences)",
  "confidence":      number between 0.0 and 1.0
}
"""


def build_system_prompt() -> str:
    """
    Static system prompt — defines the LLM's role and output format.
    This never changes between runs.
    """
    return f"""You are AutoLunch, an intelligent lunch ordering assistant for a busy professional in Bangalore, India.

Your sole job is to select the single best lunch item from the provided restaurant and menu data that:
1. Fits the user's dietary preferences and guardrails EXACTLY
2. Has an estimated net total ≤ ₹250 (base price + ~5% GST + ₹8 platform fee + delivery if applicable)
3. Has NOT been ordered recently (respect the repeat-aversion days)
4. Does NOT match any recent rejection reasons or learned blocks
5. Prioritizes preferred cuisines and meal styles

You must respond with ONLY a valid JSON object matching this exact schema (no markdown, no explanation outside JSON):
{OUTPUT_SCHEMA}

Critical rules:
- NEVER suggest a restaurant or item that appears in the blocked lists
- NEVER suggest the same restaurant ordered within avoid_repeat_days
- NEVER exceed the budget — if you\'re unsure, pick a cheaper item
- If confidence is below 0.5, still pick the best available option
- your estimated_net_total must be realistic (base_price × 1.05 + 8 + delivery_fee)
"""


def build_user_prompt(
    prefs: UserPreferences,
    memory: AgentMemory,
    restaurants: list[Restaurant],
    extra_constraints: list[str] | None = None,
) -> str:
    """
    Dynamic user prompt — built fresh every run with current context.

    Args:
        prefs: Full user preferences (10 questions)
        memory: Episodic memory (recent orders, rejections, learned blocks)
        restaurants: Filtered restaurant list with menus populated
        extra_constraints: Additional constraints injected mid-loop
                           (e.g. "₹220 was over budget, pick cheaper"
                                 "User rejected Haldiram's: too heavy today")
    """
    today = date.today()

    # ── 1. Preferences block ─────────────────────────────────────────────────
    diet_label = {
        DietType.VEGETARIAN: "Strictly VEGETARIAN (no eggs, no meat)",
        DietType.NON_VEGETARIAN: "Non-vegetarian (any protein ok)",
        DietType.EGGETARIAN: "Eggetarian (eggs ok, no meat)",
    }[prefs.diet_type]

    preference_block = f"""=== USER PREFERENCES ===
Today: {today.strftime("%A, %d %B %Y")}
Diet: {diet_label}
Spice tolerance: {prefs.spice_tolerance}/5 (1=very mild, 5=very spicy)
Preferred meal styles: {", ".join(prefs.preferred_meal_styles)}
Preferred cuisines: {", ".join(prefs.guardrails.preferred_cuisines) or "no preference"}
Blocked cuisines: {", ".join(prefs.guardrails.blocked_cuisines) or "none"}
Blocked restaurants: {", ".join(prefs.guardrails.blocked_restaurants) or "none"}
Blocked ingredients/allergens: {", ".join(prefs.guardrails.blocked_ingredients) or "none"}
Preferred restaurants: {", ".join(prefs.guardrails.preferred_restaurants) or "none"}
Max net total: ₹{prefs.max_net_budget_inr} (HARD LIMIT — do not exceed)
Additional notes: {prefs.additional_notes or "none"}
"""

    # ── 2. Recent orders block (repeat-aversion) ──────────────────────────────
    recent_orders = memory.recent_orders(days=prefs.avoid_repeat_days)
    if recent_orders:
        orders_lines = "\n".join(
            f"  - {o.order_date}: {o.item_name} from {o.restaurant_name}"
            for o in recent_orders
        )
        orders_block = f"""=== RECENT ORDERS (do NOT repeat these restaurants within {prefs.avoid_repeat_days} days) ===
{orders_lines}
"""
    else:
        orders_block = "=== RECENT ORDERS ===\nNo recent orders — all restaurants are available.\n"

    # ── 3. Recent rejections block ────────────────────────────────────────────
    recent_rejections = memory.recent_rejections(days=7)
    if recent_rejections:
        rejection_lines = "\n".join(
            f"  - Rejected '{r.suggested_item}' from {r.suggested_restaurant}: \"{r.user_reason}\""
            for r in recent_rejections
        )
        rejections_block = f"""=== RECENT REJECTIONS (avoid these patterns) ===
{rejection_lines}
"""
    else:
        rejections_block = "=== RECENT REJECTIONS ===\nNone — no recent rejections.\n"

    # ── 4. Learned blocks ─────────────────────────────────────────────────────
    active_blocks = [b for b in memory.learned_blocks if b.expires_on is None or b.expires_on >= today]
    if active_blocks:
        block_lines = "\n".join(
            f"  - Block [{b.block_type}]: '{b.blocked_entity}' — {b.reason_summary}"
            for b in active_blocks
        )
        learned_block = f"""=== LEARNED BLOCKS (permanent — never suggest these) ===
{block_lines}
"""
    else:
        learned_block = ""

    # ── 5. Extra mid-loop constraints ─────────────────────────────────────────
    constraints_block = ""
    if extra_constraints:
        constraint_lines = "\n".join(f"  ⚠ {c}" for c in extra_constraints)
        constraints_block = f"""=== ACTIVE CONSTRAINTS FOR THIS PICK ===
{constraint_lines}
"""

    # ── 6. Available menu data ────────────────────────────────────────────────
    menu_lines = []
    for r in restaurants:
        menu_lines.append(
            f"\nRestaurant: {r.name} | ID: {r.restaurant_id} | "
            f"Rating: {r.rating}⭐ ({r.review_count:,} reviews) | "
            f"Distance: {r.distance_km}km | Delivery: ~{r.delivery_time_minutes}min"
        )
        menu_lines.append(f"Cuisines: {', '.join(r.cuisine_types)}")
        if r.menu:
            menu_lines.append("Menu items:")
            for item in r.menu:
                veg = "🟢VEG" if item.is_veg else "🔴NON-VEG"
                menu_lines.append(
                    f"  [{veg}] {item.name} | ID: {item.item_id} | "
                    f"Base: ₹{item.base_price} | Category: {item.category}"
                )
                if item.description:
                    menu_lines.append(f"    → {item.description[:120]}")
        else:
            menu_lines.append("  (Menu not loaded)")

    menu_block = "=== AVAILABLE RESTAURANTS & MENUS ===\n" + "\n".join(menu_lines)

    # ── Assemble full prompt ──────────────────────────────────────────────────
    return "\n".join([
        preference_block,
        orders_block,
        rejections_block,
        learned_block,
        constraints_block,
        menu_block,
        "\nNow select the best single lunch item and respond with ONLY the JSON object.",
    ])
