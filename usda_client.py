from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"
USDA_SOURCE = "usda"

# USDA FoodData Central nutrient IDs
_NUTRIENT_MAP: dict[int, str] = {
    1008: "calories",
    1003: "protein_g",
    1004: "total_fat_g",
    1258: "saturated_fat_g",
    1257: "trans_fat_g",
    1005: "carbs_g",
    1079: "fiber_g",
    2000: "total_sugars_g",
    1235: "added_sugars_g",
    1093: "sodium_mg",
    1087: "serving_size_g",  # calcium proxy unused; serving size comes from portionSize
}


def _api_key() -> str:
    key = os.environ.get("USDA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "USDA_API_KEY is not set. Add it to your .env or export it before running."
        )
    return key


def _get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"USDA API error {exc.code}: {url}") from exc


def search_foods(
    query: str,
    page_size: int = 25,
    page_number: int = 1,
    data_type: list[str] | None = None,
) -> dict[str, Any]:
    """Search FoodData Central. Returns the raw API response dict."""
    params: dict[str, Any] = {
        "query": query,
        "pageSize": page_size,
        "pageNumber": page_number,
        "api_key": _api_key(),
    }
    if data_type:
        params["dataType"] = ",".join(data_type)
    url = f"{USDA_BASE_URL}/foods/search?{urllib.parse.urlencode(params)}"
    return _get(url)


def get_food(fdc_id: int) -> dict[str, Any]:
    """Fetch a single food by FDC ID. Returns the raw API response dict."""
    url = f"{USDA_BASE_URL}/food/{fdc_id}?api_key={_api_key()}"
    return _get(url)


def food_source_url(fdc_id: int) -> str:
    return f"{USDA_BASE_URL}/food/{fdc_id}"


def map_food_to_extracted(food: dict[str, Any]) -> dict[str, Any]:
    """Map a USDA food object (from search or get_food) to our internal 'extracted' shape."""
    name = (food.get("description") or "").strip()
    brand = (
        food.get("brandName")
        or food.get("brandOwner")
        or food.get("manufacturerName")
        or None
    )
    if brand:
        brand = brand.strip() or None

    raw_category = food.get("foodCategory") or food.get("wweiaFoodCategory", {})
    if isinstance(raw_category, dict):
        category = (raw_category.get("wweiaFoodCategoryDescription") or "").strip() or None
    else:
        category = (str(raw_category).strip()) or None

    ingredient_text = (food.get("ingredients") or "").strip() or None
    organic_flag = 1 if "organic" in (name + " " + (ingredient_text or "")).lower() else 0

    nutrition: dict[str, Any] = {}
    for nutrient_entry in food.get("foodNutrients") or []:
        nutrient = nutrient_entry.get("nutrient") or nutrient_entry
        nid = nutrient.get("id") or nutrient_entry.get("nutrientId")
        value = nutrient_entry.get("value") or nutrient_entry.get("amount")
        if nid in _NUTRIENT_MAP and value is not None:
            nutrition[_NUTRIENT_MAP[nid]] = float(value)

    serving_size = food.get("servingSize")
    serving_unit = (food.get("servingSizeUnit") or "").upper()
    if serving_size is not None and serving_unit == "G":
        nutrition["serving_size_g"] = float(serving_size)
    elif serving_size is not None and serving_unit in ("ML", ""):
        nutrition["serving_size_g"] = float(serving_size)

    return {
        "name": name,
        "brand": brand,
        "category": category,
        "organic_flag": organic_flag,
        "ingredient_text": ingredient_text,
        "nutrition": nutrition,
    }


def build_raw_payload(food: dict[str, Any]) -> dict[str, Any]:
    """Build the raw_payload dict (as stored in raw_products.raw_json) from a USDA food."""
    fdc_id = food.get("fdcId")
    extracted = map_food_to_extracted(food)
    fetch_status = "ok" if extracted["name"] else "partial"
    return {
        "source_store": USDA_SOURCE,
        "source_url": food_source_url(fdc_id) if fdc_id else "",
        "fetch_status": fetch_status,
        "error": None,
        "extracted": extracted,
    }
