from typing import Dict
from datetime import datetime, timedelta
from icalendar import Calendar, Event

def itinerary_to_ics(data: Dict, tz: str) -> bytes:
    cal = Calendar(); cal.add("prodid", "-//AI Trip Planner//EN"); cal.add("version", "2.0")
    for day in data.get("days", []):
        date = day.get("date")
        for item in day.get("items", []):
            ev = Event(); hour = {"Morning":9, "Afternoon":13, "Evening":18}.get(item.get("slot","Morning"), 9)
            try:
                dt = datetime.fromisoformat(date).replace(hour=hour, minute=0)
            except Exception:
                continue
            ev.add("summary", f'{item.get("slot","")} : {item.get("name","")}')
            ev.add("dtstart", dt); ev.add("dtend", dt + timedelta(hours=2))
            ev.add("description", f'Category: {item.get("category","")}\nMaps: {item.get("maps_link","")}')
            cal.add_component(ev)
    return cal.to_ical()

def itinerary_to_csv(data: Dict) -> str:
    rows = ["date,slot,name,category,maps_link"]
    for day in data.get("days", []):
        for item in day.get("items", []):
            rows.append(f'{day.get("date","")},{item.get("slot","")},"{item.get("name","")}",{item.get("category","")},{item.get("maps_link","")}')
    return "\n".join(rows)
