"""
AutoLunch — OpenRouter Connection Test
Run: python scripts/test_openrouter.py

Verifies:
  1. .env is properly configured with OPENROUTER_API_KEY
  2. OpenRouter API call succeeds
  3. Structured JSON output (LLMOrderDecision schema) works
  4. preferences.json and memory.json load without errors
"""
import sys
import json
import asyncio
from pathlib import Path

# ── Bootstrap path so we can import autolunch from project root ───────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autolunch.config.settings import settings
from autolunch.core.logging import setup_logging
from autolunch.repositories import get_preferences_repository, get_memory_repository

console = Console()
setup_logging()


async def test_openrouter_connection() -> bool:
    """Test basic OpenRouter connectivity."""
    console.print("\n[bold cyan]── Test 1: OpenRouter API Connection ──[/bold cyan]")
    try:
        client = AsyncOpenAI(
            api_key=settings.openrouter.api_key,
            base_url=settings.openrouter.base_url,
        )
        response = await client.chat.completions.create(
            model=settings.openrouter.model,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly: {\"status\": \"ok\", \"message\": \"AutoLunch connected!\"}",
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=50,
        )
        result = json.loads(response.choices[0].message.content)
        console.print(f"[green]✓ OpenRouter OK[/green] — Model: [yellow]{settings.openrouter.model}[/yellow]")
        console.print(f"  Response: {result}")
        return True
    except Exception as e:
        console.print(f"[red]✗ OpenRouter FAILED: {e}[/red]")
        return False


async def test_structured_output() -> bool:
    """Test that the LLM can return a structured JSON matching LLMOrderDecision schema."""
    console.print("\n[bold cyan]── Test 2: Structured JSON Output ──[/bold cyan]")
    try:
        client = AsyncOpenAI(
            api_key=settings.openrouter.api_key,
            base_url=settings.openrouter.base_url,
        )
        prompt = """
You are a lunch ordering assistant. Return a JSON object for a mock meal suggestion.

Return EXACTLY this JSON schema:
{
  "restaurant_name": "string",
  "restaurant_id": "string",
  "item_name": "string",
  "item_id": "string",
  "base_price": number,
  "estimated_net_total": number,
  "reasoning": "string",
  "confidence": number between 0 and 1
}

Suggest: a vegetarian North Indian meal under ₹250 net total.
"""
        response = await client.chat.completions.create(
            model=settings.openrouter.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        raw = response.choices[0].message.content
        parsed = json.loads(raw)

        # Validate required fields
        from autolunch.models.restaurant import LLMOrderDecision
        decision = LLMOrderDecision.model_validate(parsed)

        table = Table(title="Mock LLM Decision", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Restaurant", decision.restaurant_name)
        table.add_row("Item", decision.item_name)
        table.add_row("Base Price", f"₹{decision.base_price}")
        table.add_row("Est. Net Total", f"₹{decision.estimated_net_total}")
        table.add_row("Reasoning", decision.reasoning[:80] + "..." if len(decision.reasoning) > 80 else decision.reasoning)
        table.add_row("Confidence", f"{decision.confidence:.2f}")
        console.print(table)
        console.print("[green]✓ Structured output validated against LLMOrderDecision schema[/green]")
        return True
    except Exception as e:
        console.print(f"[red]✗ Structured output FAILED: {e}[/red]")
        return False


def test_data_files() -> bool:
    """Test that preferences.json and memory.json load correctly."""
    console.print("\n[bold cyan]── Test 3: Data File Loading ──[/bold cyan]")
    try:
        prefs_repo = get_preferences_repository(settings.data_dir)
        prefs = prefs_repo.load()
        console.print(f"[green]✓ preferences.json loaded[/green]")
        console.print(f"  Diet: {prefs.diet_type} | Spice: {prefs.spice_tolerance}/5 | Budget: ₹{prefs.max_net_budget_inr}")

        mem_repo = get_memory_repository(settings.data_dir)
        memory = mem_repo.load()
        console.print(f"[green]✓ memory.json loaded[/green]")
        console.print(f"  Past orders: {len(memory.past_orders)} | Rejections: {len(memory.rejections)}")
        return True
    except Exception as e:
        console.print(f"[red]✗ Data file loading FAILED: {e}[/red]")
        return False


async def main() -> None:
    console.print(Panel.fit(
        "[bold white]AutoLunch — Milestone 1 Connection Tests[/bold white]\n"
        "[dim]Verifying OpenRouter API, structured output, and data files[/dim]",
        border_style="bright_blue",
    ))

    results = {
        "OpenRouter API": await test_openrouter_connection(),
        "Structured JSON Output": await test_structured_output(),
        "Data Files": test_data_files(),
    }

    console.print("\n[bold]── Summary ──[/bold]")
    all_passed = True
    for test_name, passed in results.items():
        status = "[green]PASS ✓[/green]" if passed else "[red]FAIL ✗[/red]"
        console.print(f"  {status}  {test_name}")
        if not passed:
            all_passed = False

    if all_passed:
        console.print(Panel.fit(
            "🎉 [bold green]All tests passed! Milestone 1 complete.[/bold green]\n"
            "You can now proceed to Milestone 2 (Zomato MCP integration).",
            border_style="green",
        ))
    else:
        console.print(Panel.fit(
            "[bold red]Some tests failed.[/bold red]\n"
            "Check your .env file and ensure all required keys are set.",
            border_style="red",
        ))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
