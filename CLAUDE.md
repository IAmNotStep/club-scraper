# Club Scraper — architecture notes

Context for AI coding assistants (Claude Code and similar) working in this repo.

## What this is
A local web app for the Design Nation 2026 outreach team. Scrapes university club
directories to find design-related student organizations and their contact info
(email, Instagram, website).

## Run
```
pip install -r requirements.txt
python app.py
```
Open http://localhost:8080

## Architecture
- **Single-file Flask app** (`app.py`) — backend + embedded HTML/CSS/JS frontend via
  `render_template_string`
- **No database** — all data is in-memory, resets on restart
- **`platform_cache.json`** — persisted URL-to-platform detection cache

## Scraping backends
1. **CampusGroups** — JSON API (`/api/v1/groups`), falls back to HTML parsing
   (`/club_signup` page) if the API is disabled
2. **Engage / CampusLabs** — JSON API (`/api/discovery/search/organizations`)
3. **Generic** — HTML scraping with `requests` + `cloudscraper` (Cloudflare bypass),
   parses tables and heading/link patterns

## Platform detection (hybrid)
1. Local cache lookup (instant, free)
2. URL pattern hints (no HTTP needed)
3. HTML fingerprinting (regex for platform-specific scripts/globals/meta tags)
4. API probing (try CampusGroups, then Engage endpoints)
5. Fallback to generic

## Filtering & relevance
- **Search keywords** — sent to platform APIs as search queries
- **Filter keywords** — applied locally using whole-word regex (`\b` boundaries)
- **Relevance scoring** — name match = 2 pts, description match = 1 pt per keyword
- Results grouped by school, sorted by relevance within each group

## Key conventions
- All frontend code lives in the `HTML_TEMPLATE` string inside `app.py`
- Keywords (search + filter) are configurable from the UI Keywords tab
- Universities can be added/deleted from the UI
- `cloudscraper` is the fallback when `requests` gets a 403 (Cloudflare)
- URL normalization strips path suffixes (`/organizations`, `/club_signup`, etc.) before
  building API URLs

## Default universities
Yale, Stanford, MIT, Princeton, NYU, Drexel, Columbia, Parsons/The New School, Lehigh

## Dependencies
flask, requests, beautifulsoup4, openpyxl, cloudscraper

## Working in this repo
- Confirm before building new features
- One feature at a time — don't bundle changes
- Ask before integrating external APIs or services
- Keep the single-file architecture unless there's a strong reason to split
