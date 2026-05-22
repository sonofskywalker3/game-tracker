"""Xbox library scraper.

Owned games come from the Microsoft account order history, which is rendered from
an API (account.microsoft.com/billing/orders/list) rather than the HTML. The
default view only shows ~30 days, so the full range must be selected during recon.
collect() is implemented from the captured API once recon confirms the
full-date-range response shape.
"""
from __future__ import annotations

VENDOR_URL = "https://account.microsoft.com/billing/orders"
SOURCE = "xbox"
