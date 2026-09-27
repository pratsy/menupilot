import io
import logging
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.utils import retry_on_transient_error

logger = logging.getLogger(__name__)

USER_AGENT = "EuroFoodFinderBot/0.1 (+restaurant menu discovery for a personal travel assistant)"

MENU_LINK_KEYWORDS = [
    "menu", "menus", "carta", "karte", "speisekarte", "carte", "menu-du-jour",
    "food", "dishes", "our-menu", "menukaart",
]

TESTIMONIAL_LINK_KEYWORDS = [
    "review", "reviews", "testimonial", "testimonials", "opiniones", "opinion",
    "avis", "bewertungen", "bewertung", "recensioni", "comentarios",
    "clientes", "customers-say", "what-people-say", "google-reviews",
]

MAX_TEXT_CHARS = 12000


def _robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        with httpx.Client(timeout=5, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(robots_url)
        if resp.status_code >= 400:
            return True
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        return True


@retry_on_transient_error(retries=1)
def _do_fetch(url: str) -> httpx.Response:
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp


def fetch(url: str) -> httpx.Response | None:
    """Best-effort fetch: arbitrary third-party sites fail in many ways (DNS, SSL,
    non-HTML content, blocks) that aren't worth distinguishing here, so callers just
    get None and report the page as unreachable rather than crashing."""
    if not _robots_allowed(url):
        logger.info("robots.txt disallows fetching %s", url)
        return None
    try:
        return _do_fetch(url)
    except Exception as exc:
        logger.info("Failed to fetch %s: %s", url, exc)
        return None


def _find_link(homepage_url: str, html: str, keywords: list[str]) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = a["href"].lower()
        if any(kw in text for kw in keywords) or any(kw in href for kw in keywords):
            candidates.append(urljoin(homepage_url, a["href"]))
    return candidates[0] if candidates else None


def find_menu_link(homepage_url: str, html: str) -> str | None:
    return _find_link(homepage_url, html, MENU_LINK_KEYWORDS)


def find_testimonial_link(homepage_url: str, html: str) -> str | None:
    return _find_link(homepage_url, html, TESTIMONIAL_LINK_KEYWORDS)


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    text_parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(text_parts)


def _html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def get_menu_text(website_url: str) -> tuple[str, str] | None:
    """Fetch a restaurant's website, locate the menu page if needed, and return
    (raw_text, source_url) truncated to a size reasonable for an LLM prompt."""
    resp = fetch(website_url)
    if resp is None:
        return None

    content_type = resp.headers.get("content-type", "")
    if "pdf" in content_type or website_url.lower().endswith(".pdf"):
        text = _pdf_text(resp.content)
        return text[:MAX_TEXT_CHARS], website_url

    html = resp.text
    menu_url = find_menu_link(website_url, html)
    if menu_url and menu_url != website_url:
        menu_resp = fetch(menu_url)
        if menu_resp is not None:
            menu_content_type = menu_resp.headers.get("content-type", "")
            if "pdf" in menu_content_type or menu_url.lower().endswith(".pdf"):
                text = _pdf_text(menu_resp.content)
            else:
                text = _html_text(menu_resp.text)
            return text[:MAX_TEXT_CHARS], menu_url

    return _html_text(html)[:MAX_TEXT_CHARS], website_url


def get_testimonial_text(website_url: str) -> tuple[str, str] | None:
    """Fetch a restaurant's own website looking for customer testimonials/reviews -
    either a dedicated page, or embedded directly in the homepage (common for small
    restaurant sites). Returns (raw_text, source_url), or None if the site couldn't
    be reached at all."""
    resp = fetch(website_url)
    if resp is None:
        return None

    html = resp.text
    homepage_text = _html_text(html)

    testimonial_url = find_testimonial_link(website_url, html)
    if testimonial_url and testimonial_url != website_url:
        testimonial_resp = fetch(testimonial_url)
        if testimonial_resp is not None:
            combined = _html_text(testimonial_resp.text) + "\n\n" + homepage_text
            return combined[:MAX_TEXT_CHARS], testimonial_url

    return homepage_text[:MAX_TEXT_CHARS], website_url
