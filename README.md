# Grocery Store DB

A CLI tool that stores grocery products and their nutrition information in a SQLite database. Product data is sourced from the [USDA FoodData Central API](https://fdc.nal.usda.gov/). Users can import products by search query, perform CRUD operations, and query for healthy products.

## Database Schema

Below are the main tables and key columns in the Grocery Store DB SQLite database.

---

### **raw_products** (stores raw API response data)
| Column         | Data Type    | Constraints       |
|:-------------- |:------------|:------------------|
| id             | INTEGER     | PRIMARY KEY       |
| source_store   | TEXT        |                   |
| source_url     | TEXT        |                   |
| scraped_at     | TEXT        | NOT NULL          |
| raw_json       | TEXT        | NOT NULL          |

---

### **products**
| Column           | Data Type    | Constraints                                      |
|:---------------- |:------------|:-------------------------------------------------|
| id               | INTEGER     | PRIMARY KEY                                      |
| name             | TEXT        | NOT NULL                                         |
| brand            | TEXT        |                                                  |
| category         | TEXT        |                                                  |
| organic_flag     | INTEGER     | NOT NULL, DEFAULT 0, CHECK (0 or 1)              |
| ingredient_text  | TEXT        |                                                  |
| ingredient_count | INTEGER     | NOT NULL, DEFAULT 0                              |
| created_at       | TEXT        | NOT NULL                                         |

---

### **nutrition**
| Column           | Data Type | Constraints                                           |
|:---------------- |:---------|:------------------------------------------------------|
| product_id       | INTEGER  | PRIMARY KEY, FOREIGN KEY REFERENCES products(id)      |
| serving_size_g   | REAL     |                                                       |
| calories         | REAL     |                                                       |
| protein_g        | REAL     |                                                       |
| total_fat_g      | REAL     |                                                       |
| saturated_fat_g  | REAL     |                                                       |
| trans_fat_g      | REAL     |                                                       |
| carbs_g          | REAL     |                                                       |
| fiber_g          | REAL     |                                                       |
| total_sugars_g   | REAL     |                                                       |
| added_sugars_g   | REAL     |                                                       |
| sodium_mg        | REAL     |                                                       |

---

### **ingredients**
| Column         | Data Type | Constraints                  |
|:-------------- |:---------|:-----------------------------|
| id             | INTEGER  | PRIMARY KEY                  |
| canonical_name | TEXT     | NOT NULL, UNIQUE             |

---

### **product_ingredients**
| Column        | Data Type | Constraints                                                     |
|:------------- |:---------|:-----------------------------------------------------------------|
| product_id    | INTEGER  | NOT NULL, FOREIGN KEY REFERENCES products(id)                    |
| ingredient_id | INTEGER  | NOT NULL, FOREIGN KEY REFERENCES ingredients(id)                 |
| position      | INTEGER  | NOT NULL                                                        |
| *(primary key)* |        | PRIMARY KEY (product_id, ingredient_id)                         |

---

### **ingredient_flags**
| Column                    | Data Type | Constraints                                                |
|:--------------------------|:---------|:-----------------------------------------------------------|
| ingredient_id             | INTEGER  | PRIMARY KEY, FOREIGN KEY REFERENCES ingredients(id)         |
| is_added_sugar            | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| is_artificial_sweetener   | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| is_hydrogenated_oil       | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| is_seed_oil               | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| is_preservative           | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| is_junk_additive          | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |

---

### **product_health**
| Column                          | Data Type | Constraints                                                |
|:-------------------------------- |:---------|:-----------------------------------------------------------|
| product_id                      | INTEGER  | PRIMARY KEY, FOREIGN KEY REFERENCES products(id)           |
| contains_added_sugar            | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| contains_artificial_sweetener   | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| contains_hydrogenated_oil       | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| contains_seed_oil               | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| contains_preservatives          | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| high_added_sugar                | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| high_sodium                     | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| high_trans_fat                  | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |
| ultra_processed                 | INTEGER  | NOT NULL, DEFAULT 0, CHECK (0 or 1)                        |

---

### **Indexes**
- `idx_products_category` on products(category)
- `idx_nutrition_added_sugar` on nutrition(added_sugars_g)
- `idx_nutrition_sodium` on nutrition(sodium_mg)
- `idx_ingredient_name` on ingredients(canonical_name)
- `idx_pi_product` on product_ingredients(product_id)
- `idx_pi_ingredient` on product_ingredients(ingredient_id)
- `idx_flags_sugar` on ingredient_flags(is_added_sugar)
- `idx_health_filter` on product_health(contains_added_sugar, contains_artificial_sweetener, contains_hydrogenated_oil, high_added_sugar, high_sodium)
- `idx_raw_products_source_url_time` on raw_products(source_store, source_url, scraped_at)

## Setup
- Create and activate a virtual environment
- Install dependencies: `pip install -r requirements.txt`
- Add your USDA API key to `.env`:
  ```
  USDA_API_KEY=your_key_here
  ```
  Get a free key at [https://fdc.nal.usda.gov/api-key-signup.html](https://fdc.nal.usda.gov/api-key-signup.html)
- Initialize the DB schema: `python grocery_db.py init --seed-default-flags`
- Run the interactive CLI: `python grocery_db.py`
- Type `help` to see all available commands.

## USDA Import

Import products from the USDA FoodData Central API by search query. Can be run directly (not just via the interactive CLI).

```bash
python grocery_db.py usda-import --query "organic yogurt" --max-results 50
python grocery_db.py usda-import --query "almond butter" --query "peanut butter" --max-results 100
```

## CRUD Operations
All operations below are run inside the interactive CLI (`python grocery_db.py`).

### Insert a product
`insert --name "Product Name" --brand "Brand Name" --category "Category Name"`

### Delete a product
`delete --id <product_id>`

### Update a product
`update --id <product_id> --name "New Product Name" --brand "New Brand Name" --category "New Category Name"`

### Query healthy products
`query-healthy --limit <number of products to show>`
