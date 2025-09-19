
import os, math, json, time, itertools, re, random
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests
from flask import Flask, request, jsonify, make_response, render_template_string
from icalendar import Calendar, Event
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

OPENMETEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPENMETEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
WIKI_GEOSEARCH = "https://en.wikipedia.org/w/api.php"
EXCHANGERATE_URL = "https://api.exchangerate.host/latest"

AMADEUS_KEY = os.getenv("AMADEUS_API_KEY")
AMADEUS_SECRET = os.getenv("AMADEUS_API_SECRET")
GETYOURGUIDE_KEY = os.getenv("GETYOURGUIDE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
AMADEUS_HOST = "https://test.api.amadeus.com"

AMADEUS_TOKEN = {"access_token": None, "exp": 0}

def safe_get(url, params=None, timeout=25, headers=None):
    try:
        r = requests.get(url, params=params, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r
    except Exception:
        return None

def safe_post(url, data=None, timeout=30, headers=None, json_body=None):
    try:
        if json_body is not None:
            r = requests.post(url, json=json_body, timeout=timeout, headers=headers)
        else:
            r = requests.post(url, data=data, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r
    except Exception:
        return None

def geocode_city(query: str):
    r = safe_get(OPENMETEO_GEOCODE, {"name": query, "count": 1, "language": "en"}, timeout=20)
    if not r: return None
    data = r.json()
    if not data.get("results"): return None
    it = data["results"][0]
    return {
        "name": it.get("name"),
        "lat": it.get("latitude"),
        "lon": it.get("longitude"),
        "country": it.get("country"),
        "timezone": it.get("timezone") or "UTC",
    }

def get_weather(lat, lon, start_date, end_date, tz):
    params = {
        "latitude": lat, "longitude": lon,
        "daily": ["weathercode","temperature_2m_max","temperature_2m_min","precipitation_sum"],
        "hourly": ["temperature_2m","precipitation","windspeed_10m"],
        "start_date": start_date, "end_date": end_date, "timezone": tz,
    }
    r = safe_get(OPENMETEO_FORECAST, params, timeout=30)
    if not r:
        return {"daily": {"time": [], "temperature_2m_max": [], "temperature_2m_min": [], "precipitation_sum": []}}
    return r.json()

# ───────────────── POIs ─────────────────
INTEREST_TAGS = {
    "culture": [{"tourism":"museum"},{"tourism":"gallery"},{"historic":"yes"},{"tourism":"attraction"}],
    "nature": [{"leisure":"park"},{"natural":"wood"},{"natural":"beach"},{"leisure":"nature_reserve"}],
    "adventure": [{"tourism":"theme_park"},{"sport":"climbing"},{"leisure":"water_park"}],
    "food": [{"amenity":"restaurant"},{"amenity":"cafe"},{"amenity":"fast_food"},{"amenity":"bar"}],
    "nightlife": [{"amenity":"bar"},{"amenity":"pub"},{"amenity":"nightclub"}],
    "shopping": [{"shop":"mall"},{"shop":"department_store"},{"shop":"clothes"}],
    "family": [{"tourism":"zoo"},{"tourism":"aquarium"},{"leisure":"playground"}],
    "architecture": [{"building":"yes"},{"tourism":"attraction"}],
    "photography": [{"tourism":"viewpoint"},{"tourism":"attraction"},{"natural":"peak"}],
}

def classify_osm(tags: dict) -> str:
    try:
        if tags.get("amenity") in {"restaurant","cafe","bar","pub","fast_food"}: return "food"
        if "tourism" in tags:
            t = tags["tourism"]
            if t in {"museum","gallery","attraction"}: return "culture"
            if t == "viewpoint": return "photography"
            if t in {"zoo","aquarium"}: return "family"
            if t == "theme_park": return "adventure"
        if tags.get("leisure") in {"park","nature_reserve"}: return "nature"
        if tags.get("leisure") == "water_park": return "adventure"
        if "shop" in tags: return "shopping"
    except Exception:
        pass
    return "general"

def overpass_pois(lat, lon, radius_m, interests, max_items=160):
    clauses = []
    for interest in interests or []:
        for tag in INTEREST_TAGS.get(interest, []):
            for k, v in tag.items():
                clauses += [
                    f'node["{k}"="{v}"](around:{radius_m},{lat},{lon});',
                    f'way["{k}"="{v}"](around:{radius_m},{lat},{lon});',
                    f'relation["{k}"="{v}"](around:{radius_m},{lat},{lon});',
                ]
    if not clauses:
        clauses = [
            f'node["tourism"="attraction"](around:{radius_m},{lat},{lon});',
            f'node["amenity"="restaurant"](around:{radius_m},{lat},{lon});',
            f'node["leisure"="park"](around:{radius_m},{lat},{lon});',
            f'node["historic"](around:{radius_m},{lat},{lon});',
        ]
    query = f"""[out:json][timeout:25];({''.join(clauses)});out center {max_items};"""

    results = []
    used_url = None
    for url in OVERPASS_URLS:
        r = safe_post(url, {"data": query}, timeout=60)
        if not r: continue
        data = r.json()
        for el in data.get("elements", []):
            tags = el.get("tags", {}) or {}
            name = tags.get("name") or tags.get("official_name") or "Place"
            center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if center["lat"] is None or center["lon"] is None: continue
            results.append({
                "id": f'{el.get("type")}/{el.get("id")}', "name": name,
                "lat": center["lat"], "lon": center["lon"],
                "category": classify_osm(tags), "tags": tags,
                "maps_link": f'https://maps.google.com/?q={center["lat"]},{center["lon"]}',
            })
        if results:
            used_url = url
            break

    if len(results) < 20:
        radius2 = min(radius_m*2, 40000)
        q2 = f"""[out:json][timeout:25];(
            node["tourism"="attraction"](around:{radius2},{lat},{lon});
            node["amenity"="restaurant"](around:{radius2},{lat},{lon});
            node["leisure"="park"](around:{radius2},{lat},{lon});
            node["historic"](around:{radius2},{lat},{lon});
        );out center {max_items};"""
        for url in OVERPASS_URLS:
            r = safe_post(url, {"data": q2}, timeout=60)
            if not r: continue
            data = r.json()
            for el in data.get("elements", []):
                tags = el.get("tags", {}) or {}
                name = tags.get("name") or tags.get("official_name") or "Place"
                center = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
                if center["lat"] is None or center["lon"] is None: continue
                results.append({
                    "id": f'{el.get("type")}/{el.get("id")}', "name": name,
                    "lat": center["lat"], "lon": center["lon"],
                    "category": classify_osm(tags), "tags": tags,
                    "maps_link": f'https://maps.google.com/?q={center["lat"]},{center["lon"]}',
                })
            if results:
                used_url = used_url or url
                break

    uniq = {}
    for i in results:
        key = (i["name"].strip().lower(), i["category"])
        if key not in uniq: uniq[key] = i
    return list(uniq.values())[:max_items], used_url

def wikipedia_pois(lat, lon, radius_m=15000, limit=60):
    params = {"action":"query","list":"geosearch","gscoord":f"{lat}|{lon}","gsradius":min(radius_m,20000),
              "gslimit":limit,"format":"json"}
    r = safe_get(WIKI_GEOSEARCH, params, timeout=20)
    if not r: return []
    out = []
    for g in r.json().get("query", {}).get("geosearch", []):
        out.append({
            "id": f"wiki/{g.get('pageid')}", "name": g.get("title") or "Place",
            "lat": g.get("lat"), "lon": g.get("lon"), "category": "culture",
            "tags": {"source":"wikipedia"},
            "maps_link": f"https://maps.google.com/?q={g.get('lat')},{g.get('lon')}"
        })
    return out

COST_TABLE_USD = {
    "food":{"tight":8,"moderate":20,"luxury":50},
    "culture":{"tight":10,"moderate":25,"luxury":40},
    "nature":{"tight":0,"moderate":5,"luxury":10},
    "adventure":{"tight":20,"moderate":50,"luxury":100},
    "nightlife":{"tight":15,"moderate":40,"luxury":100},
    "shopping":{"tight":0,"moderate":0,"luxury":0},
    "family":{"tight":10,"moderate":25,"luxury":40},
    "photography":{"tight":0,"moderate":0,"luxury":5},
    "architecture":{"tight":0,"moderate":5,"luxury":10},
    "general":{"tight":0,"moderate":5,"luxury":10},
}
INDOOR = {"culture","shopping","food","nightlife","architecture"}
OUTDOOR = {"nature","adventure","photography"}

def fx_rate(to_code: str) -> float:
    r = safe_get(EXCHANGERATE_URL, {"base":"USD","symbols":to_code}, timeout=20)
    if not r: return 1.0
    try: return float(r.json().get("rates", {}).get(to_code, 1.0))
    except Exception: return 1.0

def estimate_day(items, budget, currency):
    usd = 0.0
    for it in items or []:
        cat = it.get("category","general")
        usd += COST_TABLE_USD.get(cat, COST_TABLE_USD["general"])[budget]
    return round(usd * fx_rate(currency), 2)

def haversine(a_lat, a_lon, b_lat, b_lon):
    R=6371
    dlat=math.radians(b_lat-a_lat); dlon=math.radians(b_lon-a_lon)
    h=math.sin(dlat/2)**2+math.cos(math.radians(a_lat))*math.cos(math.radians(b_lat))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(h))

def order_nearest_neighbor(items, center_lat, center_lon):
    if not items: return []
    def dist_to_center(i):
        la = items[i].get("lat") or center_lat
        lo = items[i].get("lon") or center_lon
        return haversine(center_lat, center_lon, la, lo)
    start_idx = min(range(len(items)), key=lambda i: dist_to_center(i))
    unvisited = set(range(len(items)))
    order = [start_idx]; unvisited.remove(start_idx); cur = start_idx
    while unvisited:
        def d(i,j):
            ai = items[i].get("lat") or center_lat
            bi = items[i].get("lon") or center_lon
            aj = items[j].get("lat") or center_lat
            bj = items[j].get("lon") or center_lon
            return haversine(ai, bi, aj, bj)
        nxt = min(unvisited, key=lambda j: d(cur, j))
        order.append(nxt); unvisited.remove(nxt); cur = nxt
    return order

def pick_under_cap(candidates, interests, budget, currency, cap):
    if cap <= 0 or not candidates: return candidates
    def score(p):
        s = 1.0
        if p.get("category") in interests: s += 1.5
        tags = json.dumps(p.get("tags","")).lower()
        if "museum" in tags and "culture" in interests: s += .3
        if "park" in tags and "nature" in interests: s += .3
        return s
    priced = []
    for p in candidates:
        cat = p.get("category","general")
        price = COST_TABLE_USD.get(cat, COST_TABLE_USD["general"])[budget] * fx_rate(currency)
        priced.append((p, score(p), price))
    priced.sort(key=lambda x: (x[1]/max(x[2],1e-6)), reverse=True)
    out = []; total=0.0
    for p, s, price in priced:
        if total+price <= cap or not out:
            out.append(p); total += price
    return out

def plan_itinerary(city, start_date, end_date, companions, budget, interests, pois, per_day_target=3, cap=0, currency="USD"):
    SLOTS = ["Morning","Afternoon","Evening"]
    try:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
    except Exception:
        start = datetime.today(); end = start
    days = max(1, (end - start).days + 1)

    ranked = list(pois or [])
    def score(p):
        s = 1.0
        if p.get("category") in interests: s += 1.5
        tags = json.dumps(p.get("tags", "")).lower()
        if "museum" in tags and "culture" in interests: s += .3
        if "park" in tags and "nature" in interests: s += .3
        return s
    ranked.sort(key=score, reverse=True)
    if not ranked:
        return {"meta": {"city": city, "companions": companions, "budget": budget, "interests": interests}, "days": [
            {"date": (start + timedelta(days=d)).date().isoformat(), "items": []} for d in range(days)
        ]}

    cycle = itertools.cycle(ranked)
    plan_days = []
    for d in range(days):
        day_date = (start + timedelta(days=d)).date().isoformat()
        items = []
        last_cat = None
        for slot in SLOTS[:per_day_target]:
            tries = 0
            while tries < len(ranked):
                cand = next(cycle); tries += 1
                if any(i["name"].lower()==cand["name"].lower() for i in items): continue
                if last_cat and cand.get("category")==last_cat and len(ranked)>3: continue
                items.append({"slot":slot,"name":cand["name"],"category":cand.get("category","general"),
                              "lat":cand.get("lat"),"lon":cand.get("lon"),"maps_link":cand.get("maps_link")})
                last_cat = cand.get("category"); break
        items = pick_under_cap(items, interests, budget, currency, cap)
        plan_days.append({"date": day_date, "items": items})
    return {"meta": {"city": city, "companions": companions, "budget": budget, "interests": interests}, "days": plan_days}

def rebalance_by_weather(itinerary, daily_precip):
    for day in itinerary["days"]:
        p = daily_precip.get(day["date"], 0)
        if p is None: continue
        if p >= 2.0:
            day["items"].sort(key=lambda i: 0 if i.get("category") in INDOOR else 1)
        else:
            day["items"].sort(key=lambda i: 0 if i.get("category") in OUTDOOR else 1)

def amadeus_token():
    if not (AMADEUS_KEY and AMADEUS_SECRET):
        return None
    now = time.time()
    if AMADEUS_TOKEN["access_token"] and now < AMADEUS_TOKEN["exp"] - 30:
        return AMADEUS_TOKEN["access_token"]
    r = safe_post(
        f"{AMADEUS_HOST}/v1/security/oauth2/token",
        data={"grant_type":"client_credentials","client_id":AMADEUS_KEY,"client_secret":AMADEUS_SECRET},
        timeout=20,
        headers={"Content-Type":"application/x-www-form-urlencoded"}
    )
    if not r: return None
    tok = r.json()
    AMADEUS_TOKEN["access_token"] = tok.get("access_token")
    AMADEUS_TOKEN["exp"] = now + int(tok.get("expires_in", 0))
    return AMADEUS_TOKEN["access_token"]

def amadeus_city_airports(keyword):
    t = amadeus_token()
    if not t: return []
    r = safe_get(
        f"{AMADEUS_HOST}/v1/reference-data/locations",
        params={"keyword": keyword, "subType": "AIRPORT,CITY", "page[limit]": 10},
        headers={"Authorization": f"Bearer {t}"},
        timeout=20
    )
    if not r: return []
    data = r.json().get("data", [])
    codes = []
    for it in data:
        code = it.get("iataCode")
        typ = it.get("subType")
        if code and typ in {"AIRPORT","CITY"} and code not in codes:
            codes.append(code)
    return codes[:3]

def parse_iata(text):
    t = (text or "").strip().upper()
    return t if len(t)==3 and t.isalpha() else None

def amadeus_flight_offers(origin_code, dest_code, depart, ret=None, adults=1, currency_code="USD"):
    t = amadeus_token()
    if not t: return []
    params = {
        "originLocationCode": origin_code,
        "destinationLocationCode": dest_code,
        "departureDate": depart,
        "adults": adults,
        "currencyCode": currency_code,
        "max": 10,
    }
    if ret: params["returnDate"] = ret
    r = safe_get(
        f"{AMADEUS_HOST}/v2/shopping/flight-offers",
        params=params,
        headers={"Authorization": f"Bearer {t}"},
        timeout=25
    )
    if not r: return []
    out = []
    for it in r.json().get("data", []):
        price = it.get("price", {})
        itin = it.get("itineraries", [])
        duration = itin[0].get("duration", "?") if itin else "?"
        carriers = set()
        for i in itin:
            for s in i.get("segments", []):
                carriers.add(s.get("carrierCode",""))
        out.append({
            "price": price.get("total"),
            "currency": price.get("currency"),
            "duration": duration,
            "carriers": ",".join(sorted([c for c in carriers if c])),
            "deeplink": "https://www.google.com/travel/flights?q=" + quote_plus(
                f"Flights from {origin_code} to {dest_code} on {depart}" + (f" returning {ret}" if ret else "")
            ),
        })
    return out

def amadeus_hotels_by_geo(lat, lon, radius=10):
    t = amadeus_token()
    if not t: return []
    r = safe_get(
        f"{AMADEUS_HOST}/v1/reference-data/locations/hotels/by-geocode",
        params={"latitude": lat, "longitude": lon, "radius": radius, "radiusUnit": "KM"},
        headers={"Authorization": f"Bearer {t}"},
        timeout=25
    )
    if not r: return []
    return [h.get("hotelId") for h in r.json().get("data", []) if h.get("hotelId")][:20]

def amadeus_hotel_offers(hotel_ids, checkin, checkout, currency_code="USD"):
    if not hotel_ids: return []
    t = amadeus_token()
    if not t: return []
    r = safe_get(
        f"{AMADEUS_HOST}/v2/shopping/hotel-offers",
        params={"hotelIds": ",".join(hotel_ids), "adults": 2, "checkInDate": checkin, "checkOutDate": checkout, "currency": currency_code},
        headers={"Authorization": f"Bearer {t}"},
        timeout=25
    )
    if not r: return []
    out = []
    for it in r.json().get("data", []):
        hotel = it.get("hotel", {})
        hname = hotel.get("name","Hotel")
        for off in it.get("offers", []):
            out.append({
                "name": hname,
                "price": off.get("price", {}).get("total"),
                "currency": off.get("price", {}).get("currency"),
                "checkin": off.get("checkInDate"), "checkout": off.get("checkOutDate"),
                "deeplink": f"https://www.booking.com/searchresults.html?ss={quote_plus(hname)}",
            })
    def p(x):
        try: return float(x.get("price", "1e9"))
        except: return 1e9
    return sorted(out, key=p)[:12]

def getyourguide_activities(lat, lon, currency="USD", limit=12):
    if not GETYOURGUIDE_KEY:
        return []
    headers = {"X-Access-Token": GETYOURGUIDE_KEY}
    params = {"lat": lat, "lng": lon, "radius": 15, "limit": limit, "currency": currency}
    r = safe_get("https://api.getyourguide.com/1/tours/", params=params, headers=headers, timeout=25)
    if not r: return []
    out = []
    for t in r.json().get("data", []):
        out.append({
            "title": t.get("title","Activity"),
            "price": t.get("price",{}).get("values", [{}])[0].get("amount", ""),
            "currency": t.get("price",{}).get("values", [{}])[0].get("currency", currency),
            "deeplink": t.get("tour_url") or ("https://www.getyourguide.com/s/?q=" + quote_plus(t.get("title","")))
        })
    return out[:limit]

# ───────────────── Demo price engines ─────────────────
def demo_flight_offers(origin_text, dest_text, depart, ret, currency="USD"):
    # distance-based price estimate
    o = geocode_city(origin_text) or {"lat":0,"lon":0}
    d = geocode_city(dest_text) or {"lat":0,"lon":0}
    dist = haversine(o["lat"], o["lon"], d["lat"], d["lon"]) if o["lat"] and d["lat"] else 3500
    base = 60.0 + 0.08*dist  # rough USD
    variants = [("DemoAir", 1.00), ("SampleJet", 0.9), ("BudgetFly", 0.75)]
    offers=[]
    for name, mult in variants:
        price = round(base*mult, 2)
        duration_hrs = max(2, dist/800.0*1.1)
        hh = int(duration_hrs); mm = int((duration_hrs-hh)*60)
        offers.append({
            "price": price, "currency": currency, "duration": f"PT{hh}H{mm}M", "carriers": name,
            "deeplink": "https://www.google.com/travel/flights?q=" + quote_plus(
                f"Flights from {origin_text} to {dest_text} on {depart}" + (f" returning {ret}" if ret else "")
            )
        })
    return offers

def demo_hotel_offers(city, start, end, currency="USD", budget="moderate"):
    # nights and budget multipliers
    try:
        nights = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days or 1
    except Exception:
        nights = 2
    base = {"tight":40, "moderate":90, "luxury":220}[budget]
    names = ["Central Stay", "Riverside Inn", "Old Town Suites", "Skyline Hotel", "Garden Residence"]
    offers=[]
    for n in names:
        total = round(base * nights * (0.8 + random.random()*0.6), 2)
        offers.append({
            "name": f"{n} · {city}",
            "price": total, "currency": currency,
            "checkin": start, "checkout": end,
            "deeplink": f"https://www.booking.com/searchresults.html?ss={quote_plus(n+' '+city)}"
        })
    return offers

def demo_activities_from_itinerary(itinerary, city, currency="USD", budget="moderate"):
    seen=set(); acts=[]
    per_cat = {"culture":25,"adventure":50,"food":20,"nature":5,"nightlife":30,"family":25,"photography":10,"architecture":10,"general":10}
    for day in itinerary.get("days", []):
        for it in day.get("items", []):
            key = it["name"].strip().lower()
            if key in seen: continue
            seen.add(key)
            price = round(per_cat.get(it.get("category","general"),10)* (0.8+random.random()*0.6), 2)
            acts.append({
                "title": it["name"],
                "price": price,
                "currency": currency,
                "deeplink": "https://www.getyourguide.com/s/?q=" + quote_plus(it["name"] or city)
            })
            if len(acts) >= 12: break
        if len(acts) >= 12: break
    if not acts:
        acts = [{"title":"Browse top activities","price":None,"currency":currency,
                 "deeplink":"https://www.getyourguide.com/s/?q="+quote_plus(city)}]
    return acts

# ───────────────── AI editor (rules fallback) ─────────────────
def ai_edit(message, state):
    text = (message or "").strip()
    if not text:
        return state, "Say: 'prefer museums & cafes', 'budget to luxury', 'radius to 18', 'remove Louvre'."
    # (LLM path removed for simplicity here; rules do the job)
    imap = {
        "museum":"culture","museums":"culture","gallery":"culture","galleries":"culture",
        "cafe":"food","cafes":"food","coffee":"food","street food":"food",
        "park":"nature","parks":"nature","hike":"adventure","hiking":"adventure",
        "nightlife":"nightlife","bars":"nightlife","shopping":"shopping",
        "kids":"family","family":"family","architecture":"architecture","photography":"photography"
    }
    changed=False
    for key, val in imap.items():
        if key in text.lower():
            arr=set(state.get("interests",[])); arr.add(val); state["interests"]=sorted(list(arr)); changed=True
    for b in ["tight","moderate","luxury"]:
        if f"budget {b}" in text.lower() or f"to {b}" in text.lower():
            state["budget"]=b; changed=True
    m=re.search(r"radius.*?(\d{1,2})", text.lower())
    if m:
        state["radius_km"]=max(4,min(30,int(m.group(1)))); changed=True
    if not changed:
        return state, "No change parsed — try interests/budget/radius or 'remove <place>'."
    return state, "Updated."

# ───────────────── API: planning ─────────────────
@app.post("/api/plan")
def api_plan():
    data = request.get_json(force=True)
    dest = (data.get("destination") or "").strip()
    origin = (data.get("origin") or "").strip()
    start = data.get("start_date")
    end = data.get("end_date")
    interests = data.get("interests") or ["culture","food"]
    budget = data.get("budget") or "moderate"
    companions = data.get("companions") or "solo"
    radius_km = int(data.get("radius_km") or 12)
    currency = data.get("currency") or "USD"
    optimize = bool(data.get("optimize", True))
    cap_enabled = bool(data.get("cap_enabled", False))
    cap_value = float(data.get("cap_value") or 0.0)

    if not dest or not start or not end:
        return jsonify({"error":"destination, start_date, end_date are required"}), 400

    geo = geocode_city(dest)
    if not geo: return jsonify({"error":"Could not geocode that city"}), 400

    weather = get_weather(geo["lat"], geo["lon"], start, end, geo["timezone"])
    pois, overpass_used = overpass_pois(geo["lat"], geo["lon"], int(radius_km*1000), interests, max_items=200)

    sources = []
    if pois: sources.append(f"Overpass ({'main' if overpass_used==OVERPASS_URLS[0] else 'mirror'})")
    if len(pois) < 20:
        wiki = wikipedia_pois(geo["lat"], geo["lon"], radius_m=int(radius_km*1200), limit=80)
        seen = set((p["name"].strip().lower() for p in pois))
        added=0
        for w in wiki:
            k=w["name"].strip().lower()
            if k not in seen:
                pois.append(w); seen.add(k); added+=1
        if added: sources.append("Wikipedia Nearby")

    itinerary = plan_itinerary(geo["name"], start, end, companions, budget, interests, pois,
                               per_day_target=3, cap=cap_value if cap_enabled else 0, currency=currency)

    daily = weather.get("daily", {})
    times = daily.get("time", []); pr = daily.get("precipitation_sum", [])
    precip = {times[i]: pr[i] for i in range(min(len(times), len(pr)))}
    itinerary["days"].sort(key=lambda d: precip.get(d["date"], 0))
    # indoor/outdoor balance on rain
    for d in itinerary["days"]:
        p = precip.get(d["date"], 0) or 0
        if p >= 2.0:
            d["items"].sort(key=lambda i: 0 if i.get("category") in INDOOR else 1)

    if optimize:
        for day in itinerary["days"]:
            items = list(day.get("items", []))
            if len(items) > 2:
                order = order_nearest_neighbor(items, geo["lat"], geo["lon"])
                day["items"] = [items[i] for i in order]

    for day in itinerary["days"]:
        for item in day.get("items", []):
            lat, lon = item.get("lat"), item.get("lon")
            item["maps_link"] = item.get("maps_link") or (f"https://maps.google.com/?q={lat},{lon}" if lat and lon
                                                          else f"https://www.google.com/maps/search/?api=1&query={quote_plus(item.get('name','')+' '+geo['name'])}")
        day["estimated_cost"] = estimate_day(day.get("items", []), budget, currency)

    provider_status = {
        "amadeus": bool(AMADEUS_KEY and AMADEUS_SECRET),
        "getyourguide": bool(GETYOURGUIDE_KEY)
    }

    return jsonify({
        "geo": geo,
        "weather": weather.get("daily", {}),
        "itinerary": itinerary,
        "currency": currency,
        "poi_count": len(pois),
        "sources_used": sources or ["(no POIs — try a bigger radius)"],
        "provider_status": provider_status
    })

# Live replan
@app.post("/api/replan")
def api_replan():
    payload = request.get_json(force=True)
    itin = payload.get("itinerary")
    geo = payload.get("geo")
    currency = payload.get("currency","USD")
    budget = payload.get("budget","moderate")
    optimize = bool(payload.get("optimize", True))
    w = get_weather(geo["lat"], geo["lon"], itin["days"][0]["date"], itin["days"][-1]["date"], geo.get("timezone","UTC"))
    daily = w.get("daily",{})
    times = daily.get("time", []); pr = daily.get("precipitation_sum", [])
    precip = {times[i]: pr[i] for i in range(min(len(times), len(pr)))}
    for d in itin["days"]:
        p = precip.get(d["date"], 0) or 0
        if p >= 2.0:
            d["items"].sort(key=lambda i: 0 if i.get("category") in INDOOR else 1)
    if optimize:
        for day in itin["days"]:
            items = list(day.get("items", []))
            if len(items) > 2:
                order = order_nearest_neighbor(items, geo["lat"], geo["lon"])
                day["items"] = [items[i] for i in order]
    for day in itin["days"]:
        day["estimated_cost"] = estimate_day(day.get("items", []), budget, currency)
    return jsonify({"itinerary": itin, "weather": daily})

# ───────────────── API: Search & Book ─────────────────
@app.post("/api/search/flights")
def api_flights():
    data = request.get_json(force=True)
    origin_text = data.get("origin") or ""
    dest_text = data.get("destination") or ""
    start = data.get("start_date")
    end = data.get("end_date")
    currency = data.get("currency","USD")

    provider = "deep-links"
    offers=[]

    if AMADEUS_KEY and AMADEUS_SECRET:
        # robust IATA inference
        o_iata = parse_iata(origin_text) or (amadeus_city_airports(origin_text)[:1] or [None])[0]
        d_iata = parse_iata(dest_text)   or (amadeus_city_airports(dest_text)[:1] or [None])[0]
        if o_iata and d_iata:
            try:
                offers = amadeus_flight_offers(o_iata, d_iata, start, end, adults=1, currency_code=currency)
            except Exception:
                offers = []
        provider = "Amadeus" if offers else "Amadeus (no results)"
    if not offers:
        # DEMO priced fallback (so UI is never empty)
        offers = demo_flight_offers(origin_text or "Your city", dest_text, start, end, currency=currency)
        provider = "demo-prices"

    return jsonify({"provider": provider, "offers": offers})

@app.post("/api/search/hotels")
def api_hotels():
    data = request.get_json(force=True)
    geo = data.get("geo")
    start = data.get("start_date")
    end = data.get("end_date")
    currency = data.get("currency","USD")
    budget = data.get("budget","moderate")

    provider = "deep-links"
    offers=[]
    if AMADEUS_KEY and AMADEUS_SECRET:
        try:
            ids = amadeus_hotels_by_geo(geo["lat"], geo["lon"], radius=10)
            offers = amadeus_hotel_offers(ids, start, end, currency_code=currency)
        except Exception:
            offers = []
        provider = "Amadeus" if offers else "Amadeus (no results)"

    if not offers:
        offers = demo_hotel_offers(geo["name"], start, end, currency=currency, budget=budget)
        provider = "demo-prices"

    return jsonify({"provider": provider, "offers": offers})

@app.post("/api/search/activities")
def api_activities():
    data = request.get_json(force=True)
    geo = data.get("geo")
    itinerary = data.get("itinerary", {})
    currency = data.get("currency","USD")
    provider = "deep-links"
    acts = []
    if GETYOURGUIDE_KEY:
        acts = getyourguide_activities(geo["lat"], geo["lon"], currency=currency, limit=12)
        provider = "GetYourGuide" if acts else "GetYourGuide (no results)"
    if not acts:
        acts = demo_activities_from_itinerary(itinerary, geo["name"], currency=currency)
        provider = "demo-prices"
    return jsonify({"provider": provider, "activities": acts})

# ───────────────── AI edit ─────────────────
@app.post("/api/ai-edit")
def api_ai_edit():
    payload = request.get_json(force=True)
    state = payload.get("state", {})
    msg = payload.get("message","")
    new_state, note = ai_edit(msg, state)
    return jsonify({"state": new_state, "note": note})

# ───────────────── Exports ─────────────────
def itinerary_to_ics_bytes(itinerary, tz="UTC"):
    cal = Calendar(); cal.add("prodid","-//AI Trip Planner//EN"); cal.add("version","2.0")
    for day in itinerary.get("days", []):
        date = day.get("date")
        for item in day.get("items", []):
            ev = Event(); hour = {"Morning":9,"Afternoon":13,"Evening":18}.get(item.get("slot","Morning"), 9)
            try:
                dt = datetime.fromisoformat(date).replace(hour=hour, minute=0)
            except Exception:
                continue
            ev.add("summary", f'{item.get("slot","")} : {item.get("name","")}')
            ev.add("dtstart", dt); ev.add("dtend", dt + timedelta(hours=2))
            ev.add("description", f'Category: {item.get("category","")}\nMaps: {item.get("maps_link","")}')
            cal.add_component(ev)
    return cal.to_ical()

def itinerary_to_csv_text(itinerary):
    rows = ["date,slot,name,category,maps_link"]
    for day in itinerary.get("days", []):
        for item in day.get("items", []):
            rows.append(f'{day.get("date","")},{item.get("slot","")},"{item.get("name","")}",{item.get("category","")},{item.get("maps_link","")}')
    return "\n".join(rows)

@app.post("/api/export/ics")
def api_export_ics():
    payload = request.get_json(force=True)
    ics = itinerary_to_ics_bytes(payload.get("itinerary", {}), tz=payload.get("tz","UTC"))
    resp = make_response(ics)
    resp.headers["Content-Type"] = "text/calendar"
    resp.headers["Content-Disposition"] = "attachment; filename=itinerary.ics"
    return resp

@app.post("/api/export/csv")
def api_export_csv():
    payload = request.get_json(force=True)
    csv_text = itinerary_to_csv_text(payload.get("itinerary", {}))
    resp = make_response(csv_text)
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=itinerary.csv"
    return resp

@app.post("/api/export/json")
def api_export_json():
    payload = request.get_json(force=True)
    resp = make_response(json.dumps(payload.get("itinerary", {}), ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=itinerary.json"
    return resp

# ───────────────── Frontend ─────────────────
HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Trip Planner</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link href="https://unpkg.com/modern-css-reset/dist/reset.min.css" rel="stylesheet">
<link href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" rel="stylesheet"/>
<style>
:root{ --bg:#f7f9fc; --card:#fff; --muted:#64748b; --text:#0f172a; --accent:#2563eb; --accent2:#fb923c; --border:#e5e7eb;}
body{ background:var(--bg); color:var(--text); font-family: ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,Helvetica Neue,Arial;}
.container{ max-width:1200px; margin:0 auto; padding:18px;}
.hero{ background: radial-gradient(1200px 420px at 10% -10%, rgba(37,99,235,.18), transparent 60%), linear-gradient(135deg, rgba(37,99,235,.06), rgba(251,146,60,.06)); border:1px solid var(--border); border-radius:18px; padding:18px; margin-bottom:14px;}
h1{ margin:0 0 8px 0; font-size:28px;}
.badges span{ display:inline-block; padding:4px 10px; margin:4px 6px 0 0; border:1px solid var(--border); background:#eef2ff; border-radius:999px; font-size:.85rem;}
.grid{ display:grid; gap:14px; grid-template-columns: 1.2fr 1fr;}
.card{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px;}
label{ font-weight:600; font-size:.9rem;}
input[type=text], input[type=date], select{ width:100%; padding:10px 12px; border:1px solid var(--border); border-radius:10px; background:white; margin:6px 0 12px 0;}
button.btn{ border:none; padding:10px 14px; border-radius:999px; font-weight:700; background:linear-gradient(90deg, var(--accent), var(--accent2)); color:white; cursor:pointer;}
button.btn.secondary{ background:#fff; color:#0f172a; border:1px solid var(--border);}
.small{ color:var(--muted); font-size:.92rem;}
.sticky{ position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--border); padding:10px 0; margin:10px 0; display:none;}
#map{ width:100%; height:340px; border-radius:10px; border:1px solid var(--border);}
.list{ padding-left:16px;}
.list li{ margin:6px 0;}
.flex{ display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
.row{ display:flex; gap:14px;}
.row > div{ flex:1;}
hr{ border:none; border-top:1px solid var(--border); margin:10px 0;}
.col3{ display:grid; gap:12px; grid-template-columns: repeat(3,1fr);}
.offer{ border:1px solid var(--border); border-radius:10px; padding:10px;}
.offer h4{ margin:0 0 6px 0;}
.tag{ display:inline-block; padding:2px 8px; background:#eef2ff; border:1px solid var(--border); border-radius:999px; margin-left:6px; font-size:.8rem;}
@media (max-width: 1000px){ .grid{ grid-template-columns: 1fr;} .col3{ grid-template-columns: 1fr;}}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1>🧭 AI Trip Planner</h1>
    <p class="small">Live data • AI edits • price-integrated flights & hotels • activities • live re-plan • seamless booking.</p>
    <div class="badges"><span>Amadeus Flights/Hotels</span><span>GetYourGuide</span><span>Overpass+Wikipedia</span><span>Budget Optimizer</span></div>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Plan</h3>
      <label>Destination</label>
      <input id="dest" type="text" placeholder="e.g., Kyoto, Paris, New York">
      <div class="row">
        <div><label>Start date</label><input id="start" type="date"></div>
        <div><label>End date</label><input id="end" type="date"></div>
      </div>
      <label>Origin (for flights)</label>
      <input id="origin" type="text" placeholder="e.g., Hyderabad, DEL, JFK">
      <div class="row">
        <div><label>Companions</label><select id="companions"><option>solo</option><option>couple</option><option>family</option><option>friends</option></select></div>
        <div><label>Budget</label><select id="budget"><option>tight</option><option selected>moderate</option><option>luxury</option></select></div>
      </div>
      <div class="row">
        <div><label>Currency</label><select id="currency"><option>USD</option><option>INR</option><option>EUR</option><option>GBP</option><option>JPY</option><option>AUD</option></select></div>
        <div><label>Radius (km)</label><input id="radius" type="text" value="12"></div>
      </div>
      <label>Interests</label>
      <div class="flex">
        <label><input type="checkbox" class="int" value="culture" checked> culture</label>
        <label><input type="checkbox" class="int" value="food" checked> food</label>
        <label><input type="checkbox" class="int" value="nature"> nature</label>
        <label><input type="checkbox" class="int" value="adventure"> adventure</label>
        <label><input type="checkbox" class="int" value="nightlife"> nightlife</label>
        <label><input type="checkbox" class="int" value="shopping"> shopping</label>
        <label><input type="checkbox" class="int" value="family"> family</label>
        <label><input type="checkbox" class="int" value="architecture"> architecture</label>
        <label><input type="checkbox" class="int" value="photography"> photography</label>
      </div>
      <hr/>
      <div class="flex">
        <label><input id="optimize" type="checkbox" checked> Optimize per-day route</label>
        <label><input id="cap_enabled" type="checkbox"> Budget cap per day</label>
        <input id="cap_value" type="text" placeholder="e.g., 100 (USD)">
      </div>
      <br/>
      <div class="flex">
        <button class="btn" onclick="generate()">Generate Itinerary</button>
        <button class="btn secondary" onclick="demo()">Demo (Paris)</button>
      </div>
      <p id="status" class="small"></p>

      <hr/>
      <h3>AI / Natural edit</h3>
      <p class="small">Try: <kbd>prefer museums and cafes</kbd>, <kbd>budget to luxury</kbd>, <kbd>radius to 18</kbd></p>
      <div class="row">
        <input id="ai_text" type="text" placeholder="Describe your change...">
        <button class="btn secondary" onclick="sendAI()">Apply</button>
      </div>
      <p id="ai_note" class="small"></p>
    </div>

    <div class="card">
      <h3>Map</h3>
      <div id="map"></div>
      <p class="small">Click a day to update markers for that day.</p>
    </div>
  </div>

  <div class="sticky" id="sticky">
    <div class="flex">
      <div id="stickySummary" class="small"></div>
      <span id="poiInfo" class="tag"></span>
      <span id="provStatus" class="tag"></span>
      <button class="btn secondary" onclick="replan()">Live Re-plan</button>
      <button class="btn secondary" onclick="exportFile('ics')">ICS</button>
      <button class="btn secondary" onclick="exportFile('csv')">CSV</button>
      <button class="btn secondary" onclick="exportFile('json')">JSON</button>
    </div>
  </div>

  <div id="output"></div>

  <div id="commerce" class="card" style="display:none;">
    <h3>Search & Book</h3>
    <div class="row">
      <div class="card" style="flex:1;">
        <h4>✈️ Flights <span id="flProv" class="small"></span></h4>
        <div id="flights" class="col3"></div>
      </div>
      <div class="card" style="flex:1;">
        <h4>🏨 Hotels <span id="hoProv" class="small"></span></h4>
        <div id="hotels" class="col3"></div>
      </div>
    </div>
    <div class="card">
      <h4>🎟️ Activities <span id="acProv" class="small"></span></h4>
      <div id="acts" class="col3"></div>
    </div>
    <hr/>
    <h4>Review & Book</h4>
    <div id="cart"></div>
  </div>

  <footer class="small" style="margin-top:12px;">Data: Open-Meteo, OSM Overpass, Wikipedia; Amadeus Flights/Hotels; GetYourGuide (optional). Demo prices show when provider APIs aren’t configured.</footer>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
let MAP, MARKERS=[], RESP=null, STATE=null, CART={flight:null, hotel:null, activities:[]};

function initMap(){ MAP = L.map('map').setView([20, 0], 2);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(MAP);}
initMap();
function setMarkers(items, center){ MARKERS.forEach(m=>MAP.removeLayer(m)); MARKERS=[];
  if(center){ MAP.setView(center, 12); }
  (items||[]).forEach(i=>{ if(i.lat && i.lon){ const m=L.marker([i.lat,i.lon]).addTo(MAP).bindPopup(`<b>${i.name}</b><br>${i.category||''}`); MARKERS.push(m); }});
}

function getVals(){
  const ints=Array.from(document.querySelectorAll('.int:checked')).map(i=>i.value);
  return {
    destination: document.getElementById('dest').value.trim(),
    origin: document.getElementById('origin').value.trim(),
    start_date: document.getElementById('start').value,
    end_date: document.getElementById('end').value,
    companions: document.getElementById('companions').value,
    budget: document.getElementById('budget').value,
    currency: document.getElementById('currency').value,
    radius_km: parseInt(document.getElementById('radius').value || '12'),
    interests: ints.length?ints:["culture","food"],
    optimize: document.getElementById('optimize')?document.getElementById('optimize').checked:true,
    cap_enabled: document.getElementById('cap_enabled')?document.getElementById('cap_enabled').checked:false,
    cap_value: parseFloat(document.getElementById('cap_value')?document.getElementById('cap_value').value:'0')
  };
}

function demo(){
  document.getElementById('dest').value="Paris";
  const t=new Date(); const s=new Date(t.getFullYear(),t.getMonth(),t.getDate()+14);
  const e=new Date(t.getFullYear(),t.getMonth(),t.getDate()+17);
  document.getElementById('start').value=s.toISOString().slice(0,10);
  document.getElementById('end').value=e.toISOString().slice(0,10);
  document.getElementById('origin').value="Hyderabad";
  document.getElementById('radius').value="12";
  document.querySelectorAll('.int').forEach(i=>i.checked=false);
  ["culture","food","photography"].forEach(v=>{const el=[...document.querySelectorAll('.int')].find(i=>i.value===v); if(el) el.checked=true;});
  generate();
}

async function generate(){
  const vals=getVals(); STATE=JSON.parse(JSON.stringify(vals));
  const s=document.getElementById('status'); s.textContent="Planning your trip...";
  try{
    const res=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(vals)});
    const data=await res.json(); if(!res.ok){ s.textContent=data.error||"Error"; return; }
    RESP=data; s.textContent="Done."; renderPlan(vals); document.getElementById('commerce').style.display='block';
    await searchCommerce(vals);
  }catch(e){ s.textContent="Network error."; }
}

function renderPlan(vals){
  const out=document.getElementById('output'); const g=RESP.geo; const w=RESP.weather; const itin=RESP.itinerary;
  document.getElementById('sticky').style.display='block';
  document.getElementById('stickySummary').textContent=`${g.name}, ${g.country||''} — ${itin.days.length}d — ${itin.meta.companions} • ${itin.meta.budget} • ${RESP.currency}`;
  document.getElementById('poiInfo').textContent=`${RESP.poi_count} places • ${RESP.sources_used.join(' + ')}`;
  const ps = RESP.provider_status || {};
  document.getElementById('provStatus').textContent = `Providers: Flights/Hotels=${ps.amadeus?'Amadeus':'demo'} • Activities=${ps.getyourguide?'GetYourGuide':'demo'}`;
  setMarkers([], [g.lat,g.lon]);
  let html = `<div class="card"><h3>Weather (daily)</h3><ul class="list">${
    (w.time||[]).length?(w.time||[]).map((d,i)=>`<li><b>${d}</b> — Max ${(w.temperature_2m_max||[])[i]??"?"}°C, Min ${(w.temperature_2m_min||[])[i]??"?"}°C, Precip ${(w.precipitation_sum||[])[i]??"?"}mm</li>`).join(''):'<li class="small">No weather data.</li>'}</ul></div>`;
  html += `<div class="card"><h3>Your Itinerary</h3>`;
  itin.days.forEach((day,idx)=>{
    const items=day.items||[];
    html += `<div class="card" style="margin-bottom:10px;">
      <div class="row" style="justify-content:space-between;align-items:center;">
        <h4 style="margin:0;cursor:pointer;" onclick="showDay(${idx})">Day ${idx+1} — ${day.date}</h4>
        <span class="small">Estimated: ${day.estimated_cost||0} ${RESP.currency}</span>
      </div>
      ${items.length?`<ul class="list">`+items.map(it=>{
        const icons={"food":"🍽️","culture":"🏛️","nature":"🌿","adventure":"🎢","shopping":"🛍️","nightlife":"🌙","family":"🧸","photography":"📸","architecture":"🏗️"};
        const ic=icons[it.category||"general"]||"📍";
        return `<li>${ic} <b>${it.slot}</b>: ${it.name} <i>#${it.category||""}</i> — <a href="${it.maps_link}" target="_blank">Map</a></li>`;}).join('')+`</ul>`:`<p class="small">No items for this day.</p>`}
    </div>`;
  });
  html += `</div>`;
  out.innerHTML=html;
}

function showDay(idx){ const day=(RESP?.itinerary?.days||[])[idx]; if(!day) return; const g=RESP.geo; setMarkers(day.items,[g.lat,g.lon]); }

async function replan(){
  const payload={itinerary:RESP.itinerary, geo:RESP.geo, currency:RESP.currency, budget:STATE.budget, optimize:STATE.optimize};
  const res=await fetch('/api/replan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await res.json(); RESP.itinerary=data.itinerary; RESP.weather=data.weather; renderPlan(getVals());
}

async function exportFile(kind){
  if(!RESP) return; const payload={itinerary:RESP.itinerary, tz:RESP.geo?.timezone||"UTC"};
  const url = kind==="ics"?"/api/export/ics":(kind==="csv"?"/api/export/csv":"/api/export/json");
  const res=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const blob=await res.blob(); const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download=kind==="ics"?"itinerary.ics":(kind==="csv"?"itinerary.csv":"itinerary.json");
  document.body.appendChild(a); a.click(); a.remove();
}

async function sendAI(){
  if(!RESP) return;
  const text=document.getElementById('ai_text').value; if(!text) return;
  const res=await fetch('/api/ai-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:STATE,message:text})});
  const data=await res.json(); STATE=data.state; document.getElementById('ai_note').textContent=data.note;
  document.getElementById('budget').value=STATE.budget;
  document.getElementById('radius').value=STATE.radius_km;
  await generate();
}

async function searchCommerce(vals){
  if(!RESP) return;
  // Flights
  const fr=await fetch('/api/search/flights',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({origin:vals.origin,destination:RESP.geo.name,start_date:vals.start_date,end_date:vals.end_date,currency:vals.currency})});
  const fdata=await fr.json(); document.getElementById('flProv').textContent = `(${fdata.provider})`;
  const fl=document.getElementById('flights'); fl.innerHTML='';
  fdata.offers.forEach((o,i)=>{
    const div=document.createElement('div'); div.className='offer';
    div.innerHTML=`<h4>${o.carriers||'Flight'}</h4><div class="small">${o.duration||''}</div><div><b>${o.price?o.price+' '+(o.currency||''): 'See provider'}</b></div>
      <div class="flex" style="margin-top:6px;">
        <button class="btn secondary" onclick='addFlight(${JSON.stringify(JSON.stringify(o))})'>Add to Trip</button>
        <a class="btn" target="_blank" href="${o.deeplink}">Book</a></div>`;
    fl.appendChild(div);
  });

  // Hotels
  const hr=await fetch('/api/search/hotels',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({geo:RESP.geo,start_date:vals.start_date,end_date:vals.end_date,currency:vals.currency,budget:vals.budget})});
  const hdata=await hr.json(); document.getElementById('hoProv').textContent = `(${hdata.provider})`;
  const ho=document.getElementById('hotels'); ho.innerHTML='';
  hdata.offers.forEach(o=>{
    const div=document.createElement('div'); div.className='offer';
    div.innerHTML=`<h4>${o.name}</h4><div><b>${o.price?o.price+' '+(o.currency||''): 'See provider'}</b></div>
      <div class="flex" style="margin-top:6px;">
        <button class="btn secondary" onclick='addHotel(${JSON.stringify(JSON.stringify(o))})'>Add to Trip</button>
        <a class="btn" target="_blank" href="${o.deeplink}">Book</a></div>`;
    ho.appendChild(div);
  });

  // Activities
  const ar=await fetch('/api/search/activities',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({geo:RESP.geo,itinerary:RESP.itinerary,currency:vals.currency})});
  const adata=await ar.json(); document.getElementById('acProv').textContent = `(${adata.provider})`;
  const ac=document.getElementById('acts'); ac.innerHTML='';
  adata.activities.forEach(a=>{
    const div=document.createElement('div'); div.className='offer';
    div.innerHTML=`<h4>${a.title}</h4><div><b>${a.price?a.price+' '+(a.currency||''):'See provider'}</b></div>
      <div class="flex" style="margin-top:6px;">
        <button class="btn secondary" onclick='addActivity(${JSON.stringify(JSON.stringify(a))})'>Add to Trip</button>
        <a class="btn" target="_blank" href="${a.deeplink}">Book</a></div>`;
    ac.appendChild(div);
  });
  renderCart();
}

function addFlight(jsonStr){ CART.flight=JSON.parse(jsonStr); renderCart(); }
function addHotel(jsonStr){ CART.hotel=JSON.parse(jsonStr); renderCart(); }
function addActivity(jsonStr){ const a=JSON.parse(jsonStr); CART.activities.push(a); renderCart(); }

function renderCart(){
  const c=document.getElementById('cart');
  const cur = (RESP && RESP.currency) || 'USD';
  const parts=[];
  let total=0.0;

  if(CART.flight){ parts.push(`<div>✈️ <b>Flight:</b> ${CART.flight.carriers||''} — ${CART.flight.price?CART.flight.price+' '+(CART.flight.currency||cur):'see provider'}</div>`);
    if(CART.flight.price) total += parseFloat(CART.flight.price||'0'); }
  if(CART.hotel){ parts.push(`<div>🏨 <b>Hotel:</b> ${CART.hotel.name} — ${CART.hotel.price?CART.hotel.price+' '+(CART.hotel.currency||cur):'see provider'}</div>`);
    if(CART.hotel.price) total += parseFloat(CART.hotel.price||'0'); }
  if(CART.activities.length){
    const sum=CART.activities.reduce((s,a)=>s+(parseFloat(a.price||'0')||0),0);
    parts.push(`<div>🎟️ <b>Activities:</b> ${CART.activities.length} item(s) — ${sum>0?sum+' '+(CART.activities[0].currency||cur):'see provider'}</div>`);
    total += sum;
  }
  parts.push(`<hr/><div><b>Trip total (shown prices):</b> ${total>0?total.toFixed(2)+' '+cur:'varies by provider'}</div>`);
  c.innerHTML = parts.join('');
}
</script>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(HTML)

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 7860))
    app.run(host="0.0.0.0", port=port, debug=False)

