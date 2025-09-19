from typing import List, Dict, Tuple
import math

def order_nearest_neighbor(items: List[Dict], center: Tuple[float,float]) -> List[int]:
    """Heuristic: start near city center, then nearest-neighbor chaining. No external API calls."""
    if not items: return []
    def hav(a,b,c,d):
        R=6371; dlat=math.radians(c-a); dlon=math.radians(d-b)
        h=math.sin(dlat/2)**2+math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(dlon/2)**2
        return 2*R*math.asin(math.sqrt(h))
    clats = [it.get("lat") for it in items]; clons = [it.get("lon") for it in items]
    lat0, lon0 = center
    # choose start as closest to center (skip None coords gracefully)
    def dist_to_center(i):
        la = clats[i] if clats[i] is not None else lat0
        lo = clons[i] if clons[i] is not None else lon0
        return hav(lat0, lon0, la, lo)
    start_idx = min(range(len(items)), key=lambda i: dist_to_center(i))
    unvisited = set(range(len(items))); order=[start_idx]; unvisited.remove(start_idx); cur=start_idx
    while unvisited:
        def d(i,j):
            ai = clats[i] if clats[i] is not None else lat0
            bi = clons[i] if clons[i] is not None else lon0
            aj = clats[j] if clats[j] is not None else lat0
            bj = clons[j] if clons[j] is not None else lon0
            return hav(ai, bi, aj, bj)
        nxt = min(unvisited, key=lambda j: d(cur, j))
        order.append(nxt); unvisited.remove(nxt); cur = nxt
    return order
