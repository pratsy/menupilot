import json
import logging
import re
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
4b. If a dish's ingredients contradict something the user asked to avoid (an allergy, a "-free" request, a \
dislike), DROP it from your final answer entirely — never include it "with a caveat" (e.g. never write a \
dish into the table and then note "(contains dairy)" next to it for a dairy-free request). A caveat is for \
genuine uncertainty (llm_inferred ingredients); a known violation of what the user asked for is not \
uncertain, it's disqualifying, and it does not belong in the recommendation at all.
5. Use search_restaurants to shortlist real candidates, find_and_scrape_menu to read and translate each \
shortlisted restaurant's menu, get_reviews_for_restaurant to pull reviews, semantic_search_menu_items to \
find dishes matching a soft preference or a hard ingredient exclusion, and semantic_search_reviews to find \
review passages specific to what this user cares about (their dietary need, meal type, or other stated \
preferences). Do this for a small shortlist (roughly 3-5 restaurants), not just one.
5b. For ingredient exclusions specifically (allergies, "-free" requests, dislikes), call \
semantic_search_menu_items ONCE across your whole shortlist with every excluded ingredient spelled out \
explicitly in exclude_ingredients (e.g. "dairy-free" -> ['milk','cheese','butter','cream','yogurt','ghee'], \
not just the word "dairy") rather than only relying on your own reading of the menu or a soft/semantic \
query - that filter is exact/deterministic, so it's the safer path for anything allergy-related. Still \
mention ingredients_source on whatever you end up recommending either way, and still apply rule 4b even to \
items that weren't run through this filter.
5c. Don't present a category="drink" item as an answer to a meal request (breakfast/lunch/dinner) - a \
cocktail or soda is not a dinner option. If a restaurant's only relevant hits are drinks, that restaurant \
doesn't have a real food option for this request; treat it the same as having none (see rule 7).
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
<comma-separated ingredient list, ALWAYS present for every row. If ingredients_source is "menu_stated", \
append " ✓" to the cell (nothing else). If it's "llm_inferred", append NOTHING per-row - do not repeat a \
caveat sentence on every line, that's noise once you're recommending multiple dishes across several \
restaurants.> |

Include every dish that genuinely qualifies (matches the meal/diet/exclusions per rules 4b and 5c) from \
find_and_scrape_menu/semantic_search_menu_items for that restaurant - not just one or two cherry-picked \
examples, but also not padded with disqualified or irrelevant items just to make the table longer. Then a \
short **Testimonials** section (with the skew caveat folded in) or "No testimonials found" if none.
7c. End the WHOLE message (once, after every restaurant section, not per-table) with a single line: \
"*✓ = ingredient list confirmed on the restaurant's own menu; everything else is inferred from the dish \
name and not confirmed - always double check with the restaurant, especially for allergies.*" Skip this \
line only if every single ingredient across every dish you showed was menu_stated (i.e. nothing was \
inferred at all).
7b. Only include a restaurant's section at all if it has at least one genuinely qualifying dish after \
applying rules 4b and 5c. A restaurant with nothing that actually fits isn't a recommendation - drop it \
silently rather than padding your answer to hit a round number like "5 restaurants". Two solid \
restaurants beat five where three don't actually have anything the user can eat. If NONE of your \
shortlisted restaurants end up qualifying, say so plainly and suggest the user relax a constraint, rather \
than presenting weak/non-compliant options anyway.
8. Be transparent about your process as you go, but keep the final write-up focused on the recommendations \
themselves, not a recap of your steps."""

MAX_TOOL_ITERATIONS = 8

_sessions: dict[str, list[dict]] = {}


def _get_history(session_id: str) -> list[dict]:
    if session_id not in _sessions:
        _sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return _sessions[session_id]


_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_HEADER_ROW_RE = re.compile(r"\bDish\b.*\bPrice\b.*\bIngredients\b", re.IGNORECASE)


def _strip_noncompliant_dish_rows(markdown: str, excluded_ingredients: set[str]) -> str:
    """Deterministic backstop, not just a prompt instruction: live testing showed the
    model sometimes writes a dish into the recommendation table anyway, with a
    self-aware "(contains dairy - not compliant)" caveat, despite an explicit rule
    against doing exactly that. A caveat is not good enough for an allergy-adjacent
    feature - this removes any dish-table row whose Ingredients cell mentions an
    excluded ingredient, no matter what the model wrote around it."""
    if not excluded_ingredients:
        return markdown

    lines = markdown.split("\n")
    out = []
    removed_any = False
    for line in lines:
        stripped = line.strip()
        match = _TABLE_ROW_RE.match(stripped)
        if match and "---" not in stripped and not _HEADER_ROW_RE.search(stripped):
            cells = [c.strip() for c in match.group(1).split("|")]
            if len(cells) >= 3 and any(ex in cells[2].lower() for ex in excluded_ingredients):
                removed_any = True
                continue
        out.append(line)

    result = "\n".join(out)
    if removed_any:
        result += (
            "\n\n*Note: one or more dishes were removed from the recommendations above "
            "because their ingredients conflicted with what you asked to avoid.*"
        )
    return result


_EMPTY_CELL_VALUES = {"", "—", "-", "–"}
_SECTION_HEADER_RE = re.compile(r"^###\s+")
_FOOTNOTE_START_RE = re.compile(r"^\*[✓✓]\s*=\s*ingredient")


def _strip_empty_placeholder_rows(markdown: str) -> str:
    """The system prompt says to omit a restaurant with nothing qualifying, not to
    include it with a "— | — | —" placeholder row - but the model does this sometimes
    anyway. Remove any data row where every cell is blank/a dash before it's ever shown."""
    lines = markdown.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        match = _TABLE_ROW_RE.match(stripped)
        if match and "---" not in stripped and not _HEADER_ROW_RE.search(stripped):
            cells = [c.strip() for c in match.group(1).split("|")]
            if cells and all(c in _EMPTY_CELL_VALUES for c in cells):
                continue
        out.append(line)
    return "\n".join(out)


def _drop_empty_restaurant_sections(markdown: str) -> str:
    """A restaurant section with zero genuinely qualifying dishes should never appear
    at all - but live testing showed the model sometimes still emits the header/
    address/table shell for one anyway (with nothing, or only placeholder rows, inside
    it once _strip_empty_placeholder_rows has run). This drops the whole shell. Trailing
    content after the last restaurant (the ✓-marker footnote) is protected by treating
    it as its own section, so dropping an empty last restaurant can't eat it."""
    lines = markdown.split("\n")

    sections: list[list[str]] = [[]]  # index 0 = any preamble before the first heading
    for line in lines:
        stripped = line.strip()
        if _SECTION_HEADER_RE.match(stripped) or (_FOOTNOTE_START_RE.match(stripped) and len(sections) > 1):
            sections.append([line])
        else:
            sections[-1].append(line)

    def _has_qualifying_row(section: list[str]) -> bool:
        has_table = False
        for line in section:
            stripped = line.strip()
            if _HEADER_ROW_RE.search(stripped):
                has_table = True
                continue
            match = _TABLE_ROW_RE.match(stripped)
            if match and "---" not in stripped and not _HEADER_ROW_RE.search(stripped):
                cells = [c.strip() for c in match.group(1).split("|")]
                if any(c not in _EMPTY_CELL_VALUES for c in cells):
                    return True
        return not has_table  # sections with no table at all (intro/closing text) are never dropped

    kept = [
        section for section in sections
        if not section or not _SECTION_HEADER_RE.match(section[0].strip()) or _has_qualifying_row(section)
    ]
    return "\n".join(line for section in kept for line in section)


def _describe_call(name: str, args: dict) -> str:
    if name == "search_restaurants":
        return f"🔍 Searching restaurants in {args.get('city')} for \"{args.get('query')}\"..."
    if name == "find_and_scrape_menu":
        return f"📋 Reading and translating the menu for {args.get('name')}..."
    if name == "get_reviews_for_restaurant":
        return f"⭐ Looking for customer testimonials on {args.get('name')}'s website..."
    if name == "semantic_search_menu_items":
        n = len(args.get("restaurant_ids") or [])
        excl = args.get("exclude_ingredients") or []
        excl_note = f", excluding {', '.join(excl)}" if excl else ""
        return f"🍽️ Searching {n} restaurant's menus for \"{args.get('query')}\"{excl_note}..."
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

    # Collected from every user message this session (a constraint stated earlier in
    # the conversation still applies) plus whatever exclude_ingredients the agent
    # actually uses in tool calls below - the union feeds the deterministic post-filter
    # right before the final answer ships, regardless of what the model's prose does.
    applied_exclusions: set[str] = set()
    for msg in history:
        if msg.get("role") == "user":
            applied_exclusions.update(tools._auto_exclusions_from_query(msg.get("content") or ""))

    for _ in range(MAX_TOOL_ITERATIONS):
        response = _client.chat.completions.create(
            model=settings.openai_model,
            messages=history,
            tools=TOOLS,
            tool_choice="auto",
        )
        message = response.choices[0].message

        if not message.tool_calls:
            content = _strip_noncompliant_dish_rows(message.content or "", applied_exclusions)
            content = _strip_empty_placeholder_rows(content)
            content = _drop_empty_restaurant_sections(content)
            history.append({"role": "assistant", "content": content})
            yield {"type": "message", "content": content}
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

            if name == "semantic_search_menu_items":
                applied_exclusions.update(e.lower() for e in (args.get("exclude_ingredients") or []))

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
