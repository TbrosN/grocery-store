from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import Any

from constants import DEFAULT_DB_PATH, initialize_schema_sql

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


def _collect_ingredient_signal_flags(
    conn: sqlite3.Connection, product_id: int
) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            COALESCE(MAX(f.is_added_sugar), 0) AS contains_added_sugar,
            COALESCE(MAX(f.is_artificial_sweetener), 0) AS contains_artificial_sweetener,
            COALESCE(MAX(f.is_hydrogenated_oil), 0) AS contains_hydrogenated_oil,
            COALESCE(MAX(f.is_seed_oil), 0) AS contains_seed_oil,
            COALESCE(MAX(f.is_preservative), 0) AS contains_preservatives,
            COALESCE(MAX(f.is_junk_additive), 0) AS contains_junk_additive
        FROM product_ingredients pi
        LEFT JOIN ingredient_flags f ON pi.ingredient_id = f.ingredient_id
        WHERE pi.product_id = ?
        """,
        (product_id,),
    ).fetchone()
    assert row is not None
    return {key: int(row[key]) for key in row.keys()}


def _compute_health_flags(
    ingredient_signals: dict[str, int],
    ingredient_count: int,
    nutrition: dict[str, Any],
) -> dict[str, int]:
    added_sugars = float(nutrition.get("added_sugars_g") or 0.0)
    sodium_mg = float(nutrition.get("sodium_mg") or 0.0)
    trans_fat = float(nutrition.get("trans_fat_g") or 0.0)

    high_added_sugar = int(added_sugars > ADDED_SUGAR_MAX_G)
    high_sodium = int(sodium_mg > SODIUM_MAX_MG)
    high_trans_fat = int(trans_fat > TRANS_FAT_MAX_G)
    ultra_processed = int(
        ingredient_count > ULTRA_PROCESSED_INGREDIENT_COUNT
        or ingredient_signals["contains_junk_additive"] == 1
    )

    return {
        "contains_added_sugar": ingredient_signals["contains_added_sugar"],
        "contains_artificial_sweetener": ingredient_signals[
            "contains_artificial_sweetener"
        ],
        "contains_hydrogenated_oil": ingredient_signals["contains_hydrogenated_oil"],
        "contains_seed_oil": ingredient_signals["contains_seed_oil"],
        "contains_preservatives": ingredient_signals["contains_preservatives"],
        "high_added_sugar": high_added_sugar,
        "high_sodium": high_sodium,
        "high_trans_fat": high_trans_fat,
        "ultra_processed": ultra_processed,
    }


def ingest_product(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    source_store: str | None = None,
    source_url: str | None = None,
    ingredient_cache: dict[str, int] | None = None,
) -> int:
    cache = ingredient_cache if ingredient_cache is not None else {}

    ingredient_text = str(payload.get("ingredient_text") or "")
    parsed_ingredients = parse_ingredients(ingredient_text)
    nutrition = dict(payload.get("nutrition") or {})

    conn.execute(
        """
        INSERT INTO raw_products(source_store, source_url, scraped_at, raw_json)
        VALUES (?, ?, ?, ?)
        """,
        (source_store, source_url, now_iso(), json.dumps(payload)),
    )
    cur = conn.execute(
        """
        INSERT INTO products(name, brand, category, organic_flag, ingredient_text, ingredient_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(payload["name"]),
            payload.get("brand"),
            payload.get("category"),
            int(bool(payload.get("organic_flag", False))),
            ingredient_text,
            len(parsed_ingredients),
            now_iso(),
        ),
    )
    product_id = int(cur.lastrowid)

    conn.execute(
        """
        INSERT INTO nutrition(
            product_id, serving_size_g, calories, protein_g, total_fat_g, saturated_fat_g,
            trans_fat_g, carbs_g, fiber_g, total_sugars_g, added_sugars_g, sodium_mg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            nutrition.get("serving_size_g"),
            nutrition.get("calories"),
            nutrition.get("protein_g"),
            nutrition.get("total_fat_g"),
            nutrition.get("saturated_fat_g"),
            nutrition.get("trans_fat_g"),
            nutrition.get("carbs_g"),
            nutrition.get("fiber_g"),
            nutrition.get("total_sugars_g"),
            nutrition.get("added_sugars_g"),
            nutrition.get("sodium_mg"),
        ),
    )

    for index, canonical_name in enumerate(parsed_ingredients):
        ingredient_id = get_or_create_ingredient_id(conn, cache, canonical_name)
        conn.execute(
            """
            INSERT INTO product_ingredients(product_id, ingredient_id, position)
            VALUES (?, ?, ?)
            ON CONFLICT(product_id, ingredient_id)
            DO UPDATE SET position = excluded.position
            """,
            (product_id, ingredient_id, index),
        )

    ingredient_signals = _collect_ingredient_signal_flags(conn, product_id)
    flags = _compute_health_flags(ingredient_signals, len(parsed_ingredients), nutrition)
    conn.execute(
        """
        INSERT INTO product_health(
            product_id, contains_added_sugar, contains_artificial_sweetener,
            contains_hydrogenated_oil, contains_seed_oil, contains_preservatives,
            high_added_sugar, high_sodium, high_trans_fat, ultra_processed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            flags["contains_added_sugar"],
            flags["contains_artificial_sweetener"],
            flags["contains_hydrogenated_oil"],
            flags["contains_seed_oil"],
            flags["contains_preservatives"],
            flags["high_added_sugar"],
            flags["high_sodium"],
            flags["high_trans_fat"],
            flags["ultra_processed"],
        ),
    )
    return product_id


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


def _cmd_ingest_json(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    path = Path(args.file)
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        products = [data]
    elif isinstance(data, list):
        products = data
    else:
        raise ValueError("JSON file must be an object or array of objects")

    initialize_schema(conn)
    with conn:
        seed_ingredient_flags(conn)
    cache: dict[str, int] = {}
    for payload in products:
        ingest_product(
            conn,
            payload=payload,
            source_store=args.source_store,
            source_url=args.source_url,
            ingredient_cache=cache,
        )
    print(f"Ingested {len(products)} product(s)")


def _cmd_query_healthy(args: argparse.Namespace, conn: sqlite3.Connection) -> None:
    rows = query_healthy_products(conn, limit=args.limit)
    for row in rows:
        organic = "organic" if row["organic_flag"] else "non-organic"
        print(
            f"[{row['id']}] {row['name']} | {row['brand'] or '-'} | "
            f"{row['category'] or '-'} | {organic}"
        )


def insert_product(
    conn: sqlite3.Connection,
    name: str,
    brand: str | None = None,
    category: str | None = None,
    **kwargs: Any,
) -> int:
    """Insert a new product. Not implemented."""
    raise NotImplementedError("insert_product not implemented")


def delete_product(conn: sqlite3.Connection, product_id: int) -> None:
    """Delete a product by id. Not implemented."""
    raise NotImplementedError("delete_product not implemented")


def update_product(
    conn: sqlite3.Connection,
    product_id: int,
    **kwargs: Any,
) -> None:
    """Update an existing product. Not implemented."""
    raise NotImplementedError("update_product not implemented")


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

    ingest_parser = sub.add_parser("ingest-json", help="Ingest product(s) from a JSON file")
    ingest_parser.add_argument("--file", required=True, help="Path to JSON payload")
    ingest_parser.add_argument("--source-store", default=None, help="Store name")
    ingest_parser.add_argument("--source-url", default=None, help="Source URL")
    ingest_parser.set_defaults(func=_cmd_ingest_json)

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
            if parsed.command != "query-healthy":
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
