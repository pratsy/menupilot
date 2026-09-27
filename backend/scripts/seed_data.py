"""Offline seed-data generator.

Populates the SQLite + Chroma cache ahead of time by running the exact same
discovery -> scrape -> extract -> embed pipeline the live agent uses
(app.agent.tools), but with none of a live chat turn's latency pressure: it can
cast a much wider net per city and simply keep whatever actually yields a real,
scrapeable menu, discarding the rest.

Nothing here is hand-picked from memory or fabricated. Every candidate considered
comes from a live OpenStreetMap query (the same source the live agent uses for
discovery) - this script's only difference from a live search is that it tries far
more candidates than a single chat turn reasonably could, and keeps a permanent,
reusable result instead of a one-off answer.

Usage:
    cd backend
    python scripts/seed_data.py                      # seed the default city list
    python scripts/seed_data.py --cities Barcelona Rome
    python scripts/seed_data.py --target 5 --max-attempts 20

Writes scripts/seed_report.json summarizing what was kept and what failed, so the
resulting dataset is auditable rather than a black box.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import tools  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.services import osm_client  # noqa: E402

configure_logging()
logger = logging.getLogger("seed_data")

DEFAULT_CITIES = ["Barcelona", "Rome", "Berlin"]
DEFAULT_QUERIES = ["vegetarian dinner", "vegan dinner", "vegetarian lunch"]
DEFAULT_TARGET_PER_CITY = 8
DEFAULT_MAX_ATTEMPTS_PER_CITY = 30

REPORT_PATH = Path(__file__).resolve().parent / "seed_report.json"


def discover_candidates(city: str, queries: list[str]) -> list[dict]:
    """Casts a wide net: merges OSM results across several diet-intent queries so we
    don't depend on any single query phrasing surfacing the best candidates."""
    seen: dict[str, dict] = {}
    for query in queries:
        for c in osm_client.search_restaurants(city, query):
            seen.setdefault(c["id"], c)
        time.sleep(1)  # be polite to the shared public Overpass instance between queries
    return list(seen.values())


def seed_city(city: str, queries: list[str], target: int, max_attempts: int) -> dict:
    logger.info("=== Seeding %s ===", city)
    candidates = discover_candidates(city, queries)
    logger.info("%s: %d unique OSM candidates found", city, len(candidates))

    upserted = tools.upsert_restaurant_candidates(city, candidates)

    result = {"city": city, "attempted": 0, "succeeded": [], "failed": []}
    for c in upserted:
        if len(result["succeeded"]) >= target or result["attempted"] >= max_attempts:
            break
        result["attempted"] += 1

        menu = tools.find_and_scrape_menu(c["id"], c["name"], city)
        if not menu.get("items"):
            reason = menu.get("note", "no menu items extracted")
            logger.info("%s: SKIP %s (%s)", city, c["name"], reason)
            result["failed"].append({"name": c["name"], "reason": reason})
            continue

        reviews = tools.get_reviews_for_restaurant(c["id"], c["name"], city)
        review_count = len(reviews.get("reviews", []))
        logger.info("%s: KEEP %s (%d menu items, %d testimonials)",
                     city, c["name"], len(menu["items"]), review_count)
        result["succeeded"].append({
            "id": c["id"],
            "name": c["name"],
            "menu_items": len(menu["items"]),
            "testimonials": review_count,
            "website": menu.get("source_url"),
        })

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cities", nargs="+", default=DEFAULT_CITIES)
    parser.add_argument("--queries", nargs="+", default=DEFAULT_QUERIES)
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET_PER_CITY,
                         help="Stop once this many restaurants have been successfully seeded per city.")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS_PER_CITY,
                         help="Give up on a city after this many scrape attempts, successful or not.")
    args = parser.parse_args()

    report = {"cities": [seed_city(city, args.queries, args.target, args.max_attempts) for city in args.cities]}

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    logger.info("Wrote seed report to %s", REPORT_PATH)

    print("\nSummary:")
    for city_result in report["cities"]:
        print(f"  {city_result['city']}: {len(city_result['succeeded'])}/{city_result['attempted']} attempts succeeded")


if __name__ == "__main__":
    main()
