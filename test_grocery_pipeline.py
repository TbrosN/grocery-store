import argparse
import json
import unittest

from grocery_db import (
    _cmd_parse_raw,
    connect,
    initialize_schema,
    insert_raw_product,
    seed_ingredient_flags,
)


class GroceryPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        initialize_schema(self.conn)
        seed_ingredient_flags(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_parse_raw_upserts_normalized_tables(self) -> None:
        payload = {
            "source_store": "whole_foods",
            "source_url": "https://www.wholefoodsmarket.com/foo/dp/B012345678",
            "fetched_at": "2026-03-14T00:00:00+00:00",
            "fetch_status": "ok",
            "http_status": 200,
            "error": None,
            "extracted": {
                "name": "Test Crackers",
                "brand": "365",
                "category": "Snacks",
                "organic_flag": 0,
                "ingredient_text": "Wheat Flour, High-Fructose Corn Syrup, Salt",
                "nutrition": {
                    "serving_size_g": 50,
                    "calories": 180,
                    "protein_g": 4,
                    "total_fat_g": 2,
                    "saturated_fat_g": 0,
                    "trans_fat_g": 0,
                    "carbs_g": 34,
                    "fiber_g": 2,
                    "total_sugars_g": 10,
                    "added_sugars_g": 8,
                    "sodium_mg": 500,
                },
            },
        }
        insert_raw_product(
            self.conn,
            source_store="whole_foods",
            source_url=payload["source_url"],
            raw_payload=payload,
            scraped_at=payload["fetched_at"],
        )
        args = argparse.Namespace(since_id=None, limit=100, dry_run=False)
        _cmd_parse_raw(args, self.conn)

        product_row = self.conn.execute("SELECT id, ingredient_count FROM products WHERE name = ?", ("Test Crackers",)).fetchone()
        self.assertIsNotNone(product_row)
        nutrition_row = self.conn.execute(
            "SELECT added_sugars_g, sodium_mg FROM nutrition WHERE product_id = ?",
            (product_row["id"],),
        ).fetchone()
        self.assertEqual(float(nutrition_row["added_sugars_g"]), 8.0)
        self.assertEqual(float(nutrition_row["sodium_mg"]), 500.0)

        health_row = self.conn.execute(
            """
            SELECT contains_added_sugar, high_added_sugar, high_sodium
            FROM product_health WHERE product_id = ?
            """,
            (product_row["id"],),
        ).fetchone()
        self.assertEqual(int(health_row["contains_added_sugar"]), 1)
        self.assertEqual(int(health_row["high_added_sugar"]), 1)
        self.assertEqual(int(health_row["high_sodium"]), 1)

    def test_parse_raw_dry_run_skips_writes(self) -> None:
        payload = {
            "source_store": "whole_foods",
            "source_url": "https://www.wholefoodsmarket.com/bar/dp/B000000000",
            "fetched_at": "2026-03-14T00:00:00+00:00",
            "fetch_status": "ok",
            "http_status": 200,
            "error": None,
            "extracted": {"name": "Dry Run Item", "nutrition": {}, "ingredient_text": "Salt"},
        }
        self.conn.execute(
            "INSERT INTO raw_products(source_store, source_url, scraped_at, raw_json) VALUES (?, ?, ?, ?)",
            ("whole_foods", payload["source_url"], payload["fetched_at"], json.dumps(payload)),
        )
        args = argparse.Namespace(since_id=None, limit=100, dry_run=True)
        _cmd_parse_raw(args, self.conn)
        product_row = self.conn.execute("SELECT id FROM products WHERE name = ?", ("Dry Run Item",)).fetchone()
        self.assertIsNone(product_row)


if __name__ == "__main__":
    unittest.main()
