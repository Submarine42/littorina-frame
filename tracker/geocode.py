"""Noms des escales par géocodage inverse Nominatim (OpenStreetMap).

Politique d'usage respectée (https://operations.osmfoundation.org/policies/nominatim/) :
- au plus 1 requête par seconde, User-Agent identifiant le projet ;
- une seule requête par nouvelle escale : le résultat est mis en cache dans state.json
  (clé = coordonnées arrondies à 0,01°), y compris les échecs « sans nom » ;
- plafond de MAX_PER_RUN requêtes par exécution (le premier passage rattrape l'historique
  sur plusieurs heures si besoin).
Nom retenu : ville / village / localité, sinon île, sinon région ; à défaut, coordonnées.
"""
from __future__ import annotations

import time

import requests

URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "littorina-frame/1.0 (+https://github.com/Submarine42/littorina-frame)"
MAX_PER_RUN = 25
NAME_KEYS = ("city", "town", "village", "hamlet", "suburb", "municipality",
             "island", "archipelago", "county", "state_district", "state", "region")


def place_key(lat: float, lon: float) -> str:
    return f"{lat:.2f},{lon:.2f}"


def format_coords(lat: float, lon: float) -> str:
    def dm(v: float, pos: str, neg: str) -> str:
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = round((v - d) * 60)
        if m == 60:
            d, m = d + 1, 0
        return f"{d}° {m:02d}′ {h}"
    return f"{dm(lat, 'N', 'S')} · {dm(lon, 'E', 'O')}"


def _lookup(lat: float, lon: float, session: requests.Session) -> dict:
    r = session.get(URL, params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10,
                                 "addressdetails": 1, "accept-language": "fr"},
                    headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    data = r.json()
    addr = data.get("address") or {}
    name = next((addr[k] for k in NAME_KEYS if addr.get(k)), None)
    return {"name": name, "country": addr.get("country")}


def resolve(places: dict, coords: list[tuple[float, float]], session: requests.Session,
            sleep=time.sleep) -> int:
    """Complète le cache `places` pour les coordonnées manquantes. Retourne le nb de requêtes."""
    done = 0
    for lat, lon in coords:
        key = place_key(lat, lon)
        if key in places:
            continue
        if done >= MAX_PER_RUN:
            break
        if done:
            sleep(1.1)
        done += 1
        try:
            places[key] = _lookup(lat, lon, session)
        except (requests.RequestException, ValueError):
            # Échec réseau : on ne met rien en cache, on réessaiera au prochain passage.
            continue
    return done


def label(places: dict, lat: float, lon: float) -> tuple[str, str | None]:
    """(nom affiché, pays) — repli sur les coordonnées si le lieu est inconnu."""
    p = places.get(place_key(lat, lon)) or {}
    return (p.get("name") or format_coords(lat, lon)), p.get("country")
