# 🍱 AutoLunch

> Autonomous Zomato lunch ordering agent — triggers at 12:45 PM Mon–Fri, picks a meal using AI, gets your Telegram approval, and logs the receipt to Google Sheets. All within ₹250 net total.

**Location:** Miraya Rose, Bangalore — `12.9572°N, 77.7322°E`  
**Delivery radius:** ≤ 7km (Zomato Gold free delivery)  
**Budget:** ≤ ₹250 net (base price + GST + platform fee + delivery)

---

## Architecture

```
autolunch/
├── config/         # Pydantic BaseSettings — all env config in one place
├── core/           # Custom exception hierarchy + loguru structured logging
├── models/         # Pydantic data models (UserPreferences, AgentMemory, Restaurant, etc.)
├── repositories/   # Repository pattern — JSON ↔ Pydantic (swap to DB without touching services)
└── services/
    └── zomato/     # Async Zomato MCP client + mock server for testing
data/
├── preferences.json  # Your 10 preference answers (diet, spice, cuisines, guardrails)
└── memory.json       # Episodic memory (past orders, rejections, learned blocks)
n8n/
└── workflow.json     # Importable n8n workflow (Milestone 4)
scripts/
├── test_openrouter.py     # Milestone 1 validator
└── test_zomato_client.py  # Milestone 2 validator
```

---

## Filter Chain (applied before any LLM call)

1. **≤ 7km** from office (Zomato Gold free delivery radius)
2. **≥ 4.0 rating** (configurable via `min_restaurant_rating`)
3. **≥ 1,000 reviews** (social proof filter, configurable via `min_review_count`)
4. **Restaurant/Cuisine blocklist** (hard rules from `preferences.json`)

After LLM picks an item → **cart simulation** checks real net total (incl. GST + platform fee + delivery). If net > ₹250, LLM re-picks automatically (max 3 attempts).

---

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Project Foundation (settings, models, repositories, logging) | ✅ Done |
| 2 | Zomato MCP Client (search, menu, cart sim, checkout) | ✅ Done |
| 3 | LLM Decision Engine (OpenRouter prompt chain) | 🔄 Next |
| 4 | n8n Workflow + Telegram HITL | ⏳ |
| 5 | Receipt (Gmail) + Google Sheets logging | ⏳ |
| 6 | End-to-end testing | ⏳ |

---

## Setup

### 1. Prerequisites
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install fastapi uvicorn  # for mock server
```

### 2. Configure environment
```bash
cp .env.example .env
# Fill in: OPENROUTER_API_KEY, ZOMATO_AUTH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Coordinates are pre-set to Miraya Rose, Bangalore
```

### 3. Edit your food preferences
Open `data/preferences.json` — set your diet type, spice level, cuisines, blocklists.

### 4. Run validators
```bash
# Milestone 1 — OpenRouter + data files
python scripts/test_openrouter.py

# Milestone 2 — Zomato MCP filters + cart simulation
# Terminal 1:
uvicorn autolunch.services.zomato.mock_server:app --port 3000
# Terminal 2:
python scripts/test_zomato_client.py
```

---

## Zomato Account Connection (needed for Milestone 4)

The Zomato MCP server requires OAuth authentication to your Zomato account to:
- Search restaurants in your area
- Add items to your cart
- Initiate checkout and generate the UPI payment link

This is a **one-time setup** during Milestone 4 (n8n configuration). You'll log in via Zomato's OAuth flow and the token gets saved in `.env` as `ZOMATO_AUTH_TOKEN`. No credentials are stored in code — only in your local `.env` file (gitignored).

---

## Key Constraints

| Constraint | Value | Enforced by |
|---|---|---|
| Net total | ≤ ₹250 | Cart simulation (not menu price) |
| Restaurant distance | ≤ 7km | `searchRestaurants` radius param |
| Minimum reviews | ≥ 1,000 | Post-search filter in client |
| Minimum rating | ≥ 4.0 | Post-search filter in client |
| Trigger time | 12:45 PM Mon–Fri | n8n Cron node |
| Max HITL rejections | 2 | Before "order manually" fallback |
