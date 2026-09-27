TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_restaurants",
            "description": (
                "Search for real restaurants in a given European city via OpenStreetMap, filtered by "
                "dietary/cuisine tags where available. Use this once you know the city and enough about "
                "the user's meal/dietary intent to form a good search query (e.g. 'vegetarian dinner', "
                "'vegan breakfast', 'gluten-free lunch'). Results may include a diet_hints field showing "
                "any diet-related tags found; when it's empty, the restaurant wasn't confirmed vegetarian-"
                "friendly by map data alone, so double-check via its menu before recommending it. Each "
                "result also has menu_available: true/false - true means its menu is already scraped and "
                "cached, so calling find_and_scrape_menu on it returns instantly with no risk of failure. "
                "Prefer trying menu_available=true candidates before ones marked false, since a false one "
                "may simply have no readable website."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Barcelona'."},
                    "query": {"type": "string", "description": "Search intent, e.g. 'vegetarian dinner'."},
                },
                "required": ["city", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_scrape_menu",
            "description": (
                "Find a shortlisted restaurant's official website and read/translate its menu into "
                "structured English items with prices and, where stated, ingredients. Cached after the "
                "first successful read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string", "description": "The restaurant id from search_restaurants."},
                    "name": {"type": "string"},
                    "city": {"type": "string"},
                },
                "required": ["restaurant_id", "name", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reviews_for_restaurant",
            "description": (
                "Fetch and translate customer testimonials for a shortlisted restaurant, scraped from the "
                "restaurant's own website (a dedicated reviews/testimonials page if it has one, else its "
                "homepage). IMPORTANT: since these are testimonials the business chose to publish about "
                "itself, treat the result's 'caveat' field as something to pass on to the user - this is not "
                "an independent, unfiltered set of positive AND negative reviews, just what the restaurant "
                "decided to showcase. If 'reviews' is empty, say testimonials weren't available rather than "
                "inventing sentiment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_id": {"type": "string"},
                    "name": {"type": "string"},
                    "city": {"type": "string"},
                },
                "required": ["restaurant_id", "name", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_menu_items",
            "description": (
                "Hybrid search over an already-scraped menu (call find_and_scrape_menu first): semantic "
                "similarity for soft/descriptive intent (e.g. 'something light and fresh', 'hearty comfort "
                "food', 'spicy street food style'), reranked against the exact query. Pass any ingredients "
                "the user must avoid in exclude_ingredients - that's applied as an exact filter on the "
                "stored ingredient list, not fuzzy matching, so it's safe to rely on for allergies/dislikes "
                "(but still double-check ingredients_source on results, since 'llm_inferred' items were not "
                "confirmed by the menu text itself). Prefer this over eyeballing the full item list yourself "
                "when the user has a specific vibe/preference or an exclusion to apply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_ids": {"type": "array", "items": {"type": "string"}},
                    "query": {"type": "string", "description": "The soft/descriptive intent to match dishes against."},
                    "exclude_ingredients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ingredients to hard-exclude, e.g. ['mushroom', 'chicken'].",
                    },
                },
                "required": ["restaurant_ids", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_reviews",
            "description": (
                "Semantically search already-fetched reviews across one or more shortlisted restaurants for "
                "passages relevant to the CURRENT user's specific context, e.g. 'vegetarian options', "
                "'good for solo travellers', 'slow service', 'mushrooms'. Use this to find reviews that speak "
                "directly to what this user cares about, not just generic star ratings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "restaurant_ids": {"type": "array", "items": {"type": "string"}},
                    "query": {"type": "string", "description": "What to search for in the reviews."},
                },
                "required": ["restaurant_ids", "query"],
            },
        },
    },
]
