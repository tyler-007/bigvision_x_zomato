# 🍱 AutoLunch — Product Requirements Document (PRD)

**Version:** 1.2  
**Author:** Aayush Jain  
**Last Updated:** April 2026  
**Status:** Active Development — Milestone 4 in progress

---

## 1. Executive Summary

**AutoLunch** is a fully autonomous lunch ordering agent that runs everyday at 12:45 PM, Monday through Friday. It uses an LLM to intelligently decide what to order from Zomato based on your dietary preferences, past rejection patterns, and a strict ₹250 budget. You get a Slack message with a suggestion and you simply click **Approve** or **Reject** — nothing else.

The system is designed for solo use by **Aayush Jain** at **Miraya Rose, Bangalore**, ordering delivery from restaurants within a 7km radius, exclusively using Zomato Gold free delivery.

---

## 2. Problem Statement

Ordering lunch every workday is a highly repetitive cognitive task:
- You spend 5–15 minutes every day deciding what to order.
- You often end up with the same few restaurants out of decision fatigue.
- You lose track of what you have ordered, what you have disliked, and what is overpriced once fees are added.
- No existing automation tool handles the full decision → cart → payment chain end-to-end.

**AutoLunch eliminates this problem entirely.** The only human interaction is a single button tap.

---

## 3. Product Vision

> *"Every weekday at 12:45 PM, Aayush gets a Slack message with a great lunch suggestion at under ₹250. He clicks Approve, and lunch shows up at 1:30 PM. He never thinks about it again."*

---

## 4. Key Stakeholders

| Role | Person | Responsibility |
|---|---|---|
| Product Owner | Aayush Jain | Defines preferences, approves/rejects suggestions |
| Developer | Aayush Jain | Maintains codebase, n8n workflow, Slack app |
| External APIs | Zomato, Slack, OpenRouter | Restaurant data, notifications, LLM decisions |

---

## 5. User Personas

### 5.1 Primary User — Aayush (The Operator)
- Works from Miraya Rose, Bangalore
- Vegetarian/specific dietary preferences (set in `preferences.json`)
- Budget-conscious — max ₹250 net inclusive of all fees
- Prefers high-rated restaurants (≥4.0 ⭐) with substantial review counts (≥1,000)
- Wants a one-tap approval, not a back-and-forth conversation
- Tends to get frustrated with repetitive suggestions or rejected items being re-suggested

---

## 6. Core Use Cases

### UC-1: Daily Lunch Suggestion (Primary Flow)
**Trigger:** 12:45 PM Monday–Friday (n8n Cron Trigger)  
**Actor:** System (AutoLunch)  
**Flow:**
1. n8n cron node fires at 12:45 PM on weekdays.
2. Calls `POST /decide` on the local FastAPI server.
3. LLM Decision Engine:
   a. Loads user preferences from [data/preferences.json](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/data/preferences.json).
   b. Loads episodic memory (past orders, rejections) from [data/memory.json](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/data/memory.json).
   c. Searches Zomato for restaurants within 7km, ≥4.0 rating, ≥1,000 reviews.
   d. Fetches menus for the top 5 restaurants concurrently.
   e. Calls OpenRouter LLM (GPT-4o class) with full context.
   f. LLM returns a structured JSON pick (`restaurant_id`, `item_id`, `reasoning`).
   g. Simulates cart to compute real net total (base + delivery + GST + platform fee).
   h. If net > ₹250, injects a budget feedback constraint and LLM re-picks (max 3 attempts).
4. Result saved to server-side memory (`LAST_SUGGESTION["pending"]`).
5. n8n sends a rich Slack Block Kit message with:
   - Item name, restaurant name
   - Distance, estimated delivery time, rating
   - Price breakdown (base, delivery, GST, platform fee, net total)
   - LLM reasoning ("Why I picked this today")
   - ✅ Approve and ❌ Reject buttons

**Success Criteria:** Slack message arrives by 12:46 PM. Buttons are fully clickable.

---

### UC-2: User Approves Suggestion
**Trigger:** User clicks ✅ Approve button in Slack  
**Actor:** Aayush  
**Flow:**
1. Slack sends a webhook POST to the ngrok tunnel → n8n Interaction Webhook.
2. n8n sends an immediate 200 OK back to Slack (within 3s to avoid timeout).
3. `Parse Slack Action` code node reads the `action.value` = `"approve"`.
4. `Approved or Rejected?` IF node routes to the approve branch.
5. n8n calls `POST /checkout` (empty body — server knows the cart from `LAST_SUGGESTION`).
6. FastAPI backend retrieves `pending["cart_id"]` and calls `ZomatoMCPClient.checkout()`.
7. Zomato returns: `order_id`, `upi_payment_link`, `upi_qr_code_url`, `amount_payable`, `estimated_delivery_minutes`.
8. `LAST_SUGGESTION` is cleared.
9. n8n sends a second Slack message with UPI payment link and order details.

**Success Criteria:** User receives UPI link in Slack within 10 seconds of clicking Approve.

---

### UC-3: User Rejects Suggestion (First Rejection)
**Trigger:** User clicks ❌ Reject button in Slack  
**Actor:** Aayush  
**Flow:**
1. Slack sends webhook POST → n8n routes to rejection branch.
2. `Count Today's Rejections` code node increments the `rejectionCount` in n8n static data.
3. `Max Rejections Hit?` IF node checks if `rejectionCount < 2`.
4. Since it's the first rejection, routes to `Record Rejection + Re-decide`.
5. n8n calls `POST /reject` with a default reason: `"User rejected via Slack button"`.
6. FastAPI reads `LAST_SUGGESTION["pending"]` to reconstruct the context.
7. LLM records the rejection to `memory.json` with a derived constraint (via LLM extraction).
8. `LAST_SUGGESTION` is cleared, then `/decide` is re-called with the rejection constraint injected.
9. A new Slack suggestion message is sent with the next pick.

**Success Criteria:** A fresh, different suggestion arrives in Slack within 30 seconds of rejection.

---

### UC-4: User Rejects Twice (Manual Fallback)
**Trigger:** User clicks ❌ Reject for the second time in a day  
**Actor:** Aayush  
**Flow:**
1. `Count Today's Rejections` increments `rejectionCount` to 2.
2. `Max Rejections Hit?` routes to the fallback branch.
3. n8n sends a Slack message: _"You've passed on 2 suggestions. No problem — today's your call! [Open Zomato](https://www.zomato.com/)"_
4. Workflow terminates. No further automation.

**Success Criteria:** User receives a clean, non-annoying fallback message with the Zomato link.

---

### UC-5: Decision Engine Fails (Error Handling)
**Trigger:** `/decide` returns an error status (budget exceeded or no restaurants found)  
**Actor:** System  
**Flow:**
1. `Decision OK?` IF node detects `status != "ok"`.
2. n8n routes to `Send Error Alert` node.
3. Slack message: _"⚠️ AutoLunch Error: {message}. Please order manually today."_

**Success Criteria:** Error message provides enough detail for the user to understand what went wrong.

---

## 7. System Architecture

```
[n8n Cron Trigger]
       │  12:45 PM Mon–Fri
       ▼
[HTTP Request → POST /decide]       ← FastAPI (localhost:8100)
       │                                    │
       │                            [LLM Decision Engine]
       │                             - Load preferences.json
       │                             - Load memory.json  
       │                             - Search Zomato (7km, 4★, 1k reviews)
       │                             - Fetch menus (top 5, parallel)
       │                             - OpenRouter LLM → structured JSON pick
       │                             - Cart simulation → verify net ≤ ₹250
       │                             - Save to LAST_SUGGESTION["pending"]
       │
       ▼
[Send Slack Suggestion]             ← n8n-nodes-base.slack (native, OAuth2)
  - Rich Block Kit message
  - Buttons: value="approve" / value="reject"
       │
       │  ← ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ USER CLICKS BUTTON
       │
[Slack Interaction Webhook]         ← n8n Webhook (POST /autolunch-hitl)
  └─ Exposed via ngrok tunnel:            ngrok forwards to localhost:5678
     https://xxxx.ngrok-free.app/webhook-test/autolunch-hitl
       │
[Acknowledge Slack (200 OK)]        ← Must respond within 3s
       │
[Parse Slack Action]                ← Reads action.value = "approve" / "reject"
       │
[Approved or Rejected?]
       │                    │
       ▼ (approve)          ▼ (reject)
[POST /checkout]       [Count Today's Rejections]
       │                    │
[Parse Result]         [Max Rejections Hit?]
       │                    │                │
[Send UPI Link]       (No) [POST /reject]   (Yes) [Send Manual Notice]
  in Slack                  │
                      [New /decide cycle → New Slack Message]
```

---

## 8. n8n Workflow Node Reference

| Node Name | Node Type | Purpose |
|---|---|---|
| `12:45 PM Trigger` | `scheduleTrigger` | Fires at 12:45 PM Mon–Fri via cron (`45 12 * * 1-5`) |
| `Run LLM Decision Engine` | `httpRequest` | POST to `/decide` — triggers the full LLM + Zomato chain |
| `Parse Decision JSON` | `code` | Pass-through; validates structure from HTTP response |
| `Decision OK?` | `if` | Routes: `status == "ok"` → send Slack. Else → error alert |
| `Send Slack Suggestion` | `slack` (native OAuth2) | Sends rich Block Kit message with Approve/Reject buttons |
| `Send Error Alert` | `slack` | Sends error message if `/decide` fails |
| `Slack Interaction Webhook` | `webhook` | Listens for button clicks from Slack (via ngrok in dev) |
| `Acknowledge Slack (200 OK)` | `respondToWebhook` | Returns empty 200 immediately to prevent Slack timeout |
| `Parse Slack Action` | `code` | Decodes Slack's URL-encoded payload, extracts `action.value` |
| `Approved or Rejected?` | `if` | Routes: `action_type == "approve"` → checkout, else → reject |
| `Trigger Zomato Checkout` | `httpRequest` | POST to `/checkout` (empty body) — uses server memory |
| `Parse Checkout Result` | `code` | Pass-through; extracts UPI link from response |
| `Send UPI Payment Link` | `slack` | Sends formatted UPI link + order details to user |
| `Count Today's Rejections` | `code` | Reads/writes n8n static data for daily rejection count |
| `Max Rejections Hit?` | `if` | `rejectionCount >= 2` → manual fallback |
| `Record Rejection + Re-decide` | `httpRequest` | POST to `/reject` with default reason — triggers new `/decide` |
| `Send Manual Order Notice` | `slack` | Sends "order manually today" Slack fallback message |

---

## 9. FastAPI Endpoint Reference ([autolunch/api.py](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/api.py))

### `POST /decide`
- **Body:** Optional `constraints: list[str]`
- **Returns:** Full decision JSON (`restaurant_name`, `item_name`, `cart_id`, `net_total`, `reasoning`, `rating`, etc.)
- **Side Effect:** Saves result to `LAST_SUGGESTION["pending"]`

### `POST /checkout`
- **Body:** `{}` (empty — uses `LAST_SUGGESTION`)
- **Returns:** `order_id`, `upi_payment_link`, `upi_qr_code_url`, `amount`, `estimated_delivery_minutes`
- **Side Effect:** Clears `LAST_SUGGESTION["pending"]` after checkout

### `POST /reject`
- **Body:** `{ "reason": "string" }`
- **Returns:** Same as `/decide` — a fresh suggestion
- **Side Effect:** Calls `engine.record_rejection()`, clears old `LAST_SUGGESTION`, saves new one

### `GET /health`
- **Returns:** `{ "status": "ok", "service": "autolunch-api" }`

---

## 10. Filter Chain (Applied Before Any LLM Call)

| # | Filter | Value | Enforced By |
|---|---|---|---|
| 1 | Distance from office | ≤ 7km | `searchRestaurants` radius param in Zomato MCP |
| 2 | Minimum rating | ≥ 4.0 ⭐ | Post-search filter in `ZomatoMCPClient` |
| 3 | Minimum review count | ≥ 1,000 reviews | Post-search filter in `ZomatoMCPClient` |
| 4 | Cuisine/restaurant blocklist | User-defined in `preferences.json` | Pre-LLM filter pass |
| 5 | Budget validation | Net total ≤ ₹250 | Cart simulation after LLM pick (retries up to 3x) |

---

## 11. State Management: `LAST_SUGGESTION`

A critical architectural pattern introduced to simplify Slack interaction payloads:

**Problem solved:** n8n's Slack node (`blocksUi`) strips non-standard dynamic values from button payloads, making it impossible to embed `cart_id`, `price`, etc. in the button `value` field.

**Solution:** Save the decision context server-side immediately after `/decide`. Slack buttons only need to send back `"approve"` or `"reject"`.

```python
LAST_SUGGESTION = {}  # Global, in-process memory

# Set by /decide
LAST_SUGGESTION["pending"] = {
    "restaurant_name": ...,
    "item_name": ...,
    "cart_id": ...,
    "net_total": ...,
}

# Consumed by /checkout (cleared after)
# Consumed by /reject (cleared, then /decide re-populates)
```

**Constraint:** This is in-process memory. If the FastAPI server restarts between `/decide` and `/checkout`, the state is lost. This is acceptable for v1 (single-user, single-machine).

---

## 12. Milestones & Story Points

### ✅ Milestone 1 — Project Foundation
> Status: **DONE**

| Story | Points | Status |
|---|---|---|
| Configure Pydantic BaseSettings from [.env](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/.env) | 2 | ✅ Done |
| Define all domain models (Restaurant, MenuItem, Cart, UserPreferences, AgentMemory) | 5 | ✅ Done |
| Build repository pattern (JSON ↔ Pydantic, swappable to DB) | 3 | ✅ Done |
| Implement structured logging via loguru | 2 | ✅ Done |
| Set up custom exception hierarchy | 2 | ✅ Done |
| [data/preferences.json](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/data/preferences.json) — 10-question onboarding filled | 1 | ✅ Done |
| Milestone 1 validator script (`test_openrouter.py`) | 1 | ✅ Done |

---

### ✅ Milestone 2 — Zomato MCP Integration
> Status: **DONE**

| Story | Points | Status |
|---|---|---|
| Implement `ZomatoMCPClient` with async context manager | 5 | ✅ Done |
| `searchRestaurants` — apply 7km, rating, review, blocklist filters | 3 | ✅ Done |
| `getMenu` — fetch full menu for a single restaurant | 3 | ✅ Done |
| `simulateCart` — compute net total including fees and GST | 5 | ✅ Done |
| [checkout](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/api.py#149-173) — place Zomato order, return UPI payment link | 5 | ✅ Done |
| Mock Zomato server for local development testing | 3 | ✅ Done |
| Milestone 2 validator script (`test_zomato_client.py`) | 2 | ✅ Done |

---

### ✅ Milestone 3 — LLM Decision Engine
> Status: **DONE**

| Story | Points | Status |
|---|---|---|
| Build [LLMDecisionEngine](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/services/llm/engine.py#81-306) class with full orchestration | 8 | ✅ Done |
| Parallel menu fetching (top 5 restaurants via `asyncio.gather`) | 3 | ✅ Done |
| OpenRouter API call with structured JSON output (`response_format`) | 5 | ✅ Done |
| Budget validation loop (max 3 LLM retries with injected constraints) | 5 | ✅ Done |
| Hallucination guard ([_resolve_pick](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/services/llm/engine.py#284-306) — ID + name fuzzy fallback) | 3 | ✅ Done |
| [record_rejection](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/services/llm/engine.py#186-212) — persist rejection + LLM constraint extraction | 5 | ✅ Done |
| `build_system_prompt` + `build_user_prompt` in [prompts.py](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/services/llm/prompts.py) | 5 | ✅ Done |
| Tenacity retry decorator on LLM calls (3 retries, exponential backoff) | 2 | ✅ Done |

---

### 🔄 Milestone 4 — n8n Workflow + Slack HITL
> Status: **In Progress — Mostly Done, Partially working**

| Story | Points | Status |
|---|---|---|
| Build initial n8n workflow JSON structure | 5 | ✅ Done |
| Cron trigger (12:45 PM Mon–Fri) | 1 | ✅ Done |
| HTTP Request node → `/decide` API | 2 | ✅ Done |
| Error branching → `Send Error Alert` Slack node | 2 | ✅ Done |
| Native Slack node (OAuth2) with Block Kit buttons | 8 | ✅ Done |
| Fix `blocksUi` stripping button values — raw `JSON.stringify` injection | 5 | ✅ Done |
| Server-side state pattern (`LAST_SUGGESTION`) to simplify payloads | 5 | ✅ Done |
| n8n Webhook trigger for Slack interaction callbacks | 3 | ✅ Done |
| Immediate 200 OK response node to prevent Slack timeout | 2 | ✅ Done |
| `Parse Slack Action` code node — decode URL-encoded payload | 3 | ✅ Done |
| Approve branch → `/checkout` → Send UPI payment link | 5 | ✅ Done |
| Rejection branch → `Count Today's Rejections` counter | 3 | ✅ Done |
| Rejection branch → Max 2 rejections → Manual fallback Slack message | 3 | ✅ Done |
| Rejection branch → `POST /reject` → re-decision loop | 5 | ✅ Done |
| Ngrok tunnel setup for local Slack webhook delivery | 2 | ✅ Done |
| Slack App interactivity → Request URL configured with ngrok | 2 | ✅ Done |
| FastAPI server ([api.py](file:///Users/aayushjain/codes/projects/personal%20projects/bigivision_x_zomato/autolunch/api.py)) as n8n bridge for all endpoints | 5 | ✅ Done |
| E2E live test: Slack buttons fire, checkout triggers, UPI link delivered | 8 | 🔄 In Progress |

---

### ⏳ Milestone 5 — Receipt + Google Sheets Logging
> Status: **Not Started**

| Story | Points | Status |
|---|---|---|
| Gmail node in n8n — send receipt email on successful order | 3 | ⏳ |
| Google Sheets node — append row: date, item, restaurant, price, order_id | 5 | ⏳ |
| Monthly spend summary formula in Sheets | 2 | ⏳ |
| Handle failed checkout gracefully — no receipt sent on error | 2 | ⏳ |

---

### ⏳ Milestone 6 — End-to-End Testing & Production Hardening
> Status: **Not Started**

| Story | Points | Status |
|---|---|---|
| Replace ngrok with a fixed webhook URL (e.g. Cloudflare Tunnel or Railway) | 5 | ⏳ |
| `LAST_SUGGESTION` persistence option — write to file instead of RAM | 3 | ⏳ |
| Alerting if FastAPI server is down at 12:45 PM | 3 | ⏳ |
| Weekly summary Slack message (what was ordered, cost this week) | 5 | ⏳ |
| Retry on Slack message delivery failure | 2 | ⏳ |
| n8n workflow activation — switch from Test to Production webhook URL | 1 | ⏳ |
| Final end-to-end test across all branches (approve, reject, 2x reject, error) | 8 | ⏳ |

---

## 13. Environment Variables

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | LLM API key (OpenRouter) |
| `OPENROUTER_MODEL` | Model ID (e.g. `openai/gpt-4o`) |
| `ZOMATO_AUTH_TOKEN` | Zomato OAuth token for the MCP client |
| `SLACK_CHANNEL_ID` | The Slack DM or channel to post messages to |
| `SLACK_BOT_TOKEN` / OAuth creds | For the native n8n Slack node |
| `LAT` / `LNG` | Location coordinates (Miraya Rose = 12.9572, 77.7322) |
| `DELIVERY_RADIUS_KM` | Max restaurant distance (default: 7km) |
| `MAX_NET_PRICE` | Budget cap (default: 250.0 INR) |
| `DATA_DIR` | Path to `data/` folder with `preferences.json` and `memory.json` |

---

## 14. Known Issues & Technical Debt

| Issue | Severity | Status |
|---|---|---|
| `LAST_SUGGESTION` is in-process memory — lost on FastAPI restart | Medium | ⏳ Fix in M6 |
| ngrok URL changes on every restart — must reconfigure Slack interactivity | Medium | ⏳ Fix in M6 |
| Rejection reason is hardcoded as a default string (not user-provided) | Low | Acceptable for v1 |
| Slack message not updated/replaced after button click (old message stays) | Low | ⏳ Nice to have |
| No graceful handling if Zomato MCP is down at trigger time | High | ⏳ Fix in M6 |
| Rejection memory accumulates without a cleanup/archival policy | Low | ⏳ Future |

---

## 15. Acceptance Criteria (Definition of Done for MVP)

1. ✅ At 12:45 PM on a weekday, an AutoLunch Slack message appears with Approve/Reject buttons.
2. ✅ Clicking Approve triggers a UPI payment link in a follow-up Slack message within 10 seconds.
3. ✅ Clicking Reject sends a fresh, different suggestion within 30 seconds.
4. ✅ Clicking Reject twice sends a manual fallback message with the Zomato link.
5. ✅ All suggested items are under ₹250 net, after all fees.
6. ✅ The same item/restaurant is not re-suggested after rejection during the same day.
7. ✅ All interactions go through the native Slack node with OAuth2 credentials (no raw HTTP workarounds).
