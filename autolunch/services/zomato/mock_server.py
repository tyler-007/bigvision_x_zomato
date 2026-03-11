"""
AutoLunch — Mock Zomato MCP Server (for testing)

A lightweight FastAPI server that mimics the Zomato MCP Node.js server's
endpoints with realistic dummy data.

Run: uvicorn autolunch.services.zomato.mock_server:app --port 3000 --reload

Used by:
  - scripts/test_zomato_client.py
  - n8n dry-run mode
"""
try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
except ImportError:
    raise ImportError(
        "FastAPI is required for the mock server. "
        "Install with: pip install fastapi uvicorn"
    )

app = FastAPI(title="AutoLunch Mock Zomato MCP Server")

# ── Realistic mock data ───────────────────────────────────────────────────────
MOCK_RESTAURANTS = [
    {
        "id": "zmt_1001",
        "name": "Haldiram's",
        "cuisines": ["north_indian", "sweets"],
        "avg_rating": 4.3,
        "total_ratings": 8200,
        "total_ratings_string": "8.2K ratings",
        "distance": 1.8,
        "delivery_time": 28,
        "is_open": True,
    },
    {
        "id": "zmt_1002",
        "name": "Saravana Bhavan",
        "cuisines": ["south_indian"],
        "avg_rating": 4.5,
        "total_ratings": 12400,
        "total_ratings_string": "12.4K ratings",
        "distance": 4.2,
        "delivery_time": 35,
        "is_open": True,
    },
    {
        "id": "zmt_1003",
        "name": "Bikanervala",
        "cuisines": ["north_indian", "chinese"],
        "avg_rating": 4.1,
        "total_ratings": 5600,
        "total_ratings_string": "5.6K ratings",
        "distance": 3.1,
        "delivery_time": 30,
        "is_open": True,
    },
    {
        "id": "zmt_1004",
        "name": "New Sketch Restaurant",   # Should be filtered: too few reviews
        "cuisines": ["north_indian"],
        "avg_rating": 4.2,
        "total_ratings": 450,
        "total_ratings_string": "450 ratings",
        "distance": 2.1,
        "delivery_time": 25,
        "is_open": True,
    },
    {
        "id": "zmt_1005",
        "name": "Faraway Place",           # Should be filtered: >7km
        "cuisines": ["chinese"],
        "avg_rating": 4.6,
        "total_ratings": 9000,
        "total_ratings_string": "9K ratings",
        "distance": 9.5,
        "delivery_time": 55,
        "is_open": True,
    },
]

MOCK_MENUS = {
    "zmt_1001": [
        {"id": "item_101", "name": "Dal Makhani + 2 Roti", "price": 179.0, "is_veg": True, "category": "Main Course", "avg_rating": 4.4, "tags": ["north_indian"]},
        {"id": "item_102", "name": "Paneer Butter Masala + Rice", "price": 209.0, "is_veg": True, "category": "Main Course", "avg_rating": 4.2, "tags": ["north_indian"]},
        {"id": "item_103", "name": "Chana Masala Thali", "price": 159.0, "is_veg": True, "category": "Thali", "avg_rating": 4.5, "tags": ["north_indian"]},
    ],
    "zmt_1002": [
        {"id": "item_201", "name": "Meals (Full South Indian Thali)", "price": 189.0, "is_veg": True, "category": "Meals", "avg_rating": 4.6, "tags": ["south_indian"]},
        {"id": "item_202", "name": "Masala Dosa + Sambar", "price": 129.0, "is_veg": True, "category": "Dosa", "avg_rating": 4.5, "tags": ["south_indian"]},
    ],
    "zmt_1003": [
        {"id": "item_301", "name": "Rajma Chawal", "price": 149.0, "is_veg": True, "category": "Rice", "avg_rating": 4.2, "tags": ["north_indian"]},
        {"id": "item_302", "name": "Veg Fried Rice + Manchurian", "price": 169.0, "is_veg": True, "category": "Chinese", "avg_rating": 4.0, "tags": ["chinese"]},
    ],
}


def _cart_total(item_price: float) -> dict:
    """Simulate realistic fee breakdown."""
    delivery_fee = 0.0 if item_price >= 149 else 30.0   # Gold: free delivery
    platform_fee = 8.0
    gst = round(item_price * 0.05, 2)
    grand_total = round(item_price + delivery_fee + platform_fee + gst, 2)
    return {
        "delivery_fee": delivery_fee,
        "platform_fee": platform_fee,
        "gst": gst,
        "grand_total": grand_total,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/tool/searchRestaurants")
async def search_restaurants(body: dict) -> JSONResponse:
    radius = body.get("radius_km", 7.0)
    filtered = [r for r in MOCK_RESTAURANTS if r["distance"] <= radius]
    return JSONResponse({"restaurants": filtered})


@app.post("/tool/getMenu")
async def get_menu(body: dict) -> JSONResponse:
    rid = body.get("restaurant_id", "")
    items = MOCK_MENUS.get(rid, [])
    return JSONResponse({"menu": items, "restaurant_id": rid})


@app.post("/tool/addToCart")
async def add_to_cart(body: dict) -> JSONResponse:
    rid = body.get("restaurant_id", "")
    iid = body.get("item_id", "")
    item_price = 0.0
    for item in MOCK_MENUS.get(rid, []):
        if item["id"] == iid:
            item_price = item["price"]
            break
    fees = _cart_total(item_price)
    return JSONResponse({
        "cart_id": f"cart_{rid}_{iid}",
        "restaurant_id": rid,
        "item_id": iid,
        "item_price": item_price,
        **fees,
    })


@app.post("/tool/checkout")
async def checkout(body: dict) -> JSONResponse:
    cart_id = body.get("cart_id", "unknown")
    return JSONResponse({
        "order_id": f"ord_{cart_id[-6:]}",
        "payment_url": "upi://pay?pa=zomato@upi&pn=Zomato&am=236.00&tn=AutoLunch+Order",
        "qr_code_url": "https://mock-zomato.local/qr/sample.png",
        "amount": 236.0,
        "estimated_delivery_minutes": 32,
    })


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "AutoLunch Mock Zomato MCP"})
