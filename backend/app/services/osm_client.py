"""Restaurant discovery via OpenStreetMap: Nominatim for geocoding the city,
Overpass API for querying restaurants within it. Free, keyless, no country
restrictions - a full replacement for a paid places-search API.

Nominatim's usage policy asks for a max of 1 request/sec and a descriptive
User-Agent, which we respect; results are cached in our own DB so a given city
is only geocoded/queried occasionally, not on every user message.
"""

import logging
import re

import httpx

from app.config import settings
from app.utils import retry_on_transient_error

logger = logging.getLogger(__name__)

_DIET_KEYWORDS = {
    "vegetarian": ["diet:vegetarian"],
    "vegan": ["diet:vegan"],
    "gluten": ["diet:gluten_free"],
    "halal": ["diet:halal"],
    "kosher": ["diet:kosher"],
}

_geocode_cache: dict[str, tuple[float, float, float, float]] = {}


def _headers() -> dict:
    return {"User-Agent": settings.osm_user_agent}


@retry_on_transient_error(retries=2)
def _fetch_geocode(city: str) -> list[dict]:
    with httpx.Client(timeout=15, headers=_headers()) as client:
        resp = client.get(
            f"{settings.osm_nominatim_url}/search",
            params={"q": city, "format": "json", "limit": 1},
        )
        resp.raise_for_status()
    return resp.json()


def geocode_city(city: str) -> tuple[float, float, float, float] | None:
    """Returns (south, north, west, east) bounding box for a city name."""
    key = city.strip().lower()
    if key in _geocode_cache:
        return _geocode_cache[key]

    results = _fetch_geocode(city)
    if not results:
        return None

    bbox = results[0]["boundingbox"]  # [south, north, west, east] as strings
    south, north, west, east = (float(x) for x in bbox)
    _geocode_cache[key] = (south, north, west, east)
    return (south, north, west, east)


def _diet_tags_for_query(query: str) -> list[str]:
    q = query.lower()
    tags = []
    for keyword, osm_tags in _DIET_KEYWORDS.items():
        if keyword in q:
            tags.extend(osm_tags)
    return tags


def _build_overpass_query(bbox: tuple[float, float, float, float], diet_tags: list[str]) -> str:
    south, north, west, east = bbox
    bbox_str = f"{south},{west},{north},{east}"

    if diet_tags:
        diet_regex = "|".join(re.escape(t.split(":")[-1]) for t in diet_tags)
        clauses = [
            f'node["amenity"="restaurant"]["diet:{diet_regex}"~"yes|only"]({bbox_str});',
            f'way["amenity"="restaurant"]["diet:{diet_regex}"~"yes|only"]({bbox_str});',
            f'node["amenity"="restaurant"]["cuisine"~"{diet_regex}",i]({bbox_str});',
            f'way["amenity"="restaurant"]["cuisine"~"{diet_regex}",i]({bbox_str});',
        ]
    else:
        clauses = [
            f'node["amenity"="restaurant"]({bbox_str});',
            f'way["amenity"="restaurant"]({bbox_str});',
        ]

    body = "\n  ".join(clauses)
    return f"[out:json][timeout:25];\n(\n  {body}\n);\nout center tags 40;"


def _format_address(tags: dict) -> str:
    parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:postcode", ""),
        tags.get("addr:city", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def _parse_elements(elements: list[dict]) -> list[dict]:
    out = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        osm_id = f"osm:{el['type']}:{el['id']}"
        out.append({
            "id": osm_id,
            "name": name,
            "address": _format_address(tags),
            "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "website": tags.get("website") or tags.get("contact:website"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "cuisine": tags.get("cuisine", ""),
            "diet_hints": {k: v for k, v in tags.items() if k.startswith("diet:")},
            "latitude": lat,
            "longitude": lon,
        })
    return out


@retry_on_transient_error(retries=2, base_delay=2.0)
def _run_overpass(query: str) -> list[dict]:
    """The public Overpass instance is prone to occasional 504s under load;
    the retry decorator backs off and retries before giving up."""
    with httpx.Client(timeout=40, headers=_headers()) as client:
        resp = client.post(settings.osm_overpass_url, data={"data": query})
        resp.raise_for_status()
    return resp.json().get("elements", [])


def search_restaurants(city: str, query: str) -> list[dict]:
    bbox = geocode_city(city)
    if bbox is None:
        logger.info("Could not geocode city %r", city)
        return []

    diet_tags = _diet_tags_for_query(query)
    overpass_query = _build_overpass_query(bbox, diet_tags)
    results = _parse_elements(_run_overpass(overpass_query))

    if not results and diet_tags:
        # Tag coverage is sparse; fall back to an unfiltered restaurant list so the
        # agent still has candidates, and let the LLM judge relevance from the name/menu.
        logger.info("No tag-filtered OSM results for %r in %s, falling back to unfiltered", query, city)
        overpass_query = _build_overpass_query(bbox, diet_tags=[])
        results = _parse_elements(_run_overpass(overpass_query))

    logger.info("OSM search: city=%s query=%r -> %d candidates", city, query, len(results))
    return results
