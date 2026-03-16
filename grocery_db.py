from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import sqlite3
import sys
from typing import Any

from constants import DEFAULT_DB_PATH, initialize_schema_sql
from usda_client import USDA_SOURCE, build_raw_payload, search_foods

# Strict thresholds from nutrition_rules.txt.
ADDED_SUGAR_MAX_G = 0.0
SODIUM_MAX_MG = 470.0
TRANS_FAT_MAX_G = 0.0
ULTRA_PROCESSED_INGREDIENT_COUNT = 12


DEFAULT_INGREDIENT_FLAG_RULES: dict[str, dict[str, int]] = {
    "partially hydrogenated oils": {
        "is_hydrogenated_oil": 1,
        "is_junk_additive": 1,
    },
    "high-fructose corn syrup": {"is_added_sugar": 1, "is_junk_additive": 1},
    "sodium nitrate": {"is_preservative": 1, "is_junk_additive": 1},
    "sodium nitrite": {"is_preservative": 1, "is_junk_additive": 1},
    "bha": {"is_preservative": 1, "is_junk_additive": 1},
    "butylated hydroxyanisole": {"is_preservative": 1, "is_junk_additive": 1},
    "bht": {"is_preservative": 1, "is_junk_additive": 1},
    "butylated hydroxytoluene": {"is_preservative": 1, "is_junk_additive": 1},
    "aspartame": {"is_artificial_sweetener": 1, "is_junk_additive": 1},
    "sucralose": {"is_artificial_sweetener": 1, "is_junk_additive": 1},
    "corn oil": {"is_seed_oil": 1},
    "soybean oil": {"is_seed_oil": 1},
    "cottonseed oil": {"is_seed_oil": 1},
    "red 40": {"is_junk_additive": 1},
    "yellow 5": {"is_junk_additive": 1},
    "yellow 6": {"is_junk_additive": 1},
    "carrageenan": {"is_junk_additive": 1},
    "xanthan gum": {"is_junk_additive": 1},
    "guar gum": {"is_junk_additive": 1},
    "cellulose gum": {"is_junk_additive": 1},
    "locust bean gum": {"is_junk_additive": 1},
    "gellan gum": {"is_junk_additive": 1},
    "gum arabic": {"is_junk_additive": 1},
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(initialize_schema_sql)


def normalize_ingredient_name(raw_name: str) -> str:
    cleaned = raw_name.lower()
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\b\d+(\.\d+)?\s*%\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9\s\-/]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_ingredients(ingredient_text: str | None) -> list[str]:
    if not ingredient_text:
        return []
    raw_parts = ingredient_text.split(",")
    parsed: list[str] = []
    for part in raw_parts:
        normalized = normalize_ingredient_name(part)
        if normalized:
            parsed.append(normalized)
    return parsed


def get_or_create_ingredient_id(
    conn: sqlite3.Connection, cache: dict[str, int], canonical_name: str
) -> int:
    cached = cache.get(canonical_name)
    if cached is not None:
        return cached

    row = conn.execute(
        "SELECT id FROM ingredients WHERE canonical_name = ?",
        (canonical_name,),
    ).fetchone()
    if row:
        ingredient_id = int(row["id"])
    else:
        cur = conn.execute(
            "INSERT INTO ingredients(canonical_name) VALUES (?)",
            (canonical_name,),
        )
        ingredient_id = int(cur.lastrowid)
    cache[canonical_name] = ingredient_id
    return ingredient_id


def seed_ingredient_flags(
    conn: sqlite3.Connection, rules: dict[str, dict[str, int]] | None = None
) -> None:
    active_rules = rules or DEFAULT_INGREDIENT_FLAG_RULES
    cache: dict[str, int] = {}
    for canonical_name, flags in active_rules.items():
        ingredient_id = get_or_create_ingredient_id(conn, cache, canonical_name)
        conn.execute(
            """
            INSERT INTO ingredient_flags(
                ingredient_id,
                is_added_sugar,
                is_artificial_sweetener,
                is_hydrogenated_oil,
                is_seed_oil,
                is_preservative,
                is_junk_additive
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ingredient_id) DO UPDATE SET
                is_added_sugar = excluded.is_added_sugar,
                is_artificial_sweetener = excluded.is_artificial_sweetener,
                is_hydrogenated_oil = excluded.is_hydrogenated_oil,
                is_seed_oil = excluded.is_seed_oil,
                is_preservative = excluded.is_preservative,
                is_junk_additive = excluded.is_junk_additive
            """,
            (
                ingredient_id,
                int(flags.get("is_added_sugar", 0)),
                int(flags.get("is_artificial_sweetener", 0)),
                int(flags.get("is_hydrogenated_oil", 0)),
                int(flags.get("is_seed_oil", 0)),
                int(flags.get("is_preservative", 0)),
                int(flags.get("is_junk_additive", 0)),
            ),
        )


def query_healthy_products(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.name, p.brand, p.category, p.organic_flag
        FROM products p
        JOIN product_health h ON p.id = h.product_id
        WHERE h.contains_added_sugar = 0
          AND h.contains_artificial_sweetener = 0
          AND h.contains_hydrogenated_oil = 0
          AND h.high_added_sugar = 0
          AND h.high_sodium = 0
          AND h.high_trans_fat = 0
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def insert_raw_product(
    conn: sqlite3.Connection,
    source_store: str,
    source_url: str,
    raw_payload: dict[str, Any],
    scraped_at: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO raw_products(source_store, source_url, scraped_at, raw_json)
        VALUES (?, ?, ?, ?)
        """,
        (source_store, source_url, scraped_at or now_iso(), json.dumps(raw_payload, separators=(",", ":"))),
    )
    return int(cur.lastrowid)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def _upsert_nutrition(conn: sqlite3.Connection, product_id: int, nutrition: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO nutrition(
            product_id, serving_size_g, calories, protein_g, total_fat_g,
            saturated_fat_g, trans_fat_g, carbs_g, fiber_g, total_sugars_g,
            added_sugars_g, sodium_mg
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            serving_size_g = excluded.serving_size_g,
            calories = excluded.calories,
            protein_g = excluded.protein_g,
            total_fat_g = excluded.total_fat_g,
            saturated_fat_g = excluded.saturated_fat_g,
            trans_fat_g = excluded.trans_fat_g,
            carbs_g = excluded.carbs_g,
            fiber_g = excluded.fiber_g,
            total_sugars_g = excluded.total_sugars_g,
            added_sugars_g = excluded.added_sugars_g,
            sodium_mg = excluded.sodium_mg
        """,
        (
            product_id,
            _safe_float(nutrition.get("serving_size_g")),
            _safe_float(nutrition.get("calories")),
            _safe_float(nutrition.get("protein_g")),
            _safe_float(nutrition.get("total_fat_g")),
            _safe_float(nutrition.get("saturated_fat_g")),
            _safe_float(nutrition.get("trans_fat_g")),
            _safe_float(nutrition.get("carbs_g")),
            _safe_float(nutrition.get("fiber_g")),
            _safe_float(nutrition.get("total_sugars_g")),
            _safe_float(nutrition.get("added_sugars_g")),
            _safe_float(nutrition.get("sodium_mg")),
        ),
    )


def _replace_product_ingredients(
    conn: sqlite3.Connection,
    product_id: int,
    ingredient_text: str | None,
    ingredient_cache: dict[str, int],
) -> int:
    parsed_ingredients = parse_ingredients(ingredient_text)
    conn.execute("DELETE FROM product_ingredients WHERE product_id = ?", (product_id,))
    for index, ingredient in enumerate(parsed_ingredients, start=1):
        ingredient_id = get_or_create_ingredient_id(conn, ingredient_cache, ingredient)
        conn.execute(
            """
            INSERT INTO product_ingredients(product_id, ingredient_id, position)
            VALUES (?, ?, ?)
            ON CONFLICT(product_id, ingredient_id) DO UPDATE SET
                position = excluded.position
            """,
            (product_id, ingredient_id, index),
        )
    return len(parsed_ingredients)


def _recompute_product_health(conn: sqlite3.Connection, product_id: int, ingredient_count: int) -> None:
    ingredient_flags = conn.execute(
        """
        SELECT
            COALESCE(MAX(f.is_added_sugar), 0) AS contains_added_sugar,
            COALESCE(MAX(f.is_artificial_sweetener), 0) AS contains_artificial_sweetener,
            COALESCE(MAX(f.is_hydrogenated_oil), 0) AS contains_hydrogenated_oil,
            COALESCE(MAX(f.is_seed_oil), 0) AS contains_seed_oil,
            COALESCE(MAX(f.is_preservative), 0) AS contains_preservatives,
            COALESCE(MAX(f.is_junk_additive), 0) AS contains_junk_additives
        FROM product_ingredients pi
        LEFT JOIN ingredient_flags f ON pi.ingredient_id = f.ingredient_id
        WHERE pi.product_id = ?
        """,
        (product_id,),
    ).fetchone()

    nutrition_row = conn.execute(
        "SELECT added_sugars_g, sodium_mg, trans_fat_g FROM nutrition WHERE product_id = ?",
        (product_id,),
    ).fetchone()

    added_sugars_g = float(nutrition_row["added_sugars_g"] or 0.0) if nutrition_row else 0.0
    sodium_mg = float(nutrition_row["sodium_mg"] or 0.0) if nutrition_row else 0.0
    trans_fat_g = float(nutrition_row["trans_fat_g"] or 0.0) if nutrition_row else 0.0
    contains_junk_additives = int(ingredient_flags["contains_junk_additives"] or 0) if ingredient_flags else 0

    conn.execute(
        """
        INSERT INTO product_health(
            product_id,
            contains_added_sugar,
            contains_artificial_sweetener,
            contains_hydrogenated_oil,
            contains_seed_oil,
            contains_preservatives,
            high_added_sugar,
            high_sodium,
            high_trans_fat,
            ultra_processed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            contains_added_sugar = excluded.contains_added_sugar,
            contains_artificial_sweetener = excluded.contains_artificial_sweetener,
            contains_hydrogenated_oil = excluded.contains_hydrogenated_oil,
            contains_seed_oil = excluded.contains_seed_oil,
            contains_preservatives = excluded.contains_preservatives,
            high_added_sugar = excluded.high_added_sugar,
            high_sodium = excluded.high_sodium,
            high_trans_fat = excluded.high_trans_fat,
            ultra_processed = excluded.ultra_processed
        """,
        (
            product_id,
            int(ingredient_flags["contains_added_sugar"] or 0) if ingredient_flags else 0,
            int(ingredient_flags["contains_artificial_sweetener"] or 0) if ingredient_flags else 0,
            int(ingredient_flags["contains_hydrogenated_oil"] or 0) if ingredient_flags else 0,
            int(ingredient_flags["contains_seed_oil"] or 0) if ingredient_flags else 0,
            int(ingredient_flags["contains_preservatives"] or 0) if ingredient_flags else 0,
            1 if added_sugars_g > ADDED_SUGAR_MAX_G else 0,
            1 if sodium_mg > SODIUM_MAX_MG else 0,
            1 if trans_fat_g > TRANS_FAT_MAX_G else 0,
            1 if ingredient_count > ULTRA_PROCESSED_INGREDIENT_COUNT or contains_junk_additives else 0,
        ),
    )


def upsert_product_from_raw_payload(
    conn: sqlite3.Connection,
    raw_payload: dict[str, Any],
    ingredient_cache: dict[str, int],
) -> int | None:
    extracted = raw_payload.get("extracted") or {}
    name = extracted.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    brand = extracted.get("brand")
    category = extracted.get("category")
    ingredient_text = extracted.get("ingredient_text")
    organic_flag = int(bool(extracted.get("organic_flag")))
    created_at = now_iso()

    existing = conn.execute(
        """
        SELECT id FROM products
        WHERE name = ?
          AND COALESCE(brand, '') = COALESCE(?, '')
          AND COALESCE(category, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (name.strip(), brand, category),
    ).fetchone()

    if existing:
        product_id = int(existing["id"])
        conn.execute(
            """
            UPDATE products
            SET organic_flag = ?, ingredient_text = ?, ingredient_count = ?
            WHERE id = ?
            """,
            (organic_flag, ingredient_text, 0, product_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO products(
                name, brand, category, organic_flag, ingredient_text, ingredient_count, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), brand, category, organic_flag, ingredient_text, 0, created_at),
        )
        product_id = int(cur.lastrowid)

    nutrition = extracted.get("nutrition") if isinstance(extracted.get("nutrition"), dict) else {}
    _upsert_nutrition(conn, product_id, nutrition)
    ingredient_count = _replace_product_ingredients(conn, product_id, ingredient_text, ingredient_cache)
    conn.execute(
        "UPDATE products SET ingredient_count = ?, ingredient_text = ? WHERE id = ?",
        (ingredient_count, ingredient_text, product_id),
    )
    _recompute_product_health(conn, product_id, ingredient_count)
    return product_id


def _cmd_init(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    initialize_schema(conn)
    if args.seed_default_flags:
        with conn:
            seed_ingredient_flags(conn)
    print(f"Initialized database: {args.db}")


def _cmd_seed_flags(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    initialize_schema(conn)
    with conn:
        seed_ingredient_flags(conn)
    print(f"Seeded ingredient flags: {args.db}")


def _cmd_query_products(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM products ORDER BY id ASC LIMIT ?",
        (args.limit,),
    ).fetchall()
    for row in rows:
        organic = "organic" if row["organic_flag"] else "non-organic"
        print(
            f"[{row['id']}] {row['name']} | {row['brand'] or '-'} | "
            f"{row['category'] or '-'} | {organic} | ingredients={row['ingredient_count']}"
        )


def _cmd_query_healthy(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    rows = query_healthy_products(conn, limit=args.limit)
    for row in rows:
        organic = "organic" if row["organic_flag"] else "non-organic"
        print(
            f"[{row['id']}] {row['name']} | {row['brand'] or '-'} | "
            f"{row['category'] or '-'} | {organic}"
        )


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no-op if missing)."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


def _cmd_usda_import(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    _load_dotenv()

    queries = list(args.query or [])
    if not queries:
        raise ValueError("usda-import requires at least one --query")

    ingredient_cache: dict[str, int] = {}
    total_inserted = 0
    total_skipped = 0

    for query_str in queries:
        remaining = args.max_results
        page = 1
        while remaining > 0:
            page_size = min(remaining, 200)
            try:
                result = search_foods(query_str, page_size=page_size, page_number=page)
            except RuntimeError as exc:
                print(f"[ERROR] {exc}")
                break

            foods = result.get("foods") or []
            if not foods:
                break

            for food in foods:
                raw_payload = build_raw_payload(food)
                source_url = raw_payload["source_url"]
                insert_raw_product(
                    conn,
                    source_store=USDA_SOURCE,
                    source_url=source_url,
                    raw_payload=raw_payload,
                )
                if raw_payload["fetch_status"] in ("ok", "partial"):
                    product_id = upsert_product_from_raw_payload(conn, raw_payload, ingredient_cache)
                    if product_id is not None:
                        total_inserted += 1
                    else:
                        total_skipped += 1
                else:
                    total_skipped += 1

            remaining -= len(foods)
            total_pages = result.get("totalPages") or 1
            if page >= total_pages:
                break
            page += 1

    print(f"usda-import: inserted={total_inserted}, skipped={total_skipped}")


def insert_product(
    conn: sqlite3.Connection,
    name: str,
    brand: str | None = None,
    category: str | None = None,
    **kwargs: Any,
) -> int:
    """Insert a new product. Not implemented."""
    cur = conn.execute(
        """
        INSERT INTO products(name, brand, category, organic_flag, ingredient_text, ingredient_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, brand, category, kwargs.get("organic_flag", 0), kwargs.get("ingredient_text", ""), kwargs.get("ingredient_count", 0), now_iso()),
    )
    return int(cur.lastrowid)


def delete_product(conn: sqlite3.Connection, product_id: int) -> None:
    """Delete a product by id. Not implemented."""
    return conn.execute(
        """
        DELETE FROM products WHERE id = ?
        """,
        (product_id,),
    )


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    name: str,
    brand: str | None = None,
    category: str | None = None,
    **kwargs: Any,
) -> None:
    """Update an existing product. Not implemented."""
    return conn.execute(
        """
        UPDATE products SET name = ?, brand = ?, category = ?, organic_flag = ?, ingredient_text = ?, ingredient_count = ?
        WHERE id = ?
        """,
        (name, brand, category, kwargs.get("organic_flag", 0), kwargs.get("ingredient_text", ""), kwargs.get("ingredient_count", 0), product_id),
    )


def _cmd_insert(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    product_id = insert_product(
        conn,
        name=args.name,
        brand=args.brand or None,
        category=args.category or None,
    )
    print(f"Inserted product id {product_id}")


def _cmd_delete(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    delete_product(conn, product_id=args.id)
    print(f"Deleted product id {args.id}")


def _cmd_update(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    update_product(
        conn,
        product_id=args.id,
        name=getattr(args, "name", None),
        brand=getattr(args, "brand", None),
        category=getattr(args, "category", None),
    )
    print(f"Updated product id {args.id}")


def _cmd_quit(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    del args
    conn.close()
    sys.exit(0)


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grocery database CLI — query, insert, update, and delete products."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # Main operations
    query_parser = sub.add_parser("query-healthy", help="List healthy products")
    query_parser.add_argument("--limit", type=int, default=25, help="Max rows to show")
    query_parser.set_defaults(func=_cmd_query_healthy)

    query_products_parser = sub.add_parser(
        "query-all",
        help="List all products (SELECT * FROM products ORDER BY id ASC)",
    )
    query_products_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max rows to show (default: 10)",
    )
    query_products_parser.set_defaults(func=_cmd_query_products)

    insert_parser = sub.add_parser("insert", help="Insert a new product")
    insert_parser.add_argument("--name", required=True, help="Product name")
    insert_parser.add_argument("--brand", default=None, help="Brand")
    insert_parser.add_argument("--category", default=None, help="Category")
    insert_parser.set_defaults(func=_cmd_insert)

    delete_parser = sub.add_parser("delete", help="Delete a product by id")
    delete_parser.add_argument("--id", type=int, required=True, dest="id", help="Product id")
    delete_parser.set_defaults(func=_cmd_delete)

    update_parser = sub.add_parser("update", help="Update an existing product")
    update_parser.add_argument("--id", type=int, required=True, dest="id", help="Product id")
    update_parser.add_argument("--name", default=None, help="New product name")
    update_parser.add_argument("--brand", default=None, help="New brand")
    update_parser.add_argument("--category", default=None, help="New category")
    update_parser.set_defaults(func=_cmd_update)

    usda_import_parser = sub.add_parser(
        "usda-import",
        help="Search USDA FoodData Central and import results into the database",
    )
    usda_import_parser.add_argument(
        "--query",
        action="append",
        default=[],
        required=True,
        help="Search term to query (can repeat for multiple queries)",
    )
    usda_import_parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Max foods to import per query (default: 10)",
    )
    usda_import_parser.set_defaults(func=_cmd_usda_import)

    # Setup / maintenance
    init_parser = sub.add_parser("init", help="Create database schema (optional: seed flags)")
    init_parser.add_argument(
        "--seed-default-flags",
        action="store_true",
        help="Seed ingredient_flags with built-in avoid list",
    )
    init_parser.set_defaults(func=_cmd_init)

    seed_flags_parser = sub.add_parser("seed-flags", help="Seed ingredient flags")
    seed_flags_parser.set_defaults(func=_cmd_seed_flags)

    quit_parser = sub.add_parser("quit", help="Exit and choose to commit or discard changes")
    quit_parser.set_defaults(func=_cmd_quit)
    sub.add_parser("exit", help="Alias for quit").set_defaults(func=_cmd_quit)

    return parser


def _run_interactive_loop(parser: argparse.ArgumentParser, db_path: str) -> None:
    conn = connect(db_path)
    try:
        while True:
            try:
                line = input("grocery> ").strip()
            except EOFError:
                line = "quit"
            if not line:
                continue
            tokens = shlex.split(line)
            try:
                parsed = parser.parse_args(["--db", db_path] + tokens)
            except SystemExit:
                continue
            if parsed.command is None:
                parser.print_help()
                continue
            if parsed.command in ("quit", "exit"):
                _cmd_quit(parsed, conn)
                return
            parsed.func(parsed, conn)
            # Commit after each modifying command (read-only commands are no-op)
            if parsed.command not in ("query-healthy", "query-products"):
                conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = build_cli()
    args = parser.parse_args()
    if args.command is None:
        _run_interactive_loop(parser, args.db)
        return
    conn = connect(args.db)
    try:
        args.func(args, conn)
        if args.command not in ("quit", "exit"):
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
