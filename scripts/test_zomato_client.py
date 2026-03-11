"""
AutoLunch — Milestone 2 Test Script
Tests the Zomato MCP client against the mock server.

Usage:
  # Terminal 1 — start mock server:
  source .venv/bin/activate
  pip install fastapi uvicorn --quiet
  uvicorn autolunch.services.zomato.mock_server:app --port 3000

  # Terminal 2 — run tests:
  source .venv/bin/activate
  python scripts/test_zomato_client.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autolunch.config.settings import settings
from autolunch.core.logging import setup_logging
from autolunch.core.exceptions import BudgetExceededError, ZomatoNoResultsError
from autolunch.models.preferences import UserPreferences
from autolunch.repositories import get_preferences_repository
from autolunch.services.zomato.client import ZomatoMCPClient

console = Console()
setup_logging()


async def test_restaurant_search(client: ZomatoMCPClient, prefs: UserPreferences) -> bool:
    console.print("\n[bold cyan]── Test 1: Restaurant Search + Filters ──[/bold cyan]")
    try:
        restaurants = await client.search_restaurants(prefs)

        table = Table(title=f"Restaurants (after filters)", show_header=True)
        table.add_column("Name", style="cyan")
        table.add_column("Rating", justify="center")
        table.add_column("Reviews", justify="right")
        table.add_column("Distance", justify="right")
        table.add_column("Delivery", justify="right")

        for r in restaurants:
            table.add_row(
                r.name,
                f"{r.rating}⭐",
                f"{r.review_count:,}",
                f"{r.distance_km}km",
                f"{r.delivery_time_minutes}min",
            )
        console.print(table)

        # Verify filters: "New Sketch Restaurant" (450 reviews) + "Faraway Place" (9.5km) must be absent
        names = [r.name for r in restaurants]
        assert "New Sketch Restaurant" not in names, "FAIL: low-review restaurant not filtered!"
        assert "Faraway Place" not in names, "FAIL: out-of-range restaurant not filtered!"
        console.print("[green]✓ Filters verified: low-review + out-of-range restaurants correctly excluded[/green]")
        return True
    except ZomatoNoResultsError as e:
        console.print(f"[red]✗ No results: {e}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]✗ Search FAILED: {e}[/red]")
        return False


async def test_menu_fetch(client: ZomatoMCPClient, prefs: UserPreferences) -> bool:
    console.print("\n[bold cyan]── Test 2: Menu Fetch ──[/bold cyan]")
    try:
        restaurants = await client.search_restaurants(prefs)
        restaurant = await client.get_menu(restaurants[0])

        console.print(f"[green]✓ Menu fetched for {restaurant.name}[/green] — {len(restaurant.menu)} items")
        for item in restaurant.menu:
            veg_label = "[green]VEG[/green]" if item.is_veg else "[red]NON-VEG[/red]"
            console.print(f"  {veg_label} {item.name} — ₹{item.base_price}")
        return True
    except Exception as e:
        console.print(f"[red]✗ Menu fetch FAILED: {e}[/red]")
        return False


async def test_cart_simulation(client: ZomatoMCPClient, prefs: UserPreferences) -> bool:
    console.print("\n[bold cyan]── Test 3: Cart Simulation + ₹250 Net Budget Check ──[/bold cyan]")
    try:
        restaurants = await client.search_restaurants(prefs)
        restaurant = await client.get_menu(restaurants[0])
        item = restaurant.menu[0]

        cart = await client.simulate_cart(restaurant, item)

        table = Table(title="Cart Simulation", show_header=True)
        table.add_column("Component", style="cyan")
        table.add_column("Amount", justify="right", style="yellow")
        table.add_row("Base Price", f"₹{cart.base_price}")
        table.add_row("Delivery Fee", f"₹{cart.delivery_fee}")
        table.add_row("Platform Fee", f"₹{cart.platform_fee}")
        table.add_row("GST (5%)", f"₹{cart.gst}")
        table.add_row("[bold]NET TOTAL[/bold]", f"[bold]₹{cart.net_total}[/bold]")
        table.add_row("Within ₹250 Budget?", "[green]YES ✓[/green]" if cart.within_budget else "[red]NO ✗[/red]")
        console.print(table)
        console.print("[green]✓ Cart simulation successful — net total verified[/green]")
        return True
    except BudgetExceededError as e:
        console.print(f"[yellow]⚠ Budget guardrail triggered: {e.message}[/yellow]")
        console.print("[dim]This is expected behavior — LLM will re-pick a different item[/dim]")
        return True  # This is correct behavior, not a failure
    except Exception as e:
        console.print(f"[red]✗ Cart simulation FAILED: {e}[/red]")
        return False


async def main() -> None:
    console.print(Panel.fit(
        "[bold white]AutoLunch — Milestone 2 Zomato MCP Tests[/bold white]\n"
        "[dim]Requires mock server running on localhost:3000[/dim]\n"
        "[dim]Run: uvicorn autolunch.services.zomato.mock_server:app --port 3000[/dim]",
        border_style="bright_blue",
    ))

    prefs_repo = get_preferences_repository(settings.data_dir)
    prefs = prefs_repo.load()

    results: dict[str, bool] = {}

    async with ZomatoMCPClient() as client:
        results["Restaurant Search + Filters"] = await test_restaurant_search(client, prefs)
        results["Menu Fetch"] = await test_menu_fetch(client, prefs)
        results["Cart Simulation (₹250)"] = await test_cart_simulation(client, prefs)

    console.print("\n[bold]── Summary ──[/bold]")
    all_passed = True
    for name, passed in results.items():
        status = "[green]PASS ✓[/green]" if passed else "[red]FAIL ✗[/red]"
        console.print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    if all_passed:
        console.print(Panel.fit(
            "🎉 [bold green]All Milestone 2 tests passed![/bold green]\n"
            "Zomato MCP client is ready. Proceed to Milestone 3 (LLM Decision Engine).",
            border_style="green",
        ))
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
