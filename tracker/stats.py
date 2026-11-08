"""Stats ludiques et détection escale / navigation, à partir de la trace.

Entrée : liste de points [t, lat, lon, bearing, bsp] (format de state.json).
Sortie : un dict sérialisable en JSON, consommé par le rendu (piste B « Carte marine »).

Règles (cf. spec UX dans claude/ressources-projet.md) :
- Segment « à l'arrêt » : vitesse sur le fond < STOP_SPEED_KN, ou déplacement < 1 mille.
- Escale : suite de segments à l'arrêt durant au moins STOP_MIN_H heures.
  Deux escales consécutives à moins de MERGE_NM milles sont fusionnées (changement de
  mouillage dans la même baie).
- Mode escale : le bateau est immobile depuis au moins CURRENT_STOP_MIN_H heures.
- Segments à plus de GLITCH_KN nœuds : saut GPS, exclus des distances.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_NM = 21600.0           # circonférence terrestre en milles nautiques
NM_KM = 1.852
MARATHON_KM = 42.195
HOME = (43.5, 4.0)           # maison du destinataire : sud de la France (décision UX 08/11)

STOP_SPEED_KN = 0.6
STOP_MIN_H = 24
CURRENT_STOP_MIN_H = 6
MERGE_NM = 5.0
GLITCH_KN = 30.0


def haversine_nm(lat1, lon1, lat2, lon2) -> float:
    r = 3440.065  # rayon terrestre en milles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def initial_bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


@dataclass
class Stop:
    start_t: int
    end_t: int
    lat: float
    lon: float
    first_idx: int
    last_idx: int

    @property
    def hours(self) -> float:
        return (self.end_t - self.start_t) / 3600

    def to_dict(self) -> dict:
        return {"start_t": self.start_t, "end_t": self.end_t, "lat": round(self.lat, 4),
                "lon": round(self.lon, 4), "days": round(self.hours / 24, 1)}


def _segments(pts: list[list]) -> list[tuple[float, float, bool, bool]]:
    """Pour chaque segment i→i+1 : (milles, heures, à_l'arrêt, aberrant)."""
    out = []
    for a, b in zip(pts, pts[1:]):
        d = haversine_nm(a[1], a[2], b[1], b[2])
        h = max((b[0] - a[0]) / 3600, 1e-6)
        sog = d / h
        glitch = sog > GLITCH_KN and d > 2
        stopped = not glitch and (sog < STOP_SPEED_KN or d < 1.0)
        out.append((0.0 if glitch else d, h, stopped, glitch))
    return out


def detect_stops(pts: list[list], min_hours: float = STOP_MIN_H) -> list[Stop]:
    segs = _segments(pts)
    runs: list[Stop] = []
    i = 0
    while i < len(segs):
        if not segs[i][2]:
            i += 1
            continue
        j = i
        while j + 1 < len(segs) and segs[j + 1][2]:
            j += 1
        first, last = i, j + 1  # indices de points
        lats = sorted(p[1] for p in pts[first:last + 1])
        lons = sorted(p[2] for p in pts[first:last + 1])
        mid = len(lats) // 2
        runs.append(Stop(pts[first][0], pts[last][0], lats[mid], lons[mid], first, last))
        i = j + 1

    # fusion des arrêts proches (même baie), puis filtre de durée
    merged: list[Stop] = []
    for r in runs:
        if merged and haversine_nm(merged[-1].lat, merged[-1].lon, r.lat, r.lon) < MERGE_NM:
            m = merged[-1]
            merged[-1] = Stop(m.start_t, r.end_t, m.lat, m.lon, m.first_idx, r.last_idx)
        else:
            merged.append(r)
    return [s for s in merged if s.hours >= min_hours]


def cardinal_fr(deg: float | None) -> str:
    if deg is None:
        return ""
    names = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return names[int(((deg % 360) + 22.5) // 45) % 8]


def compute(points: list[list], now: int) -> dict:
    if len(points) < 2:
        return {"mode": "inconnu"}

    segs = _segments(points)
    cum = [0.0]
    for d, *_ in segs:
        cum.append(cum[-1] + d)
    total_nm = cum[-1]
    first, last = points[0], points[-1]

    # Escales détectées (≥ 24 h) + escale en cours éventuelle (≥ 6 h)
    stops = detect_stops(points)
    tail = detect_stops(points, min_hours=CURRENT_STOP_MIN_H)
    current = tail[-1] if tail and tail[-1].last_idx == len(points) - 1 else None
    mode = "escale" if current else "navigation"

    # Cap et vitesse : sur les 3 dernières heures de trace (plus robuste que le
    # champ bearing, bruité à l'arrêt) ; bsp du dernier point s'il est présent.
    k = len(points) - 1
    while k > 0 and last[0] - points[k - 1][0] <= 3 * 3600:
        k -= 1
    ref = points[k] if k < len(points) - 1 else points[-2]
    d_recent = haversine_nm(ref[1], ref[2], last[1], last[2])
    h_recent = max((last[0] - ref[0]) / 3600, 1e-6)
    course = initial_bearing(ref[1], ref[2], last[1], last[2]) if d_recent > 0.2 else last[3]
    speed = last[4] if last[4] is not None else d_recent / h_recent

    # Moyenne sur 24 h
    j = len(points) - 1
    while j > 0 and last[0] - points[j - 1][0] <= 24 * 3600:
        j -= 1
    span_h = (last[0] - points[j][0]) / 3600
    avg24 = (total_nm - cum[j]) / span_h if span_h >= 1 else None

    out = {
        "mode": mode,
        "total_nm": round(total_nm),
        "total_km": round(total_nm * NM_KM),
        "marathons": round(total_nm * NM_KM / MARATHON_KM),
        "earth_pct": round(100 * total_nm / EARTH_NM),
        "voyage_day": (now - first[0]) // 86400 + 1,
        "departure": {"t": first[0], "lat": first[1], "lon": first[2]},
        "position": {"t": last[0], "lat": last[1], "lon": last[2]},
        "course": None if course is None else round(course),
        "course_card": cardinal_fr(course),
        "speed_kn": None if speed is None else round(speed, 1),
        "avg24_kn": None if avg24 is None else round(avg24, 1),
        "home_km": round(haversine_nm(HOME[0], HOME[1], last[1], last[2]) * NM_KM, -2),
        "stops": [s.to_dict() for s in stops],
    }

    if current:
        out["current_stop"] = current.to_dict()
        out["stop_days"] = max(0, (now - current.start_t) // 86400)
        # l'escale en cours n'est pas une escale « passée »
        out["stops"] = [s.to_dict() for s in stops if s.last_idx != current.last_idx]
    else:
        prev = stops[-1] if stops else None
        start_idx = prev.last_idx if prev else 0
        out["leg"] = {
            "since_t": points[start_idx][0],
            "days": max(0, (now - points[start_idx][0]) // 86400),
            "nm": round(total_nm - cum[start_idx]),
            "from_stop": prev.to_dict() if prev else None,
        }
    return out
