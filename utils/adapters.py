import os, json, requests
from typing import List, Dict, Optional

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OPENMETEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPENMETEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPENTRIPMAP_API_KEY = os.getenv("OPENTRIPMAP_API_KEY")
TICKETMASTER_API_KEY = os.getenv("TICKETMASTER_API_KEY")

def _safe_get(url: str, params: dict=None, timeout: int=20) -> Optional[requests.Response]:
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None

def _safe_post(url: str, data=None, timeout: int=30) -> Optional[requests.Response]:
    try:
        r = requests.post(url, data=data, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None

def geocode_city(query: str) -> Optional[Dict]:
    r = _safe_get(OPENMETEO_GEOCODE, {"name": query, "count": 1, "language": "en"}, 20)
    if not r:
        return None
    try:
        data = r.json()
        if data.get("results"):
            item = data["results"][0]
            return {
                "name": item.get("name"),
                "lat": item.get("latitude"),
                "lon": item.get("longitude"),
                "country": item.get("country"),
                "timezone": item.get("timezone") or "UTC",
            }
    except Exception:
        pass
    return None

def get_weather(lat: float, lon: float, date_from: str, date_to: str, timezone: str) -> Dict:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ["temperature_2m", "precipitation", "windspeed_10m"],
        "daily": ["weathercode", "temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
        "timezone": timezone, "start_date": date_from, "end_date": date_to,
    }
    r = _safe_get(OPENMETEO_FORECAST, params, 30)
    if not r:
        return {"daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}}
    try:
        return r.json()
    except Exception:
        return {"daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}}

# Map interests to Overpass tags
INTEREST_TAGS = {
    "culture": [{"tourism": "museum"}, {"tourism": "gallery"}, {"historic": "yes"}, {"tourism": "attraction"}],
    "nature": [{"leisure": "park"}, {"natural": "wood"}, {"natural": "beach"}, {"leisure": "nature_reserve"}],
    "adventure": [{"tourism": "theme_park"}, {"sport": "climbing"}, {"leisure": "water_park"}],
    "food": [{"amenity": "restaurant"}, {"amenity": "cafe"}, {"amenity": "fast_food"}, {"amenity": "bar"}],
    "nightlife": [{"amenity": "bar"}, {"amenity": "pub"}, {"amenity": "nightclub"}],
    "shopping": [{"shop": "mall"}, {"shop": "department_store"}, {"shop": "clothes"}],
    "family": [{"tourism": "zoo"}, {"tourism": "aquarium"}, {"leisure": "playground"}],
    "architecture": [{"building": "yes"}, {"tourism": "attraction"}],
    "photography": [{"tourism": "viewpoint"}, {"tourism": "attraction"}, {"natural": "peak"}]
}

def classify_osm(tags: Dict) -> str:
    try:
        if "amenity" in tags and tags["amenity"] in {"restaurant","cafe","bar","pub","fast_food"}:
            return "food"
        if "tourism" in tags:
            t = tags["tourism"]
            if t in {"museum","gallery","attraction"}: return "culture"
            if t in {"viewpoint"}: return "photography"
            if t in {"zoo","aquarium"}: return "family"
            if t in {"theme_park"}: return "adventure"
        if tags.get("leisure") in {"park","nature_reserve","water_park"}:
            return "nature" if tags.get("leisure") != "water_park" else "adventure"
        if "shop" in tags: return "shopping"
    except Exception:
        pass
    return "general"

def overpass_pois(lat: float, lon: float, radius_m: int, interests: List[str], max_items: int = 100) -> List[Dict]:
    clauses = []
    for interest in interests:
        for tag in INTEREST_TAGS.get(interest, []):
            for k, v in tag.items():
                clauses.append(f'node["{k}"="{v}"](around:{radius_m},{lat},{lon});')
                clauses.append(f'way["{k}"="{v}"](around:{radius_m},{lat},{lon});')
                clauses.append(f'relation["{k}"="{v}"](around:{radius_m},{lat},{lon});')
    if not clauses:
        clauses = [
            f'node["tourism"="attraction"](around:{radius_m},{lat},{lon});',
            f'node["amenity"="restaurant"](around:{radius_m},{lat},{lon});'
        ]
    query = f"""[out:json][timeout:25];({''.join(clauses)});out center {max_items};"""
    r = _safe_post(OVERPASS_URL, {"data": query}, 60)
    if not r:
        return []
    try:
        data = r.json()
        results = []
        for el in data.get("elements", []):
            tags = el.get("tags", {}) or {}
            name = tags.get("name") or tags.get("official_name") or "Unnamed place"
            center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if center["lat"] is None or center["lon"] is None: continue
            cat = classify_osm(tags)
            results.append({
                "id": f'{el.get("type")}/{el.get("id")}',
                "name": name, "lat": center["lat"], "lon": center["lon"],
                "category": cat, "tags": tags,
                "maps_link": f'https://maps.google.com/?q={center["lat"]},{center["lon"]}',
                "opening_hours": tags.get("opening_hours", "")
            })
        uniq = {}
        for it in results:
            key = (it["name"].lower(), it["category"])
            if key not in uniq: uniq[key] = it
        return list(uniq.values())[:max_items]
    except Exception:
        return []

def opentripmap_places(lat: float, lon: float, radius_m: int = 10000, limit: int = 50) -> List[Dict]:
    if not OPENTRIPMAP_API_KEY:
        return []
    base = "https://api.opentripmap.com/0.1/en/places/radius"
    params = {"apikey": OPENTRIPMAP_API_KEY, "radius": radius_m, "lon": lon, "lat": lat, "limit": limit, "rate": 2}
    r = _safe_get(base, params, 30)
    if not r:
        return []
    try:
        data = r.json(); out = []
        for it in data.get("features", []):
            props = it.get("properties", {})
            geom = it.get("geometry", {}).get("coordinates", [lon, lat])
            out.append({
                "id": props.get("xid",""),
                "name": props.get("name") or "Place",
                "lat": geom[1], "lon": geom[0],
                "category": props.get("kinds","").split(",")[0] if props.get("kinds") else "general",
                "tags": {"kinds": props.get("kinds","")},
                "maps_link": f'https://maps.google.com/?q={geom[1]},{geom[0]}'
            })
        return out
    except Exception:
        return []

def ticketmaster_events(city: str, start_date: str, end_date: str, size: int = 20) -> List[Dict]:
    if not TICKETMASTER_API_KEY: return []
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {"apikey": TICKETMASTER_API_KEY, "city": city, "startDateTime": start_date + "T00:00:00Z", "endDateTime": end_date + "T23:59:59Z", "size": size, "sort": "date,asc"}
    r = _safe_get(url, params, 30)
    if not r: return []
    try:
        data = r.json(); events = []
        for e in data.get("_embedded", {}).get("events", []):
            name = e.get("name","Event"); url = e.get("url",""); dates = e.get("dates", {}).get("start", {})
            events.append({"name": name, "date": dates.get("localDate") or dates.get("dateTime",""), "url": url, "category": "event"})
        return events
    except Exception:
        return []

def get_fx_rates(base: str = "USD", symbols: Optional[List[str]] = None) -> Dict:
    url = "https://api.exchangerate.host/latest"; params = {"base": base}
    if symbols: params["symbols"] = ",".join(symbols)
    r = _safe_get(url, params, 20)
    if not r: return {}
    try:
        data = r.json(); return data.get("rates", {})
    except Exception:
        return {}
