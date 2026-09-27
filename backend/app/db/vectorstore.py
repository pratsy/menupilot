"""Embedding + vector retrieval, via Chroma with OpenAI embeddings.

Chunking policy: every embedded document here is already a naturally atomic
unit - one review/testimonial, one menu dish - so the chunking strategy is
"1 record = 1 chunk". We deliberately never embed a raw scraped page directly;
it's first passed through the LLM extraction step (see services/llm.py) which
turns unstructured page text into these atomic structured records, and only
those get embedded. That avoids the usual chunking problem (where to split a
long document without cutting a sentence/fact in half) because there's nothing
long to split - a menu description or a review quote is already the right size
for a single embedding.

Retrieval policy: vector similarity is used for *soft* relevance ("something
light and comforting", "good for solo travellers") where fuzzy matching is
exactly what's wanted. Hard constraints (allergen exclusions, price ceilings)
are applied as deterministic metadata filters, never left to embedding
similarity alone - a false-negative on "no mushrooms" is a safety issue, not
just an imprecision, so it must be exact.
"""

import chromadb
from chromadb.utils import embedding_functions

from app.config import settings

_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)

_embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
    api_key=settings.openai_api_key,
    model_name=settings.openai_embedding_model,
)

_reviews_collection = _client.get_or_create_collection(
    name="reviews",
    embedding_function=_embedding_fn,
)

_menu_items_collection = _client.get_or_create_collection(
    name="menu_items",
    embedding_function=_embedding_fn,
)


def upsert_reviews(reviews: list[dict]) -> None:
    """Batched: one embedding-API call for every review from this scrape, not one call
    per review. Each dict needs review_id, restaurant_id, text, rating, sentiment."""
    reviews = [r for r in reviews if r["text"].strip()]
    if not reviews:
        return
    _reviews_collection.upsert(
        ids=[f"review-{r['review_id']}" for r in reviews],
        documents=[r["text"] for r in reviews],
        metadatas=[{
            "restaurant_id": r["restaurant_id"],
            "rating": r["rating"] if r["rating"] is not None else -1.0,
            "sentiment": r["sentiment"] or "unknown",
        } for r in reviews],
    )


def search_reviews(query: str, restaurant_ids: list[str], n_results: int = 8) -> list[dict]:
    if not restaurant_ids:
        return []
    result = _reviews_collection.query(
        query_texts=[query],
        n_results=n_results,
        where={"restaurant_id": {"$in": restaurant_ids}},
    )
    hits = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, distances):
        hits.append({
            "text": doc,
            "restaurant_id": meta.get("restaurant_id"),
            "rating": meta.get("rating"),
            "sentiment": meta.get("sentiment"),
            "relevance": 1 - dist,
        })
    return hits


def upsert_menu_items(items: list[dict]) -> None:
    """Batched: one embedding-API call for a whole scraped menu instead of one call per
    dish - matters a lot once menus run to 50-100+ items. Each dict needs menu_item_id,
    restaurant_id, text, price, currency, ingredients, ingredients_source, category."""
    items = [i for i in items if i["text"].strip()]
    if not items:
        return
    _menu_items_collection.upsert(
        ids=[f"menu_item-{i['menu_item_id']}" for i in items],
        documents=[i["text"] for i in items],
        metadatas=[{
            "restaurant_id": i["restaurant_id"],
            "price": i["price"] if i["price"] is not None else -1.0,
            "currency": i["currency"] or "",
            "ingredients": i["ingredients"],
            "ingredients_source": i["ingredients_source"],
            "category": i.get("category", "food"),
        } for i in items],
    )


def delete_menu_items_for_restaurant(menu_item_ids: list[int]) -> None:
    if menu_item_ids:
        _menu_items_collection.delete(ids=[f"menu_item-{i}" for i in menu_item_ids])


def search_menu_items(query: str, restaurant_ids: list[str], n_results: int = 15) -> list[dict]:
    """Soft semantic search only - callers apply hard constraints (e.g. excluded
    ingredients) themselves against the returned `ingredients` metadata, since that
    must be an exact filter, not a similarity-ranked one."""
    if not restaurant_ids:
        return []
    result = _menu_items_collection.query(
        query_texts=[query],
        n_results=n_results,
        where={"restaurant_id": {"$in": restaurant_ids}},
    )
    hits = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, distances):
        hits.append({
            "text": doc,
            "restaurant_id": meta.get("restaurant_id"),
            "price": meta.get("price"),
            "currency": meta.get("currency"),
            "ingredients": meta.get("ingredients"),
            "ingredients_source": meta.get("ingredients_source"),
            "category": meta.get("category", "food"),
            "relevance": 1 - dist,
        })
    return hits
