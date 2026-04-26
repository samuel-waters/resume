#!/usr/bin/env python3
"""
Meal Planner
Generate weekly meal plans from your saved HelloFresh recipes.
Supports filtering by tag, cuisine, max time, and servings.
"""

import json
import random
import argparse
from pathlib import Path
from datetime import date, timedelta

RECIPES_FILE = Path("data/recipes.json")
PLANS_FILE = Path("data/meal_plans.json")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_recipes(
    tag: str = "",
    cuisine: str = "",
    max_minutes: int = 0,
    min_rating: float = 0,
) -> list[dict]:
    if not RECIPES_FILE.exists():
        raise FileNotFoundError(
            "No recipes found. Run: python hellofresh_scraper.py scrape --pages 5"
        )

    recipes = json.loads(RECIPES_FILE.read_text())

    # Filter out non-meal items (supplements, add-ons)
    recipes = [r for r in recipes if r.get("steps") and len(r["steps"]) > 0
               and r.get("ingredients") and len(r["ingredients"]) > 1]

    if tag:
        t = tag.lower()
        recipes = [r for r in recipes if any(t in tg.lower() for tg in r["tags"])]
    if cuisine:
        c = cuisine.lower()
        recipes = [r for r in recipes if any(c in cu.lower() for cu in r["cuisines"])]
    if max_minutes:
        recipes = [r for r in recipes if r["totalMinutes"] and r["totalMinutes"] <= max_minutes]
    if min_rating:
        recipes = [r for r in recipes if r["rating"] >= min_rating]

    return recipes


def generate_plan(
    meals_per_day: int = 1,
    days: int = 7,
    servings: int = 2,
    tag: str = "",
    cuisine: str = "",
    max_minutes: int = 0,
    min_rating: float = 0,
    seed: int | None = None,
    start_date: str = "",
) -> dict:
    """
    Generate a meal plan.

    Args:
        meals_per_day: Meals to plan per day (1=dinner only, 2=lunch+dinner, 3=all)
        days:          Number of days to plan (default 7)
        servings:      Number of people (affects shopping list amounts)
        tag:           Only use recipes with this tag
        cuisine:       Only use recipes from this cuisine
        max_minutes:   Exclude recipes taking longer than this
        min_rating:    Minimum recipe rating
        seed:          Random seed for reproducibility
        start_date:    Plan start date (YYYY-MM-DD, defaults to next Monday)
    """
    recipes = load_recipes(tag=tag, cuisine=cuisine, max_minutes=max_minutes, min_rating=min_rating)

    total_meals = days * meals_per_day
    if len(recipes) < total_meals:
        print(f"Warning: only {len(recipes)} recipes match your filters "
              f"(need {total_meals}). Recipes will repeat.")

    if seed is not None:
        random.seed(seed)

    # Shuffle and cycle through recipes
    pool = recipes.copy()
    random.shuffle(pool)
    selected = []
    while len(selected) < total_meals:
        batch = pool.copy()
        random.shuffle(batch)
        selected.extend(batch)
    selected = selected[:total_meals]

    meal_labels = {1: ["Dinner"], 2: ["Lunch", "Dinner"], 3: ["Breakfast", "Lunch", "Dinner"]}
    labels = meal_labels.get(meals_per_day, [f"Meal {i+1}" for i in range(meals_per_day)])

    if start_date:
        start = date.fromisoformat(start_date)
    else:
        today = date.today()
        days_until_monday = (7 - today.weekday()) % 7 or 7
        start = today + timedelta(days=days_until_monday)

    plan_days = []
    idx = 0
    for d in range(days):
        day_date = start + timedelta(days=d)
        day_name = DAYS[day_date.weekday()]
        meals = []
        for label in labels:
            recipe = selected[idx]
            idx += 1
            meals.append({
                "label": label,
                "recipe_id": recipe["id"],
                "name": recipe["name"],
                "totalMinutes": recipe["totalMinutes"],
                "rating": recipe["rating"],
                "tags": recipe["tags"][:3],
                "cuisines": recipe["cuisines"],
                "url": recipe.get("url", ""),
            })
        plan_days.append({
            "day": day_name,
            "date": day_date.isoformat(),
            "meals": meals,
        })

    plan = {
        "id": f"plan_{start.isoformat()}",
        "created": date.today().isoformat(),
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(),
        "days": days,
        "meals_per_day": meals_per_day,
        "servings": servings,
        "filters": {"tag": tag, "cuisine": cuisine, "max_minutes": max_minutes, "min_rating": min_rating},
        "plan": plan_days,
    }
    return plan


def save_plan(plan: dict) -> None:
    PLANS_FILE.parent.mkdir(exist_ok=True)
    plans = []
    if PLANS_FILE.exists():
        plans = json.loads(PLANS_FILE.read_text())
    # Replace existing plan with same id
    plans = [p for p in plans if p["id"] != plan["id"]]
    plans.insert(0, plan)
    PLANS_FILE.write_text(json.dumps(plans, indent=2, ensure_ascii=False))
    print(f"Plan saved → {PLANS_FILE}")


def print_plan(plan: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  MEAL PLAN: {plan['start_date']} → {plan['end_date']}")
    print(f"  Servings: {plan['servings']}  |  Generated: {plan['created']}")
    print(f"{'='*60}")
    for day in plan["plan"]:
        print(f"\n  {day['day'].upper()} ({day['date']})")
        print("  " + "-" * 40)
        for meal in day["meals"]:
            time_str = f"{meal['totalMinutes']}m" if meal["totalMinutes"] else "—"
            tags = ", ".join(meal["tags"])
            print(f"    [{meal['label']}] {meal['name']}")
            print(f"           Time: {time_str}  |  Rating: {meal['rating']:.1f}  |  {tags}")
            if meal["url"]:
                print(f"           {meal['url']}")


def list_plans() -> None:
    if not PLANS_FILE.exists():
        print("No meal plans saved yet.")
        return
    plans = json.loads(PLANS_FILE.read_text())
    print(f"\n{'ID':<30} {'Start':<12} {'End':<12} {'Days':>4} {'Meals/day':>9} {'Servings':>8}")
    print("-" * 80)
    for p in plans:
        print(f"{p['id']:<30} {p['start_date']:<12} {p['end_date']:<12} "
              f"{p['days']:>4} {p['meals_per_day']:>9} {p['servings']:>8}")


def main():
    parser = argparse.ArgumentParser(
        description="HelloFresh Meal Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python meal_planner.py generate
  python meal_planner.py generate --days 7 --meals-per-day 2 --servings 4
  python meal_planner.py generate --tag calorie-smart --max-minutes 40
  python meal_planner.py generate --cuisine italian --min-rating 4.0
  python meal_planner.py generate --start-date 2026-05-05 --seed 42
  python meal_planner.py list
        """,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_gen = sub.add_parser("generate", help="Generate a new meal plan")
    p_gen.add_argument("--days", type=int, default=7, help="Number of days (default 7)")
    p_gen.add_argument("--meals-per-day", type=int, default=1,
                       help="Meals per day: 1=dinner, 2=lunch+dinner, 3=all (default 1)")
    p_gen.add_argument("--servings", type=int, default=2, help="Number of people (default 2)")
    p_gen.add_argument("--tag", default="", help="Filter recipes by tag")
    p_gen.add_argument("--cuisine", default="", help="Filter recipes by cuisine")
    p_gen.add_argument("--max-minutes", type=int, default=0, help="Max cook time in minutes")
    p_gen.add_argument("--min-rating", type=float, default=0, help="Minimum recipe rating")
    p_gen.add_argument("--start-date", default="", help="Plan start date (YYYY-MM-DD)")
    p_gen.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p_gen.add_argument("--no-save", action="store_true", help="Print plan without saving")

    sub.add_parser("list", help="List saved meal plans")

    args = parser.parse_args()

    if args.cmd == "generate":
        plan = generate_plan(
            meals_per_day=args.meals_per_day,
            days=args.days,
            servings=args.servings,
            tag=args.tag,
            cuisine=args.cuisine,
            max_minutes=args.max_minutes,
            min_rating=args.min_rating,
            seed=args.seed,
            start_date=args.start_date,
        )
        print_plan(plan)
        if not args.no_save:
            save_plan(plan)
    elif args.cmd == "list":
        list_plans()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
