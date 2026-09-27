# Architecture

Written for: engineers evaluating this build — how it's put together and why.

## Overview

Euro Food Finder is a tool-calling LLM agent wrapped in a thin FastAPI service, backed by a
free/keyless data-discovery pipeline and a hybrid (structured + vector) retrieval layer. There
is no proprietary restaurant/review dataset behind it — every fact the agent states was fetched
live (or from cache) from a real, verifiable source, and the system is deliberately designed to
say "I don't know" rather than let the LLM fill a gap with a plausible-sounding guess.

```
                         ┌─────────────────────┐
   Browser (SSE) ───────▶│   FastAPI  /api/chat │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │   Agent Orchestrator   │  tool-calling loop, per-session history
                         │  (OpenAI Chat + tools) │
                         └──────────┬───────────┘
                                    │ dispatches to
        ┌───────────────┬──────────┼──────────┬──────────────────┐
        ▼               ▼          ▼          ▼                  ▼
  search_restaurants  find_and_  get_reviews  semantic_search_  semantic_search_
                       scrape_menu _for_       menu_items        reviews
                                   restaurant
        │               │          │          │                  │
        ▼               ▼          ▼          └────────┬─────────┘
   OpenStreetMap    scraper.py  scraper.py              ▼
  (Nominatim +      (own site)  (own site)      Chroma (vector) + rerank.py (LLM rerank)
   Overpass)             │          │
                         └────┬─────┘
                              ▼
                       llm.py (OpenAI): translate + structure
                              │
                              ▼
                   SQLite (restaurants, menu_items, reviews)
                   + Chroma embeddings (menu_items, reviews)
```

## The agent layer

`backend/app/agent/orchestrator.py` runs a standard ReAct-style tool-calling loop against the
OpenAI Chat Completions API (not the Assistants API — kept deliberately thin and synchronous so
every step can be streamed to the client as a discrete, human-readable event rather than opaque
token noise). Each iteration:

1. Calls the model with the full conversation history + tool schemas (`tool_specs.py`).
2. If the model returns tool calls, each is executed, logged, and its result appended to
   history as a `tool` message; a human-readable "step" and "step_result" event is streamed to
   the browser over SSE for each one (`🔍 Searching restaurants in Barcelona...` →
   `Found 12 candidate restaurant(s).`).
3. Loops until the model responds with plain text (its final answer) or a hard iteration cap
   is hit (guards against runaway tool-call loops).

Conversation state is kept in-process, keyed by a client-generated `session_id`. See
[SCALING.md](SCALING.md) for why that doesn't survive a restart or a second server process.

### Guardrails against fabrication

This is the property the whole design optimizes for, because a restaurant recommendation
agent that hallucinates a dish, a price, or a review isn't a lesser version of the product —
it's actively dangerous for anyone with an allergy. Three mechanisms enforce it:

- **System prompt contract**: the model is explicitly instructed to only state facts that came
  from a tool result, and to say "not found" rather than infer when a tool comes back empty.
- **Ingredient provenance tracking**: every extracted ingredient is tagged `menu_stated` or
  `llm_inferred` at extraction time (`schemas.py::MenuItemLLM`), carried through storage, and
  the agent is instructed to flag inferred ingredients explicitly rather than state them with
  false confidence.
- **Hard filtering for exclusions**: an excluded ingredient (allergy, dislike) is enforced as
  an exact string match against the stored ingredient list, never left to the LLM's judgment
  or to vector-similarity fuzziness. See **Retrieval design** below.

## Data pipeline

### 1. Discovery — OpenStreetMap

`services/osm_client.py` geocodes the city via Nominatim, then queries Overpass for
`amenity=restaurant` nodes/ways in that bounding box, first filtered by relevant `diet:*`/
`cuisine` tags, falling back to an unfiltered query if tag coverage is sparse (OSM tagging is
contributor-driven and incomplete). Chosen over Yelp/Google/Foursquare because it's free,
keyless, and has no geographic account-creation restrictions — see the "why not X" table below.

Results are shortlisted server-side (`tools.py::_candidate_quality`) before ever reaching the
LLM — prioritizing candidates with diet tags, a website, and a real address, capped at 12. This
isn't just noise reduction: early testing showed that handing the model 40 raw candidate ids to
track across several follow-up tool calls caused it to mistranscribe ids and fail lookups on
roughly half of them. A second line of defense, `tools.py::_resolve_restaurant`, falls back to a
name+city match when an id doesn't resolve, so a transcription slip degrades gracefully instead
of failing the whole turn.

Before ranking, `search_restaurants` also merges in any restaurant in that city we've *already*
successfully scraped a menu for (`tools.py::_get_known_good_restaurants`) and prioritizes those
above fresh OSM results. A proven-good restaurant is a stronger signal than any OSM tag, and this
is what makes the offline seeding below actually take effect in live chat, rather than sitting
unused in the database.

### 2. Acquisition — scraping

`services/scraper.py` fetches each restaurant's official website (from an OSM `website` tag if
present and not a link-aggregator domain like Linktree, else discovered via a web-search
fallback), looks for a menu or testimonials page by keyword (multilingual: `carta`, `speisekarte`,
`opiniones`, `avis`, ...), and extracts visible text, handling both HTML and PDF. It respects
`robots.txt` and identifies itself with a descriptive User-Agent.

Because the web-search fallback can return an unrelated page for a generic restaurant name, a
relevance check (`tools.py::_looks_relevant`) verifies the restaurant's name actually appears on
the fetched page before any data from it is trusted — a cheap guard that caught a real false
match (a scholarship-forum page) during testing.

This stage does **not** render JavaScript. It's a known, documented limitation, not a silent
gap: a JS-heavy site returns an empty shell, and the agent reports "menu not found" rather than
fabricating one.

### 3. Extraction — LLM structuring

`services/llm.py` turns raw scraped text (in any language) into structured English records via
two constrained JSON-mode prompts:

- `extract_menu_items`: dish name, description, ingredients (with `menu_stated`/`llm_inferred`
  provenance), price, currency.
- `extract_testimonials`: only text that reads as an actual attributed customer quote — the
  prompt explicitly excludes the restaurant's own marketing copy about itself, which a naive
  extraction would otherwise happily label a "positive review."

### 4. Storage & embedding

SQLite (`db/models.py`) holds the structured, queryable record: `restaurants`, `menu_items`,
`reviews`. Every menu item and testimonial is also embedded (OpenAI `text-embedding-3-small`)
into Chroma (`db/vectorstore.py`) for semantic retrieval. Caching is the point of this split:
the slow, expensive part (scrape → translate → embed) happens once per restaurant per freshness
window (30 days by default); every subsequent question about that restaurant is a cache read.

### 5. Offline seeding

Live scraping succeeding mid-conversation is inherently probabilistic — plenty of real
restaurants run JS-heavy sites this pipeline can't read, or the free web-search fallback simply
doesn't find a working link. `backend/scripts/seed_data.py` runs stages 1-4 above **offline**,
with none of a live chat turn's time pressure: it queries OSM across several diet-intent phrasings
per city to cast a wide net (`discover_candidates`), then attempts each candidate through the
same `find_and_scrape_menu`/`get_reviews_for_restaurant` functions the live agent calls, keeping
whichever ones actually succeed and discarding the rest. It's the exact same pipeline, not a
separate one, and it writes a `seed_report.json` next to itself listing every restaurant kept
and why every skipped one failed — an audit trail, not a black box. Nothing in it is hand-picked
by name or fabricated; every candidate it tries came from a live OpenStreetMap query.

This is deliberately a small-scale precursor to the "decouple ingestion from serving" direction
described in [SCALING.md](SCALING.md#phase-1--decouple-ingestion-from-serving) — same idea (an
offline pipeline populates the cache; live requests only ever read from it), just without a job
queue or scheduler around it yet.

## Retrieval design

**Chunking policy: 1 record = 1 chunk.** A menu dish and a testimonial quote are already
atomic semantic units by the time they reach the embedding step — they were structured out of
the raw page by the LLM extraction stage specifically so there'd be nothing long left to
chunk. This sidesteps the standard RAG chunking problem (where to split a document without
severing a fact from its context) entirely, rather than solving it with a splitter.

**Hybrid retrieval, not vector-search-everywhere.** `semantic_search_menu_items` demonstrates
the pattern directly: `exclude_ingredients` is applied as an exact filter against the stored
ingredient string *before* anything is ranked; only the survivors are ranked by embedding
similarity against the soft part of the query ("something light and comforting"). This is a
deliberate choice, not an oversight — a missed allergen because of a fuzzy embedding match is a
safety bug, not an imprecision, so it can never be the thing doing the filtering.

**Reranking.** Raw top-k cosine similarity is a well-documented weak point of naive RAG: it's a
rough proxy for relevance with no notion of the current query's specific nuance (two dishes can
be embedding-close because they're both "spicy noodle soup" when only one is actually
vegetarian). `services/rerank.py` hands the vector-search survivors back to the LLM with the
exact query and has it re-score and reorder them. This is the pragmatic, no-new-infra version of
what a dedicated cross-encoder reranker would do in a larger system (see SCALING.md); it fails
open to the original vector-similarity order if the rerank call itself errors, since reranking
is a quality improvement, not a correctness dependency.

## Reliability engineering

- **Retries with backoff** on every external HTTP call (Overpass, Nominatim, the scraper,
  DuckDuckGo search), distinguishing retryable transport/5xx errors from non-retryable 4xx
  (`app/utils.py::retry_on_transient_error`).
- **Graceful degradation everywhere**: every tool returns a structured `note`/`caveat` on
  partial failure instead of raising, and the agent is instructed to surface that note to the
  user rather than paper over it.
- **Structured logging** (`app/logging_config.py`) through every service and the orchestrator's
  tool-execution loop, so a failed run is debuggable from logs, not just from re-running it.

## Testing

`backend/tests/` covers the pure logic and the new retrieval infrastructure with all external
calls mocked — fast, deterministic, no network or API cost: the relevance guard, candidate
shortlisting, the known-good prioritization merge, the id-mismatch fallback resolver, the
Overpass query builder, menu/testimonial link discovery, schema validation, the retry decorator,
and reranking (including its fallback
path). It deliberately does not try to assert against real scraped websites, since that's
inherently non-deterministic — those paths were instead verified live during development against
real restaurant sites and are documented as a known limitation rather than "tested."

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + SSE | Simple, streams naturally, no websocket complexity needed for one-directional agent-step events |
| Agent | OpenAI Chat Completions, custom tool loop | Full control over streaming individual steps; heavier frameworks (LangChain/Assistants API) would obscure exactly the transparency this product needs |
| Restaurant discovery | OpenStreetMap (Nominatim + Overpass) | Free, keyless, no geographic restrictions (see decisions table) |
| Menu/review acquisition | httpx + BeautifulSoup + pypdf | Lightweight, no browser dependency, sufficient for static/server-rendered sites |
| Extraction/translation | OpenAI (JSON mode) | One model for both structuring and translation, avoids a second translation API |
| Structured storage | SQLite + SQLAlchemy | Zero-infra for a single-node app; swappable ORM-level for Postgres at scale |
| Vector storage | Chroma (embedded/persistent) | Zero-infra, same reasoning as SQLite |
| Reranking | LLM-based | No new model/infra dependency vs. a dedicated cross-encoder |
| Frontend | Vanilla HTML/JS + SSE + marked.js | The product is a chat interface and nothing else — no framework overhead justified |

## Key design decisions

| Decision | Alternative considered | Why this way |
|---|---|---|
| OSM for restaurant search | Yelp Fusion, Google Places, Foursquare | Yelp blocks developer signup from some countries; Foursquare's review endpoint is paid from call 1 with no free tier; Google Places needs a Cloud billing account that wasn't available here. OSM has none of those blockers. |
| Website testimonials for reviews | Scraping Google Maps/TripAdvisor | Real review platforms have the richest data, but scraping them violates their ToS and is technically fragile. Scraping a business's own public site is clean; the tradeoff (self-selected, positively-skewed testimonials) is explicitly surfaced to the user rather than hidden. |
| SQL exact-match for hard constraints, vector search for soft preferences | Vector search for everything | A missed allergen from a fuzzy match is a safety issue; exact filtering is strictly more correct for that class of constraint, and vector search is reserved for what it's actually good at. |
| LLM-based reranking | Dedicated cross-encoder reranker model | No new model/infra dependency; acceptable quality/cost tradeoff at this volume (revisit at scale — see SCALING.md). |
| In-process tool-calling loop | LangChain / OpenAI Assistants API | Full control over per-step streaming to the UI, which is a core product requirement, not an implementation detail. |
| Offline seed script + known-good prioritization | Hand-picking restaurant names/URLs; a static hard-coded fixture; a paid structured-menu API | Live scraping mid-conversation is inherently probabilistic (JS-heavy sites, dead links). Hand-picking names isn't reproducible or auditable. The candidates surveyed (Documenu, TheFork, Zomato's public API) turned out to be US-only, partnership-only, or discontinued for open access respectively. Running the *same* real pipeline offline, with no latency pressure, and keeping only what actually works, stays honest (still real scraped data) and reproducible (rerunnable, reports what it kept/skipped) without a new dependency. |
