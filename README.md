# Euro Food Finder

**Your AI dining concierge for Europe.** Tell it a city, a meal, and what you do (or don't)
eat — it finds real restaurants, reads their actual menus and reviews for you, translates
everything into English, and tells you exactly what to order and why. No fifteen tabs of
Google Maps, TripAdvisor, and Google Translate. One chat.

Built for the traveler who's vegetarian, vegan, allergic, or just picky, standing outside a
restaurant in Barcelona with a menu in Catalan and no idea what's actually in the "amanida
de temporada."

## What it actually does

- **Finds real restaurants** near you, filtered for your diet, not just "restaurants that
  showed up in a search."
- **Reads the menu for you** — scrapes the restaurant's actual site, translates every dish
  into English, and tells you the price in the local currency.
- **Knows what's in your food.** Ask for "something light, no mushrooms" and it filters
  dishes by real extracted ingredients, not vibes — and it's upfront when an ingredient
  is a best guess rather than something the menu actually states, which matters if you're
  asking because of an allergy.
- **Tells you what other diners think**, pulled from the restaurant's own site, sentiment
  and all — and it's honest that this isn't the same as an independent review platform.
- **Shows its work.** Every search, every page it reads, every filter it applies streams
  into the chat live, so you're never staring at a spinner wondering what it's doing.
- **Never makes things up.** If it can't find a menu, it says so. It doesn't invent a dish,
  a price, or a review to fill the silence.

## Try it

> **You:** Barcelona, dinner, vegetarian, no mushrooms
>
> **Euro Food Finder:** *(streaming)* 🔍 Searching restaurants in Barcelona for "vegetarian
> dinner"... → Found 12 candidates. 📋 Reading and translating the menu for Biocenter... →
> Extracted 35 menu items. ⭐ Looking for customer testimonials on Biocenter's website...
>
> **Euro Food Finder:**
> ### Biocenter
> *Carrer del Pintor Fortuny 25, Barcelona*
>
> Fully vegetarian, with a daily changing menu of fresh, seasonal dishes.
>
> - **Roasted Sweet Potato**, green basil sauce, white tahini — €8.00
> - **Tofu Curry**, pumpkin, carrot, cauliflower, rice — €14.50
> - **Lentil & Sweet Potato Burger**, spinach, pesto — €14.50 *(ingredients confirmed from menu)*
>
> [OpenStreetMap Listing](#) · [Menu](#)

## Get started

You need one thing: an OpenAI API key. Everything else — restaurant search, menus, reviews —
runs on free, keyless sources.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r ../requirements.txt
cp ../.env.example ../.env   # add your OPENAI_API_KEY
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** and start chatting.

### Or with Docker

```bash
docker compose up --build
```

Same result, containerized, with your data persisted across restarts.

### Want a reliable demo dataset instead of live scraping?

Live scraping is inherently hit-or-miss — some restaurants have simple static sites, others
are JS-heavy single-page apps a plain HTTP fetch can't read. For a dependable demo, seed the
cache offline first:

```bash
cd backend
python scripts/seed_data.py
```

This runs the exact same discovery → scrape → extract → embed pipeline the live agent uses,
just offline and with no request-latency pressure, against Barcelona, Rome, and Berlin by
default — trying many more candidates than a live chat turn reasonably could, and keeping only
the ones that actually yield a real, scrapeable menu. See
[`backend/scripts/seed_data.py`](backend/scripts/seed_data.py) and the generated
`seed_report.json` next to it for exactly what got kept and why anything was skipped — nothing
here is hand-picked or fabricated, every candidate comes from a live OpenStreetMap query.

Once seeded, asking about those cities in the chat hits the warm cache instantly instead of
scraping live — see **[ARCHITECTURE.md](ARCHITECTURE.md#5-offline-seeding)** for how the live
agent knows to prefer already-seeded restaurants.

## Want the technical details?

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how the agent, retrieval, and data pipeline
  actually work under the hood: the tool-calling loop, the hybrid vector-search + reranking
  design, the chunking policy, and the reliability engineering that keeps it from lying to
  you when the data isn't there.
- **[SCALING.md](SCALING.md)** — what changes if this needs to go from "runs on my laptop"
  to "runs like Zomato": what breaks first, and the roadmap to get there.

## Current limitations

This is an early-stage build, not a finished product — a few honest caveats:

- Menu/review coverage depends on each restaurant having a readable, mostly-static
  website. Modern JS-heavy sites come back empty rather than wrong — it tells you when it
  couldn't find something instead of guessing.
- Reviews come from each restaurant's own website, which only ever shows testimonials the
  business chose to publish — not the balanced, independent picture a review platform
  would give you. The agent always says so.
- One user, one browser tab, one session at a time — conversation history isn't persisted
  across server restarts yet.
