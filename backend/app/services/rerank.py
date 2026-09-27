"""LLM-based reranking of vector search results.

Naive top-k vector similarity is a well-known weak point of RAG: cosine distance
on an embedding is a rough proxy for relevance, not the thing itself, and it has
no notion of the *current* query's nuance (e.g. two dishes can be embedding-close
because they're both "spicy noodle soups" while only one actually satisfies "not
too spicy"). A dedicated cross-encoder reranker model would be the textbook fix,
but that means another model dependency; the pragmatic version used here is to
hand the top vector hits back to the LLM with the exact user query and have it
re-score/reorder them - cheap (small model, short prompts), no new infra, and it
catches exactly the class of error described above.
"""

import json
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=settings.openai_api_key)

RERANK_SYSTEM_PROMPT = """You are given a search query and a numbered list of candidate passages \
(reviews or menu items). Score each candidate's genuine relevance to the query from 0 (irrelevant) to \
10 (perfect match), using the specific wording and intent of the query - not just topical similarity. \
Return a JSON object: {"scores": [{"index": int, "score": number}, ...]} covering every candidate index."""


def rerank(query: str, candidates: list[dict], text_key: str = "text", top_k: int | None = None) -> list[dict]:
    """Reorders `candidates` (each a dict with a `text_key` field) by relevance to
    `query`. Falls back to the original (vector-similarity) order if the rerank
    call fails for any reason - reranking is a quality improvement, not a
    correctness requirement, so a failure here should never break the pipeline."""
    if not candidates:
        return []

    numbered = "\n".join(f"{i}. {c.get(text_key, '')}" for i, c in enumerate(candidates))
    user_content = f"Query: {query}\n\nCandidates:\n{numbered}"

    try:
        resp = _client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        scores = {int(s["index"]): float(s["score"]) for s in data.get("scores", [])}
        ranked = sorted(range(len(candidates)), key=lambda i: scores.get(i, -1), reverse=True)
        reordered = [candidates[i] for i in ranked if i in scores]
        if not reordered:
            raise ValueError("rerank returned no usable scores")
    except Exception as exc:
        logger.warning("Rerank failed, falling back to vector-similarity order: %s", exc)
        reordered = candidates

    return reordered[:top_k] if top_k else reordered
