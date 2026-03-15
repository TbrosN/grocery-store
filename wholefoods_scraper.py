from __future__ import annotations

import datetime as dt
import json
import random
import re
import time
import typing as t
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

WHOLEFOODS_HOST = "www.wholefoodsmarket.com"
WHOLEFOODS_SOURCE = "whole_foods"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 1.0

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

PRODUCT_PATH_RE = re.compile(r"^/(?P<slug>[a-z0-9\-]+)/dp/(?P<asin>[A-Z0-9]{10})/?$", re.I)
HREF_PRODUCT_RE = re.compile(r'href=["\']([^"\']+/dp/[A-Z0-9]{10}[^"\']*)["\']', re.I)
SCRIPT_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
HtmlFetchResult = dict[str, Any]
HtmlFetcher = t.Callable[[str], HtmlFetchResult]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    collapsed = re.sub(r"\s+", " ", without_tags)
    return collapsed.strip()


def canonicalize_product_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url.strip())
    if not parsed.scheme:
        parsed = urllib.parse.urlparse(f"https://{WHOLEFOODS_HOST}{url.strip()}")
    if parsed.netloc and WHOLEFOODS_HOST not in parsed.netloc:
        return None
    match = PRODUCT_PATH_RE.match(parsed.path)
    if not match:
        return None
    slug = match.group("slug").lower()
    asin = match.group("asin").upper()
    return f"https://{WHOLEFOODS_HOST}/{slug}/dp/{asin}"


def canonicalize_link(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def extract_product_urls_from_html(html: str, base_url: str) -> list[str]:
    urls: set[str] = set()
    for match in HREF_PRODUCT_RE.finditer(html):
        raw_href = match.group(1)
        full_url = canonicalize_link(base_url, raw_href)
        canonical = canonicalize_product_url(full_url)
        if canonical:
            urls.add(canonical)
    return sorted(urls)


def extract_next_aisle_urls_from_html(html: str, base_url: str) -> list[str]:
    # Keep crawl bounded to likely discovery pages only.
    href_re = re.compile(r'href=["\']([^"\']+)["\']', re.I)
    urls: set[str] = set()
    for match in href_re.finditer(html):
        href = match.group(1)
        full_url = canonicalize_link(base_url, href)
        parsed = urllib.parse.urlparse(full_url)
        if WHOLEFOODS_HOST not in parsed.netloc:
            continue
        if "/shop-aisles" in parsed.path or "/fmc/deals" in parsed.path:
            urls.add(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, "")))
    return sorted(urls)


def fetch_html(
    url: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> dict[str, Any]:
    last_error: str | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                body = response.read().decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status_code": response.status,
                    "final_url": response.geturl(),
                    "content_type": content_type,
                    "html": body,
                    "error": None,
                    "attempt": attempt,
                }
        except urllib.error.HTTPError as exc:
            last_error = f"http_error:{exc.code}"
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                return {
                    "ok": False,
                    "status_code": exc.code,
                    "final_url": url,
                    "content_type": "",
                    "html": "",
                    "error": last_error,
                    "attempt": attempt,
                }
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        if attempt < retries:
            jitter = random.uniform(0.0, 0.35)
            time.sleep(backoff_seconds * attempt + jitter)
    return {
        "ok": False,
        "status_code": None,
        "final_url": url,
        "content_type": "",
        "html": "",
        "error": last_error or "unknown_error",
        "attempt": retries,
    }


def fetch_html_playwright(
    url: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> dict[str, Any]:
    """Fetch rendered HTML via Playwright for JS/storefront-gated PDPs."""

    last_error: str | None = None
    for attempt in range(1, retries + 1):
        browser = None
        context = None
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "ok": False,
                "status_code": None,
                "final_url": url,
                "content_type": "text/html",
                "html": "",
                "error": "playwright_not_installed",
                "attempt": attempt,
            }

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=REQUEST_HEADERS["User-Agent"],
                    locale="en-US",
                    extra_http_headers={
                        "Accept-Language": REQUEST_HEADERS["Accept-Language"],
                        "Cache-Control": REQUEST_HEADERS["Cache-Control"],
                    },
                )
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=max(3000, timeout_seconds * 1000))
                except PlaywrightError:
                    # Some pages keep long-polling; DOM is still usable.
                    pass
                html = page.content()
                return {
                    "ok": True,
                    "status_code": response.status if response else None,
                    "final_url": page.url,
                    "content_type": "text/html",
                    "html": html,
                    "error": None,
                    "attempt": attempt,
                }
        except Exception as exc:  # pragma: no cover - runtime-only network/browser errors
            last_error = str(exc)
        finally:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()

        if attempt < retries:
            jitter = random.uniform(0.0, 0.35)
            time.sleep(backoff_seconds * attempt + jitter)

    return {
        "ok": False,
        "status_code": None,
        "final_url": url,
        "content_type": "text/html",
        "html": "",
        "error": last_error or "playwright_error",
        "attempt": retries,
    }


def detect_store_gate(html: str) -> bool:
    probes = (
        "Select Store | Whole Foods Market",
        "Log in with your Amazon account to see shipping options",
        "Find a Store",
        "Please select a location near you",
    )
    return any(token in html for token in probes)


def _extract_ld_json_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for match in SCRIPT_JSON_LD_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        elif isinstance(payload, list):
            objects.extend(x for x in payload if isinstance(x, dict))
    return objects


def _extract_product_name(html: str, ld_json_objects: list[dict[str, Any]]) -> str | None:
    for obj in ld_json_objects:
        if obj.get("@type") in ("Product", "FoodProduct"):
            name = obj.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    if h1_match:
        name = clean_text(h1_match.group(1))
        if name:
            return name
    og_match = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if og_match:
        return clean_text(og_match.group(1))
    return None


def _extract_brand(ld_json_objects: list[dict[str, Any]]) -> str | None:
    for obj in ld_json_objects:
        if obj.get("@type") not in ("Product", "FoodProduct"):
            continue
        brand = obj.get("brand")
        if isinstance(brand, dict):
            name = brand.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        if isinstance(brand, str) and brand.strip():
            return brand.strip()
    return None


def _extract_ingredients(html: str, ld_json_objects: list[dict[str, Any]]) -> str | None:
    for obj in ld_json_objects:
        if obj.get("@type") in ("Product", "FoodProduct"):
            val = obj.get("ingredients") or obj.get("ingredient")
            if isinstance(val, str) and val.strip():
                return clean_text(val)
            if isinstance(val, list):
                parts = [clean_text(x) for x in val if isinstance(x, str) and clean_text(x)]
                if parts:
                    return ", ".join(parts)

    header_re = re.compile(
        r"<h4[^>]*>\s*Ingredients\s*</h4>\s*<p[^>]*>(.*?)</p>",
        re.I | re.S,
    )
    match = header_re.search(html)
    if match:
        ingredient_text = clean_text(match.group(1))
        if ingredient_text:
            return ingredient_text
    return None


def _extract_category_from_breadcrumbs(html: str) -> str | None:
    bread_re = re.search(
        r'<nav[^>]+aria-label=["\']Breadcrumb["\'][^>]*>(.*?)</nav>',
        html,
        re.I | re.S,
    )
    if not bread_re:
        return None
    text = clean_text(bread_re.group(1))
    if not text:
        return None
    parts = [x.strip() for x in text.split("/") if x.strip()]
    if not parts:
        return None
    return parts[-1]


def _extract_serving_size_grams(serving_size_text: str | None) -> float | None:
    if not serving_size_text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*g\b", serving_size_text.lower())
    if match:
        return float(match.group(1))
    return None


def _nutrition_label_to_column(label: str) -> str | None:
    lowered = label.lower().strip()
    mapping = {
        "calories": "calories",
        "total fat": "total_fat_g",
        "saturated fat": "saturated_fat_g",
        "trans fat": "trans_fat_g",
        "cholesterol": "cholesterol_mg",
        "sodium": "sodium_mg",
        "total carbohydrate": "carbs_g",
        "dietary fiber": "fiber_g",
        "total sugars": "total_sugars_g",
        "added sugars": "added_sugars_g",
        "protein": "protein_g",
        "serving size": "serving_size_text",
    }
    if lowered in mapping:
        return mapping[lowered]
    for key, target in mapping.items():
        if lowered.startswith(key):
            return target
    return None


def _parse_numeric_value(raw_value: str) -> float | None:
    match = re.search(r"(-?\d+(?:\.\d+)?)", raw_value)
    if not match:
        return None
    return float(match.group(1))


def _extract_nutrition_from_dom(html: str) -> dict[str, Any]:
    nutrition: dict[str, Any] = {}
    section = html

    row_pair_re = re.compile(
        r'<div[^>]*class=["\'][^"\']*nutrition-row[^"\']*["\'][^>]*>\s*'
        r'<div[^>]*class=["\'][^"\']*nutrition-column[^"\']*["\'][^>]*>(.*?)</div>\s*'
        r'<div[^>]*class=["\'][^"\']*nutrition-column[^"\']*["\'][^>]*>(.*?)</div>',
        re.I | re.S,
    )
    for row_match in row_pair_re.finditer(section):
        label = clean_text(row_match.group(1))
        value = clean_text(row_match.group(2))
        if not label or not value:
            continue
        key = _nutrition_label_to_column(label)
        if not key:
            continue
        if key == "serving_size_text":
            nutrition[key] = value
        else:
            nutrition[key] = _parse_numeric_value(value)

    servings_match = re.search(r'<div[^>]*class=["\']servings["\'][^>]*>(.*?)</div>', section, re.I | re.S)
    if servings_match:
        nutrition["servings_per_container_text"] = clean_text(servings_match.group(1))

    serving_size_g = _extract_serving_size_grams(nutrition.get("serving_size_text"))
    if serving_size_g is not None:
        nutrition["serving_size_g"] = serving_size_g
    return nutrition


def parse_product_html(source_url: str, html: str, http_status: int | None) -> dict[str, Any]:
    fetched_at = now_iso()
    gate_detected = detect_store_gate(html)
    if gate_detected:
        return {
            "source_store": WHOLEFOODS_SOURCE,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "fetch_status": "store_gate",
            "http_status": http_status,
            "error": "store_selection_required",
            "extracted": {},
        }

    ld_json_objects = _extract_ld_json_objects(html)
    name = _extract_product_name(html, ld_json_objects)
    brand = _extract_brand(ld_json_objects)
    category = _extract_category_from_breadcrumbs(html)
    ingredient_text = _extract_ingredients(html, ld_json_objects)
    nutrition = _extract_nutrition_from_dom(html)
    organic_flag = 1 if "organic" in (ingredient_text or "").lower() or "organic" in (name or "").lower() else 0

    fetch_status = "ok" if name else "partial"
    return {
        "source_store": WHOLEFOODS_SOURCE,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "fetch_status": fetch_status,
        "http_status": http_status,
        "error": None,
        "extracted": {
            "name": name,
            "brand": brand,
            "category": category,
            "organic_flag": organic_flag,
            "ingredient_text": ingredient_text,
            "nutrition": nutrition,
        },
    }


def scrape_product_url(
    url: str,
    prefer_browser: bool = True,
    http_fallback: bool = True,
    browser_fetcher: HtmlFetcher | None = None,
    http_fetcher: HtmlFetcher | None = None,
) -> dict[str, Any]:
    canonical_url = canonicalize_product_url(url) or url
    browser_fetch = browser_fetcher or fetch_html_playwright
    http_fetch = http_fetcher or fetch_html

    browser_error: str | None = None
    if prefer_browser:
        browser_result = browser_fetch(canonical_url)
        if browser_result["ok"]:
            payload = parse_product_html(canonical_url, browser_result["html"], browser_result["status_code"])
            payload["final_url"] = browser_result["final_url"]
            payload["content_type"] = browser_result["content_type"]
            payload["attempt"] = browser_result["attempt"]
            payload["fetch_method"] = "playwright"
            return payload
        browser_error = browser_result.get("error")

    if not http_fallback and prefer_browser:
        return {
            "source_store": WHOLEFOODS_SOURCE,
            "source_url": canonical_url,
            "fetched_at": now_iso(),
            "fetch_status": "fetch_error",
            "http_status": None,
            "error": browser_error or "playwright_error",
            "extracted": {},
            "fetch_method": "playwright",
        }

    fetch_result = http_fetch(canonical_url)
    if not fetch_result["ok"]:
        errors = [fetch_result["error"]]
        if browser_error:
            errors.insert(0, f"playwright:{browser_error}")
        return {
            "source_store": WHOLEFOODS_SOURCE,
            "source_url": canonical_url,
            "fetched_at": now_iso(),
            "fetch_status": "fetch_error",
            "http_status": fetch_result["status_code"],
            "error": " | ".join(x for x in errors if x),
            "extracted": {},
            "fetch_method": "http",
        }

    payload = parse_product_html(canonical_url, fetch_result["html"], fetch_result["status_code"])
    payload["final_url"] = fetch_result["final_url"]
    payload["content_type"] = fetch_result["content_type"]
    payload["attempt"] = fetch_result["attempt"]
    payload["fetch_method"] = "http"
    if browser_error:
        payload["browser_error"] = browser_error
    return payload


def crawl_product_urls(
    seed_urls: list[str],
    max_pages: int = 25,
    delay_seconds: float = 0.35,
) -> list[str]:
    queue: list[str] = []
    for seed in seed_urls:
        parsed = urllib.parse.urlparse(seed)
        if not parsed.scheme:
            queue.append(f"https://{WHOLEFOODS_HOST}{seed}")
        else:
            queue.append(seed)

    seen_pages: set[str] = set()
    product_urls: set[str] = set()
    pages_processed = 0

    while queue and pages_processed < max_pages:
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        pages_processed += 1
        response = fetch_html(page_url)
        if not response["ok"]:
            continue
        html = response["html"]
        for product_url in extract_product_urls_from_html(html, page_url):
            product_urls.add(product_url)
        for next_url in extract_next_aisle_urls_from_html(html, page_url):
            if next_url not in seen_pages:
                queue.append(next_url)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    return sorted(product_urls)
