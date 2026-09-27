from app.services import osm_client


def test_diet_tags_for_query_matches_known_keywords():
    assert osm_client._diet_tags_for_query("vegetarian dinner") == ["diet:vegetarian"]
    assert osm_client._diet_tags_for_query("vegan breakfast") == ["diet:vegan"]
    assert osm_client._diet_tags_for_query("gluten free lunch") == ["diet:gluten_free"]


def test_diet_tags_for_query_returns_empty_for_unrecognised_intent():
    assert osm_client._diet_tags_for_query("cheap lunch spot") == []


def test_build_overpass_query_includes_diet_filter_when_present():
    bbox = (41.32, 41.47, 2.05, 2.23)
    query = osm_client._build_overpass_query(bbox, ["diet:vegetarian"])
    assert 'diet:vegetarian' in query
    assert 'amenity"="restaurant"' in query
    assert "41.32,2.05,41.47,2.23" in query


def test_build_overpass_query_omits_diet_filter_when_absent():
    bbox = (41.32, 41.47, 2.05, 2.23)
    query = osm_client._build_overpass_query(bbox, [])
    assert "diet:" not in query


def test_format_address_joins_available_parts_only():
    tags = {"addr:housenumber": "12", "addr:street": "Carrer de Sant Pau", "addr:city": "Barcelona"}
    assert osm_client._format_address(tags) == "12 Carrer de Sant Pau Barcelona"


def test_format_address_empty_when_no_addr_tags():
    assert osm_client._format_address({}) == ""


def test_parse_elements_skips_unnamed_and_builds_synthetic_id():
    elements = [
        {"type": "node", "id": 123, "lat": 41.4, "lon": 2.2, "tags": {"name": "Cafe Verde", "diet:vegetarian": "yes"}},
        {"type": "way", "id": 456, "center": {"lat": 41.3, "lon": 2.1}, "tags": {}},  # no name - should be skipped
    ]
    parsed = osm_client._parse_elements(elements)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "osm:node:123"
    assert parsed[0]["name"] == "Cafe Verde"
    assert parsed[0]["diet_hints"] == {"diet:vegetarian": "yes"}
    assert parsed[0]["latitude"] == 41.4


def test_geocode_city_caches_between_calls(monkeypatch):
    calls = {"n": 0}

    def fake_fetch(city):
        calls["n"] += 1
        return [{"boundingbox": ["41.32", "41.47", "2.05", "2.23"]}]

    monkeypatch.setattr(osm_client, "_fetch_geocode", fake_fetch)
    osm_client._geocode_cache.clear()

    first = osm_client.geocode_city("Barcelona")
    second = osm_client.geocode_city("Barcelona")

    assert first == (41.32, 41.47, 2.05, 2.23)
    assert second == first
    assert calls["n"] == 1  # second call hit the cache, not the network


def test_geocode_city_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(osm_client, "_fetch_geocode", lambda city: [])
    osm_client._geocode_cache.clear()
    assert osm_client.geocode_city("Nonexistentville") is None
