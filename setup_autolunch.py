#!/usr/bin/env python3
"""
AutoLunch Setup Wizard

Interactive setup that gets a new user from zero to ordering lunch.
Run: python setup_autolunch.py

What it does:
  1. Collects your food preferences (diet, spice, cuisines, budget)
  2. Sets up Zomato OAuth (one-time browser login)
  3. Configures Slack notifications
  4. Configures Google Sheets logging (optional)
  5. Sets your delivery address
  6. Configures the lunch schedule
  7. Writes .env and data/preferences.json
  8. Runs a test to verify everything works
"""
import asyncio
import hashlib
import base64
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlencode, parse_qs

# ── Styling ──────────────────────────────────────────────────────────────────

def banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🍱  AutoLunch Setup                                        ║
║                                                              ║
║   Autonomous lunch ordering — AI picks, you approve,         ║
║   Zomato delivers.                                           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}\n")

def ask(prompt, default=None, options=None, required=True):
    """Ask user a question with optional default and validation."""
    suffix = f" [{default}]" if default else ""
    if options:
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
        prompt = f"\n{prompt}{suffix}: "
    else:
        prompt = f"{prompt}{suffix}: "

    while True:
        val = input(prompt).strip()
        if not val and default:
            return default
        if not val and required:
            print("  This field is required.")
            continue
        if options:
            try:
                idx = int(val) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            except ValueError:
                if val in options:
                    return val
            print(f"  Please pick 1-{len(options)}")
            continue
        return val

def ask_yn(prompt, default="y"):
    val = input(f"{prompt} [{'Y/n' if default == 'y' else 'y/N'}]: ").strip().lower()
    if not val:
        return default == "y"
    return val in ("y", "yes")

def ask_multi(prompt, options):
    """Ask user to pick multiple options (comma-separated numbers)."""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    val = input(f"\n{prompt} (comma-separated, e.g. 1,3,5): ").strip()
    selected = []
    for part in val.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
        except ValueError:
            pass
    return selected if selected else [options[0]]


# ── Steps ────────────────────────────────────────────────────────────────────

def step_food_preferences():
    section("Step 1: Food Preferences")

    diet = ask("What's your diet?", options=["vegetarian", "non_vegetarian", "eggetarian"])
    spice = int(ask("Spice tolerance (1=mild, 5=very spicy)", default="3"))

    print("\nMeal styles you enjoy:")
    styles = ask_multi("Pick your favorites", [
        "rice_bowl", "roti_based", "sandwich_wrap",
        "noodles_pasta", "salad_light", "no_preference",
    ])

    print("\nCuisines you prefer:")
    cuisines = ask_multi("Pick your favorites", [
        "north_indian", "south_indian", "chinese",
        "bengali", "italian", "continental", "healthy",
    ])

    blocked_cuisines = []
    if ask_yn("Any cuisines to NEVER order?", "n"):
        blocked = input("  Which ones (comma-separated): ").strip()
        blocked_cuisines = [c.strip() for c in blocked.split(",") if c.strip()]

    blocked_restaurants = []
    if ask_yn("Any restaurants to NEVER order from?", "n"):
        blocked = input("  Which ones (comma-separated): ").strip()
        blocked_restaurants = [r.strip() for r in blocked.split(",") if r.strip()]

    budget = int(ask("Max net budget per order (INR)", default="250"))
    repeat_days = int(ask("Avoid same restaurant within N days", default="3"))
    notes = ask("Any other preferences? (free text, optional)", default="", required=False)

    return {
        "diet_type": diet,
        "spice_tolerance": min(max(spice, 1), 5),
        "preferred_meal_styles": styles,
        "avoid_repeat_days": repeat_days,
        "min_restaurant_rating": 4.0,
        "min_review_count": 1000,
        "max_net_budget_inr": budget,
        "max_distance_km": 7,
        "guardrails": {
            "blocked_restaurants": blocked_restaurants,
            "blocked_ingredients": [],
            "blocked_cuisines": blocked_cuisines,
            "preferred_restaurants": [],
            "preferred_cuisines": cuisines,
        },
        "preferred_delivery_by": "13:30",
        "additional_notes": notes,
    }


def step_openrouter():
    section("Step 2: AI Brain (OpenRouter)")
    print("AutoLunch uses an AI model to pick your meals.")
    print("Get a free API key at: https://openrouter.ai/keys\n")

    key = ask("OpenRouter API Key")
    model = ask("AI Model", default="google/gemini-2.0-flash-001")

    return {
        "OPENROUTER_API_KEY": key,
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_MODEL": model,
    }


def step_zomato_address():
    section("Step 3: Delivery Address")
    print("We need your Zomato delivery address.")
    print("After OAuth, we'll fetch your saved addresses.\n")

    lat = ask("Office latitude", default="12.957175865299629")
    lng = ask("Office longitude", default="77.7321612558208")

    return {
        "ZOMATO_DELIVERY_LATITUDE": lat,
        "ZOMATO_DELIVERY_LONGITUDE": lng,
        "ZOMATO_MAX_DISTANCE_KM": "7",
        "ZOMATO_MAX_BUDGET_INR": "250",
        "ZOMATO_MIN_RESTAURANT_RATING": "4.0",
        "ZOMATO_MCP_SERVER_URL": "http://localhost:3000",
        "ZOMATO_AUTH_TOKEN": "placeholder",
    }


def step_zomato_oauth():
    section("Step 4: Zomato Account Login")
    print("One-time OAuth to connect your Zomato account.")
    print("This lets AutoLunch search restaurants, create carts, and order.\n")

    import httpx
    # Register client
    print("Registering with Zomato...")
    r = httpx.post("https://mcp-server.zomato.com/register", json={
        "redirect_uris": ["https://oauth.pstmn.io/v1/callback"],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "AutoLunch",
    }, timeout=15)
    client_info = r.json()
    client_id = client_info["client_id"]

    # PKCE
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    auth_url = "https://mcp-server.zomato.com/authorize?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "https://oauth.pstmn.io/v1/callback",
        "scope": "mcp:tools mcp:resources mcp:prompts",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })

    print(f"\nOpen this URL in your browser:\n")
    print(f"  {auth_url}\n")
    print("After login, you'll be redirected. Copy the FULL URL and paste below.")
    print("(It starts with https://oauth.pstmn.io/... or postman://...)\n")

    callback_url = input("Paste callback URL: ").strip()
    query = callback_url.split("?", 1)[1] if "?" in callback_url else ""
    params = parse_qs(query)
    code = params.get("code", [""])[0]

    if not code:
        print("ERROR: No code found. Try again later with:")
        print("  python scripts/exchange_token.py '<callback_url>'")
        return False

    # Exchange code for token
    print("Exchanging code for token...")
    token_resp = httpx.post("https://mcp-server.zomato.com/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://oauth.pstmn.io/v1/callback",
        "client_id": client_id,
        "code_verifier": code_verifier,
    }, timeout=15)

    if token_resp.status_code == 200:
        tokens = token_resp.json()
        Path("data").mkdir(exist_ok=True)
        Path("data/zomato_oauth.json").write_text(json.dumps({
            "client_id": client_id,
            "client_info": client_info,
            "code_verifier": code_verifier,
            "tokens": tokens,
        }, indent=2))
        print("Zomato connected!\n")

        # Fetch addresses
        print("Fetching your saved delivery addresses...")
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import httpx as hx

            async def get_addresses():
                http = hx.AsyncClient(
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                    timeout=30,
                )
                async with streamable_http_client("https://mcp-server.zomato.com/mcp", http_client=http) as (r, w, _):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        result = await s.call_tool("get_saved_addresses_for_user", arguments={})
                        for c in result.content:
                            if hasattr(c, 'text'):
                                return json.loads(c.text)
                return {}

            addr_data = asyncio.run(get_addresses())
            addresses = addr_data.get("addresses", [])
            if addresses:
                print("\nYour saved addresses:")
                for i, a in enumerate(addresses, 1):
                    print(f"  {i}. {a['location_name'][:80]}")
                choice = ask("Pick your delivery address", options=[a["location_name"][:60] for a in addresses])
                idx = next(i for i, a in enumerate(addresses) if choice in a["location_name"][:60])
                selected = addresses[idx]
                return {"address_id": selected["address_id"], "address_name": selected["location_name"]}
        except Exception as e:
            print(f"  Couldn't fetch addresses: {e}")
            print("  Using default coordinates from Step 3.")
        return True
    else:
        print(f"Token exchange failed: {token_resp.text}")
        print("You can retry later with: python scripts/exchange_token.py")
        return False


def step_slack():
    section("Step 5: Slack Notifications")
    print("AutoLunch sends you meal suggestions via Slack DM with Yes/No buttons.\n")

    if not ask_yn("Set up Slack notifications?"):
        return {}

    print("\nYou need a Slack App. Create one at: https://api.slack.com/apps")
    print("  1. Bot Token Scopes: chat:write, im:write, im:history")
    print("  2. Enable Interactivity")
    print("  3. Install to workspace\n")

    token = ask("Slack Bot Token (xoxb-...)")
    channel = ask("Slack DM Channel ID (starts with D)")
    signing = ask("Slack Signing Secret", required=False, default="")

    return {
        "SLACK_BOT_TOKEN": token,
        "SLACK_CHANNEL_ID": channel,
        "SLACK_SIGNING_SECRET": signing,
    }


def step_google_sheets():
    section("Step 6: Google Sheets Logging (Optional)")
    print("Log every order to a Google Sheet for expense tracking.\n")

    if not ask_yn("Set up Google Sheets?", "n"):
        return {}

    print("\nYou need:")
    print("  1. A Google Cloud Service Account (JSON key)")
    print("  2. A Google Sheet shared with the service account email\n")

    sa_path = ask("Path to service account JSON", default="secrets/google_service_account.json")
    sheet_id = ask("Google Sheet ID (from the URL)")

    return {
        "GOOGLE_SERVICE_ACCOUNT_JSON": sa_path,
        "GOOGLE_SHEET_ID": sheet_id,
        "GOOGLE_DRIVE_FOLDER_ID": "",
    }


def step_schedule():
    section("Step 7: Lunch Schedule")
    print("When should AutoLunch suggest your meal?\n")

    time = ask("Suggestion time (HH:MM, 24hr)", default="12:45")
    days = ask("Which days? (mon-fri / everyday / custom)", default="mon-fri")

    return {"trigger_time": time, "trigger_days": days}


def write_env(env_vars):
    """Write .env file."""
    lines = []
    for key, val in env_vars.items():
        lines.append(f"{key}={val}")

    env_path = Path(".env")
    env_path.write_text("\n".join(lines) + "\n")
    print(f"  Written: {env_path}")


def write_preferences(prefs):
    """Write data/preferences.json."""
    Path("data").mkdir(exist_ok=True)
    path = Path("data/preferences.json")
    path.write_text(json.dumps(prefs, indent=2))
    print(f"  Written: {path}")


def run_test():
    """Quick smoke test."""
    section("Verification")
    print("Running quick test...\n")

    try:
        from autolunch.config.settings import settings
        print(f"  Settings loaded")
        print(f"    OpenRouter: {'configured' if settings.openrouter else 'missing'}")
        print(f"    Zomato: {'configured' if settings.zomato else 'missing'}")
        print(f"    Slack: {'configured' if settings.slack else 'not set up'}")
        print(f"    Google: {'configured' if settings.google else 'not set up'}")

        from autolunch.repositories import get_preferences_repository
        prefs = get_preferences_repository(settings.data_dir).load()
        print(f"    Diet: {prefs.diet_type}")
        print(f"    Budget: ₹{prefs.max_net_budget_inr}")

        if Path("data/zomato_oauth.json").exists():
            print(f"    Zomato OAuth: connected")
        else:
            print(f"    Zomato OAuth: not connected (using mock server)")

        print("\n  All good!\n")
        return True
    except Exception as e:
        print(f"\n  Verification failed: {e}")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    banner()

    if ask_yn("Ready to set up AutoLunch?"):
        # Collect everything
        prefs = step_food_preferences()
        openrouter = step_openrouter()
        zomato_env = step_zomato_address()
        zomato_oauth = step_zomato_oauth()
        slack_env = step_slack()
        sheets_env = step_google_sheets()
        schedule = step_schedule()

        # Update address_id if OAuth returned one
        if isinstance(zomato_oauth, dict) and "address_id" in zomato_oauth:
            # Store address_id in preferences for the real client
            prefs["_zomato_address_id"] = zomato_oauth["address_id"]

        # Write configs
        section("Writing Configuration")
        env_vars = {
            **openrouter,
            **zomato_env,
            **slack_env,
            **sheets_env,
            "LOG_LEVEL": "INFO",
            "DATA_DIR": "./data",
            "MAX_LLM_RETRY_ATTEMPTS": "3",
            "MAX_HITL_REJECTIONS": "2",
        }
        write_env(env_vars)
        write_preferences(prefs)

        # Test
        run_test()

        section("Setup Complete!")
        print("To start AutoLunch:")
        print("")
        print("  # Start the API server:")
        print("  source .venv/bin/activate")
        print("  uvicorn autolunch.api:app --port 8100 --host 0.0.0.0")
        print("")
        print("  # Trigger a suggestion manually:")
        print("  curl -X POST http://localhost:8100/trigger")
        print("")
        print("  # Or set up the cron schedule:")
        time_parts = schedule["trigger_time"].split(":")
        h, m = time_parts[0], time_parts[1]
        if schedule["trigger_days"] == "mon-fri":
            print(f"  # Add to crontab: {m} {h} * * 1-5 curl -X POST http://localhost:8100/trigger")
        else:
            print(f"  # Add to crontab: {m} {h} * * * curl -X POST http://localhost:8100/trigger")
        print("")
        print("  Enjoy your lunches! 🍱")
    else:
        print("Setup cancelled. Run again anytime with: python setup_autolunch.py")


if __name__ == "__main__":
    main()
