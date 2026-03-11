"""
AutoLunch — Application Settings
Loaded from .env file via Pydantic BaseSettings.
All config is centralized here — no magic strings scattered across modules.
"""
from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenRouterSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENROUTER_", extra="ignore")

    api_key: str = Field(..., description="OpenRouter API key")
    base_url: str = Field("https://openrouter.ai/api/v1")
    model: str = Field("google/gemini-2.0-flash-001", description="Default LLM model")


class ZomatoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZOMATO_", extra="ignore")

    mcp_server_url: str = Field("http://localhost:3000", description="Zomato MCP server URL")
    auth_token: str = Field(..., description="Zomato OAuth token")
    delivery_latitude: float = Field(..., description="Office latitude for delivery")
    delivery_longitude: float = Field(..., description="Office longitude for delivery")
    max_distance_km: int = Field(7, ge=1, le=20, description="Max restaurant distance (Gold = free ≤7km)")
    max_budget_inr: int = Field(250, ge=50, le=500, description="Max NET total in INR (after all fees)")
    min_restaurant_rating: float = Field(4.0, ge=1.0, le=5.0, description="Minimum restaurant rating")


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLACK_", extra="ignore")

    bot_token: str = Field(..., description="Slack Bot OAuth token (xoxb-...)")
    channel_id: str = Field(..., description="DM channel ID to send lunch suggestions (starts with D)")
    signing_secret: str = Field(..., description="Slack signing secret for verifying interactive payloads")


class GoogleSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOOGLE_", extra="ignore")

    service_account_json: Path = Field(..., description="Path to Google service account JSON")
    sheet_id: str = Field(..., description="Google Sheets document ID")
    drive_folder_id: str = Field(..., description="Google Drive folder ID for receipts")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field("INFO", description="Logging level")
    data_dir: Path = Field(Path("./data"), description="Directory for JSON data files")
    max_llm_retry_attempts: int = Field(3, ge=1, le=5, description="Max LLM re-picks on budget overage")
    max_hitl_rejections: int = Field(2, ge=1, le=5, description="Max user rejections before manual fallback")

    # Nested settings — loaded separately to keep env prefix scoping clean
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    zomato: ZomatoSettings = Field(default_factory=ZomatoSettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    google: GoogleSettings = Field(default_factory=GoogleSettings)

    @model_validator(mode="after")
    def ensure_data_dir(self) -> "AppSettings":
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self


# ── Singleton ────────────────────────────────────────────────────────────────
# Import and use `settings` everywhere — never instantiate AppSettings directly
settings = AppSettings()
