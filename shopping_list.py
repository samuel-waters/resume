#!/usr/bin/env python3
"""
Shopping List Generator
Aggregates ingredients across a meal plan into a consolidated shopping list.
Groups by ingredient family, deduplicates, and scales to desired servings.
"""

import json
import argparse
from pathlib import Path
from collections import defaultdict

RECIPES_FILE = Path("data/recipes.json")
PLANS_FILE = Path("data/meal_plans.json")
LISTS_FILE = Path("data/shopping_lists.json")

# Units that can be numerically added together
ADDABLE_UNITS = {
    "oz", "ounce", "ounces",
    "lb", "lbs", "pound", "pounds",
    "cup", "cups",
    "tbsp", "tablespoon", "tablespoons",
    "tsp", "teaspoon", "teaspoons", "teaspoon (tsp)",
    "ml", "l", "liter", "liters",
    "g", "gram", "grams",
    "kg",
    "unit", "units",
    "slice", "slices",
    "clove", "cloves",
    "bunch", "bunches",
    "sprig", "sprigs",
    "can", "cans",
    "package", "packages", "pkg",
}

UNIT_ALIASES = {
    "ounce": "oz", "ounces": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "tablespoon": "tbsp", "tablespoons": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "teaspoon (tsp)": "tsp",
    "gram": "g", "grams": "g",
    "liter": "l", "liters": "l",
    "cup": "cups",
    "slice": "slices",
    "clove": "cloves",
    "bunch": "bunches",
    "sprig": "sprigs",
    "can": "cans",
    "package": "packages", "pkg": "packages",
    "unit": "units",
}


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return "units"
    u = unit.strip().lower()
    return UNIT_ALIASES.get(u, u)


def load_recipes_by_id() -> dict[str, dict]:
    if not RECIPES_FILE.exists():
        raise FileNotFoundError("Run: python hellofresh_scraper.py scrape --pages 5")
    recipes = json.loads(RECIPES_FILE.read_text())
    return {r["id"]: r for r in recipes}


def load_plan(plan_id: str | None = None) -> dict:
    if not PLANS_FILE.exists():
        raise FileNotFoundError("No meal plans. Run: python meal_planner.py generate")
    plans = json.loads(PLANS_FILE.read_text())
    if not plans:
        raise ValueError("No meal plans saved.")
    if plan_id:
        plan = next((p for p in plans if p["id"] == plan_id), None)
        if not plan:
            raise ValueError(f"Plan {plan_id!r} not found.")
        return plan
    return plans[0]  # most recent


def scale_amount(amount: float | None, base_servings: int, target_servings: int) -> float | None:
    if amount is None:
        return None
    if base_servings <= 0:
        return amount
    return round(amount * target_servings / base_servings, 2)


def build_shopping_list(plan: dict, recipes_by_id: dict) -> dict:
    """
    Aggregate all ingredients across all meals in the plan.
    Scales amounts to plan's target servings.
    Returns a structured shopping list grouped by ingredient family.
    """
    # {(name_lower, unit): {name, unit, family, amounts: [float], allergens, imageLink}}
    aggregated: dict[tuple, dict] = {}

    target_servings = plan.get("servings", 2)

    for day in plan["plan"]:
        for meal in day["meals"]:
            recipe = recipes_by_id.get(meal["recipe_id"])
            if not recipe:
                continue

            # The parsed recipe stores amounts directly on each ingredient
            # (already scaled to the smallest yield size from build_ingredient_map).
            # Find what serving count those amounts correspond to.
            yields = recipe.get("yields", [])
            base_servings = 2
            if yields:
                base_servings = sorted(yields, key=lambda yy: yy["yields"])[0]["yields"]
            elif recipe.get("servingSize"):
                base_servings = recipe["servingSize"]

            for ing in recipe.get("ingredients", []):
                name = ing.get("name", "Unknown")
                family = ing.get("family") or "Other"

                raw_amount = ing.get("amount")
                unit = normalize_unit(ing.get("unit"))
                scaled = scale_amount(raw_amount, base_servings, target_servings)

                key = (name.lower(), unit)
                if key not in aggregated:
                    aggregated[key] = {
                        "name": name,
                        "unit": unit,
                        "family": family,
                        "total_amount": 0.0 if scaled is not None else None,
                        "count": 0,
                        "allergens": ing.get("allergens", []),
                        "imageLink": ing.get("imageLink"),
                        "recipes": [],
                    }

                item = aggregated[key]
                item["recipes"].append(meal["name"])
                item["count"] += 1

                if scaled is not None and item["total_amount"] is not None:
                    item["total_amount"] = round(item["total_amount"] + scaled, 2)
                elif scaled is not None:
                    item["total_amount"] = scaled

    # Group by family
    by_family: dict[str, list] = defaultdict(list)
    for item in aggregated.values():
        by_family[item["family"]].append(item)

    # Sort each group alphabetically
    for family in by_family:
        by_family[family].sort(key=lambda x: x["name"].lower())

    return {
        "plan_id": plan["id"],
        "servings": target_servings,
        "date_range": f"{plan['start_date']} → {plan['end_date']}",
        "total_items": len(aggregated),
        "groups": dict(sorted(by_family.items())),
    }


def print_shopping_list(sl: dict, show_recipes: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"  SHOPPING LIST")
    print(f"  Plan: {sl['date_range']}  |  Servings: {sl['servings']}")
    print(f"  {sl['total_items']} unique items")
    print(f"{'='*60}")

    for family, items in sl["groups"].items():
        print(f"\n  {family.upper()}")
        print("  " + "-" * 40)
        for item in items:
            if item["total_amount"] is not None:
                qty = f"{item['total_amount']} {item['unit']}"
            else:
                qty = "as needed"
            allergen = f"  ⚠ {', '.join(item['allergens'])}" if item["allergens"] else ""
            dupe = f" (×{item['count']})" if item["count"] > 1 else ""
            print(f"    [ ] {qty:<20} {item['name']}{dupe}{allergen}")
            if show_recipes:
                unique_recipes = list(dict.fromkeys(item["recipes"]))
                print(f"        → {', '.join(unique_recipes)}")


def export_shopping_list(sl: dict, fmt: str = "txt") -> str:
    """Export shopping list as plain text or markdown."""
    lines = []
    if fmt == "md":
        lines.append(f"# Shopping List")
        lines.append(f"**Plan:** {sl['date_range']}  |  **Servings:** {sl['servings']}")
        lines.append(f"**{sl['total_items']} unique items**\n")
        for family, items in sl["groups"].items():
            lines.append(f"## {family}")
            for item in items:
                if item["total_amount"] is not None:
                    qty = f"{item['total_amount']} {item['unit']}"
                else:
                    qty = "as needed"
                allergen = f" ⚠ {', '.join(item['allergens'])}" if item["allergens"] else ""
                lines.append(f"- [ ] **{qty}** {item['name']}{allergen}")
            lines.append("")
    else:
        lines.append(f"SHOPPING LIST — {sl['date_range']} (Servings: {sl['servings']})")
        lines.append("=" * 60)
        for family, items in sl["groups"].items():
            lines.append(f"\n{family.upper()}")
            lines.append("-" * 40)
            for item in items:
                if item["total_amount"] is not None:
                    qty = f"{item['total_amount']} {item['unit']}"
                else:
                    qty = "as needed"
                allergen = f"  [ALLERGEN: {', '.join(item['allergens'])}]" if item["allergens"] else ""
                lines.append(f"  [ ] {qty:<22} {item['name']}{allergen}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="HelloFresh Shopping List Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python shopping_list.py generate
  python shopping_list.py generate --plan plan_2026-05-05
  python shopping_list.py generate --show-recipes
  python shopping_list.py export --format md > shopping_list.md
  python shopping_list.py export --format txt > shopping_list.txt
        """,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_gen = sub.add_parser("generate", help="Generate shopping list from latest (or named) plan")
    p_gen.add_argument("--plan", default="", help="Plan ID (default: most recent)")
    p_gen.add_argument("--show-recipes", action="store_true",
                       help="Show which recipes use each ingredient")

    p_exp = sub.add_parser("export", help="Export shopping list as text or markdown")
    p_exp.add_argument("--plan", default="", help="Plan ID (default: most recent)")
    p_exp.add_argument("--format", choices=["txt", "md"], default="txt", help="Output format")

    args = parser.parse_args()

    if args.cmd in ("generate", "export"):
        recipes_by_id = load_recipes_by_id()
        plan = load_plan(args.plan or None)
        sl = build_shopping_list(plan, recipes_by_id)

        LISTS_FILE.parent.mkdir(exist_ok=True)
        lists = []
        if LISTS_FILE.exists():
            lists = json.loads(LISTS_FILE.read_text())
        lists = [l for l in lists if l["plan_id"] != sl["plan_id"]]
        lists.insert(0, sl)
        LISTS_FILE.write_text(json.dumps(lists, indent=2, ensure_ascii=False))

        if args.cmd == "generate":
            print_shopping_list(sl, show_recipes=args.show_recipes)
            print(f"\n  Saved → {LISTS_FILE}")
        else:
            print(export_shopping_list(sl, fmt=args.format))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
