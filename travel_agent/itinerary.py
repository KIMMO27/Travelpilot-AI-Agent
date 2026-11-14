"""Stage 5: evidence-only 1–5 day city-centre itinerary + optional real walking routes.

POI results are supplied by the existing Geoapify/OSM tool. A successful
routing result gives estimated street-network walk times, not live travel times.
No flight/hotel/restaurant prices, opening hours or reservations are inferred.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from travel_agent.osm_places import (
    CACHE_DIR, CITY_CENTERS, _geoapify_key, _ssl_context, search_places,
)

ROUTING_ENDPOINT = "https://api.geoapify.com/v1/routing"
ROUTE_DIR = CACHE_DIR / "routes"
SLOT_NAMES = ("上午", "下午", "晚上")
INTERESTS = {"咖啡": "cafe", "coffee": "cafe", "cafe": "cafe"}
CATEGORY_WORDS = {"cafe": "咖啡", "attraction": "博物馆", "park": "公园", "restaurant": "餐厅"}
# Suggestions, not verified opening times. Deliberately favor cafes on day 2.
DEFAULT_DAY_TYPES = (
    ("attraction", "park", "restaurant"),
    ("cafe", "attraction", "restaurant"),
    ("cafe", "park", "restaurant"),
    ("attraction", "park", "restaurant"),
    ("cafe", "attraction", "restaurant"),
)


def _valid_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _geo_distance_km(lat1, lon1, lat2, lon2):
    """Only for choosing nearby POIs, never presented as walking distance."""
    phi1, phi2 = map(math.radians, (lat1, lat2))
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1-a)))


def _valid_places(rows, category):
    cleaned = []
    if not isinstance(rows, list):
        return cleaned
    for row in rows:
        if not isinstance(row, dict):
            continue
        name, lat, lon = row.get("name"), row.get("latitude"), row.get("longitude")
        if not isinstance(name, str) or not name.strip() or not (_valid_number(lat) and _valid_number(lon)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        url = row.get("osm_url")
        if not isinstance(url, str) or not url.startswith("https://www.openstreetmap.org/"):
            continue
        cleaned.append({"name": name[:120], "latitude": float(lat), "longitude": float(lon),
                        "address": str(row.get("address") or "地址未提供")[:220],
                        "osm_url": url, "category": category})
    return cleaned


def walking_route(stops):
    """Geoapify street-network walking route; no invented metrics on failure."""
    if len(stops) < 2:
        return {"status": "not_needed"}
    if len(stops) > 5:
        return {"status": "unavailable", "reason": "路点超出演示限制"}
    key = _geoapify_key()
    if not key:
        return {"status": "unavailable", "reason": "未配置 GEOAPIFY_API_KEY"}

    coordinates = [(s["latitude"], s["longitude"]) for s in stops]
    path = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in coordinates)
    ROUTE_DIR.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(("walk|" + path).encode()).hexdigest()[:24]
    cache_path = ROUTE_DIR / (signature + ".json")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
        if isinstance(cached, dict) and cached.get("status") == "ok":
            return {**cached, "fetch_mode": "cache"}
    except (ValueError, OSError):
        pass

    # Routing API documents the GET `apiKey` parameter. Never print this URL,
    # returned exception body or raw response (they may contain private keys).
    url = ROUTING_ENDPOINT + "?" + urlencode({"waypoints": path, "mode": "walk", "format": "geojson", "apiKey": key})
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "AI-Agent-Portfolio/1.0"})
    try:
        with urlopen(request, timeout=12, context=_ssl_context()) as response:
            raw = response.read(1_500_001)
        if len(raw) > 1_500_000:
            raise ValueError("response too large")
        payload = json.loads(raw)
        features = payload.get("features") if isinstance(payload, dict) else None
        props = (features[0].get("properties") if features and isinstance(features[0], dict) else None)
        if not isinstance(props, dict):
            raise ValueError("missing route properties")
        distance, seconds = props.get("distance"), props.get("time")
        if not (_valid_number(distance) and _valid_number(seconds) and distance >= 0 and seconds >= 0):
            raise ValueError("missing distance/time")
        legs_data = props.get("legs") or []
        legs = []
        for i in range(len(stops) - 1):
            item = legs_data[i] if i < len(legs_data) and isinstance(legs_data[i], dict) else {}
            d, t = item.get("distance"), item.get("time")
            leg = {"from": stops[i]["name"], "to": stops[i+1]["name"]}
            if _valid_number(d) and _valid_number(t) and d >= 0 and t >= 0:
                leg.update({"distance_km": round(d / 1000, 2), "estimated_minutes": round(t / 60)})
            legs.append(leg)
        result = {"status": "ok", "transport": "walking", "source": "Geoapify Routing API",
                  "distance_km": round(distance/1000, 2), "estimated_minutes": round(seconds/60),
                  "legs": legs, "fetch_mode": "network",
                  "disclaimer": "道路网络模型估算，非实时路况；不含参观或排队时间"}
        temp = cache_path.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temp.replace(cache_path)
        return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError):
        return {"status": "unavailable", "reason": "步行路线接口本次未返回可用结果，不能提供步行时间"}


def plan_trip(arguments):
    """Use 4 real POI categories, local proximity ordering, and up to 5 routes.

    No LLM writes names or metrics. The outer Agent can summarize this result.
    """
    if not isinstance(arguments, dict):
        return {"error": "参数必须是 JSON 对象"}
    raw_city = str(arguments.get("city", ""))
    city = "墨尔本" if "墨尔本" in raw_city or "melbourne" in raw_city.lower() else ("悉尼" if "悉尼" in raw_city or "sydney" in raw_city.lower() else None)
    if city not in CITY_CENTERS:
        return {"error": "Stage 5 暂时只支持墨尔本和悉尼市中心"}
    try:
        days = int(arguments.get("days", 3))
    except (TypeError, ValueError):
        return {"error": "days 必须是数字"}
    if isinstance(arguments.get("days"), bool) or not 1 <= days <= 5:
        return {"error": "天数只能为 1–5 天"}
    if os.getenv("TRAVEL_MAP_PROVIDER", "").lower() != "geoapify" or os.getenv("TRAVEL_DATA_MODE", "").lower() != "osm":
        return {"error": "需用 TRAVEL_DATA_MODE=osm TRAVEL_MAP_PROVIDER=geoapify 启动，才能规划真实地点"}

    interest = str(arguments.get("interest", "咖啡"))[:120].lower()
    coffee_preference = any(word in interest for word in INTERESTS)
    daily_budget = arguments.get("daily_budget_aud")
    if daily_budget is not None:
        try:
            daily_budget = float(daily_budget)
        except (TypeError, ValueError):
            return {"error": "每日预算必须是数字"}
        if not _valid_number(daily_budget) or not 0 <= daily_budget <= 10000:
            return {"error": "每日预算超出演示范围"}

    catalog, issues, meta = {}, [], {}
    for category, keyword in CATEGORY_WORDS.items():
        response = search_places(city, "search_activities", keyword)
        if not isinstance(response, dict) or "error" in response:
            issues.append(f"{keyword}地点查询失败，相关活动留空")
            catalog[category] = []
            continue
        catalog[category] = _valid_places(response.get("activities"), category)
        meta[category] = {"source": response.get("data_source", "unknown"), "fetch_mode": response.get("fetch_mode", "unknown")}
        if not catalog[category]:
            issues.append(f"未查到可验证的{keyword}地点")

    visited, schedule = set(), []
    for index in range(days):
        order = list(DEFAULT_DAY_TYPES[index])
        if not coffee_preference and order[0] == "cafe":
            order[0] = "attraction"
        stops = []
        current = CITY_CENTERS[city]
        slots = []
        for slot, category in zip(SLOT_NAMES, order):
            candidates = [p for p in catalog.get(category, []) if p["osm_url"] not in visited]
            candidates.sort(key=lambda p: _geo_distance_km(current[0], current[1], p["latitude"], p["longitude"]))
            if not candidates:
                slots.append({"slot": slot, "category": category, "status": "unavailable",
                              "note": "这个时段没有足够经过地点 API 核验的候选，需补充数据"})
                continue
            place = candidates[0]
            visited.add(place["osm_url"])
            current = (place["latitude"], place["longitude"])
            item = {"slot": slot, "status": "place_found", **place}
            slots.append(item)
            stops.append(place)
        route = walking_route(stops)
        schedule.append({"day": index + 1, "slots": slots, "route": route})

    found = sum(1 for d in schedule for slot in d["slots"] if slot["status"] == "place_found")
    return {
        "city": city, "days": days, "interest": interest, "status": "complete_candidates" if found == days * 3 else "partial_candidates",
        "data_type": "verified_poi_candidates", "slot_count": days * 3, "found_slots": found,
        "itinerary": schedule, "poi_sources": meta, "issues": issues,
        "budget_note": (f"用户设定人均每日预算 AUD {daily_budget:.2f}；缺少实时价格，未验证是否符合预算" if daily_budget is not None else "未提供预算；没有验证价格"),
        "limitations": "均为实际地点数据来源中的候选记录，不代表营业或可预约。上午/下午/晚上仅表示推荐时段，不代表已核实营业时间；不含酒店、机票和实时天气。",
    }
