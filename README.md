# AutoLunch

**AI-powered lunch ordering for your team.** AutoLunch picks a meal from Zomato based on your preferences, sends a Slack message for approval, and creates your cart with the best promo applied. All within your budget.

## How It Works

```
12:45 PM  →  AI picks a meal  →  Slack: "Dal Makhani ₹168 — Yes/No?"
                                         ↓ Yes         ↓ No
                                    Cart created    New suggestion
                                    Open Zomato     (max 2 retries)
                                    Pay & eat!
```

**What you get:**
- Real restaurants from Zomato near your office
- AI picks based on your diet, budget, and past orders
- Auto-applies best Zomato promo (₹40-80 off typical)
- Slack DM with Approve/Reject buttons
- Never repeats the same restaurant within 3 days
- Remembers what you rejected and why
- Every order logged to Google Sheets

---

## Quick Start (5 minutes)

```bash
# 1. Clone and install
git clone git@github.com:tyler-007/bigvision_x_zomato.git
cd bigvision_x_zomato
make install

# 2. Interactive setup (preferences, Zomato login, Slack, etc.)
make setup

# 3. Start the server
make start

# 4. Get your first suggestion
make trigger
```

That's it. The setup wizard walks you through everything.

---

## What the Setup Wizard Configures

| Step | What it asks | Why |
|------|-------------|-----|
| **Food Preferences** | Diet (veg/non-veg), spice level, cuisines, blocked restaurants | AI uses this to pick meals |
| **OpenRouter API Key** | Free key from openrouter.ai | Powers the AI brain |
| **Zomato Login** | One-time browser OAuth | Searches restaurants, creates carts |
| **Delivery Address** | Picks from your saved Zomato addresses | Knows where to deliver |
| **Slack** | Bot token + channel ID | Sends you suggestions with buttons |
| **Google Sheets** | Service account + sheet ID (optional) | Logs every order for expenses |
| **Schedule** | What time, which days | When to suggest lunch |

---

## Daily Usage

AutoLunch runs on a schedule (default: 12:45 PM Mon-Fri). You interact via Slack:

**When a suggestion arrives:**
- **"Yes, Order This!"** → Cart is created on Zomato with the best promo. Tap "Open My Cart" to pay.
- **"No, Suggest Again"** → AI picks a different meal and sends a new message.
- **After 2 rejections** → "Order manually today" with a Zomato link.

**Manual trigger:**
```bash
make trigger              # or: curl -X POST http://localhost:8100/trigger
```

**Check order history:**
```bash
make history              # or: curl http://localhost:8100/history
```

---

## API Endpoints

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/trigger` | POST | Run AI → send suggestion to Slack |
| `/decide` | POST | Run AI → return JSON (no Slack) |
| `/reject` | POST | Record rejection → re-decide |
| `/checkout` | POST | Create Zomato cart → return link |
| `/history` | GET | Order history (Zomato + Sheets + memory) |
| `/slack/interact` | POST | Handle Slack button clicks |

---

## Configuration

All config is in `.env` (created by setup wizard):

```bash
# AI
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemini-2.0-flash-001

# Zomato
ZOMATO_DELIVERY_LATITUDE=12.957175
ZOMATO_DELIVERY_LONGITUDE=77.732161
ZOMATO_MAX_DISTANCE_KM=7
ZOMATO_MAX_BUDGET_INR=250

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=D0...

# Google Sheets (optional)
GOOGLE_SERVICE_ACCOUNT_JSON=secrets/google_service_account.json
GOOGLE_SHEET_ID=...
```

Food preferences are in `data/preferences.json` (also created by wizard).

---

## How the AI Picks Your Meal

1. **Search** — Queries Zomato for restaurants within 7km of your office
2. **Filter** — Rating ≥ 4.0, reviews ≥ 1000, not blocked, not recently ordered
3. **Menu** — Fetches menus for top 5 restaurants
4. **AI Decision** — LLM picks the best item considering your preferences, past orders, and rejections
5. **Cart** — Creates real Zomato cart, auto-applies best promo
6. **Budget Check** — If net total > budget, AI re-picks (max 3 attempts)
7. **Send** — Slack message with full breakdown and Approve/Reject buttons

---

## Architecture

```
autolunch/
├── api.py              # FastAPI server (all endpoints + Slack handler)
├── cli.py              # CLI entrypoint
├── config/             # Settings from .env
├── core/               # Exceptions + structured logging
├── models/             # Pydantic models (preferences, memory, restaurant)
├── repositories/       # JSON file storage (swap to DB anytime)
└── services/
    ├── zomato/         # Real Zomato MCP client + mock server
    │   ├── real_mcp_client.py   # Connects to mcp-server.zomato.com
    │   ├── client.py            # Mock server client (development)
    │   └── mock_server.py       # Local test server
    ├── llm/            # OpenRouter decision engine + prompts
    ├── slack/          # Slack Block Kit notifier
    └── sheets/         # Google Sheets order logger
```

---

## For Developers

```bash
# Run tests
make test

# Run with mock Zomato (no OAuth needed)
uvicorn autolunch.services.zomato.mock_server:app --port 3000 &
make start

# Re-run setup
make setup
```

**Key files:**
- `data/preferences.json` — Your food preferences
- `data/memory.json` — Past orders, rejections, learned blocks
- `data/zomato_oauth.json` — Zomato OAuth tokens (auto-refreshes)
- `.env` — All API keys and config
- `secrets/` — Google service account (gitignored)

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Zomato OAuth tokens not found" | Run `make setup` or `python scripts/exchange_token.py` |
| "No restaurants match your filters" | Lower `min_review_count` in preferences.json |
| Slack buttons don't work | Check Interactivity URL in Slack App settings |
| "Settings not configured" | Check `.env` has all required keys |
| Token expired | Re-run Zomato OAuth (tokens last 30 days) |

---

Built with Zomato MCP, OpenRouter, and Slack Block Kit.
