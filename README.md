# 🍱 AutoLunch

> Autonomous Zomato lunch ordering agent — triggers at 12:45 PM Mon–Fri, picks a meal using AI, gets your Telegram approval, and logs the receipt to Google Sheets. All within ₹250 net.

---

## Architecture

```
autolunch/
├── config/         # Pydantic BaseSettings (all env config)
├── core/           # Exceptions + structured logging
├── models/         # Pydantic data models (preferences, memory, restaurant)
├── repositories/   # Data access layer (JSON ↔ Pydantic)
├── services/       # Business logic (Zomato MCP, LLM engine, Telegram, Sheets)
data/
├── preferences.json  # Your 10 preference answers
└── memory.json       # Episodic memory (orders, rejections, learned blocks)
n8n/
└── workflow.json     # Importable n8n workflow
scripts/
└── test_openrouter.py
```

## Setup

### 1. Create virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and coordinates
```

### 3. Edit your preferences

Open `data/preferences.json` and fill in your actual food preferences.

### 4. Run Milestone 1 tests

```bash
python scripts/test_openrouter.py
```

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Project Foundation (settings, models, repositories) | ✅ Done |
| 2 | Zomato MCP Client (search, menu, cart sim, checkout) | 🔄 Next |
| 3 | LLM Decision Engine (OpenRouter + budget guardrail) | ⏳ |
| 4 | n8n Workflow + Telegram HITL | ⏳ |
| 5 | Receipt (Gmail) + Google Sheets logging | ⏳ |
| 6 | End-to-end testing | ⏳ |

## Key Constraints

- **Net total ≤ ₹250** — enforced by cart simulation, not just item price
- **Restaurants ≤ 7km** — Zomato Gold free delivery radius
- **Triggers at 12:45 PM** — food arrives by 1:30 PM
- **Max 2 HITL rejections** — then fallback to "order manually" Telegram message
