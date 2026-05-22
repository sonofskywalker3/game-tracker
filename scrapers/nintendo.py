"""Nintendo library scraper.

Owned games come from the eShop transaction history, reached via the account
portal (Funds and Payment Methods -> Purchase History -> Transaction History).
Online history only goes back ~2 years. Login requires a real-browser channel to
pass Nintendo's bot detection. collect() is implemented from the captured API
once recon succeeds.
"""
from __future__ import annotations

VENDOR_URL = "https://accounts.nintendo.com/"
SOURCE = "nintendo"
