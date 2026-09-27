# Scaling: from prototype to Zomato-scale

Written for: engineers assessing what this architecture is missing to run as a real,
multi-city, high-traffic product — and what order to fix it in.

The current build is optimized for a completely different problem than a production dining
platform: it's optimized to prove a data/retrieval pipeline works correctly end-to-end for one
user at a time, honestly, with zero infrastructure cost. Almost everything below is a direct
consequence of that scope, not a mistake — but it's worth being explicit about exactly what
would need to change, because the two designs are not incremental variations of each other.

## What breaks first, in order

If traffic went from "one developer testing it" to real concurrent users tomorrow, here's the
order things would actually fall over:

1. **Live scraping in the request path.** Every uncached restaurant question currently triggers
   a synchronous scrape + LLM extraction *during* the chat response. That's 5-15 seconds of
   latency per new restaurant today, and it will not survive concurrent users hitting
   uncached restaurants at the same time — the scraper isn't rate-limit-aware across requests,
   and nothing queues or dedupes two users asking about the same new restaurant simultaneously.
2. **SQLite.** Single-writer-friendly, not built for concurrent multi-process writes. The moment
   this runs as more than one worker process (which horizontal scaling requires), SQLite becomes
   a correctness problem, not just a performance one.
3. **In-memory conversation history.** Tied to a single process's memory. A second server
   instance, a restart, or a rolling deploy silently drops every active conversation.
4. **The public Overpass/Nominatim instances.** These are shared community infrastructure with
   explicit usage policies (1 req/sec, no heavy automated use). They are correct for a personal
   project and would get us rate-limited or blocked within minutes of real production traffic.

## Phase 1 — Decouple ingestion from serving

This is the single most important architectural change, and everything else is smaller than it.

Today, "read the menu" happens live, in the critical path of a user's chat turn. In production,
it should never do that. The target architecture:

- A **background ingestion pipeline** (Celery/Temporal/a plain job queue) continuously and
  independently discovers restaurants (OSM extracts, or a paid places API once revenue
  justifies it), crawls their sites, extracts, translates, and embeds — on its own schedule,
  with its own retry/backoff/rate-limiting, entirely decoupled from any live user request.
- User-facing queries become **pure reads** against already-populated Postgres + vector store.
  Worst case for an uncovered restaurant is "we haven't indexed this one yet," queued for the
  next crawl pass — not a 15-second live scrape blocking someone's chat response.
- Freshness becomes a **scheduling problem**: popular/high-traffic restaurants get recrawled
  more often; the current flat 30-day TTL becomes a priority queue driven by actual query
  volume and detected staleness (e.g., a changed page hash).
- The scraper itself needs to graduate from "a Python function per request" to real crawler
  infrastructure: a headless-browser fleet (for the JS-rendered sites this build currently
  can't handle at all), proxy rotation, per-domain rate limiting, and monitoring for scrapers
  breaking silently when a restaurant redesigns their site.

## Phase 2 — Storage and retrieval at scale

- **Postgres** replaces SQLite (managed — RDS/Cloud SQL), with connection pooling and read
  replicas as read traffic grows.
- **A managed/distributed vector store** (Pinecone, Weaviate, Qdrant, or `pgvector` on the same
  Postgres if operational simplicity is prioritized over top vector-search performance) replaces
  the single-node embedded Chroma instance, with proper sharding and replication.
- **A caching layer (Redis)** in front of both — hot restaurants/menus, and session state, so a
  repeat query for "vegetarian dinner in Barcelona" doesn't hit Postgres + a vector query on
  every request.
- **Session/conversation history** moves out of process memory into Redis or Postgres, keyed by
  session id, so any server instance can serve any request (a hard requirement for horizontal
  scaling and rolling deploys).
- **Reranking** likely moves from an LLM call per query to a dedicated, self-hosted
  cross-encoder reranker (e.g., a BGE or Cohere-style reranker) once query volume makes
  "one extra LLM round-trip per search" a meaningful cost line item rather than a rounding
  error — the current LLM-based approach was the right no-new-infra choice at prototype
  volume, not the right choice indefinitely.

## Phase 3 — Serving infrastructure

- Stateless FastAPI instances behind a load balancer, horizontally autoscaled on request volume.
- An API gateway in front for auth, per-user rate limiting, and abuse detection — today the
  `/api/chat` endpoint has none of this, which is fine for a local demo and not acceptable for
  anything public.
- Multi-region deployment for latency across Europe, which for a European product also means
  **data residency and GDPR compliance become real legal requirements**, not implementation
  details — this product handles user dietary/allergy information, which is sensitive enough
  that "where is this stored and who can access it" needs an actual answer, not just a
  `.env` file on a laptop.
- Real secrets management (Vault / cloud provider secrets manager) instead of a local `.env`.
- The frontend graduates from a single static HTML page to a proper web app (and eventually
  native mobile), with a real design system and UI-level i18n (today only the *content* is
  translated to English; the interface itself would need to support the traveler's own
  language too at that point).

## Phase 4 — Data model and business shift

This is the part that's less "add infrastructure" and more "the product itself changes shape":

- **From scraped to claimed data.** At Zomato's scale, restaurants actively manage their own
  listings — menus, hours, photos — because there's a business incentive to (visibility, direct
  ordering). That means a business-account system, a verification/claim flow, and a completely
  different trust model: verified restaurant-submitted data alongside (or replacing) scraped
  data, with provenance tracked either way.
- **Moderation and data-quality pipeline.** Scraped/submitted data at scale needs automated
  quality checks (translation errors, stale prices, mismatched photos) and a human review queue
  for flagged content — this build's honesty guardrails (never fabricate) handle the "agent"
  side of that problem; they don't handle "the underlying data itself was wrong."
- **Personalization.** Real recommendation quality at scale comes from more than RAG over public
  data — order history, saved preferences, past ratings, a proper recommendation model layered
  on top of the retrieval this build already does.
- **Revenue-shaped incentives to fix the data problem this build works around.** The core reason
  this project scrapes restaurant websites instead of using a review platform API is that every
  free tier hit a wall. At real scale, the economics invert: paying for Google Places, or
  striking data partnerships directly with review platforms/restaurants, becomes justified and
  should replace the scraping-based acquisition path entirely rather than scaling it up.

## Observability and cost, at scale

- Centralized logging (the current per-process Python logging becomes one node's view of
  nothing) and distributed tracing across the agent's tool-call chain, so a slow or failed
  recommendation is debuggable across services, not just readable in one server's stdout.
- LLM cost dashboards and budgets per request — today's cost profile (a handful of LLM calls per
  chat turn: orchestration, extraction, translation, rerank) is fine at prototype volume and
  needs active management (caching, cheaper models for sub-tasks, batching) once multiplied by
  real traffic.
- Alerting on scraper/tool failure rates specifically — a silent rise in "menu not found" across
  many restaurants is a crawler-infrastructure incident, not background noise, and needs to page
  someone rather than just degrade individual chat responses gracefully forever.

## The output-safety layer will need to graduate past regex

Live testing found that a system-prompt rule alone isn't reliable enough for safety-critical
behavior (a dish that violates a stated allergen exclusion getting written into the answer
anyway, just with a caveat next to it) - see ARCHITECTURE.md's "defense in depth" section. The
fix that shipped is a set of regex passes over the model's finished markdown that strip
non-compliant rows, empty placeholder rows, and now-empty restaurant sections. That's the right
fast fix for the current scope, and it's genuinely deterministic where it matters (an excluded
ingredient cannot survive to the user), but regex-over-markdown is inherently coupled to the
exact table format the prompt asks for - a wording change that drifts the model's output shape
could silently make the filter stop matching. At real scale this should graduate to **structured
output for the final answer** (the model returns a typed list of {restaurant, dish, price,
ingredients} objects, validated and filtered in code, e.g. an excluded-ingredient dish literally
cannot be constructed into that structure) **with deterministic template rendering** turning that
into markdown/HTML afterward - the same safety property, without depending on text pattern-
matching against free-form prose.

## What doesn't need to change

Worth naming explicitly, since not everything here is a rewrite: the **agent's honesty
guardrails** (never fabricate, flag inferred data, surface caveats), the **hybrid
retrieval design** (exact filtering for hard constraints, vector search for soft preferences,
reranking on top), and the **chunking policy** (structure before embedding, not raw-document
splitting) are all correct architectural decisions independent of scale, and carry forward
unchanged into every phase above. Scaling this product is almost entirely an infrastructure and
data-acquisition problem, not a retrieval-design problem.
