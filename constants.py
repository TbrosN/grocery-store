DEFAULT_DB_PATH = "grocery.db"

initialize_schema_sql = """
    CREATE TABLE IF NOT EXISTS raw_products (
        id INTEGER PRIMARY KEY,
        source_store TEXT,
        source_url TEXT,
        scraped_at TEXT NOT NULL,
        raw_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        brand TEXT,
        category TEXT,
        organic_flag INTEGER NOT NULL DEFAULT 0 CHECK (organic_flag IN (0, 1)),
        ingredient_text TEXT,
        ingredient_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS nutrition (
        product_id INTEGER PRIMARY KEY,
        serving_size_g REAL,
        calories REAL,
        protein_g REAL,
        total_fat_g REAL,
        saturated_fat_g REAL,
        trans_fat_g REAL,
        carbs_g REAL,
        fiber_g REAL,
        total_sugars_g REAL,
        added_sugars_g REAL,
        sodium_mg REAL,
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ingredients (
        id INTEGER PRIMARY KEY,
        canonical_name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS product_ingredients (
        product_id INTEGER NOT NULL,
        ingredient_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        PRIMARY KEY (product_id, ingredient_id),
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
        FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS ingredient_flags (
        ingredient_id INTEGER PRIMARY KEY,
        is_added_sugar INTEGER NOT NULL DEFAULT 0 CHECK (is_added_sugar IN (0, 1)),
        is_artificial_sweetener INTEGER NOT NULL DEFAULT 0 CHECK (is_artificial_sweetener IN (0, 1)),
        is_hydrogenated_oil INTEGER NOT NULL DEFAULT 0 CHECK (is_hydrogenated_oil IN (0, 1)),
        is_seed_oil INTEGER NOT NULL DEFAULT 0 CHECK (is_seed_oil IN (0, 1)),
        is_preservative INTEGER NOT NULL DEFAULT 0 CHECK (is_preservative IN (0, 1)),
        is_junk_additive INTEGER NOT NULL DEFAULT 0 CHECK (is_junk_additive IN (0, 1)),
        FOREIGN KEY (ingredient_id) REFERENCES ingredients(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS product_health (
        product_id INTEGER PRIMARY KEY,
        contains_added_sugar INTEGER NOT NULL DEFAULT 0 CHECK (contains_added_sugar IN (0, 1)),
        contains_artificial_sweetener INTEGER NOT NULL DEFAULT 0 CHECK (contains_artificial_sweetener IN (0, 1)),
        contains_hydrogenated_oil INTEGER NOT NULL DEFAULT 0 CHECK (contains_hydrogenated_oil IN (0, 1)),
        contains_seed_oil INTEGER NOT NULL DEFAULT 0 CHECK (contains_seed_oil IN (0, 1)),
        contains_preservatives INTEGER NOT NULL DEFAULT 0 CHECK (contains_preservatives IN (0, 1)),
        high_added_sugar INTEGER NOT NULL DEFAULT 0 CHECK (high_added_sugar IN (0, 1)),
        high_sodium INTEGER NOT NULL DEFAULT 0 CHECK (high_sodium IN (0, 1)),
        high_trans_fat INTEGER NOT NULL DEFAULT 0 CHECK (high_trans_fat IN (0, 1)),
        ultra_processed INTEGER NOT NULL DEFAULT 0 CHECK (ultra_processed IN (0, 1)),
        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
    CREATE INDEX IF NOT EXISTS idx_nutrition_added_sugar ON nutrition(added_sugars_g);
    CREATE INDEX IF NOT EXISTS idx_nutrition_sodium ON nutrition(sodium_mg);
    CREATE INDEX IF NOT EXISTS idx_ingredient_name ON ingredients(canonical_name);
    CREATE INDEX IF NOT EXISTS idx_pi_product ON product_ingredients(product_id);
    CREATE INDEX IF NOT EXISTS idx_pi_ingredient ON product_ingredients(ingredient_id);
    CREATE INDEX IF NOT EXISTS idx_flags_sugar ON ingredient_flags(is_added_sugar);
    CREATE INDEX IF NOT EXISTS idx_health_filter ON product_health(
        contains_added_sugar,
        contains_artificial_sweetener,
        contains_hydrogenated_oil,
        high_added_sugar,
        high_sodium
    );
    """