"""Resolve an Xbox add-on's parent GAME via Microsoft's public displaycatalog.

displaycatalog needs no auth. Each Durable/Consumable product carries a
`RelatedProducts` entry of type "addOnParent" pointing at the base game's Store
product id; we keep it only when that parent's ProductType is "Game". Responses are
cached on disk (mirrors steam_dlc's appdetails cache). The network fetch is injected
so tests run offline.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from addon_parent import ParentRef

logger = logging.getLogger(__name__)

CATALOG_URL = "https://displaycatalog.mp.microsoft.com/v7.0/products"
MARKET = "US"
LANGUAGES = "en-US"
ADDON_PARENT_REL = "addOnParent"
PARENT_GAME_TYPE = "Game"
BATCH_SIZE = 20           # displaycatalog bigIds cap is generous; stay modest
REQUEST_DELAY_S = 0.4
CACHE_DIR = Path(__file__).parent.parent / ".xbox_cache"

# fetch(ids) -> {product_id: product_dict | None}
CatalogFetch = Callable[[list[str]], "dict[str, dict | None]"]


def _parent_id_of(product: dict | None) -> str | None:
    """The product's addOnParent RelatedProductId, or None."""
    if not product:
        return None
    mp = (product.get("MarketProperties") or [{}])[0]
    for rel in mp.get("RelatedProducts") or []:
        if rel.get("RelationshipType") == ADDON_PARENT_REL and rel.get("RelatedProductId"):
            return rel["RelatedProductId"]
    return None


def _title_of(product: dict | None) -> str | None:
    if not product:
        return None
    loc = (product.get("LocalizedProperties") or [{}])[0]
    return loc.get("ProductTitle")


def _fetch_products(ids: list[str], *, cache_dir: Path = CACHE_DIR,
                    session=requests, delay_s: float = REQUEST_DELAY_S) -> dict[str, dict | None]:
    """Return {id: product_dict | None}, cached per id on disk.

    Cache hits skip the network. Misses are fetched via the bigIds batch endpoint
    in chunks; a not-found id is cached as an empty object so it isn't refetched.
    """
    cache_dir = Path(cache_dir)
    out: dict[str, dict | None] = {}
    misses: list[str] = []
    for pid in ids:
        f = cache_dir / f"{pid}.json"
        if f.exists():
            out[pid] = json.loads(f.read_text(encoding="utf-8")) or None
        else:
            misses.append(pid)
    for i in range(0, len(misses), BATCH_SIZE):
        chunk = misses[i:i + BATCH_SIZE]
        params = {"bigIds": ",".join(chunk), "market": MARKET,
                  "languages": LANGUAGES, "fieldsTemplate": "details"}
        try:
            resp = session.get(CATALOG_URL, params=params, timeout=30)
            resp.raise_for_status()
            products = (resp.json() or {}).get("Products") or []
        except (requests.RequestException, json.JSONDecodeError) as exc:
            logger.warning("xbox displaycatalog batch failed (%s): %s", chunk, exc)
            for pid in chunk:
                out[pid] = None          # this run only; NOT cached, retried next scrape
            if delay_s:
                time.sleep(delay_s)
            continue
        by_id = {p.get("ProductId"): p for p in products if p.get("ProductId")}
        cache_dir.mkdir(parents=True, exist_ok=True)
        for pid in chunk:
            prod = by_id.get(pid)
            (cache_dir / f"{pid}.json").write_text(
                json.dumps(prod or {}, ensure_ascii=False), encoding="utf-8")
            out[pid] = prod
        if delay_s:
            time.sleep(delay_s)
    return out


def resolve_addon_parents(product_ids: list[str], *,
                          fetch: CatalogFetch = _fetch_products) -> dict[str, ParentRef | None]:
    """Map each add-on product id to its parent GAME ParentRef (or None).

    Two passes: fetch the add-ons, read each one's addOnParent id, then fetch those
    parents and keep only ProductType == "Game", returning their id + title.
    """
    from addon_parent import ParentRef  # local import breaks addon_parent<->xbox_catalog cycle

    addons = fetch(list(dict.fromkeys(product_ids)))
    parent_ids: dict[str, str | None] = {pid: _parent_id_of(p) for pid, p in addons.items()}
    wanted = list({pid for pid in parent_ids.values() if pid})
    parents = fetch(wanted) if wanted else {}

    result: dict[str, ParentRef | None] = {}
    for pid in product_ids:
        ppid = parent_ids.get(pid)
        parent = parents.get(ppid) if ppid else None
        if parent and parent.get("ProductType") == PARENT_GAME_TYPE:
            result[pid] = ParentRef(product_id=ppid, name=_title_of(parent))
        else:
            result[pid] = None
    return result
