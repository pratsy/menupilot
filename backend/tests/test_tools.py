from datetime import datetime, timedelta, timezone

from app.agent import tools
from app.config import settings
from app.db.models import MenuItem, Restaurant
from app.db.session import get_session
from app.schemas import MenuItemLLM


def test_looks_relevant_matches_significant_word():
    text = "Welcome to Biocenter, a vegetarian restaurant in the heart of Barcelona."
    assert tools._looks_relevant("Biocenter", text)


def test_looks_relevant_rejects_unrelated_page():
    text = "World Scholarship Forum - courses, grants and study abroad opportunities."
    assert not tools._looks_relevant("Tapeo", text)


def test_looks_relevant_ignores_stopwords_and_short_name():
    # "La Bascula" -> "la" is a stopword, "bascula" is the significant word
    text = "Bienvenidos a La Bàscula, restaurante en el Born."
    assert tools._looks_relevant("La Bàscula", text)


def test_candidate_quality_prefers_diet_hints_then_website_then_address():
    best = {"diet_hints": {"diet:vegetarian": "yes"}, "website": "https://x.com", "address": "1 Main St"}
    worst = {"diet_hints": {}, "website": None, "address": ""}
    assert tools._candidate_quality(best) > tools._candidate_quality(worst)


def test_resolve_restaurant_finds_by_correct_id(db_session):
    r = Restaurant(id="osm:node:1", name="Test Place", city="Barcelona")
    db_session.add(r)
    db_session.flush()

    resolved = tools._resolve_restaurant(db_session, "osm:node:1", "Test Place", "Barcelona")
    assert resolved is not None
    assert resolved.id == "osm:node:1"


def test_resolve_restaurant_falls_back_to_name_and_city_on_bad_id(db_session):
    r = Restaurant(id="osm:node:2", name="Sesamo", city="Barcelona")
    db_session.add(r)
    db_session.flush()

    # model hallucinated/mistranscribed a different id string
    resolved = tools._resolve_restaurant(db_session, "osm:node:999", "Sesamo", "Barcelona")
    assert resolved is not None
    assert resolved.id == "osm:node:2"


def test_resolve_restaurant_returns_none_when_truly_unknown(db_session):
    resolved = tools._resolve_restaurant(db_session, "osm:node:404", "Nonexistent", "Nowhere")
    assert resolved is None


def test_search_restaurants_shortlists_and_persists(monkeypatch):
    many_candidates = [
        {
            "id": f"osm:node:{i}", "name": f"Place {i}", "address": "" if i % 2 else "1 Main St",
            "osm_url": f"https://osm.org/node/{i}", "website": None, "phone": None,
            "cuisine": "vegetarian", "diet_hints": {"diet:vegetarian": "yes"} if i < 3 else {},
            "latitude": 41.0, "longitude": 2.0,
        }
        for i in range(30)
    ]
    monkeypatch.setattr(tools.osm_client, "search_restaurants", lambda city, query: many_candidates)

    results = tools.search_restaurants("Barcelona", "vegetarian dinner")

    assert len(results) == tools.MAX_CANDIDATES_RETURNED
    # the diet-tagged candidates should be prioritised to the front
    assert results[0]["diet_hints"]


def test_search_restaurants_skips_excluded_website_domains(monkeypatch):
    candidates = [{
        "id": "osm:node:1", "name": "Sabes una Cosa", "address": "1 Main St",
        "osm_url": "https://osm.org/node/1", "website": "https://linktr.ee/sabesunacosa",
        "phone": None, "cuisine": "", "diet_hints": {}, "latitude": 41.0, "longitude": 2.0,
    }]
    monkeypatch.setattr(tools.osm_client, "search_restaurants", lambda city, query: candidates)

    tools.search_restaurants("Barcelona", "vegetarian dinner")

    from app.db.session import get_session
    with get_session() as session:
        restaurant = session.get(Restaurant, "osm:node:1")
        assert restaurant.official_website is None  # linktr.ee should have been rejected


def test_semantic_search_menu_items_applies_hard_ingredient_exclusion(monkeypatch):
    candidates = [
        {"text": "Mushroom risotto", "restaurant_id": "r1", "price": 12.0, "currency": "EUR",
         "ingredients": "mushroom, rice, parmesan", "ingredients_source": "menu_stated"},
        {"text": "Tomato bruschetta", "restaurant_id": "r1", "price": 6.0, "currency": "EUR",
         "ingredients": "tomato, bread, basil", "ingredients_source": "menu_stated"},
    ]
    monkeypatch.setattr(tools, "vector_search_menu_items", lambda query, restaurant_ids, n_results=20: candidates)
    monkeypatch.setattr(tools.rerank, "rerank", lambda query, items, text_key="text", top_k=None: items)

    results = tools.semantic_search_menu_items(["r1"], "something tasty", exclude_ingredients=["mushroom"])

    assert len(results) == 1
    assert results[0]["text"] == "Tomato bruschetta"


def test_get_known_good_restaurants_requires_actual_menu_items():
    city = "KnownGoodCity1"
    with get_session() as session:
        session.add_all([
            Restaurant(id="osm:node:kg1", name="Has Menu", city=city),
            Restaurant(id="osm:node:kg2", name="No Menu Yet", city=city),
        ])
        session.flush()
        session.add(MenuItem(restaurant_id="osm:node:kg1", translated_name="Salad"))

    known_good = tools._get_known_good_restaurants(city)

    names = {r["name"] for r in known_good}
    assert names == {"Has Menu"}


def test_search_restaurants_prioritizes_known_good_even_when_osm_misses_it(monkeypatch):
    city = "KnownGoodCity2"
    with get_session() as session:
        session.add(Restaurant(id="osm:node:seeded", name="Already Scraped Place", city=city,
                                address="1 Seed St", official_website="https://seeded.example.com"))
        session.flush()
        session.add(MenuItem(restaurant_id="osm:node:seeded", translated_name="Soup"))

    # OSM's live query this time doesn't return the already-seeded restaurant at all
    fresh_osm_candidates = [{
        "id": "osm:node:fresh", "name": "Brand New Place", "address": "2 New St",
        "osm_url": "https://osm.org/node/fresh", "website": None, "phone": None,
        "cuisine": "", "diet_hints": {}, "latitude": 41.0, "longitude": 2.0,
    }]
    monkeypatch.setattr(tools.osm_client, "search_restaurants", lambda city, query: fresh_osm_candidates)

    results = tools.search_restaurants(city, "vegetarian dinner")

    assert results[0]["name"] == "Already Scraped Place"


def test_semantic_search_menu_items_no_exclusion_returns_all(monkeypatch):
    candidates = [
        {"text": "Mushroom risotto", "restaurant_id": "r1", "price": 12.0, "currency": "EUR",
         "ingredients": "mushroom, rice", "ingredients_source": "menu_stated"},
    ]
    monkeypatch.setattr(tools, "vector_search_menu_items", lambda query, restaurant_ids, n_results=20: candidates)
    monkeypatch.setattr(tools.rerank, "rerank", lambda query, items, text_key="text", top_k=None: items)

    results = tools.semantic_search_menu_items(["r1"], "risotto")

    assert len(results) == 1


def test_in_failure_cooldown_true_for_recent_false_for_old_or_none():
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)
    old = now - timedelta(days=settings.failure_retry_cooldown_days + 1)

    assert tools._in_failure_cooldown(recent) is True
    assert tools._in_failure_cooldown(old) is False
    assert tools._in_failure_cooldown(None) is False


def test_upsert_restaurant_candidates_flags_menu_available():
    city = "MenuAvailableCity"
    with get_session() as session:
        session.add(Restaurant(id="osm:node:ma1", name="Has Menu", city=city))
        session.flush()
        session.add(MenuItem(restaurant_id="osm:node:ma1", translated_name="Soup"))

    candidates = [
        {"id": "osm:node:ma1", "name": "Has Menu", "address": "", "osm_url": "", "cuisine": "", "diet_hints": {}},
        {"id": "osm:node:ma2", "name": "No Menu Yet", "address": "", "osm_url": "", "cuisine": "", "diet_hints": {}},
    ]
    results = tools.upsert_restaurant_candidates(city, candidates)

    by_id = {r["id"]: r for r in results}
    assert by_id["osm:node:ma1"]["menu_available"] is True
    assert by_id["osm:node:ma2"]["menu_available"] is False


def test_find_and_scrape_menu_skips_network_during_failure_cooldown(monkeypatch):
    city = "CooldownCity"
    with get_session() as session:
        session.add(Restaurant(id="osm:node:cd1", name="Recently Failed", city=city,
                                menu_attempt_failed_at=datetime.now(timezone.utc)))

    def _boom(*args, **kwargs):
        raise AssertionError("should not attempt network discovery during cooldown")

    monkeypatch.setattr(tools.web_search, "find_official_website", _boom)
    monkeypatch.setattr(tools.scraper, "get_menu_text", _boom)

    result = tools.find_and_scrape_menu("osm:node:cd1", "Recently Failed", city)

    assert result["items"] == []
    assert "recent check" in result["note"]


def test_find_and_scrape_menu_preserves_existing_menu_on_failed_refresh(monkeypatch):
    """A failed re-scrape (site briefly down, parse failure) must never wipe out a
    previously-successful menu - only a successful new extraction should replace it."""
    city = "PreserveCity"
    stale_time = datetime.now(timezone.utc) - timedelta(days=settings.cache_freshness_days + 1)
    with get_session() as session:
        session.add(Restaurant(id="osm:node:pv1", name="Old Good Data", city=city,
                                official_website="https://example.com",
                                last_scraped_menu_at=stale_time))
        session.flush()
        session.add(MenuItem(restaurant_id="osm:node:pv1", translated_name="Existing Dish"))

    monkeypatch.setattr(tools.scraper, "get_menu_text", lambda url: ("some raw text", url))
    monkeypatch.setattr(tools.llm, "extract_menu_items", lambda text: tools.llm.MenuExtractionResult(items=[]))

    result = tools.find_and_scrape_menu("osm:node:pv1", "Old Good Data", city)

    assert result["items"] == []  # this call reports failure...
    with get_session() as session:
        restaurant = session.get(Restaurant, "osm:node:pv1")
        assert len(restaurant.menu_items) == 1  # ...but the old good data is still there
        assert restaurant.menu_items[0].translated_name == "Existing Dish"


def test_find_and_scrape_menu_replaces_existing_items_on_successful_refresh(monkeypatch):
    """Regression test: re-scraping a restaurant that already has cached menu_items and
    getting a real new extraction back must actually replace the old rows, without
    SQLAlchemy choking on the just-deleted MenuItem instances still referenced by the
    (stale) relationship collection - this used to raise InvalidRequestError."""
    city = "ReplaceCity"
    stale_time = datetime.now(timezone.utc) - timedelta(days=settings.cache_freshness_days + 1)
    with get_session() as session:
        session.add(Restaurant(id="osm:node:rp1", name="Refreshed Place", city=city,
                                official_website="https://example.com",
                                last_scraped_menu_at=stale_time))
        session.flush()
        session.add(MenuItem(restaurant_id="osm:node:rp1", translated_name="Old Dish"))

    monkeypatch.setattr(tools.scraper, "get_menu_text", lambda url: ("some raw text", url))
    monkeypatch.setattr(
        tools.llm, "extract_menu_items",
        lambda text: tools.llm.MenuExtractionResult(items=[
            MenuItemLLM(translated_name="New Dish", ingredients=["tomato", "basil"]),
        ]),
    )
    monkeypatch.setattr(tools, "upsert_menu_items", lambda *args, **kwargs: None)  # avoid a real embedding call
    monkeypatch.setattr(tools, "delete_menu_items_for_restaurant", lambda ids: None)

    result = tools.find_and_scrape_menu("osm:node:rp1", "Refreshed Place", city)

    assert [i["name"] for i in result["items"]] == ["New Dish"]
    with get_session() as session:
        restaurant = session.get(Restaurant, "osm:node:rp1")
        assert [mi.translated_name for mi in restaurant.menu_items] == ["New Dish"]


def test_auto_exclusions_from_query_detects_dairy_free():
    exclusions = tools._auto_exclusions_from_query("something tasty, dairy-free please")
    assert "milk" in exclusions
    assert "cheese" in exclusions
    assert "butter" in exclusions


def test_auto_exclusions_from_query_detects_vegan():
    exclusions = tools._auto_exclusions_from_query("vegan dinner options")
    assert "egg" in exclusions
    assert "honey" in exclusions


def test_auto_exclusions_from_query_empty_for_unrelated_query():
    assert tools._auto_exclusions_from_query("something light and fresh") == []


def test_semantic_search_menu_items_auto_excludes_dairy_even_without_explicit_param(monkeypatch):
    """Regression test for a real bug seen live: the agent searched with
    query="dairy-free" but never populated exclude_ingredients, and a dish
    explicitly containing cheese was recommended anyway. The query-phrase
    detection must catch this even when the caller doesn't pass exclude_ingredients."""
    candidates = [
        {"text": "Goat Cheese Pizza", "restaurant_id": "r1", "price": 12.0, "currency": "EUR",
         "ingredients": "goat cheese, caramelized onion", "ingredients_source": "menu_stated", "category": "food"},
        {"text": "Tomato Bruschetta", "restaurant_id": "r1", "price": 6.0, "currency": "EUR",
         "ingredients": "tomato, bread, basil", "ingredients_source": "menu_stated", "category": "food"},
    ]
    monkeypatch.setattr(tools, "vector_search_menu_items", lambda query, restaurant_ids, n_results=20: candidates)
    monkeypatch.setattr(tools.rerank, "rerank", lambda query, items, text_key="text", top_k=None: items)

    results = tools.semantic_search_menu_items(["r1"], "dairy-free dinner options")

    assert len(results) == 1
    assert results[0]["text"] == "Tomato Bruschetta"
