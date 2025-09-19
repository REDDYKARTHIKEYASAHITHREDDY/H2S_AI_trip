from urllib.parse import quote_plus
from typing import Dict

def flight_link(origin: str, destination: str, depart: str, return_date: str = None) -> Dict:
    origin = origin or "Your city"
    q = f"Flights from {origin} to {destination} on {depart}" + (f" returning {return_date}" if return_date else "")
    gflights = "https://www.google.com/travel/flights?q=" + quote_plus(q)
    if return_date:
        kiwi = f"https://www.kiwi.com/en/search/results/{quote_plus(origin)}-{quote_plus(destination)}/{depart}/{return_date}"
    else:
        kiwi = f"https://www.kiwi.com/en/search/results/{quote_plus(origin)}-{quote_plus(destination)}/{depart}"
    return {"google_flights": gflights, "kiwi": kiwi}

def hotel_link(city: str, checkin: str, checkout: str) -> str:
    return f"https://www.booking.com/searchresults.html?ss={quote_plus(city)}&checkin={checkin}&checkout={checkout}"

def activities_link(city: str, query: str = "") -> str:
    q = quote_plus(query) if query else quote_plus(city)
    return f"https://www.getyourguide.com/s/?q={q}"

def restaurants_link(city: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus('restaurants in ' + city)}"

def book_link_for_item(city: str, name: str, category: str) -> str:
    if category in {"culture","adventure","family","photography","architecture","general","nature"}:
        return activities_link(city, name)
    if category in {"food","nightlife","shopping"}:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(name + ' ' + city)}"
    return activities_link(city, name)
