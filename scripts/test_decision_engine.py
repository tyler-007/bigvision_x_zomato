"""
AutoLunch — Milestone 3 Test Script
Tests the full LLM decision engine end-to-end against the mock Zomato server.

Usage:
  # Terminal 1 — mock Zomato server:
  uvicorn autolunch.services.zomato.mock_server:app --port 3000

  # Terminal 2 — run tests:
  python scripts/test_decision_engine.py
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
from autolunch.core.exceptions import MaxRetriesExceededError, ZomatoNoResultsError
from autolunch.services.llm.engine import LLMDecisionEngine

console = Console()
setup_logging()


async def test_basic_decision() -> bool:
    """Test 1: Normal flow — LLM picks a meal within budget."""
    console.print("\n[bold cyan]── Test 1: Basic LLM Decision (Happy Path) ──[/bold cyan]")
    try:
        engine = LLMDecisionEngine()
        result = await engine.decide()

        table = Table(title="LLM Decision Result", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Restaurant", result.decision.restaurant_name)
        table.add_row("Item", result.decision.item_name)
        table.add_row("Base Price", f"₹{result.cart.base_price}")
        table.add_row("Delivery", f"₹{result.cart.delivery_fee} (Gold)")
        table.add_row("Platform Fee", f"₹{result.cart.platform_fee}")
        table.add_row("GST", f"₹{result.cart.gst}")
        table.add_row("[bold]Net Total[/bold]", f"[bold]₹{result.cart.net_total}[/bold]")
        table.add_row("Budget OK?", "[green]YES ✓[/green]" if result.cart.within_budget else "[red]NO ✗[/red]")
        table.add_row("Confidence", f"{result.decision.confidence:.0%}")
        table.add_row("Reasoning", result.decision.reasoning[:80] + "..." if len(result.decision.reasoning) > 80 else result.decision.reasoning)
        console.print(table)

        assert result.cart.net_total <= settings.zomato.max_budget_inr, \
            f"Net total ₹{result.cart.net_total} exceeds budget ₹{settings.zomato.max_budget_inr}"
        console.print("[green]✓ Decision validated: within budget[/green]")
        return True
    except Exception as e:
        console.print(f"[red]✗ Basic decision FAILED: {e}[/red]")
        return False


async def test_rejection_flow() -> bool:
    """Test 2: Rejection flow — user rejects, engine re-picks with constraint."""
    console.print("\n[bold cyan]── Test 2: Rejection Flow ──[/bold cyan]")
    try:
        engine = LLMDecisionEngine()

        # First pick
        result1 = await engine.decide()
        console.print(f"  First pick: [yellow]{result1.decision.item_name}[/yellow] from [yellow]{result1.decision.restaurant_name}[/yellow]")

        # Simulate user rejection
        await engine.record_rejection(result1, "Too heavy for today, want something lighter")
        console.print("  User rejected: [dim]'Too heavy for today, want something lighter'[/dim]")

        # Re-pick with rejection constraint
        constraint = f"User rejected '{result1.decision.item_name}' from '{result1.decision.restaurant_name}': too heavy. Suggest something lighter."
        result2 = await engine.decide(extra_constraints=[constraint])
        console.print(f"  New pick: [green]{result2.decision.item_name}[/green] from [green]{result2.decision.restaurant_name}[/green]")
        console.print(f"  Reasoning: [dim]{result2.decision.reasoning}[/dim]")

        console.print("[green]✓ Rejection flow complete — new suggestion generated[/green]")
        return True
    except Exception as e:
        console.print(f"[red]✗ Rejection flow FAILED: {e}[/red]")
        return False


async def test_slack_message() -> bool:
    """Test 3: Verify Slack message format is complete and correct."""
    console.print("\n[bold cyan]── Test 3: Slack Message Format ──[/bold cyan]")
    try:
        engine = LLMDecisionEngine()
        result = await engine.decide()

        console.print(Panel(
            result.slack_summary,
            title="[bold]Slack Preview[/bold]",
            border_style="blue",
        ))

        # Validate all key fields are present
        assert result.decision.restaurant_name in result.slack_summary
        assert str(result.cart.net_total) in result.slack_summary
        assert "NET TOTAL" in result.slack_summary
        console.print("[green]✓ Slack message format is complete[/green]")
        return True
    except Exception as e:
        console.print(f"[red]✗ Slack format FAILED: {e}[/red]")
        return False


async def main() -> None:
    console.print(Panel.fit(
        "[bold white]AutoLunch — Milestone 3 LLM Decision Engine Tests[/bold white]\n"
        "[dim]Requires: mock Zomato server on :3000 + valid OpenRouter API key[/dim]",
        border_style="bright_blue",
    ))

    results = {
        "Basic LLM Decision (Happy Path)": await test_basic_decision(),
        "Rejection Flow + Re-pick": await test_rejection_flow(),
        "Slack Message Format": await test_slack_message(),
    }

    console.print("\n[bold]── Summary ──[/bold]")
    all_passed = True
    for name, passed in results.items():
        status = "[green]PASS ✓[/green]" if passed else "[red]FAIL ✗[/red]"
        console.print(f"  {status}  {name}")
        if not passed:
            all_passed = False

    if all_passed:
        console.print(Panel.fit(
            "🎉 [bold green]All Milestone 3 tests passed![/bold green]\n"
            "LLM Decision Engine is ready. Proceed to Milestone 4 (n8n + Telegram HITL).",
            border_style="green",
        ))
    else:
        console.print(Panel.fit("[bold red]Some tests failed.[/bold red]", border_style="red"))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
