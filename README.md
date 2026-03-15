# Grocery Store DB

## Whole Foods scraper workflow

- Install browser scraper dependency once:
  - `pip install playwright`
  - `python -m playwright install chromium`
- Initialize DB schema: `python grocery_db.py init --seed-default-flags`
- Discover product URLs from aisle pages:
  - `python grocery_db.py crawl-wholefoods --seed-url https://www.wholefoodsmarket.com/shop-aisles --max-pages 10`
- Scrape product pages into `raw_products`:
  - `python grocery_db.py scrape-wholefoods --seed-url https://www.wholefoodsmarket.com/shop-aisles --max-pages 10 --max-products 50`
  - PDP extraction now uses Playwright-rendered HTML first, with HTTP fallback if browser fetch fails.
  - or scrape specific URLs: `python grocery_db.py scrape-wholefoods --product-url <url>`
- Parse raw rows into normalized tables:
  - `python grocery_db.py parse-raw --limit 250`
  - dry run only: `python grocery_db.py parse-raw --limit 250 --dry-run`