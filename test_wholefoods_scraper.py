import unittest

from wholefoods_scraper import (
    canonicalize_product_url,
    detect_store_gate,
    extract_product_urls_from_html,
    parse_product_html,
    scrape_product_url,
)


class WholeFoodsScraperTests(unittest.TestCase):
    def test_canonicalize_product_url(self) -> None:
        raw = (
            "https://www.wholefoodsmarket.com/365-Everyday-Value-Chocolate-Pretzels/"
            "dp/B07NNRQKCH?pd_rd_w=abc"
        )
        self.assertEqual(
            canonicalize_product_url(raw),
            "https://www.wholefoodsmarket.com/365-everyday-value-chocolate-pretzels/dp/B07NNRQKCH",
        )

    def test_extract_product_urls_from_html(self) -> None:
        html = """
        <a href="/foo-bar/dp/B012345678?x=1">One</a>
        <a href="https://www.wholefoodsmarket.com/baz/dp/B07NNRQKCH">Two</a>
        <a href="/not-a-product">Nope</a>
        """
        urls = extract_product_urls_from_html(html, "https://www.wholefoodsmarket.com/shop-aisles")
        self.assertEqual(
            urls,
            [
                "https://www.wholefoodsmarket.com/baz/dp/B07NNRQKCH",
                "https://www.wholefoodsmarket.com/foo-bar/dp/B012345678",
            ],
        )

    def test_parse_product_html_extracts_ingredients_and_nutrition(self) -> None:
        html = """
        <html><body>
          <h1>365 Pretzels</h1>
          <h4 class="bds--heading-4 mb-2 w-full text-squid-ink">Ingredients</h4>
          <p class="bds--body-1 w-full text-squid-ink">Milk Chocolate, Wheat Flour, Salt</p>
          <h4 class="bds--heading-4 w-full text-squid-ink">Nutrition Facts</h4>
          <div id="tabpanel_Nutrition Facts" data-testid="tabpanel-Nutrition Facts">
            <section class="w-pie--nutrition-facts">
              <div class="servings">2.0 servings per container</div>
              <div class="nutrition-row" data-testid="nutri-facts-serving">
                <div class="nutrition-column text-md text-bold">Serving size</div>
                <div class="nutrition-column text-md text-bold text-right">56 g</div>
              </div>
              <div class="nutrition-row">
                <div class="nutrition-column">Calories</div>
                <div class="nutrition-column text-right">230</div>
              </div>
              <div class="nutrition-row">
                <div class="nutrition-column">Added Sugars</div>
                <div class="nutrition-column text-right">9g</div>
              </div>
              <div class="nutrition-row">
                <div class="nutrition-column">Sodium</div>
                <div class="nutrition-column text-right">140mg</div>
              </div>
            </section>
          </div>
        </body></html>
        """
        payload = parse_product_html("https://www.wholefoodsmarket.com/foo/dp/B012345678", html, 200)
        extracted = payload["extracted"]
        self.assertEqual(payload["fetch_status"], "ok")
        self.assertEqual(extracted["name"], "365 Pretzels")
        self.assertEqual(extracted["ingredient_text"], "Milk Chocolate, Wheat Flour, Salt")
        self.assertEqual(extracted["nutrition"]["serving_size_g"], 56.0)
        self.assertEqual(extracted["nutrition"]["calories"], 230.0)
        self.assertEqual(extracted["nutrition"]["added_sugars_g"], 9.0)
        self.assertEqual(extracted["nutrition"]["sodium_mg"], 140.0)

    def test_detect_store_gate(self) -> None:
        html = "<title>Select Store | Whole Foods Market</title>"
        self.assertTrue(detect_store_gate(html))

    def test_scrape_product_url_prefers_playwright(self) -> None:
        html = """
        <html><body>
          <h1>Avocado Hass</h1>
          <h4>Ingredients</h4><p>Avocado</p>
        </body></html>
        """

        def browser_fetcher(_: str) -> dict[str, object]:
            return {
                "ok": True,
                "status_code": 200,
                "final_url": "https://www.wholefoodsmarket.com/avocado-hass/dp/B07FYCB4WX",
                "content_type": "text/html",
                "html": html,
                "error": None,
                "attempt": 1,
            }

        def http_fetcher(_: str) -> dict[str, object]:
            raise AssertionError("HTTP fallback should not run when browser succeeds")

        payload = scrape_product_url(
            "https://www.wholefoodsmarket.com/avocado-hass/dp/B07FYCB4WX",
            browser_fetcher=browser_fetcher,
            http_fetcher=http_fetcher,
        )
        self.assertEqual(payload["fetch_method"], "playwright")
        self.assertEqual(payload["fetch_status"], "ok")
        self.assertEqual(payload["extracted"]["name"], "Avocado Hass")

    def test_scrape_product_url_falls_back_to_http(self) -> None:
        html = """
        <html><body>
          <h1>Siete Fajita Seasoning</h1>
          <h4>Ingredients</h4><p>Spices, Sea Salt</p>
        </body></html>
        """

        def browser_fetcher(_: str) -> dict[str, object]:
            return {
                "ok": False,
                "status_code": None,
                "final_url": "https://www.wholefoodsmarket.com/siete/dp/B0CPF1N6PX",
                "content_type": "",
                "html": "",
                "error": "playwright_launch_failed",
                "attempt": 1,
            }

        def http_fetcher(_: str) -> dict[str, object]:
            return {
                "ok": True,
                "status_code": 200,
                "final_url": "https://www.wholefoodsmarket.com/siete/dp/B0CPF1N6PX",
                "content_type": "text/html",
                "html": html,
                "error": None,
                "attempt": 1,
            }

        payload = scrape_product_url(
            "https://www.wholefoodsmarket.com/siete/dp/B0CPF1N6PX",
            browser_fetcher=browser_fetcher,
            http_fetcher=http_fetcher,
        )
        self.assertEqual(payload["fetch_method"], "http")
        self.assertEqual(payload["fetch_status"], "ok")
        self.assertEqual(payload.get("browser_error"), "playwright_launch_failed")
        self.assertEqual(payload["extracted"]["ingredient_text"], "Spices, Sea Salt")


if __name__ == "__main__":
    unittest.main()
