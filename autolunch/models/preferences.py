"""
AutoLunch — Pydantic Data Models: User Preferences
This is the "brain" of the system — loaded fresh on every workflow run.
"""
from enum import StrEnum
from pydantic import BaseModel, Field


class DietType(StrEnum):
    VEGETARIAN = "vegetarian"
    NON_VEGETARIAN = "non_vegetarian"
    EGGETARIAN = "eggetarian"


class MealStyle(StrEnum):
    RICE_BOWL = "rice_bowl"
    ROTI_BASED = "roti_based"
    SANDWICH_WRAP = "sandwich_wrap"
    NOODLES_PASTA = "noodles_pasta"
    SALAD_LIGHT = "salad_light"
    NO_PREFERENCE = "no_preference"


class Guardrails(BaseModel):
    """Hard rules — LLM must never violate these."""

    blocked_restaurants: list[str] = Field(
        default_factory=list,
        description="Restaurant names the system must NEVER order from",
        examples=[["McDonald's", "Burger King"]],
    )
    blocked_ingredients: list[str] = Field(
        default_factory=list,
        description="Ingredients/allergens to hard-block (e.g. 'peanuts', 'dairy')",
    )
    blocked_cuisines: list[str] = Field(
        default_factory=list,
        description="Cuisine types to never suggest",
    )
    preferred_restaurants: list[str] = Field(
        default_factory=list,
        description="Restaurants to prioritize when all else is equal",
    )
    preferred_cuisines: list[str] = Field(
        default_factory=list,
        description="Cuisine types to prefer",
    )


class UserPreferences(BaseModel):
    """
    Answers to the 10 foundational questions.
    Loaded from data/preferences.json by PreferencesRepository.
    """

    # Q1 — Diet
    diet_type: DietType = Field(
        ...,
        description="Vegetarian, Non-Vegetarian, or Eggetarian",
    )

    # Q2 — Spice
    spice_tolerance: int = Field(
        ...,
        ge=1,
        le=5,
        description="1 = very mild, 5 = very spicy",
    )

    # Q3 — Meal style
    preferred_meal_styles: list[MealStyle] = Field(
        ...,
        min_length=1,
        description="Ranked list of preferred meal styles (first = most preferred)",
    )

    # Q4 — Repeat aversion
    avoid_repeat_days: int = Field(
        default=3,
        ge=0,
        le=14,
        description="Avoid ordering from the same restaurant within N days",
    )

    # Q5 — Minimum restaurant rating
    min_restaurant_rating: float = Field(
        default=4.0,
        ge=1.0,
        le=5.0,
        description="Only show restaurants with rating ≥ this value",
    )

    # Q5b — Minimum review count (social proof filter)
    min_review_count: int = Field(
        default=1000,
        ge=0,
        description="Only show restaurants with at least this many total reviews (0 = no filter)",
    )

    # Q6 — Budget (mirrored from settings but set here for LLM context)
    max_net_budget_inr: int = Field(
        default=250,
        ge=50,
        le=500,
        description="Maximum NET order total in INR (includes delivery + taxes + platform fee)",
    )

    # Q7 — Distance (for Zomato Gold free delivery)
    max_distance_km: int = Field(
        default=7,
        ge=1,
        le=20,
        description="Maximum restaurant distance in km",
    )

    # Q8 — Guardrails
    guardrails: Guardrails = Field(
        default_factory=Guardrails,
        description="Hard rules on blocklisted/preferred restaurants, cuisines, ingredients",
    )

    # Q9 — Preferred order time window
    preferred_delivery_by: str = Field(
        default="13:30",
        pattern=r"^\d{2}:\d{2}$",
        description="Target delivery time (HH:MM). Workflow triggers 45 min before this.",
    )

    # Q10 — Notes / free text context for the LLM
    additional_notes: str = Field(
        default="",
        max_length=500,
        description="Free-text preferences given directly to the LLM (e.g. 'prefer light meals on Mondays')",
    )
