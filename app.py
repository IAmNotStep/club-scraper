#!/usr/bin/env python3
"""
Design Nation 2026 — Club Outreach Scraper
==========================================
A local web app for scraping university club directories.

Setup:
  pip install flask requests beautifulsoup4 openpyxl

Run:
  python app.py

Then open: http://localhost:5000
"""

from flask import Flask, render_template_string, jsonify, request
import requests as http_requests
import cloudscraper
import sys
import re
import time
import json
import threading
from datetime import datetime
from bs4 import BeautifulSoup

app = Flask(__name__)

# ============================================================
# IN-MEMORY DATA STORE
# ============================================================
scraped_clubs = []
scrape_status = {"running": False, "progress": 0, "total": 0, "current": "", "log": []}


# ============================================================
# SCRAPING CONFIG
# ============================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
}

SEARCH_KEYWORDS = ["design", "fashion", "marketing", "creative", "art", "UX", "media", "graphic", "visual", "brand"]

FILTER_KEYWORDS = [
    "design", "fashion", "marketing", "creative", "graphic", "ux", "ui",
    "visual", "brand", "advertising", "media", "art", "photo", "photography",
    "illustration", "architecture", "product design", "interaction", "typography",
    "film", "animation", "digital", "studio", "maker", "innovation",
    "entrepreneur", "style", "aesthetic", "branding",
]

# Active keyword sets (mutable — updated by the UI)
active_search_keywords = list(SEARCH_KEYWORDS)
active_filter_keywords = list(FILTER_KEYWORDS)

# Pre-loaded university directories
UNIVERSITIES = [
    # CampusGroups (API or HTML fallback)
    {"name": "Yale University", "url": "https://yaleconnect.yale.edu", "platform": "campusgroups", "enabled": True},
    {"name": "Stanford University", "url": "https://cardinalengage.stanford.edu", "platform": "campusgroups", "enabled": True},
    {"name": "MIT", "url": "https://engage.mit.edu", "platform": "campusgroups", "enabled": True},
    # Engage / CampusLabs
    {"name": "Princeton University", "url": "https://odus.princeton.edu/undergraduate-student-organizations", "platform": "generic", "enabled": True},
    {"name": "NYU", "url": "https://engage.nyu.edu", "platform": "engage", "enabled": True},
    {"name": "Drexel University", "url": "https://drexel.campuslabs.com/engage", "platform": "engage", "enabled": True},
    {"name": "Columbia University", "url": "https://columbia.campuslabs.com/engage", "platform": "engage", "enabled": True},
    {"name": "Parsons / The New School", "url": "https://newschool.campuslabs.com/engage", "platform": "engage", "enabled": True},
    {"name": "Lehigh University", "url": "https://lehigh.campuslabs.com/engage", "platform": "engage", "enabled": True},
]


# ============================================================
# PLATFORM DETECTION & URL NORMALIZATION
# ============================================================
import os

# Path suffixes to strip when normalizing URLs for API access
STRIP_PATHS = ["/organizations", "/club_signup", "/clubs", "/student-organizations",
               "/student-life/student-organizations", "/student-services/student-groups"]

# --- Level 1: Local Cache ---
# When running as a PyInstaller exe, __file__ points inside the temp bundle.
# Use the exe's directory instead so the cache persists next to the executable.
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_FILE = os.path.join(_APP_DIR, "platform_cache.json")


def _load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# --- Level 2: HTML Fingerprinting ---
# Signatures to look for in page source (script srcs, global vars, meta tags, etc.)
FINGERPRINTS = {
    "campusgroups": [
        # Script sources
        r'src=["\'][^"\']*campusgroups\.com',
        # Global JS variables
        r'window\.CampusGroupsConfig',
        r'CampusGroups\.init',
        # HTML markers
        r'id=["\']campusgroups',
        r'/club_signup',
        r'data-cgid=',
    ],
    "engage": [
        # Script sources & API patterns
        r'src=["\'][^"\']*campuslabs\.com',
        r'src=["\'][^"\']*collegiatelink\.net',
        # Global JS variables
        r'window\.AppConfig',
        r'window\.Engage',
        r'EngageApp',
        # API discovery pattern in inline scripts or links
        r'/api/discovery/',
        r'CampusLabs',
        # Meta tags
        r'campuslabs',
    ],
}

# URL-based hints (fast, no HTTP request needed)
URL_HINTS = {
    "campusgroups": ["yaleconnect", "cardinalengage", "campusgroups", "aggielife", "tigerlink"],
    "engage": ["campuslabs", "getinvolved", "collegiatelink", "presence", "engage.nyu", "callink"],
}


def _fingerprint_page(html):
    """Scan page HTML for platform-specific signatures."""
    for platform, patterns in FINGERPRINTS.items():
        for pattern in patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return platform
    return None


def _strip_base(url):
    """Strip known path suffixes to get the API base URL."""
    base = url.rstrip("/")
    for suffix in sorted(STRIP_PATHS, key=len, reverse=True):
        if base.lower().endswith(suffix):
            base = base[:len(base) - len(suffix)]
            break
    return base


def normalize_url(url, platform):
    """Strip trailing path segments so the URL works as an API base."""
    url = url.rstrip("/")
    if platform == "generic":
        return url  # generic scraper needs the full page URL
    return _strip_base(url)


def detect_platform(url):
    """
    Hybrid platform detection:
      Level 1 — Check local cache (free, instant)
      Level 2 — URL hints + HTML fingerprinting + API probing (free, 1-2 requests)
      Save result to cache for next time.
    """
    cache_key = _strip_base(url.rstrip("/")).lower()

    # --- Level 1: Cache lookup ---
    cache = _load_cache()
    if cache_key in cache:
        return cache[cache_key]

    # --- Level 2a: URL hints (no HTTP needed) ---
    url_lower = url.lower()
    for platform, hints in URL_HINTS.items():
        if any(hint in url_lower for hint in hints):
            cache[cache_key] = platform
            _save_cache(cache)
            return platform

    # --- Level 2b: Fetch the page and fingerprint its HTML ---
    base = _strip_base(url)
    try:
        resp = http_requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.ok:
            result = _fingerprint_page(resp.text)
            if result:
                cache[cache_key] = result
                _save_cache(cache)
                return result
            # Also fingerprint the final redirected URL
            final = resp.url.lower()
            for platform, hints in URL_HINTS.items():
                if any(hint in final for hint in hints):
                    cache[cache_key] = platform
                    _save_cache(cache)
                    return platform
    except Exception:
        pass

    # --- Level 2c: API probing (last resort before generic) ---
    try:
        r = http_requests.get(f"{base}/api/v1/groups", params={"limit": 1}, headers=HEADERS, timeout=8)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            cache[cache_key] = "campusgroups"
            _save_cache(cache)
            return "campusgroups"
    except Exception:
        pass

    try:
        r = http_requests.get(f"{base}/api/discovery/search/organizations", params={"top": 1}, headers=HEADERS, timeout=8)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            cache[cache_key] = "engage"
            _save_cache(cache)
            return "engage"
    except Exception:
        pass

    # --- Fallback ---
    cache[cache_key] = "generic"
    _save_cache(cache)
    return "generic"


# ============================================================
# SCRAPING FUNCTIONS
# ============================================================
def _word_match(keyword, text):
    """Check if keyword appears as a whole word (not a substring) in text."""
    return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE))


def matches_filter(name, description=""):
    text = f"{name} {description}".lower()
    return any(_word_match(kw, text) for kw in active_filter_keywords)


def relevance_score(name, description=""):
    """Score a club by how many filter keywords match. Name matches count double."""
    name_lower = name.lower()
    desc_lower = (description or "").lower()
    score = 0
    for kw in active_filter_keywords:
        if _word_match(kw, name_lower):
            score += 2  # name match = strong signal
        elif _word_match(kw, desc_lower):
            score += 1  # description match = weaker signal
    return score


def _parse_campusgroups_html(soup, university, base_url):
    """Parse club listings from CampusGroups HTML pages (/club_signup)."""
    results = []
    # Strategy 1: .list-group-item with aria-label (modern CampusGroups layout)
    for item in soup.select(".list-group-item"):
        aria = item.find(attrs={"aria-label": True})
        if not aria:
            continue
        name = aria.get("aria-label", "").strip()
        if not name or len(name) <= 3:
            continue
        if not matches_filter(name):
            continue
        # Try to grab the org page link and email
        website = ""
        email = ""
        for a in item.find_all("a", href=True):
            href = a.get("href", "").strip()
            if href.startswith("http") and not website:
                website = href
            elif href.startswith("mailto:"):
                email = href.replace("mailto:", "")
        results.append({
            "university": university, "club_name": name,
            "description": "", "email": email, "website": website,
            "instagram": "", "source": base_url, "status": "Not Started",
            "relevance": relevance_score(name),
        })
    # Strategy 2: headings / named elements (older CampusGroups layout)
    if not results:
        for el in soup.select("h3, h4, h5, .club-name, .group-name"):
            name = el.get_text(strip=True)
            if name and len(name) > 3 and matches_filter(name):
                results.append({
                    "university": university, "club_name": name,
                    "description": "", "email": "", "website": "",
                    "instagram": "", "source": base_url, "status": "Not Started",
                    "relevance": relevance_score(name),
                })
    return results


def scrape_campusgroups(university, base_url):
    results = []
    api_works = None  # None = untested, True/False after first keyword

    for keyword in active_search_keywords:
        try:
            # Try JSON API first (only on first keyword, then remember)
            if api_works is not False:
                api_url = f"{base_url}/api/v1/groups"
                resp = http_requests.get(api_url, params={"search": keyword, "limit": 100}, headers=HEADERS, timeout=15)
                if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                    api_works = True
                    data = resp.json()
                    groups = data if isinstance(data, list) else data.get("groups", data.get("data", []))
                    for g in groups:
                        name = g.get("name", g.get("Name", ""))
                        desc = g.get("description", g.get("Description", ""))
                        if desc:
                            desc = BeautifulSoup(str(desc), "html.parser").get_text(separator=" ")[:400]
                        if name and matches_filter(name, desc or ""):
                            results.append({
                                "university": university,
                                "club_name": name,
                                "description": desc or "",
                                "email": g.get("email", g.get("Email", "")),
                                "website": g.get("website", g.get("Website", "")),
                                "instagram": "",
                                "source": base_url,
                                "status": "Not Started",
                                "relevance": relevance_score(name, desc or ""),
                            })
                    time.sleep(0.4)
                    continue
                else:
                    api_works = False

            # Fallback: HTML scraping via /club_signup
            resp = http_requests.get(f"{base_url}/club_signup", params={"search": keyword}, headers=HEADERS, timeout=15)
            if resp.ok:
                soup = BeautifulSoup(resp.text, "html.parser")
                results.extend(_parse_campusgroups_html(soup, university, base_url))
            time.sleep(0.4)
        except Exception:
            continue
    return results


def scrape_engage(university, base_url):
    results = []
    api_url = f"{base_url}/api/discovery/search/organizations"
    for keyword in active_search_keywords:
        try:
            params = {"orderBy[0]": "UpperName asc", "top": "200", "filter": "", "query": keyword}
            resp = http_requests.get(api_url, params=params, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for org in data.get("value", []):
                    name = org.get("Name", "")
                    desc = org.get("Description", org.get("Summary", ""))
                    if desc:
                        desc = BeautifulSoup(str(desc), "html.parser").get_text(separator=" ")
                        desc = re.sub(r'\s+', ' ', desc).strip()[:400]
                    if name and matches_filter(name, desc or ""):
                        social = org.get("SocialMedia", {})
                        ig = social.get("Instagram", "") if isinstance(social, dict) else ""
                        results.append({
                            "university": university, "club_name": name,
                            "description": desc or "", "email": org.get("Email", ""),
                            "website": org.get("WebsiteKey", org.get("Website", "")),
                            "instagram": ig, "source": base_url, "status": "Not Started",
                            "relevance": relevance_score(name, desc or ""),
                        })
            time.sleep(0.4)
        except Exception:
            continue
    return results


def scrape_generic(university, url):
    results = []
    try:
        resp = http_requests.get(url, headers=HEADERS, timeout=15)
        # Fall back to cloudscraper if blocked by Cloudflare (403)
        if resp.status_code == 403:
            scraper = cloudscraper.create_scraper()
            resp = scraper.get(url, timeout=20)
        if resp.ok:
            soup = BeautifulSoup(resp.text, "html.parser")
            seen = set()
            # Strategy 1: Table rows (e.g. Princeton-style org lists)
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    for cell in row.find_all("td"):
                        text = cell.get_text(strip=True)
                        if 3 < len(text) < 200 and text.lower() not in seen and matches_filter(text):
                            seen.add(text.lower())
                            link = cell.find("a")
                            href = link.get("href", "") if link else ""
                            results.append({
                                "university": university, "club_name": text,
                                "description": "", "email": "",
                                "website": href if href.startswith("http") else "",
                                "instagram": "", "source": url, "status": "Not Started",
                                "relevance": relevance_score(text),
                            })
            # Strategy 2: Headings and links (original approach)
            for el in soup.find_all(["h2", "h3", "h4", "h5", "a", "strong"]):
                text = el.get_text(strip=True)
                if 3 < len(text) < 200 and text.lower() not in seen and matches_filter(text):
                    seen.add(text.lower())
                    email = ""
                    ig = ""
                    parent = el.parent
                    if parent:
                        em = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', parent.get_text())
                        if em:
                            email = em.group()
                        igm = re.search(r'@([a-zA-Z0-9_.]+)', parent.get_text())
                        if igm and "instagram" in parent.get_text().lower():
                            ig = f"@{igm.group(1)}"
                    href = el.get("href", "") if el.name == "a" else ""
                    results.append({
                        "university": university, "club_name": text,
                        "description": "", "email": email,
                        "website": href if href.startswith("http") else "",
                        "instagram": ig, "source": url, "status": "Not Started",
                        "relevance": relevance_score(text),
                    })
    except Exception:
        pass
    return results


def deduplicate(results):
    seen = set()
    unique = []
    for club in results:
        key = (club["university"].lower(), club["club_name"].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(club)
    return unique


def run_scrape(selected_indices):
    global scraped_clubs, scrape_status
    scrape_status = {"running": True, "progress": 0, "total": len(selected_indices), "current": "", "log": []}

    new_results = []
    for i, idx in enumerate(selected_indices):
        uni = UNIVERSITIES[idx]
        scrape_status["progress"] = i + 1
        scrape_status["current"] = uni["name"]
        scrape_status["log"].append(f"Scraping {uni['name']}...")

        if uni["platform"] == "campusgroups":
            clubs = scrape_campusgroups(uni["name"], uni["url"])
        elif uni["platform"] == "engage":
            clubs = scrape_engage(uni["name"], uni["url"])
        else:
            clubs = scrape_generic(uni["name"], uni["url"])

        new_results.extend(clubs)
        scrape_status["log"].append(f"  → Found {len(clubs)} clubs at {uni['name']}")

    new_results = deduplicate(new_results)
    # Merge with existing (don't duplicate)
    existing_keys = {(c["university"].lower(), c["club_name"].lower()) for c in scraped_clubs}
    for club in new_results:
        key = (club["university"].lower(), club["club_name"].lower())
        if key not in existing_keys:
            scraped_clubs.append(club)
            existing_keys.add(key)

    # Sort all results by relevance (highest first)
    scraped_clubs.sort(key=lambda c: c.get("relevance", 0), reverse=True)

    scrape_status["running"] = False
    scrape_status["log"].append(f"\n✅ Done! {len(new_results)} new clubs found. Total: {len(scraped_clubs)}")


# ============================================================
# ROUTES
# ============================================================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, universities=UNIVERSITIES)


@app.route("/api/universities", methods=["GET"])
def get_universities():
    return jsonify(UNIVERSITIES)


@app.route("/api/universities", methods=["POST"])
def add_university():
    data = request.json
    raw_url = data["url"].strip().rstrip("/")
    platform = data.get("platform") or detect_platform(raw_url)
    url = normalize_url(raw_url, platform)
    UNIVERSITIES.append({
        "name": data["name"],
        "url": url,
        "platform": platform,
        "enabled": True,
    })
    return jsonify({"ok": True, "index": len(UNIVERSITIES) - 1, "platform": platform})


@app.route("/api/universities/<int:idx>", methods=["DELETE"])
def delete_university(idx):
    if 0 <= idx < len(UNIVERSITIES):
        UNIVERSITIES.pop(idx)
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/clubs/clear", methods=["POST"])
def clear_clubs():
    global scraped_clubs
    scraped_clubs = []
    return jsonify({"ok": True})


@app.route("/api/cache")
def get_cache():
    return jsonify(_load_cache())


@app.route("/api/cache", methods=["DELETE"])
def clear_cache():
    _save_cache({})
    return jsonify({"ok": True})


@app.route("/api/keywords")
def get_keywords():
    return jsonify({
        "search": active_search_keywords,
        "filter": active_filter_keywords,
        "defaults": {"search": SEARCH_KEYWORDS, "filter": FILTER_KEYWORDS},
    })


@app.route("/api/keywords", methods=["PUT"])
def update_keywords():
    global active_search_keywords, active_filter_keywords
    data = request.json
    if "search" in data:
        active_search_keywords = [k.strip().lower() for k in data["search"] if k.strip()]
    if "filter" in data:
        active_filter_keywords = [k.strip().lower() for k in data["filter"] if k.strip()]
    return jsonify({"ok": True, "search": active_search_keywords, "filter": active_filter_keywords})


@app.route("/api/scrape", methods=["POST"])
def start_scrape():
    if scrape_status["running"]:
        return jsonify({"error": "Scrape already running"}), 409
    data = request.json
    indices = data.get("indices", [])
    if not indices:
        indices = [i for i, u in enumerate(UNIVERSITIES) if u["enabled"]]
    thread = threading.Thread(target=run_scrape, args=(indices,))
    thread.start()
    return jsonify({"ok": True, "scraping": len(indices)})


@app.route("/api/scrape/status")
def get_scrape_status():
    return jsonify(scrape_status)


@app.route("/api/clubs")
def get_clubs():
    return jsonify(scraped_clubs)


@app.route("/api/clubs/<int:idx>", methods=["DELETE"])
def delete_club(idx):
    if 0 <= idx < len(scraped_clubs):
        scraped_clubs.pop(idx)
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


# ============================================================
# HTML TEMPLATE (embedded single-page app)
# ============================================================
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Design Nation 2026 — Club Scraper</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,500;0,9..40,700;1,9..40,400&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0c0a14;
    --surface: #161224;
    --surface2: #1e1833;
    --border: #2a2440;
    --text: #e8e4f0;
    --text2: #9b93b0;
    --accent: #a78bfa;
    --accent2: #7c3aed;
    --success: #34d399;
    --warning: #fbbf24;
    --danger: #f87171;
    --glow: rgba(167, 139, 250, 0.15);
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'DM Sans', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* Ambient background */
  body::before {
    content: '';
    position: fixed;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: radial-gradient(ellipse at 20% 50%, rgba(124, 58, 237, 0.06) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 20%, rgba(167, 139, 250, 0.04) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 80%, rgba(99, 102, 241, 0.03) 0%, transparent 50%);
    pointer-events: none;
    z-index: -1;
  }

  .container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }

  /* ---- HEADER ---- */
  header {
    padding: 32px 0 24px;
    border-bottom: 1px solid var(--border);
  }
  header .top { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px; }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-icon {
    width: 40px; height: 40px;
    background: linear-gradient(135deg, var(--accent2), var(--accent));
    border-radius: 10px;
    display: grid; place-items: center;
    font-family: 'Space Mono', monospace;
    font-weight: 700; font-size: 16px; color: white;
  }
  .logo h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
  .logo h1 span { color: var(--accent); }
  .logo p { font-size: 12px; color: var(--text2); font-family: 'Space Mono', monospace; }

  .header-actions { display: flex; gap: 8px; }

  /* ---- BUTTONS ---- */
  .btn {
    font-family: 'DM Sans', sans-serif;
    font-size: 13px; font-weight: 500;
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: all 0.2s;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .btn:hover { background: var(--surface2); border-color: var(--accent); }
  .btn.primary {
    background: linear-gradient(135deg, var(--accent2), var(--accent));
    border: none; color: white; font-weight: 700;
  }
  .btn.primary:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 4px 20px var(--glow); }
  .btn.primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn.danger { border-color: var(--danger); color: var(--danger); }
  .btn.danger:hover { background: rgba(248, 113, 113, 0.1); }
  .btn.sm { padding: 4px 10px; font-size: 12px; }

  /* ---- TABS ---- */
  .tabs {
    display: flex; gap: 0; margin-top: 24px;
    border-bottom: 2px solid var(--border);
  }
  .tab {
    padding: 10px 20px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    color: var(--text2);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: all 0.2s;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ---- PANELS ---- */
  .panel { display: none; padding: 24px 0; }
  .panel.active { display: block; }

  /* ---- UNIVERSITY GRID ---- */
  .uni-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .uni-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    display: flex; align-items: center; gap: 12px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .uni-card:hover { border-color: var(--accent); background: var(--surface2); }
  .uni-card.selected { border-color: var(--accent); background: var(--glow); }
  .uni-card input[type="checkbox"] { accent-color: var(--accent); width: 16px; height: 16px; }
  .uni-card .info { flex: 1; }
  .uni-card .info .name { font-weight: 500; font-size: 14px; }
  .uni-card .info .platform {
    font-family: 'Space Mono', monospace;
    font-size: 10px; color: var(--text2);
    background: var(--bg); padding: 2px 6px; border-radius: 4px;
    display: inline-block; margin-top: 4px;
  }

  /* ---- ADD UNIVERSITY FORM ---- */
  .add-form {
    background: var(--surface);
    border: 1px dashed var(--border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 20px;
    display: none;
  }
  .add-form.show { display: block; }
  .add-form .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; }
  .field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 150px; }
  .field label { font-size: 11px; color: var(--text2); font-family: 'Space Mono', monospace; text-transform: uppercase; letter-spacing: 0.5px; }
  .field input, .field select {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 12px; color: var(--text); font-family: 'DM Sans', sans-serif; font-size: 13px;
  }
  .field input:focus, .field select:focus { outline: none; border-color: var(--accent); }

  /* ---- PROGRESS ---- */
  .progress-bar {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 20px;
    display: none;
  }
  .progress-bar.show { display: block; }
  .progress-track { background: var(--bg); border-radius: 6px; height: 8px; overflow: hidden; margin: 10px 0; }
  .progress-fill {
    height: 100%; border-radius: 6px;
    background: linear-gradient(90deg, var(--accent2), var(--accent));
    transition: width 0.5s ease;
  }
  .progress-text { font-family: 'Space Mono', monospace; font-size: 12px; color: var(--text2); }
  .log-box {
    background: var(--bg); border-radius: 6px; padding: 10px;
    max-height: 120px; overflow-y: auto;
    font-family: 'Space Mono', monospace; font-size: 11px;
    color: var(--text2); line-height: 1.8;
    margin-top: 10px;
  }

  /* ---- RESULTS TABLE ---- */
  .table-controls {
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 12px; margin-bottom: 16px;
  }
  .search-box {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 14px; color: var(--text); font-size: 13px;
    font-family: 'DM Sans', sans-serif; width: 280px;
  }
  .search-box:focus { outline: none; border-color: var(--accent); }
  .count { font-family: 'Space Mono', monospace; font-size: 12px; color: var(--text2); }

  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead { background: var(--surface2); position: sticky; top: 0; }
  th {
    text-align: left; padding: 12px 14px;
    font-family: 'Space Mono', monospace;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--text2); border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td {
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    vertical-align: top; max-width: 250px;
  }
  tr:hover { background: var(--surface); }
  td.desc { font-size: 11px; color: var(--text2); max-width: 300px; }
  td.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .empty-state {
    text-align: center; padding: 60px 20px; color: var(--text2);
  }
  .empty-state .icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
  .empty-state p { font-size: 14px; max-width: 400px; margin: 0 auto; line-height: 1.6; }

  .delete-btn {
    background: none; border: none; color: var(--text2); cursor: pointer; font-size: 14px; padding: 2px 6px; border-radius: 4px;
  }
  .delete-btn:hover { color: var(--danger); background: rgba(248,113,113,0.1); }

  /* ---- SCHOOL GROUPS ---- */
  .school-group { margin-bottom: 20px; }
  .school-group table { margin-bottom: 0; }
  .school-header {
    font-size: 15px; font-weight: 700; padding: 12px 14px;
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 10px 10px 0 0;
    display: flex; align-items: center; gap: 10px;
  }
  .school-count {
    font-family: 'Space Mono', monospace; font-size: 11px;
    color: var(--text2); font-weight: 400;
  }
  .school-group .table-wrap { border-top: none; }
  .school-group table { border-radius: 0 0 10px 10px; }

  /* ---- RELEVANCE BADGE ---- */
  .rel-badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-family: 'Space Mono', monospace; font-size: 11px; font-weight: 700;
    text-align: center; min-width: 28px;
  }
  .rel-high { background: rgba(52, 211, 153, 0.2); color: #34d399; }
  .rel-med  { background: rgba(251, 191, 36, 0.2); color: #fbbf24; }
  .rel-low  { background: rgba(156, 163, 175, 0.2); color: #9ca3af; }

  /* ---- KEYWORD CHIPS ---- */
  .keyword-chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .kw-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 10px; font-size: 12px; font-family: 'Space Mono', monospace;
    cursor: pointer; transition: all 0.2s; user-select: none;
  }
  .kw-chip:hover { border-color: var(--accent); }
  .kw-chip.active { border-color: var(--accent); background: var(--glow); color: var(--accent); }
  .kw-chip.inactive { opacity: 0.4; text-decoration: line-through; }
  .kw-chip .remove {
    font-size: 10px; color: var(--text2); cursor: pointer;
    padding: 0 2px; border-radius: 3px;
  }
  .kw-chip .remove:hover { color: var(--danger); }

  /* ---- RESPONSIVE ---- */
  @media (max-width: 640px) {
    .uni-grid { grid-template-columns: 1fr; }
    .search-box { width: 100%; }
    .header-actions { width: 100%; }
    .header-actions .btn { flex: 1; justify-content: center; }
  }
</style>
</head>
<body>

<div class="container">
  <header>
    <div class="top">
      <div class="logo">
        <div class="logo-icon">DN</div>
        <div>
          <h1>Design <span>Nation</span> Scraper</h1>
          <p>Club outreach tool · 2026</p>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn danger sm" onclick="clearResults()" title="Clear all scraped results">Clear Results</button>
        <button class="btn primary" id="scrapeBtn" onclick="startScrape()">▶ Scrape Selected</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="universities" onclick="switchTab(this)">Universities</div>
      <div class="tab" data-tab="keywords" onclick="switchTab(this)">Keywords</div>
      <div class="tab" data-tab="results" onclick="switchTab(this)">Results <span id="resultCount"></span></div>
    </div>
  </header>

  <!-- UNIVERSITIES PANEL -->
  <div class="panel active" id="panel-universities">

    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:8px;">
      <div>
        <button class="btn sm" onclick="selectAll(true)">Select All</button>
        <button class="btn sm" onclick="selectAll(false)">Deselect All</button>
      </div>
      <button class="btn sm" onclick="toggleAddForm()">+ Add University</button>
    </div>

    <div class="add-form" id="addForm">
      <div class="row">
        <div class="field" style="flex:2;">
          <label>University Name</label>
          <input type="text" id="newName" placeholder="e.g. Duke University">
        </div>
        <div class="field" style="flex:3;">
          <label>Club Directory URL</label>
          <input type="text" id="newUrl" placeholder="e.g. https://duke.campuslabs.com/engage">
        </div>
        <button class="btn primary sm" onclick="addUniversity()" style="align-self:end; margin-bottom:1px;">Add</button>
      </div>
      <div style="margin-top:8px; font-size:11px; color:var(--text2); font-family:'Space Mono',monospace;">
        Platform is auto-detected from the URL.
      </div>
    </div>

    <div class="progress-bar" id="progressBar">
      <div class="progress-text" id="progressText">Scraping...</div>
      <div class="progress-track"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
      <div class="log-box" id="logBox"></div>
    </div>

    <div class="uni-grid" id="uniGrid"></div>
  </div>

  <!-- KEYWORDS PANEL -->
  <div class="panel" id="panel-keywords">
    <div style="margin-bottom:24px;">
      <h3 style="font-size:14px; margin-bottom:4px;">Search Keywords</h3>
      <p style="font-size:11px; color:var(--text2); font-family:'Space Mono',monospace; margin-bottom:12px;">
        These are sent as search queries to club directory APIs. Toggle off keywords you don't need.
      </p>
      <div class="keyword-chips" id="searchChips"></div>
      <div class="add-keyword-row" style="margin-top:10px; display:flex; gap:8px;">
        <input type="text" id="newSearchKw" class="search-box" placeholder="Add search keyword..." style="width:220px;">
        <button class="btn sm" onclick="addKeyword('search')">+ Add</button>
      </div>
    </div>
    <div>
      <h3 style="font-size:14px; margin-bottom:4px;">Filter Keywords</h3>
      <p style="font-size:11px; color:var(--text2); font-family:'Space Mono',monospace; margin-bottom:12px;">
        Results are kept only if their name or description contains at least one of these. Use partial words (e.g. "advertis" matches "advertising").
      </p>
      <div class="keyword-chips" id="filterChips"></div>
      <div class="add-keyword-row" style="margin-top:10px; display:flex; gap:8px;">
        <input type="text" id="newFilterKw" class="search-box" placeholder="Add filter keyword..." style="width:220px;">
        <button class="btn sm" onclick="addKeyword('filter')">+ Add</button>
      </div>
    </div>
    <div style="margin-top:20px; display:flex; gap:8px;">
      <button class="btn primary sm" onclick="saveKeywords()">Save Changes</button>
      <button class="btn sm danger" onclick="resetKeywords()">Reset to Defaults</button>
    </div>
    <div id="kwStatus" style="margin-top:10px; font-size:12px; color:var(--success); font-family:'Space Mono',monospace; display:none;"></div>
  </div>

  <!-- RESULTS PANEL -->
  <div class="panel" id="panel-results">
    <div class="table-controls">
      <input type="text" class="search-box" placeholder="Search clubs..." oninput="filterTable(this.value)" id="searchInput">
      <span class="count" id="tableCount">0 clubs</span>
    </div>
    <div class="table-wrap" id="tableWrap">
      <div class="empty-state" id="emptyState">
        <div class="icon">🔍</div>
        <p>No clubs scraped yet. Go to the Universities tab, select schools, and hit "Scrape Selected" to get started.</p>
      </div>
    </div>
  </div>
</div>

<script>
  let universities = {{ universities | tojson }};
  let clubs = [];
  let pollInterval = null;

  // ---- RENDER UNIVERSITIES ----
  function renderUniversities() {
    const grid = document.getElementById('uniGrid');
    grid.innerHTML = universities.map((u, i) => `
      <div class="uni-card ${u.enabled ? 'selected' : ''}" onclick="toggleUni(${i})">
        <input type="checkbox" ${u.enabled ? 'checked' : ''} onclick="event.stopPropagation(); toggleUni(${i})">
        <div class="info">
          <div class="name">${u.name}</div>
          <span class="platform">${u.platform}</span>
        </div>
        <button class="delete-btn" onclick="event.stopPropagation(); deleteUni(${i})" title="Remove university">✕</button>
      </div>
    `).join('');
  }

  function toggleUni(idx) {
    universities[idx].enabled = !universities[idx].enabled;
    renderUniversities();
  }

  function selectAll(val) {
    universities.forEach(u => u.enabled = val);
    renderUniversities();
  }

  function deleteUni(idx) {
    if (!confirm(`Remove ${universities[idx].name}?`)) return;
    fetch(`/api/universities/${idx}`, {method: 'DELETE'}).then(r => r.json()).then(() => {
      universities.splice(idx, 1);
      renderUniversities();
    });
  }

  function toggleAddForm() {
    document.getElementById('addForm').classList.toggle('show');
  }

  function addUniversity() {
    const name = document.getElementById('newName').value.trim();
    const url = document.getElementById('newUrl').value.trim();
    if (!name || !url) return alert('Please fill in name and URL');
    fetch('/api/universities', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, url})
    }).then(r => r.json()).then(data => {
      universities.push({name, url, platform: data.platform, enabled: true});
      renderUniversities();
      document.getElementById('newName').value = '';
      document.getElementById('newUrl').value = '';
      document.getElementById('addForm').classList.remove('show');
    });
  }

  // ---- SCRAPING ----
  function startScrape() {
    const indices = universities.map((u, i) => u.enabled ? i : null).filter(i => i !== null);
    if (indices.length === 0) return alert('Select at least one university');

    document.getElementById('scrapeBtn').disabled = true;
    document.getElementById('progressBar').classList.add('show');

    fetch('/api/scrape', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({indices})
    }).then(r => r.json()).then(() => {
      pollInterval = setInterval(pollStatus, 800);
    });
  }

  function pollStatus() {
    fetch('/api/scrape/status').then(r => r.json()).then(s => {
      const pct = s.total > 0 ? Math.round((s.progress / s.total) * 100) : 0;
      document.getElementById('progressFill').style.width = pct + '%';
      document.getElementById('progressText').textContent =
        s.running ? `Scraping ${s.current}... (${s.progress}/${s.total})` : 'Done!';
      document.getElementById('logBox').innerHTML = s.log.map(l => `<div>${l}</div>`).join('');
      document.getElementById('logBox').scrollTop = 99999;

      if (!s.running) {
        clearInterval(pollInterval);
        document.getElementById('scrapeBtn').disabled = false;
        setTimeout(() => {
          loadClubs();
          switchTab(document.querySelector('[data-tab="results"]'));
        }, 500);
      }
    });
  }

  // ---- RESULTS TABLE ----
  function loadClubs() {
    fetch('/api/clubs').then(r => r.json()).then(data => {
      clubs = data;
      renderTable(clubs);
      document.getElementById('resultCount').textContent = `(${clubs.length})`;
    });
  }

  function relBadge(score) {
    const s = score || 0;
    const cls = s >= 4 ? 'rel-high' : s >= 2 ? 'rel-med' : 'rel-low';
    return `<span class="rel-badge ${cls}">${s}</span>`;
  }

  function renderTable(data) {
    const wrap = document.getElementById('tableWrap');
    document.getElementById('tableCount').textContent = `${data.length} clubs`;

    if (data.length === 0) {
      wrap.innerHTML = `<div class="empty-state"><div class="icon">🔍</div><p>No clubs found yet.</p></div>`;
      return;
    }

    // Group by university, then sort each group by relevance
    const grouped = {};
    data.forEach(c => {
      if (!grouped[c.university]) grouped[c.university] = [];
      grouped[c.university].push(c);
    });
    // Sort groups alphabetically, clubs within each group by relevance desc
    const sortedSchools = Object.keys(grouped).sort();
    sortedSchools.forEach(school => {
      grouped[school].sort((a, b) => (b.relevance || 0) - (a.relevance || 0));
    });

    let html = '';
    sortedSchools.forEach(school => {
      const schoolClubs = grouped[school];
      html += `<div class="school-group">
        <div class="school-header">${school} <span class="school-count">${schoolClubs.length} clubs</span></div>
        <table>
          <thead><tr>
            <th>Rel.</th><th>Club</th><th>Description</th>
            <th>Email</th><th>Instagram</th><th>Website</th><th></th>
          </tr></thead>
          <tbody>${schoolClubs.map((c, i) => {
            const idx = clubs.indexOf(c);
            return `<tr>
              <td>${relBadge(c.relevance)}</td>
              <td><strong>${c.club_name}</strong></td>
              <td class="desc">${(c.description || '').substring(0, 150)}${(c.description || '').length > 150 ? '...' : ''}</td>
              <td class="truncate">${c.email ? `<a href="mailto:${c.email}" style="color:var(--accent)">${c.email}</a>` : '—'}</td>
              <td class="truncate">${c.instagram ? `<a href="https://instagram.com/${c.instagram.replace('@','')}" target="_blank" style="color:var(--accent)">${c.instagram}</a>` : '—'}</td>
              <td class="truncate">${c.website ? `<a href="${c.website.startsWith('http') ? c.website : 'https://'+c.website}" target="_blank" style="color:var(--accent)">Link</a>` : '—'}</td>
              <td><button class="delete-btn" onclick="deleteClub(${idx})" title="Remove">✕</button></td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </div>`;
    });
    wrap.innerHTML = html;
  }

  function filterTable(query) {
    const q = query.toLowerCase();
    const filtered = clubs.filter(c =>
      c.university.toLowerCase().includes(q) ||
      c.club_name.toLowerCase().includes(q) ||
      (c.description || '').toLowerCase().includes(q)
    );
    renderTable(filtered);
  }

  function deleteClub(idx) {
    if (!confirm('Remove this club?')) return;
    fetch(`/api/clubs/${idx}`, {method: 'DELETE'}).then(() => loadClubs());
  }

  // ---- CLEAR ----
  function clearResults() {
    if (!confirm('Clear all scraped results? This cannot be undone.')) return;
    fetch('/api/clubs/clear', {method: 'POST'}).then(r => r.json()).then(() => {
      clubs = [];
      renderTable([]);
      document.getElementById('resultCount').textContent = '';
    });
  }

  // ---- TABS ----
  function switchTab(el) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('panel-' + el.dataset.tab).classList.add('active');
    if (el.dataset.tab === 'results') loadClubs();
    if (el.dataset.tab === 'keywords') loadKeywords();
  }

  // ---- KEYWORDS ----
  let keywordData = {search: [], filter: [], defaults: {search: [], filter: []}};
  // Track which keywords are enabled (by index)
  let kwEnabled = {search: new Set(), filter: new Set()};

  function loadKeywords() {
    fetch('/api/keywords').then(r => r.json()).then(data => {
      keywordData = data;
      // All active by default
      kwEnabled.search = new Set(data.search.map((_, i) => i));
      kwEnabled.filter = new Set(data.filter.map((_, i) => i));
      renderKeywords();
    });
  }

  function renderKeywords() {
    renderChips('search', 'searchChips');
    renderChips('filter', 'filterChips');
  }

  function renderChips(type, containerId) {
    const el = document.getElementById(containerId);
    el.innerHTML = keywordData[type].map((kw, i) => {
      const active = kwEnabled[type].has(i);
      return `<span class="kw-chip ${active ? 'active' : 'inactive'}" onclick="toggleKw('${type}', ${i})">
        ${kw}
        <span class="remove" onclick="event.stopPropagation(); removeKw('${type}', ${i})">✕</span>
      </span>`;
    }).join('');
  }

  function toggleKw(type, idx) {
    if (kwEnabled[type].has(idx)) kwEnabled[type].delete(idx);
    else kwEnabled[type].add(idx);
    renderKeywords();
  }

  function removeKw(type, idx) {
    keywordData[type].splice(idx, 1);
    // Rebuild enabled set (shift indices down)
    const newSet = new Set();
    kwEnabled[type].forEach(i => {
      if (i < idx) newSet.add(i);
      else if (i > idx) newSet.add(i - 1);
    });
    kwEnabled[type] = newSet;
    renderKeywords();
  }

  function addKeyword(type) {
    const inputId = type === 'search' ? 'newSearchKw' : 'newFilterKw';
    const val = document.getElementById(inputId).value.trim().toLowerCase();
    if (!val) return;
    if (keywordData[type].includes(val)) return;
    keywordData[type].push(val);
    kwEnabled[type].add(keywordData[type].length - 1);
    document.getElementById(inputId).value = '';
    renderKeywords();
  }

  function saveKeywords() {
    const search = keywordData.search.filter((_, i) => kwEnabled.search.has(i));
    const filter = keywordData.filter.filter((_, i) => kwEnabled.filter.has(i));
    fetch('/api/keywords', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({search, filter})
    }).then(r => r.json()).then(data => {
      keywordData.search = data.search;
      keywordData.filter = data.filter;
      kwEnabled.search = new Set(data.search.map((_, i) => i));
      kwEnabled.filter = new Set(data.filter.map((_, i) => i));
      renderKeywords();
      const st = document.getElementById('kwStatus');
      st.textContent = `Saved! ${data.search.length} search + ${data.filter.length} filter keywords active.`;
      st.style.display = 'block';
      setTimeout(() => st.style.display = 'none', 3000);
    });
  }

  function resetKeywords() {
    if (!confirm('Reset all keywords to defaults?')) return;
    fetch('/api/keywords', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({search: keywordData.defaults.search, filter: keywordData.defaults.filter})
    }).then(r => r.json()).then(data => {
      keywordData.search = data.search;
      keywordData.filter = data.filter;
      kwEnabled.search = new Set(data.search.map((_, i) => i));
      kwEnabled.filter = new Set(data.filter.map((_, i) => i));
      renderKeywords();
      const st = document.getElementById('kwStatus');
      st.textContent = 'Reset to defaults.';
      st.style.display = 'block';
      setTimeout(() => st.style.display = 'none', 3000);
    });
  }

  // ---- INIT ----
  renderUniversities();
  loadKeywords();
</script>
</body>
</html>
"""

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    import webbrowser

    port = 8080
    print("\n" + "=" * 50)
    print("  Design Nation 2026 — Club Scraper")
    print(f"  Open in browser: http://localhost:{port}")
    print("=" * 50 + "\n")

    # Auto-open browser after a short delay
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    # debug=False when packaged, True when running from source
    is_frozen = getattr(sys, 'frozen', False)
    app.run(debug=not is_frozen, port=port)
