#!/usr/bin/env python3
"""
HelloFresh Recipe Scraper
Fetches recipes from the HelloFresh API and saves structured data locally.
Auto-refreshes the Bearer token from the public recipes page.
"""

import json
import re
import subprocess
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

import requests

API_BASE = "https://gw.hellofresh.com/api"
HF_RECIPES_URL = "https://www.hellofresh.com/recipes"
DATA_DIR = Path("data")
RECIPES_FILE = DATA_DIR / "recipes.json"
TOKEN_FILE = DATA_DIR / "token.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.hellofresh.com/recipes",
    "Origin": "https://www.hellofresh.com",
}


def _curl_get(url: str, extra_headers: list[str] | None = None) -> str:
    """Fetch a URL via curl (bypasses Python TLS fingerprint blocking)."""
    cmd = [
        "curl", "-s", "-L",
        "-H", f"User-Agent: {HEADERS['User-Agent']}",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
    ]
    for h in (extra_headers or []):
        cmd += ["-H", h]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")
    return result.stdout


def get_bearer_token() -> str:
    """Extract a Bearer token from the HelloFresh public recipes page."""
    cached = _load_cached_token()
    if cached:
        return cached

    print("Fetching Bearer token from HelloFresh...")
    html = _curl_get(HF_RECIPES_URL)

    m = re.search(r'"access_token"\s*:\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError("Could not find access_token in HelloFresh page")

    token = m.group(1)
    expires_match = re.search(r'"expires_in"\s*:\s*(\d+)', html)
    expires_in = int(expires_match.group(1)) if expires_match else 3600

    DATA_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "expires_at": time.time() + expires_in - 60,
    }))
    print("Token acquired.")
    return token


def _load_cached_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    data = json.loads(TOKEN_FILE.read_text())
    if time.time() < data.get("expires_at", 0):
        return data["token"]
    return None


def parse_duration(iso: str | None) -> int:
    """Convert ISO 8601 duration (PT30M, PT1H5M) to total minutes."""
    if not iso:
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes


def build_ingredient_map(recipe: dict) -> dict:
    """
    Merge ingredient metadata with per-serving amounts from yields.
    Returns {ingredient_id: {name, amount, unit, allergens, family, imageLink}}.
    Picks amounts for the smallest available serving size.
    """
    meta = {ing["id"]: ing for ing in recipe.get("ingredients", [])}

    yields = recipe.get("yields", [])
    if not yields:
        return {id_: {"name": v["name"], "amount": None, "unit": None,
                       "allergens": v.get("allergens", []),
                       "family": (v.get("family") or {}).get("name"),
                       "imageLink": v.get("imageLink")}
                for id_, v in meta.items()}

    # Use the smallest serving count as the canonical amounts
    yields_sorted = sorted(yields, key=lambda y: y["yields"])
    base_yield = yields_sorted[0]

    result = {}
    for item in base_yield["ingredients"]:
        id_ = item["id"]
        info = meta.get(id_, {})
        result[id_] = {
            "name": info.get("name", id_),
            "amount": item.get("amount"),
            "unit": item.get("unit"),
            "allergens": info.get("allergens", []),
            "family": (info.get("family") or {}).get("name"),
            "imageLink": info.get("imageLink"),
            "shipped": info.get("shipped", True),
        }
    return result


def parse_recipe(raw: dict) -> dict:
    """Transform a raw API recipe dict into a clean structured format."""
    ingredient_map = build_ingredient_map(raw)

    # Attach ingredient amounts to each step
    steps = []
    for step in raw.get("steps", []):
        step_ings = []
        for sid in step.get("ingredients", []):
            ing_id = sid if isinstance(sid, str) else sid.get("id", "")
            if ing_id in ingredient_map:
                step_ings.append(ingredient_map[ing_id])
        steps.append({
            "index": step["index"],
            "instructions": step.get("instructions", ""),
            "ingredients": step_ings,
            "timers": step.get("timers", []),
            "utensils": step.get("utensils", []),
        })

    nutrition = {n["name"]: {"amount": n["amount"], "unit": n["unit"]}
                 for n in raw.get("nutrition", [])}

    tags = [t["name"] for t in raw.get("tags", []) if t.get("name")]
    cuisines = [c["name"] for c in raw.get("cuisines", []) if c.get("name")]

    return {
        "id": raw["id"],
        "name": raw.get("name", ""),
        "slug": raw.get("slug", ""),
        "headline": raw.get("headline", ""),
        "description": raw.get("description", ""),
        "url": raw.get("websiteUrl") or raw.get("link", ""),
        "imageLink": raw.get("imageLink", ""),
        "cardLink": raw.get("cardLink"),
        "totalMinutes": parse_duration(raw.get("totalTime")),
        "prepMinutes": parse_duration(raw.get("prepTime")),
        "difficulty": raw.get("difficulty", 1),
        "rating": raw.get("averageRating", 0),
        "ratingsCount": raw.get("ratingsCount", 0),
        "servingSize": raw.get("servingSize", 2),
        "cuisines": cuisines,
        "tags": tags,
        "allergens": raw.get("allergens", []),
        "ingredients": list(ingredient_map.values()),
        "steps": steps,
        "nutrition": nutrition,
        "yields": raw.get("yields", []),
        "utensils": [u.get("name") for u in raw.get("utensils", []) if u.get("name")],
        "scrapedAt": datetime.utcnow().isoformat(),
    }


def search_recipes(
    token: str,
    limit: int = 20,
    offset: int = 0,
    query: str = "",
    cuisine: str = "",
    tags: str = "",
    min_rating: float = 0,
    country: str = "us",
    locale: str = "en-US",
) -> dict:
    """Call the HelloFresh search API and return the raw JSON response."""
    params = {
        "country": country,
        "locale": locale,
        "limit": limit,
        "skip": offset,
    }
    if query:
        params["q"] = query
    if cuisine:
        params["cuisine"] = cuisine
    if tags:
        params["tags"] = tags
    if min_rating:
        params["min-rating"] = min_rating

    from urllib.parse import urlencode
    url = f"{API_BASE}/recipes/search?{urlencode(params)}"
    raw = _curl_get(url, extra_headers=[
        f"Authorization: Bearer {token}",
        "Referer: https://www.hellofresh.com/recipes",
        "Origin: https://www.hellofresh.com",
    ])
    return json.loads(raw)


def scrape(
    pages: int = 1,
    per_page: int = 20,
    query: str = "",
    cuisine: str = "",
    tags: str = "",
    min_rating: float = 0,
    append: bool = True,
) -> list[dict]:
    """
    Scrape HelloFresh recipes and save to data/recipes.json.

    Args:
        pages:      Number of pages to fetch
        per_page:   Recipes per page (max ~100)
        query:      Free-text search
        cuisine:    Filter by cuisine slug (e.g. 'italian', 'asian', 'american')
        tags:       Filter by tag slug (e.g. 'calorie-smart', 'quick')
        min_rating: Minimum average rating (0-5)
        append:     Merge with existing data/recipes.json (True) or overwrite (False)
    """
    DATA_DIR.mkdir(exist_ok=True)
    token = get_bearer_token()

    existing: dict[str, dict] = {}
    if append and RECIPES_FILE.exists():
        for r in json.loads(RECIPES_FILE.read_text()):
            existing[r["id"]] = r

    new_count = 0
    for page in range(pages):
        offset = page * per_page
        print(f"Fetching page {page + 1}/{pages} (offset {offset})...")
        try:
            data = search_recipes(
                token,
                limit=per_page,
                offset=offset,
                query=query,
                cuisine=cuisine,
                tags=tags,
                min_rating=min_rating,
            )
        except (RuntimeError, json.JSONDecodeError):
            # Token may have expired — force refresh and retry once
            TOKEN_FILE.unlink(missing_ok=True)
            token = get_bearer_token()
            data = search_recipes(
                token, limit=per_page, offset=offset,
                query=query, cuisine=cuisine, tags=tags, min_rating=min_rating,
            )

        items = data.get("items", [])
        if not items:
            print("No more results.")
            break

        for raw in items:
            parsed = parse_recipe(raw)
            if parsed["id"] not in existing:
                existing[parsed["id"]] = parsed
                new_count += 1

        time.sleep(0.3)  # be polite

    recipes = list(existing.values())
    RECIPES_FILE.write_text(json.dumps(recipes, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(recipes)} total recipes ({new_count} new) → {RECIPES_FILE}")
    return recipes


def list_recipes(query: str = "", tag: str = "", cuisine: str = "") -> list[dict]:
    """Print a summary table of locally saved recipes."""
    if not RECIPES_FILE.exists():
        print("No recipes saved yet. Run: python hellofresh_scraper.py scrape")
        return []

    recipes = json.loads(RECIPES_FILE.read_text())

    if query:
        q = query.lower()
        recipes = [r for r in recipes if q in r["name"].lower() or q in r["description"].lower()]
    if tag:
        t = tag.lower()
        recipes = [r for r in recipes if any(t in tg.lower() for tg in r["tags"])]
    if cuisine:
        c = cuisine.lower()
        recipes = [r for r in recipes if any(c in cu.lower() for cu in r["cuisines"])]

    print(f"\n{'ID':<26} {'Name':<40} {'Time':>5} {'Rating':>6}  Tags")
    print("-" * 100)
    for r in recipes:
        tags_str = ", ".join(r["tags"][:3])
        time_str = f"{r['totalMinutes']}m" if r["totalMinutes"] else "—"
        print(f"{r['id']:<26} {r['name'][:38]:<40} {time_str:>5} {r['rating']:>6.1f}  {tags_str}")

    print(f"\n{len(recipes)} recipe(s) found.")
    return recipes


def show_recipe(recipe_id: str) -> None:
    """Print full details for a single recipe."""
    if not RECIPES_FILE.exists():
        print("No recipes saved yet.")
        return

    recipes = json.loads(RECIPES_FILE.read_text())
    recipe = next((r for r in recipes if r["id"] == recipe_id), None)
    if not recipe:
        print(f"Recipe {recipe_id!r} not found.")
        return

    print(f"\n{'='*60}")
    print(f"  {recipe['name']}")
    print(f"{'='*60}")
    print(f"  {recipe['headline']}")
    print(f"\n  Time: {recipe['totalMinutes']}m  |  Difficulty: {recipe['difficulty']}/3  "
          f"|  Rating: {recipe['rating']:.1f} ({recipe['ratingsCount']} ratings)")
    print(f"  Servings: {recipe['servingSize']}  |  Cuisines: {', '.join(recipe['cuisines'])}")
    print(f"  Tags: {', '.join(recipe['tags'])}")
    if recipe["url"]:
        print(f"  URL: {recipe['url']}")

    print(f"\n  INGREDIENTS ({len(recipe['ingredients'])} items)")
    print("  " + "-" * 40)
    for ing in recipe["ingredients"]:
        amt = f"{ing['amount']} {ing['unit']}" if ing["amount"] else "to taste"
        allergen_note = f"  ⚠ {', '.join(ing['allergens'])}" if ing["allergens"] else ""
        print(f"    • {amt:<18} {ing['name']}{allergen_note}")

    print(f"\n  STEPS")
    print("  " + "-" * 40)
    for step in recipe["steps"]:
        print(f"\n  Step {step['index']}: {step['instructions']}")
        if step["ingredients"]:
            names = ", ".join(i["name"] for i in step["ingredients"])
            print(f"    [Ingredients: {names}]")
        if step["timers"]:
            print(f"    [Timers: {step['timers']}]")

    print(f"\n  NUTRITION (per serving)")
    print("  " + "-" * 40)
    for name, val in recipe["nutrition"].items():
        print(f"    {name:<20} {val['amount']} {val['unit']}")

    print(f"\n  UTENSILS: {', '.join(recipe['utensils']) or 'not specified'}")


def main():
    parser = argparse.ArgumentParser(
        description="HelloFresh Recipe Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python hellofresh_scraper.py scrape --pages 5
  python hellofresh_scraper.py scrape --query "pasta" --min-rating 4
  python hellofresh_scraper.py scrape --cuisine italian --pages 3
  python hellofresh_scraper.py scrape --tags calorie-smart --pages 2
  python hellofresh_scraper.py list
  python hellofresh_scraper.py list --query chicken --tag quick
  python hellofresh_scraper.py show <recipe-id>
        """,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_scrape = sub.add_parser("scrape", help="Fetch recipes from HelloFresh API")
    p_scrape.add_argument("--pages", type=int, default=1, help="Pages to fetch (default 1)")
    p_scrape.add_argument("--per-page", type=int, default=20, help="Results per page (default 20)")
    p_scrape.add_argument("--query", default="", help="Search query")
    p_scrape.add_argument("--cuisine", default="", help="Cuisine filter (e.g. italian)")
    p_scrape.add_argument("--tags", default="", help="Tag filter (e.g. calorie-smart)")
    p_scrape.add_argument("--min-rating", type=float, default=0, help="Minimum rating (0-5)")
    p_scrape.add_argument("--overwrite", action="store_true", help="Replace existing data")

    p_list = sub.add_parser("list", help="List saved recipes")
    p_list.add_argument("--query", default="", help="Filter by name/description")
    p_list.add_argument("--tag", default="", help="Filter by tag")
    p_list.add_argument("--cuisine", default="", help="Filter by cuisine")

    p_show = sub.add_parser("show", help="Show full recipe details")
    p_show.add_argument("id", help="Recipe ID")

    args = parser.parse_args()

    if args.cmd == "scrape":
        scrape(
            pages=args.pages,
            per_page=args.per_page,
            query=args.query,
            cuisine=args.cuisine,
            tags=args.tags,
            min_rating=args.min_rating,
            append=not args.overwrite,
        )
    elif args.cmd == "list":
        list_recipes(query=args.query, tag=args.tag, cuisine=args.cuisine)
    elif args.cmd == "show":
        show_recipe(args.id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
