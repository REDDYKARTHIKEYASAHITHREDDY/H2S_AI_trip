from typing import List, Dict
from datetime import datetime, timedelta
import random

SLOTS = ["Morning", "Afternoon", "Evening"]

def score_place(place: Dict, interests: List[str]) -> float:
    base = 1.0
    try:
        if place.get("category") in interests: base += 1.5
        tags_str = str(place.get("tags", "")).lower()
        if "park" in tags_str and "nature" in interests: base += 0.3
        if "museum" in tags_str and "culture" in interests: base += 0.3
    except Exception:
        pass
    return base + random.uniform(0, 0.2)

def plan_itinerary(city: str, start_date: str, end_date: str, companions: str, budget: str, interests: List[str], pois: List[Dict]) -> Dict:
    try:
        start = datetime.fromisoformat(start_date); end = datetime.fromisoformat(end_date)
    except Exception:
        start = datetime.today(); end = start
    days = max((end - start).days + 1, 1)
    ranked = sorted(pois or [], key=lambda p: score_place(p, interests), reverse=True)
    used = set(); plan = []; idx = 0
    for d in range(days):
        day_date = start + timedelta(days=d); entries = []
        for slot in SLOTS:
            chosen = None
            for k in range(idx, len(ranked)):
                cand = ranked[k]; key = (str(cand.get("name","")).lower(), cand.get("category","general"))
                if key in used: continue
                if entries and cand.get("category") == entries[-1].get("category"): continue
                chosen = cand; idx = k + 1; break
            if chosen:
                used.add((str(chosen.get("name","")).lower(), chosen.get("category","general")))
                entries.append({"slot": slot, "name": chosen.get("name","Place"), "category": chosen.get("category","general"),
                                "lat": chosen.get("lat"), "lon": chosen.get("lon"), "maps_link": chosen.get("maps_link")})
        plan.append({"date": day_date.date().isoformat(), "items": entries})
    meta = {"city": city, "companions": companions, "budget": budget, "interests": interests}
    return {"meta": meta, "days": plan}
