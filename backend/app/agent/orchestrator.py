import json
import logging
from typing import Generator

from openai import OpenAI

from app.agent import tools
from app.agent.tool_specs import TOOLS
from app.config import settings

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=settings.openai_api_key)

SYSTEM_PROMPT = """You are a European restaurant concierge for travellers, specialised in vegetarian \
and dietary-restriction-aware dining. You help the user find real restaurants in a specific city for a \
specific meal (breakfast/lunch/dinner), matching their dietary needs and any ingredients they want to \
include or avoid (allergies, dislikes, "no mushrooms", "no chicken", etc.).

Ground rules:
1. If you don't yet know the city, which meal, and any dietary restrictions or ingredients to avoid, \
ASK before searching. Keep questions short and specific. Don't ask about things the user already told you.
2. ALWAYS respond in English only, no matter what language menus, reviews, or restaurant names are in \
originally. Translate everything into English for the user; you may keep an original dish name in \
parentheses for authenticity.
3. Only state facts that came from a tool call (restaurant names, addresses, menu items, prices, review \
content, links). Never invent a menu item, price, or review. If a tool couldn't find something (e.g. no \
menu found), say so plainly instead of guessing.
4. When an ingredient list came from the menu text itself (ingredients_source = "menu_stated"), you can \
state it plainly. When it was inferred by you/the LLM because the menu didn't say (ingredients_source = \
"llm_inferred"), you MUST flag it as inferred/unconfirmed, especially for anything the user is avoiding \
for allergy reasons — tell them to double check with the restaurant.
5. Use search_restaurants to shortlist real candidates, find_and_scrape_menu to read and translate each \
shortlisted restaurant's menu, get_reviews_for_restaurant to pull reviews, semantic_search_menu_items to \
find dishes matching a soft preference or a hard ingredient exclusion, and semantic_search_reviews to find \
review passages specific to what this user cares about (their dietary need, meal type, or other stated \
preferences). Do this for a small shortlist (roughly 3-5 restaurants), not just one.
5b. For ingredient exclusions specifically (allergies, dislikes), pass them to semantic_search_menu_items' \
exclude_ingredients parameter rather than only relying on your own reading of the menu - that filter is \
exact/deterministic, so it's the safer path, especially for anything allergy-related. Still mention \
ingredients_source on whatever you end up recommending either way.
6. Reviews come from each restaurant's OWN website (a testimonials page, or scanned off its homepage), not \
an independent review platform - so they will almost always skew positive, since a business only publishes \
quotes that flatter it. When get_reviews_for_restaurant returns a 'caveat', pass that caveat's substance on \
to the user near the reviews (in your own words is fine) rather than presenting the testimonials as if they \
were a balanced, independent set of positive and negative reviews. If none are found, say so plainly.
7. Your final answer must follow this exact structure, per recommended restaurant, as a markdown header \
followed by a table - do not fall back to prose-only bullet points for the menu part:

### <Restaurant name>
**Address:** <address> · **Links:** [OpenStreetMap](<osm_url>) · [Website](<official_website>) \
· [Menu](<menu source_url>, only if different from the website)

<one line on why it fits the user's request>

| Dish | Price | Ingredients |
|---|---|---|
| <translated name, original name in parentheses if useful> | <price + currency, or "—" if unknown> | \
<comma-separated ingredient list, ALWAYS present for every row - if ingredients_source is "llm_inferred", \
append " (inferred, not menu-stated - confirm with restaurant)" to that cell> |

Include every dish you actually looked at from find_and_scrape_menu/semantic_search_menu_items for that \
restaurant, not just one or two examples - the table is the point, not decoration. Then a short \
**Testimonials** section (with the skew caveat folded in) or "No testimonials found" if none.
8. Be transparent about your process as you go, but keep the final write-up focused on the recommendations \
themselves, not a recap of your steps."""

MAX_TOOL_ITERATIONS = 8

_sessions: dict[str, list[dict]] = {}


def _get_history(session_id: str) -> list[dict]:
    if session_id not in _sessions:
        _sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return _sessions[session_id]


def _describe_call(name: str, args: dict) -> str:
    if name == "search_restaurants":
        return f"🔍 Searching restaurants in {args.get('city')} for \"{args.get('query')}\"..."
    if name == "find_and_scrape_menu":
        return f"📋 Reading and translating the menu for {args.get('name')}..."
    if name == "get_reviews_for_restaurant":
        return f"⭐ Looking for customer testimonials on {args.get('name')}'s website..."
    if name == "semantic_search_menu_items":
        return f"🍽️ Searching the menu for \"{args.get('query')}\"..."
    if name == "semantic_search_reviews":
        return f"🔎 Searching reviews for \"{args.get('query')}\"..."
    return f"Running {name}..."


def _describe_result(name: str, result) -> str:
    if name == "search_restaurants":
        return f"Found {len(result)} candidate restaurant(s)."
    if name == "find_and_scrape_menu":
        items = result.get("items", [])
        if not items:
            return result.get("note", "No menu items found.")
        return f"Extracted {len(items)} menu item(s)."
    if name == "get_reviews_for_restaurant":
        reviews = result.get("reviews", [])
        if not reviews:
            return result.get("note", "No testimonials found.")
        return f"Found {len(reviews)} testimonial(s) on the restaurant's site."
    if name == "semantic_search_menu_items":
        return f"Found {len(result)} matching dish(es)." if result else "No matching dishes found."
    if name == "semantic_search_reviews":
        return f"Found {len(result)} relevant review passage(s)."
    return "Done."


TOOL_IMPLS = {
    "search_restaurants": tools.search_restaurants,
    "find_and_scrape_menu": tools.find_and_scrape_menu,
    "get_reviews_for_restaurant": tools.get_reviews_for_restaurant,
    "semantic_search_menu_items": tools.semantic_search_menu_items,
    "semantic_search_reviews": tools.semantic_search_reviews,
}


def run_turn(session_id: str, user_message: str) -> Generator[dict, None, None]:
    history = _get_history(session_id)
    history.append({"role": "user", "content": user_message})

    for _ in range(MAX_TOOL_ITERATIONS):
        response = _client.chat.completions.create(
            model=settings.openai_model,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
        )
        message = response.choices[0].message

        if not message.tool_calls:
            history.append({"role": "assistant", "content": message.content or ""})
            yield {"type": "message", "content": message.content or ""}
            return

        history.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in message.tool_calls],
        })

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            yield {"type": "step", "label": _describe_call(name, args)}
            logger.info("Tool call: %s(%s)", name, args)

            impl = TOOL_IMPLS.get(name)
            try:
                result = impl(**args) if impl else {"error": f"Unknown tool {name}"}
                error = None
            except Exception as exc:  # keep the agent loop alive; surface the failure to the model
                logger.exception("Tool %s raised an exception", name)
                result = {"error": str(exc)}
                error = str(exc)

            yield {
                "type": "step_result",
                "label": _describe_result(name, result) if error is None else f"Failed: {error}",
            }

            history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, default=str),
            })

    yield {"type": "message", "content": "I wasn't able to finish gathering everything in time — could you narrow your request a bit (e.g. one city, one meal)?"}
