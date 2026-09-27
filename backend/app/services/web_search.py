import logging
import time
from urllib.parse import urlparse

from ddgs import DDGS

logger = logging.getLogger(__name__)

_EXCLUDED_DOMAINS = {
    "yelp.com", "tripadvisor.com", "tripadvisor.co.uk", "facebook.com", "instagram.com",
    "thefork.com", "thefork.es", "ubereats.com", "deliveroo.com", "deliveroo.co.uk",
    "glovoapp.com", "zomato.com", "opentable.com", "google.com", "maps.google.com",
    "wikipedia.org", "foursquare.com", "twitter.com", "x.com",
    # link-in-bio aggregators: never have real menu/review content of their own
    "linktr.ee", "linktree.com", "bio.link", "beacons.ai", "campsite.bio",
}


def is_excluded_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in _EXCLUDED_DOMAINS)


def find_official_website(name: str, city: str, retries: int = 1) -> str | None:
    """Best-effort discovery of a restaurant's own website via web search,
    filtering out aggregator/social domains. Not guaranteed to be correct - callers
    should sanity-check the result content before trusting it (see tools._looks_relevant)."""
    query = f"{name} {city} restaurant official website"
    results = []
    for attempt in range(retries + 1):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=8))
            break
        except Exception as exc:
            logger.info("Web search failed (attempt %d/%d) for %r: %s", attempt + 1, retries + 1, query, exc)
            if attempt < retries:
                time.sleep(1.5)

    for r in results:
        url = r.get("href") or r.get("url")
        if not url or is_excluded_domain(url):
            continue
        return url
    logger.info("No usable web search result for %r", query)
    return None
