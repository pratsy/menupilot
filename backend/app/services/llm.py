import json
import logging

from openai import OpenAI

from app.config import settings
from app.schemas import MenuExtractionResult, TestimonialExtractionResult

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=settings.openai_api_key)


def _chat_json(system: str, user: str) -> dict:
    resp = _client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Model returned non-JSON content, treating as empty result")
        return {}


MENU_SYSTEM_PROMPT = """You extract structured menu data from raw text scraped off a restaurant's \
website, which may be in any language. Return a JSON object: {"items": [...]}.

For each dish/drink you can identify, output an object with:
- original_name: the name as written on the menu (native language)
- original_description: any description text as written (native language), or ""
- translated_name: English translation of the name
- translated_description: English translation of the description, or ""
- ingredients: a list of English ingredient words, ONLY if the menu text explicitly states them \
(e.g. from the description). If you must guess likely ingredients from the dish name/type because \
the menu does not state them, still provide your best-effort list but set ingredients_source to \
"llm_inferred" instead of "menu_stated". Never fabricate specific allergens or exclusions with false \
confidence.
- ingredients_source: "menu_stated" or "llm_inferred"
- price: numeric price if present, else null
- currency: ISO-ish currency symbol/code as seen (e.g. "EUR", "$"), else null

Skip navigation text, boilerplate, and anything that isn't an actual menu item. If the text contains \
no identifiable menu items, return {"items": []}."""


def extract_menu_items(raw_text: str) -> MenuExtractionResult:
    data = _chat_json(MENU_SYSTEM_PROMPT, raw_text)
    try:
        result = MenuExtractionResult.model_validate(data)
    except Exception:
        logger.warning("Menu extraction returned an unparseable shape, treating as empty")
        return MenuExtractionResult(items=[])
    logger.info("Extracted %d menu item(s)", len(result.items))
    return result


TESTIMONIAL_SYSTEM_PROMPT = """You extract genuine customer testimonials/reviews from raw text scraped \
off a restaurant's own website, which may be in any language. Return a JSON object: {"items": [...]}.

Only extract text that actually reads as a real customer's quoted feedback about their experience - a \
named or attributed quote, a "what our customers say" snippet, star-rating blurbs, etc. Do NOT extract \
generic marketing copy written by the restaurant about itself (e.g. "we use the freshest ingredients", \
"family-owned since 1990") - that is not a testimonial even if it sounds positive. If you're not confident \
something is an actual customer quote, leave it out. If there are none, return {"items": []}.

For each genuine testimonial found, output an object with:
- original_text: the quote as written (native language)
- translated_text: English translation, faithful and complete, not summarized
- original_language: best-guess language code/name, or null if already English
- author: the name/initial attributed to it, or null if unattributed
- sentiment: "positive" | "negative" | "mixed" | "unknown\""""


def extract_testimonials(raw_text: str) -> TestimonialExtractionResult:
    data = _chat_json(TESTIMONIAL_SYSTEM_PROMPT, raw_text)
    try:
        result = TestimonialExtractionResult.model_validate(data)
    except Exception:
        logger.warning("Testimonial extraction returned an unparseable shape, treating as empty")
        return TestimonialExtractionResult(items=[])
    logger.info("Extracted %d testimonial(s)", len(result.items))
    return result
