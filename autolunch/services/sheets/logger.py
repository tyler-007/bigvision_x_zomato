from __future__ import annotations
"""
AutoLunch — Google Sheets Order Logger

Logs every order to a Google Sheet with full breakdown:
date, restaurant, item, base_price, promo, discount, net_total, etc.

Setup:
  1. Create a Google Service Account → download JSON key
  2. Save to secrets/google_service_account.json
  3. Share your Google Sheet with the service account email
  4. Set GOOGLE_SHEET_ID in .env
"""
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from autolunch.config.settings import settings
from autolunch.core.exceptions import SheetsError

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sheet headers (auto-created on first run)
HEADERS = [
    "Date",
    "Time",
    "Restaurant",
    "Item",
    "Base Price (₹)",
    "Promo Code",
    "Promo Discount (₹)",
    "Delivery Fee (₹)",
    "Platform Fee (₹)",
    "GST (₹)",
    "Net Total (₹)",
    "Cart ID",
    "Status",
    "Source",
]


class SheetsLogger:
    """Logs orders to Google Sheets."""

    def __init__(self) -> None:
        if not settings.google:
            raise SheetsError("Google settings not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID in .env")

        sa_path = Path(settings.google.service_account_json)
        if not sa_path.exists():
            raise SheetsError(f"Service account JSON not found: {sa_path}")

        creds = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._sheet_id = settings.google.sheet_id
        self._ws = None

    def _get_worksheet(self) -> gspread.Worksheet:
        """Get or create the orders worksheet."""
        if self._ws:
            return self._ws

        sheet = self._gc.open_by_key(self._sheet_id)

        # Try to get "Orders" worksheet, create if missing
        try:
            ws = sheet.worksheet("Orders")
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title="Orders", rows=1000, cols=len(HEADERS))
            ws.append_row(HEADERS)
            ws.format("1", {"textFormat": {"bold": True}})
            logger.info("Created 'Orders' worksheet with headers")

        # Ensure headers exist
        first_row = ws.row_values(1)
        if not first_row or first_row[0] != HEADERS[0]:
            ws.insert_row(HEADERS, 1)

        self._ws = ws
        return ws

    def log_order(
        self,
        restaurant_name: str,
        item_name: str,
        base_price: float,
        promo_code: str = "",
        promo_discount: float = 0,
        delivery_fee: float = 0,
        platform_fee: float = 0,
        gst: float = 0,
        net_total: float = 0,
        cart_id: str = "",
        status: str = "approved",
        source: str = "autolunch",
    ) -> int:
        """
        Append an order row to the Google Sheet.
        Returns the row number.
        """
        ws = self._get_worksheet()

        now = datetime.now()
        row = [
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            restaurant_name,
            item_name,
            round(base_price, 2),
            promo_code,
            round(promo_discount, 2),
            round(delivery_fee, 2),
            round(platform_fee, 2),
            round(gst, 2),
            round(net_total, 2),
            cart_id,
            status,
            source,
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        row_num = len(ws.get_all_values())
        logger.info(f"Order logged to Sheet row {row_num}", restaurant=restaurant_name, item=item_name)
        return row_num

    def get_recent_orders(self, limit: int = 10) -> list[dict]:
        """Get the last N orders from the sheet."""
        ws = self._get_worksheet()
        all_rows = ws.get_all_records()
        return all_rows[-limit:] if all_rows else []


def get_sheets_logger() -> SheetsLogger | None:
    """Factory — returns None if Google isn't configured."""
    try:
        return SheetsLogger()
    except Exception as e:
        logger.debug(f"Sheets logger not available: {e}")
        return None
