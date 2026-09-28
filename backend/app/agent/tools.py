"""Tool implementations the agent can call. Each function does its own DB-backed
caching so repeat questions (same city/restaurant) skip the slow scraping/LLM work
and read from SQLite/Chroma instead."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.db.models import MenuItem, Restaurant, Review
from app.db.session import get_session
from app.db.vectorstore import delete_menu_items_for_restaurant
from app.db.vectorstore import search_menu_items as vector_search_menu_items
from app.db.vectorstore import search_reviews as vector_search_reviews
from app.db.vectorstore import upsert_menu_items, upsert_reviews
from app.services import llm, osm_client, rerank, scraper, web_search

logger = logging.getLogger(__name__)


def _within(dt: datetime | None, days: int) -> bool:
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt < timedelta(days=days)


def _fresh(dt: datetime | None) -> bool:
    return _within(dt, settings.cache_freshness_days)


def _in_failure_cooldown(dt: datetime | None) -> bool:
    """A shorter window than the success-freshness cache: a failed scrape (dead
    link, no website found, JS-rendered site) is unlikely to resolve itself
    quickly, so we skip re-attempting it on every single live query - but it's
    also shorter than the success TTL, since transient failures (a timeout, a
    429) are worth retrying sooner than a genuinely stale-but-working menu."""
    return _within(dt, settings.failure_retry_cooldown_days)


_NAME_STOPWORDS = {
    "restaurant", "restaurante", "bar", "cafe", "café", "the", "el", "la", "los", "las",
    "de", "del", "und", "and", "&", "co", "house", "kitchen",
}


def _resolve_restaurant(session, restaurant_id: str, name: str, city: str) -> Restaurant | None:
    """Tool-calling models occasionally mistranscribe the exact id string across a
    multi-step conversation; fall back to a name+city match rather than failing
    outright, since we already have both from the same tool call."""
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is not None:
        return restaurant
    return session.execute(
        select(Restaurant).where(Restaurant.name == name, Restaurant.city == city)
    ).scalars().first()


def _looks_relevant(name: str, text: str) -> bool:
    """Cheap sanity check that a scraped page actually belongs to this restaurant,
    since the free web-search fallback occasionally returns an unrelated site."""
    text_lower = text.lower()
    words = [w.strip(".,'\"()") for w in name.lower().split()]
    significant = [w for w in words if len(w) >= 4 and w not in _NAME_STOPWORDS]
    if not significant:
        significant = words  # very short name (e.g. "Tapeo"); use whatever we have
    return any(w in text_lower for w in significant)


MAX_CANDIDATES_RETURNED = 12


def _candidate_quality(c: dict) -> tuple[bool, bool, bool, bool]:
    # already-proven-good (a real scrape succeeded before) beats everything else,
    # since it's a stronger signal than any OSM tag ever is.
    return (bool(c.get("known_good")), bool(c.get("diet_hints")), bool(c.get("website")), bool(c.get("address")))


def _get_known_good_restaurants(city: str) -> list[dict]:
    """Restaurants in this city we've already successfully scraped a menu for -
    whether via a prior live chat turn or an offline seed run (see scripts/seed_data.py).
    Surfacing these first means a populated cache actually gets used by search, instead of
    depending on OSM's live query happening to return the same restaurant again."""
    with get_session() as session:
        rows = session.execute(select(Restaurant).where(Restaurant.city == city)).scalars().all()
        return [
            {
                "id": r.id, "name": r.name, "address": r.address, "osm_url": r.osm_url,
                "website": r.official_website, "phone": r.phone, "cuisine": r.categories,
                "diet_hints": {}, "latitude": r.latitude, "longitude": r.longitude,
                "known_good": True,
            }
            for r in rows if r.menu_items
        ]


def upsert_restaurant_candidates(city: str, candidates: list[dict]) -> list[dict]:
    """Writes OSM-shaped candidate dicts into the DB and returns the same shape the
    agent/seed script consume. Public (no leading underscore) since scripts/seed_data.py
    calls this directly with a much larger, uncapped candidate pool than live search uses."""
    results = []
    with get_session() as session:
        for c in candidates:
            existing = session.get(Restaurant, c["id"])
            if existing is None:
                existing = Restaurant(id=c["id"])
                session.add(existing)
            existing.name = c["name"]
            existing.city = city
            existing.address = c.get("address", "")
            existing.osm_url = c.get("osm_url", "")
            existing.phone = c.get("phone")
            existing.categories = c.get("cuisine", "")
            existing.latitude = c.get("latitude")
            existing.longitude = c.get("longitude")
            existing.last_synced_osm_at = datetime.now(timezone.utc)
            if c.get("website") and not existing.official_website and not web_search.is_excluded_domain(c["website"]):
                existing.official_website = c["website"]

            results.append({
                "id": existing.id,
                "name": existing.name,
                "address": existing.address,
                "osm_url": existing.osm_url,
                "categories": existing.categories,
                "diet_hints": c.get("diet_hints", {}),
                "menu_available": bool(existing.menu_items),
            })
    return results


def search_restaurants(city: str, query: str) -> list[dict]:
    """Discovery via OpenStreetMap - free, keyless - merged with any restaurant in this
    city we already know is good (a real scrape succeeded before), which are prioritized
    to the front so a warmed cache (including an offline seed run) actually gets used."""
    osm_candidates = osm_client.search_restaurants(city=city, query=query)
    known_good = _get_known_good_restaurants(city)

    merged: dict[str, dict] = {c["id"]: c for c in osm_candidates}
    for kg in known_good:
        merged[kg["id"]] = {**merged.get(kg["id"], {}), **kg}  # known_good data wins on conflict

    # OSM can return dozens of hits; keep only the most promising ones so the agent
    # isn't juggling a huge list of ids across several follow-up tool calls.
    candidates = sorted(merged.values(), key=_candidate_quality, reverse=True)[:MAX_CANDIDATES_RETURNED]
    return upsert_restaurant_candidates(city, candidates)


def get_reviews_for_restaurant(restaurant_id: str, name: str, city: str) -> dict:
    """Reviews are sourced from the restaurant's own website (a testimonials/reviews
    page if it has one, else scanned off the homepage) - no third-party review API
    needed. Important caveat, surfaced to the caller: a business's own site only ever
    shows testimonials IT chose to publish, so this skews positive and is not a
    substitute for an independent, unfiltered review platform."""
    with get_session() as session:
        restaurant = _resolve_restaurant(session, restaurant_id, name, city)
        if restaurant is None:
            return {"reviews": [], "note": "Unknown restaurant id."}

        existing_reviews = session.execute(
            select(Review).where(Review.restaurant_id == restaurant.id)
        ).scalars().all()
        if existing_reviews:
            return {
                "reviews": [
                    {"text": r.translated_text, "sentiment": r.sentiment, "author": r.author}
                    for r in existing_reviews
                ],
                "caveat": "Sourced from the restaurant's own website, not an independent platform - likely skewed positive.",
            }

        if _in_failure_cooldown(restaurant.review_attempt_failed_at):
            return {"reviews": [], "note": "No testimonials found on a recent check for this restaurant; not re-attempting yet."}

        def _fail(note: str) -> dict:
            # `restaurant` is already persistent (loaded via _resolve_restaurant), so
            # mutating it is enough - no session.add() needed, and calling it here
            # would cascade into any deleted children still referenced by a stale
            # relationship collection (see the menu equivalent of this function).
            restaurant.review_attempt_failed_at = datetime.now(timezone.utc)
            return {"reviews": [], "note": note}

        website = restaurant.official_website
        discovered_via_search = False
        if not website:
            website = web_search.find_official_website(name, city)
            discovered_via_search = True
            restaurant.menu_website_checked = True

        if not website:
            return _fail("Could not locate an official website for this restaurant.")

        testimonial_text_result = scraper.get_testimonial_text(website)
        if testimonial_text_result is None:
            return _fail(f"Website found ({website}) but could not be fetched.")

        raw_text, source_url = testimonial_text_result

        if discovered_via_search and not _looks_relevant(name, raw_text):
            return _fail(f"Found a website ({website}) via search but it doesn't appear to actually be {name}'s site; skipping rather than risk wrong review data.")

        restaurant.official_website = website
        extraction = llm.extract_testimonials(raw_text)

        if not extraction.items:
            return _fail("Website reached but no customer testimonials could be found on it.")

        reviews = [
            Review(
                restaurant_id=restaurant.id,
                source="website_testimonial",
                author=item.author,
                original_text=item.original_text,
                original_language=item.original_language,
                translated_text=item.translated_text,
                sentiment=item.sentiment,
            )
            for item in extraction.items
        ]
        session.add_all(reviews)
        session.flush()  # assigns .id to every review in one round-trip

        upsert_reviews([
            {"review_id": r.id, "restaurant_id": restaurant.id, "text": r.translated_text,
             "rating": None, "sentiment": r.sentiment}
            for r in reviews
        ])
        out = [{"text": r.translated_text, "sentiment": r.sentiment, "author": r.author} for r in reviews]

        restaurant.review_attempt_failed_at = None

        return {
            "reviews": out,
            "caveat": "Sourced from the restaurant's own website, not an independent platform - likely skewed positive.",
        }


def find_and_scrape_menu(restaurant_id: str, name: str, city: str) -> dict:
    with get_session() as session:
        restaurant = _resolve_restaurant(session, restaurant_id, name, city)
        if restaurant is None:
            return {"items": [], "note": "Unknown restaurant id."}

        if restaurant.menu_items and _fresh(restaurant.last_scraped_menu_at):
            items = [
                {
                    "name": mi.translated_name,
                    "description": mi.translated_description,
                    "ingredients": mi.ingredients.split(", ") if mi.ingredients else [],
                    "ingredients_source": mi.ingredients_source,
                    "price": mi.price,
                    "currency": mi.currency,
                    "category": mi.category,
                }
                for mi in restaurant.menu_items
            ]
            return {"items": items, "source_url": restaurant.official_website, "cached": True}

        if not restaurant.menu_items and _in_failure_cooldown(restaurant.menu_attempt_failed_at):
            return {"items": [], "note": "No menu found on a recent check for this restaurant; not re-attempting yet."}

        def _fail(note: str) -> dict:
            # no session.add() here - restaurant is already persistent, and adding it
            # explicitly cascades into any just-deleted MenuItem children still held by
            # a stale relationship collection, raising "has been deleted" on flush.
            restaurant.menu_attempt_failed_at = datetime.now(timezone.utc)
            return {"items": [], "note": note}

        website = restaurant.official_website
        discovered_via_search = False
        if not website:
            website = web_search.find_official_website(name, city)
            discovered_via_search = True
            restaurant.menu_website_checked = True

        if not website:
            return _fail("Could not locate an official website for this restaurant.")

        menu_text_result = scraper.get_menu_text(website)
        if menu_text_result is None:
            return _fail(f"Website found ({website}) but could not be fetched.")

        raw_text, source_url = menu_text_result

        if discovered_via_search and not _looks_relevant(name, raw_text):
            return _fail(f"Found a website ({website}) via search but it doesn't appear to actually be {name}'s site; skipping rather than risk wrong menu data.")

        extraction = llm.extract_menu_items(raw_text)

        if not extraction.items:
            # deliberately don't touch any existing cached menu_items here - a failed
            # re-scrape (e.g. the site is briefly down) should never wipe out
            # previously-good data, only a successful one should replace it.
            return _fail("Website reached but no menu items could be parsed.")

        restaurant.official_website = website

        stale_ids = [mi.id for mi in restaurant.menu_items]
        for mi in restaurant.menu_items:
            session.delete(mi)
        session.flush()
        delete_menu_items_for_restaurant(stale_ids)

        menu_items = [
            MenuItem(
                restaurant_id=restaurant.id,
                original_name=item.original_name,
                original_description=item.original_description,
                translated_name=item.translated_name,
                translated_description=item.translated_description,
                ingredients=", ".join(item.ingredients),
                ingredients_source=item.ingredients_source,
                price=item.price,
                currency=item.currency,
                category=item.category,
                source_url=source_url,
            )
            for item in extraction.items
        ]
        session.add_all(menu_items)
        session.flush()  # assigns .id to every row in one round-trip, instead of per-item

        upsert_menu_items([
            {
                "menu_item_id": mi.id, "restaurant_id": restaurant.id,
                "text": f"{mi.translated_name}. {mi.translated_description}".strip(),
                "price": mi.price, "currency": mi.currency,
                "ingredients": mi.ingredients, "ingredients_source": mi.ingredients_source,
                "category": mi.category,
            }
            for mi in menu_items
        ])

        items_out = [
            {
                "name": mi.translated_name,
                "description": mi.translated_description,
                "ingredients": mi.ingredients.split(", ") if mi.ingredients else [],
                "ingredients_source": mi.ingredients_source,
                "price": mi.price,
                "currency": mi.currency,
                "category": mi.category,
            }
            for mi in menu_items
        ]

        restaurant.last_scraped_menu_at = datetime.now(timezone.utc)
        restaurant.menu_attempt_failed_at = None

        return {"items": items_out, "source_url": source_url, "cached": False}


# A safety net, not just a prompt instruction: live testing showed the agent searching
# with query="dairy-free" instead of populating exclude_ingredients, which only does a
# *soft* similarity match for a *hard* safety constraint and let dairy-containing dishes
# through. Rather than trust tool-calling compliance alone for an allergy-adjacent
# feature, common diet-restriction phrases are recognized here in code and their
# ingredient exclusions are always applied, whether or not the caller remembered to.
_DIET_EXCLUSION_KEYWORDS: dict[str, list[str]] = {
    # Each list includes its own trigger word too (e.g. "dairy" in the dairy list) -
    # a menu extraction or the model's own write-up sometimes labels a dish generically
    # ("contains dairy") rather than always naming the specific ingredient, so the
    # generic label itself needs to be a filterable term, not just its expansions.
    "dairy": ["dairy", "milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "ghee", "paneer", "whey", "curd"],
    "lactose": ["dairy", "milk", "cheese", "butter", "cream", "yogurt", "yoghurt", "ghee", "paneer", "whey", "curd"],
    "gluten": ["gluten", "wheat", "flour", "bread", "pasta", "barley", "rye", "breadcrumb", "breadcrumbs", "semolina"],
    "nut": ["nut", "peanut", "almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan"],
    "egg": ["egg", "eggs", "mayonnaise"],
    "shellfish": ["shellfish", "shrimp", "prawn", "crab", "lobster", "mussel", "clam", "oyster", "scallop"],
    "vegan": ["milk", "cheese", "butter", "cream", "yogurt", "egg", "eggs", "honey",
              "meat", "chicken", "beef", "pork", "fish", "gelatin"],
}


def _auto_exclusions_from_query(query: str) -> list[str]:
    q = query.lower()
    extra: list[str] = []
    for trigger, ingredients in _DIET_EXCLUSION_KEYWORDS.items():
        if trigger in q:
            extra.extend(ingredients)
    return extra


def _enrich_with_restaurant_identity(items: list[dict]) -> list[dict]:
    """These results only carry restaurant_id, which forces the model to mentally
    map id -> name/address from earlier context when writing its final answer - a
    real bug caught live: it mismatched a dish to the wrong restaurant's address and
    invented a name for the result ("Tofu Ali") rather than getting this right. Stamp
    the real name/address onto every row here so there's nothing left to reconstruct
    or guess - the fix is giving the model the right data, not asking it to be more
    careful with the wrong data."""
    ids = {i["restaurant_id"] for i in items if i.get("restaurant_id")}
    if not ids:
        return items
    with get_session() as session:
        # extract plain values while the session is open - the ORM objects
        # themselves become unusable once it closes (DetachedInstanceError)
        identities = {
            rid: (r.name, r.address) if (r := session.get(Restaurant, rid)) else (None, None)
            for rid in ids
        }
    for item in items:
        name, address = identities.get(item.get("restaurant_id"), (None, None))
        item["restaurant_name"] = name
        item["restaurant_address"] = address
    return items


def semantic_search_menu_items(restaurant_ids: list[str], query: str, exclude_ingredients: list[str] | None = None) -> list[dict]:
    """Hybrid retrieval over already-scraped menu items: vector similarity for the
    *soft* part of the query (e.g. "something light and comforting"), reranked by
    the LLM against the exact query, with excluded ingredients applied as a hard,
    deterministic post-filter on the stored ingredient list - never left to
    embedding similarity, since a missed allergen is a safety issue."""
    candidates = vector_search_menu_items(query, restaurant_ids, n_results=20)

    all_exclusions = list(exclude_ingredients or []) + _auto_exclusions_from_query(query)
    if all_exclusions:
        excluded_lower = [e.lower() for e in all_exclusions]
        candidates = [
            c for c in candidates
            if not any(ex in (c.get("ingredients") or "").lower() for ex in excluded_lower)
        ]

    ranked = rerank.rerank(query, candidates, text_key="text", top_k=8)
    return _enrich_with_restaurant_identity(ranked)


def semantic_search_reviews(restaurant_ids: list[str], query: str) -> list[dict]:
    with get_session() as session:
        for rid in restaurant_ids:
            restaurant = session.get(Restaurant, rid)
            if restaurant is not None and not restaurant.reviews:
                get_reviews_for_restaurant(rid, restaurant.name, restaurant.city)

    candidates = vector_search_reviews(query, restaurant_ids, n_results=15)
    ranked = rerank.rerank(query, candidates, text_key="text", top_k=6)
    return _enrich_with_restaurant_identity(ranked)
