# Grocery SQLite MVP

Local SQLite pipeline for grocery products with:

- Raw payload storage (`raw_products`)
- Normalized product + nutrition tables
- Parsed ingredients + canonical ingredient dictionary
- Derived boolean health flags for fast filtering

## Quick Start

Initialize schema and seed the avoid-list flags:

```bash
python3 grocery_db.py --db grocery.db init --seed-default-flags
```

Ingest sample products:

```bash
python3 grocery_db.py --db grocery.db ingest-json --file sample_products.json --source-store demo
```

Query healthy products:

```bash
python3 grocery_db.py --db grocery.db query-healthy --limit 20
```

## JSON Input Shape

Use either a single object or an array:

```json
{
  "name": "Product name",
  "brand": "Brand",
  "category": "Category",
  "organic_flag": true,
  "ingredient_text": "Ingredient A, Ingredient B, Ingredient C",
  "nutrition": {
    "serving_size_g": 40,
    "calories": 120,
    "protein_g": 4,
    "total_fat_g": 3,
    "saturated_fat_g": 0.5,
    "trans_fat_g": 0,
    "carbs_g": 21,
    "fiber_g": 2,
    "total_sugars_g": 3,
    "added_sugars_g": 0,
    "sodium_mg": 150
  }
}
```

## Notes

- PRAGMAs are enabled for SQLite performance: WAL + normal sync + memory temp store.
- Ingredient parsing is intentionally simple and conservative.
- Fast filtering uses precomputed `product_health` flags and indexes.
