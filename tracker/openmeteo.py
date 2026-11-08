"""Enrichissement météo/mer à la dernière position via Open-Meteo (sans clé API).

- Forecast : vent à 10 m (nœuds), rafales, température de l'air, code météo,
  et surtout le fuseau horaire du point → heure locale à bord.
- Marine : hauteur/direction/période des vagues et de la houle, température de l'eau.
  Près des côtes, la grille marine peut renvoyer null : on le tolère.

Chaque appel est indépendant : l'échec de l'un n'empêche pas l'autre.
"""
from __future__ import annotations

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

FORECAST_VARS = [
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "temperature_2m", "weather_code", "is_day",
]
MARINE_VARS = [
    "wave_height", "wave_direction", "wave_period",
    "swell_wave_height", "swell_wave_direction", "swell_wave_period",
    "sea_surface_temperature",
]


class WeatherError(Exception):
    pass


def _get(url: str, params: dict, session: requests.Session, timeout: float) -> dict:
    try:
        r = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise WeatherError(f"réseau : {e}") from e
    if r.status_code != 200:
        raise WeatherError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
    except ValueError as e:
        raise WeatherError("réponse non JSON") from e
    if not isinstance(data.get("current"), dict):
        raise WeatherError("bloc 'current' absent")
    return data


def fetch_forecast(lat: float, lon: float, session: requests.Session, timeout: float = 20) -> dict:
    d = _get(FORECAST_URL, {
        "latitude": lat, "longitude": lon,
        "current": ",".join(FORECAST_VARS),
        "wind_speed_unit": "kn", "timezone": "auto",
        "daily": "sunrise,sunset", "forecast_days": 1,
    }, session, timeout)
    c = d["current"]
    daily = d.get("daily") if isinstance(d.get("daily"), dict) else {}
    first = lambda k: (daily.get(k) or [None])[0]  # noqa: E731
    return {
        "sunrise": first("sunrise"),   # heure locale à bord, ISO « 2026-11-09T06:05 »
        "sunset": first("sunset"),
        "time_utc_offset_s": d.get("utc_offset_seconds"),
        "timezone": d.get("timezone"),
        "timezone_abbr": d.get("timezone_abbreviation"),
        "wind_kn": c.get("wind_speed_10m"),
        "wind_dir": c.get("wind_direction_10m"),
        "gust_kn": c.get("wind_gusts_10m"),
        "air_temp_c": c.get("temperature_2m"),
        "weather_code": c.get("weather_code"),
        "is_day": c.get("is_day"),
        "observed_at": c.get("time"),
    }


def fetch_marine(lat: float, lon: float, session: requests.Session, timeout: float = 20) -> dict:
    d = _get(MARINE_URL, {
        "latitude": lat, "longitude": lon,
        "current": ",".join(MARINE_VARS),
        "timezone": "auto",
    }, session, timeout)
    c = d["current"]
    out = {
        "wave_m": c.get("wave_height"),
        "wave_dir": c.get("wave_direction"),
        "wave_period_s": c.get("wave_period"),
        "swell_m": c.get("swell_wave_height"),
        "swell_dir": c.get("swell_wave_direction"),
        "swell_period_s": c.get("swell_wave_period"),
        "water_temp_c": c.get("sea_surface_temperature"),
        "observed_at": c.get("time"),
    }
    if all(v is None for k, v in out.items() if k != "observed_at"):
        raise WeatherError("aucune donnée marine à ce point (côte / hors grille)")
    return out
