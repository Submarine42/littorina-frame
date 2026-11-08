"""Rendu de l'écran (piste B « Carte marine ») → PNG 800 × 480 pour le cadre e-ink.

Lit uniquement data/state.json et produit dans le dossier de sortie (publié sur GitHub Pages) :
  frame.png          image pour l'écran, réduite aux couleurs pures de la palette (sans tramage)
  frame-preview.png  même image lissée, pour les humains
  meta.json          horodatages et statut
  index.html         page d'aperçu

Fidèle aux maquettes du canvas « Cadre e-ink voilier — pistes » (artboards B-Navigation et
B-Escale) : fond de carte Natural Earth, trace rouge, escales nommées, étiquette jaune,
cartouche à double filet, encadré instruments. Ajout : ligne d'état « à jour / position du »
au centre bas, seul indice pour le destinataire que le cadre n'est pas figé.

Usage : python -m tracker.render --state data/state.json --out public [--now 1794200000]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import geocode
from .stats import cardinal_fr

W, H = 800, 480
LON0, LAT1 = 130.0, 28.0
K = W / 180.0
ASSETS = Path(__file__).resolve().parent.parent / "assets"
HOME_TZ = ZoneInfo("Europe/Paris")
STALE_AFTER_S = 6 * 3600

BLACK, WHITE, RED, YELLOW, BLUE = "#000000", "#FFFFFF", "#D0021B", "#FFD400", "#1E50A2"
PALETTE = [(0, 0, 0), (255, 255, 255), (208, 2, 27), (255, 212, 0), (30, 80, 162)]

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
JOURS_ABR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS_ABR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.",
            "nov.", "déc."]

# Encarts fixes (obstacles pour les étiquettes) : x0, y0, x1, y1
BOX_CARTOUCHE = (16, 334, 318, 466)
BOX_INSTR = (514, 328, 786, 466)
BOX_COMPASS = (744, 92, 784, 132)
BOX_STATUS = (318, 452, 514, 478)


# ---------- mise en forme française ----------

def fint(n) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def fdec(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def hm(dt: datetime) -> str:
    return f"{dt.hour} h {dt.minute:02d}"


def day_month(dt: datetime) -> str:
    return f"{dt.day} {MOIS_ABR[dt.month - 1]}"


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# ---------- géométrie ----------

def project(lat: float, lon: float) -> tuple[float, float]:
    lon360 = lon + 360 if lon < 0 else lon
    return (lon360 - LON0) * K, (LAT1 - lat) * K


def simplify(pts: list[tuple[float, float]], tol: float = 0.7) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        md, mi = 0.0, -1
        for i in range(a + 1, b):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L
            if d > md:
                md, mi = d, i
        if md > tol and mi > 0:
            keep[mi] = True
            stack += [(a, mi), (mi, b)]
    return [p for p, k in zip(pts, keep) if k]


def _hit(r, obst) -> bool:
    if r[0] < 4 or r[2] > W - 4 or r[1] < 4 or r[3] > H - 4:
        return True
    return any(not (r[2] < o[0] or r[0] > o[2] or r[3] < o[1] or r[1] > o[3]) for o in obst)


def place_stops(stops: list[dict], trace_px: list[tuple[float, float]], extra_obst: list,
                radius: float = 28) -> tuple[list[dict], list[dict]]:
    """Regroupement + placement des étiquettes (portage de claude/ux-escales-algo.js)."""
    order = sorted(stops, key=lambda e: (0 if e["type"] else 1, -e["days"]))
    shown: list[dict] = []
    for e in order:
        if not any(math.hypot(s["x"] - e["x"], s["y"] - e["y"]) < radius for s in shown):
            shown.append(e)

    obst = list(extra_obst)
    obst += [(s["x"] - 8, s["y"] - 8, s["x"] + 8, s["y"] + 8) for s in shown]
    for (ax, ay), (bx, by) in zip(trace_px, trace_px[1:]):
        n = max(1, math.ceil(math.hypot(bx - ax, by - ay) / 5))
        for j in range(n + 1):
            x, y = ax + (bx - ax) * j / n, ay + (by - ay) * j / n
            obst.append((x - 3, y - 3, x + 3, y + 3))

    labels = []
    for s in shown:
        fs = 16 if s["type"] == "en-cours" else 14
        w, h = len(s["name"]) * fs * 0.52, fs
        x, y = s["x"], s["y"]
        if s["type"] == "en-cours":
            off = 25  # marqueur d'escale plus gros (r = 17)
            cands = [(x + off, y + 6, "start", (x + off - 2, y - h + 6, x + off + w, y + 8)),
                     (x - off, y + 6, "end", (x - off - w, y - h + 6, x - off + 2, y + 8)),
                     (x, y - off, "middle", (x - w / 2, y - off - h, x + w / 2, y - off + 2)),
                     (x, y + off + 14, "middle", (x - w / 2, y + off, x + w / 2, y + off + 16))]
        else:
            cands = [(x + 11, y + 5, "start", (x + 9, y - h + 5, x + 11 + w, y + 7)),
                     (x - 11, y + 5, "end", (x - 11 - w, y - h + 5, x - 9, y + 7)),
                     (x, y - 11, "middle", (x - w / 2, y - 11 - h, x + w / 2, y - 9)),
                     (x, y + 22, "middle", (x - w / 2, y + 22 - h, x + w / 2, y + 24))]
        c = next((c for c in cands if not _hit(c[3], obst)), None)
        if c is None:
            if not s["type"]:
                continue  # escale secondaire : point sans nom plutôt qu'un texte illisible
            c = cands[0]  # départ et escale en cours toujours nommés
        obst.append(c[3])
        labels.append({"name": s["name"], "x": c[0], "y": c[1], "anchor": c[2], "fs": fs,
                       "type": s["type"]})
    return shown, labels


# ---------- modèle de vue ----------

def build_view(state: dict, now: int) -> dict:
    st = state.get("stats") or {}
    pts = state.get("track", {}).get("points") or []
    places = state.get("places") or {}
    fc = (state.get("weather") or {}).get("forecast") or {}
    mar = (state.get("weather") or {}).get("marine") or {}
    boat = (state.get("boat") or {}).get("name") or "Littorina"

    if len(pts) < 2 or not st.get("position"):
        return {"empty": True, "boat": boat, "status": status_line(state, now)}

    pos = st["position"]
    offset = fc.get("time_utc_offset_s")
    if offset is None:  # repli : fuseau nautique d'après la longitude
        offset = round(pos["lon"] / 15) * 3600
    t_now = datetime.fromtimestamp(now, timezone.utc)
    board = t_now + timedelta(seconds=offset)
    home = t_now.astimezone(HOME_TZ)

    # --- carte
    trace = simplify([project(p[1], p[2]) for p in pts])
    bx, by = project(pos["lat"], pos["lon"])
    dep = st["departure"]
    dep_name, _ = geocode.label(places, dep["lat"], dep["lon"])

    stops = [{"x": project(dep["lat"], dep["lon"])[0], "y": project(dep["lat"], dep["lon"])[1],
              "name": dep_name, "days": 0, "type": "depart"}]
    for s in st.get("stops", []):
        x, y = project(s["lat"], s["lon"])
        stops.append({"x": x, "y": y, "name": geocode.label(places, s["lat"], s["lon"])[0],
                      "days": s["days"], "type": ""})

    mode = st.get("mode", "navigation")
    obst = [BOX_CARTOUCHE, BOX_INSTR, BOX_COMPASS, BOX_STATUS]
    if mode == "escale":
        cs = st["current_stop"]
        cur_name, cur_country = geocode.label(places, cs["lat"], cs["lon"])
        stops.append({"x": bx, "y": by, "name": cur_name, "days": 1e9, "type": "en-cours"})
    else:
        obst.append((bx - 20, by - 20, bx + 20, by + 20))

    # --- étiquette jaune (stat tournante : change chaque jour)
    day = st["voyage_day"]
    line1 = f"{fint(st['total_nm'])} milles parcourus"
    if (now // 86400) % 2 == 0:
        line2 = f"soit {fint(st['marathons'])} marathons · jour {fint(day)} du voyage"
    else:
        line2 = f"{st['earth_pct']} % d'un tour de la Terre · jour {fint(day)}"
    yw = max(len(line1) * 22 * 0.5, len(line2) * 16 * 0.45) + 34
    obst.append((16, 16, 16 + yw, 86))

    shown, labels = place_stops(stops, trace, obst)

    # --- instruments
    def wind():
        if fc.get("wind_kn") is None:
            return ("Vent prévu", "—", "")
        return ("Vent prévu", fint(fc["wind_kn"]), f"nds {cardinal_fr(fc.get('wind_dir'))}".strip())

    def water_or_air():
        if mar.get("water_temp_c") is not None:
            return ("Eau", fint(mar["water_temp_c"]), "°C")
        if fc.get("air_temp_c") is not None:
            return ("Air", fint(fc["air_temp_c"]), "°C")
        return ("Eau", "—", "")

    def sun(key, title):
        v = fc.get(key)
        return (title, v[11:16] if isinstance(v, str) and len(v) >= 16 else "—", "")

    if mode == "escale":
        instruments = [wind(), water_or_air(), sun("sunrise", "Lever"), sun("sunset", "Coucher")]
        n = int(st.get("stop_days", 0))
        head = "En escale · " + ("depuis aujourd'hui" if n == 0 else f"depuis {plural(n, 'jour', 'jours')}")
        place_line = " · ".join(x for x in [cur_country, f"à {fint(st['home_km'])} km"] if x)
        cartouche = [head, cur_name, place_line,
                     f"{JOURS[board.weekday()]} {hm(board)} à bord — {JOURS[home.weekday()]} {hm(home)} ici"]
    else:
        swell = ("Houle", fdec(mar["wave_m"]), f"m · {fint(mar['wave_period_s'])} s") \
            if mar.get("wave_m") is not None and mar.get("wave_period_s") is not None else water_or_air()
        course = st.get("course")
        instruments = [
            ("Cap", "—" if course is None else f"{course}°", st.get("course_card", "")),
            ("Vitesse", "—" if st.get("speed_kn") is None else fdec(st["speed_kn"]), "nds"),
            wind(), swell,
        ]
        leg = st.get("leg") or {}
        n = int(leg.get("days", 0))
        frm = leg.get("from_stop")
        frm_name = geocode.label(places, frm["lat"], frm["lon"])[0] if frm else dep_name
        cartouche = [f"{boat} · en mer",
                     "Premier jour de traversée" if n == 0 else f"{plural(n, 'jour', 'jours')} de traversée",
                     f"{fint(leg.get('nm', 0))} milles depuis {frm_name}",
                     f"{JOURS_ABR[board.weekday()]} {day_month(board)} · {hm(board)} à bord"]

    heading = None
    if mode != "escale" and st.get("course") is not None:
        a = math.radians(st["course"])
        heading = {"x2": bx + 70 * math.sin(a), "y2": by - 70 * math.cos(a), "rot": st["course"]}

    return {
        "empty": False, "mode": mode, "boat": boat,
        "trace": trace, "boat_xy": (bx, by), "heading": heading,
        "departure_xy": project(dep["lat"], dep["lon"]),
        "points": [s for s in shown if s["type"] == ""],
        "labels": labels,
        "yellow": (line1, line2),
        "cartouche": cartouche,
        "instruments": instruments,
        "status": status_line(state, now),
    }


def status_line(state: dict, now: int) -> dict:
    upd = (state.get("track") or {}).get("updated")
    home = datetime.fromtimestamp(now, timezone.utc).astimezone(HOME_TZ)
    if upd is None:
        return {"text": "en attente de données", "alert": True}
    if now - upd > STALE_AFTER_S:
        t = datetime.fromtimestamp(upd, timezone.utc).astimezone(HOME_TZ)
        return {"text": f"position du {day_month(t)} à {t.hour} h", "alert": True}
    return {"text": f"à jour : {JOURS_ABR[home.weekday()]} {day_month(home)} {hm(home)}", "alert": False}


# ---------- HTML ----------

def _font_faces() -> str:
    faces = []
    for fam, file_stem, weight, style in [
        ("Plex Cond", "ibm-plex-sans-condensed-{s}-400-normal", 400, "normal"),
        ("Plex Cond", "ibm-plex-sans-condensed-{s}-600-normal", 600, "normal"),
        ("Plex Cond", "ibm-plex-sans-condensed-{s}-700-normal", 700, "normal"),
        ("Plex Serif", "ibm-plex-serif-{s}-400-italic", 400, "italic"),
        ("Plex Serif", "ibm-plex-serif-{s}-500-italic", 500, "italic"),
    ]:
        for subset, rng in [("latin-ext", "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1E00-1EFF,U+20A0-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF"),
                            ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")]:
            url = (ASSETS / "fonts" / (file_stem.format(s=subset) + ".woff2")).as_uri()
            faces.append(f"@font-face{{font-family:'{fam}';src:url('{url}') format('woff2');"
                         f"font-weight:{weight};font-style:{style};unicode-range:{rng}}}")
    return "\n".join(faces)


def to_html(v: dict) -> str:
    base_map = (ASSETS / "carte-pacifique.svg").read_text(encoding="utf-8")
    base_uri = "data:image/svg+xml;charset=utf-8," + base_map.replace("#", "%23").replace("\n", " ")
    css = f"""{_font_faces()}
*{{box-sizing:border-box}}
body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#fff;color:#000;
font-family:'Plex Cond',sans-serif;-webkit-font-smoothing:antialiased}}
.abs{{position:absolute}}
.k{{font-size:14px;font-weight:600;letter-spacing:.12em;text-transform:uppercase}}
.v{{font-size:28px;font-weight:700;line-height:1.05;white-space:nowrap}}
.u{{font-size:16px;font-weight:400}}
"""
    parts = [f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><style>{css}</style></head><body>",
             f"<div class='abs' style='left:0;top:0;width:{W}px;height:{H}px;overflow:hidden'>",
             f"<img class='abs' src=\"{esc(base_uri)}\" width='{W}' height='{H}' style='left:0;top:0'>"]

    svg = [f"<svg class='abs' style='left:0;top:0' width='{W}' height='{H}' viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg'>",
           f"<g font-family='Plex Serif' font-style='italic' fill='{BLUE}' stroke='#fff' stroke-width='4' paint-order='stroke'>"
           "<text x='24' y='119' font-size='14' letter-spacing='2'>équateur</text>"
           "<text x='453' y='98' font-size='16' letter-spacing='5' text-anchor='middle'>OCÉAN PACIFIQUE</text></g>"]

    if v.get("empty"):
        svg.append("</svg>")
        parts += svg
        parts.append(f"<div class='abs' style='left:200px;top:180px;width:400px;background:{YELLOW};border:3px solid #000;"
                     "padding:14px 18px;text-align:center'><div style='font-size:24px;font-weight:700'>"
                     f"{esc(v['boat'])}</div><div style='font-size:16px'>En attente des premières positions</div></div>")
    else:
        if v["heading"]:
            h = v["heading"]
            svg.append(f"<path d='M{v['boat_xy'][0]:.1f} {v['boat_xy'][1]:.1f} L{h['x2']:.1f} {h['y2']:.1f}' fill='none' "
                       "stroke='#000' stroke-width='2.5' stroke-dasharray='4 5' stroke-linecap='round'/>")
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in v["trace"])
        svg.append(f"<path d='{d}' fill='none' stroke='{RED}' stroke-width='4.5' stroke-linejoin='round' stroke-linecap='round'/>")
        svg.append("<g fill='#fff' stroke='#000' stroke-width='2'>")
        svg += [f"<circle cx='{p['x']:.1f}' cy='{p['y']:.1f}' r='5'/>" for p in v["points"]]
        svg.append("</g>")
        dx, dy = v["departure_xy"]
        svg.append(f"<rect x='{dx - 5.5:.1f}' y='{dy - 5.5:.1f}' width='11' height='11' fill='#000'/>")
        svg.append("<g font-family='Plex Cond' fill='#000' stroke='#fff' stroke-width='4' paint-order='stroke' stroke-linejoin='round'>")
        for lb in v["labels"]:
            fw = 700 if lb["type"] == "en-cours" else 600
            svg.append(f"<text x='{lb['x']:.1f}' y='{lb['y']:.1f}' text-anchor='{lb['anchor']}' font-size='{lb['fs']}' "
                       f"font-weight='{fw}'>{esc(lb['name'])}</text>")
        svg.append("</g>")
        bx, by = v["boat_xy"]
        if v["mode"] == "escale":
            svg.append(f"<g transform='translate({bx:.1f} {by:.1f})'><circle r='17' fill='{YELLOW}' stroke='#000' stroke-width='2.5'/>"
                       "<circle cx='0' cy='-8' r='2.6' fill='none' stroke='#000' stroke-width='2'/>"
                       "<path d='M0 -5.5 V10 M-5 -1 H5 M-9 3 Q-8 10 0 10 Q8 10 9 3' fill='none' stroke='#000' stroke-width='2.4' stroke-linecap='round'/></g>")
        else:
            rot = v["heading"]["rot"] if v["heading"] else 0
            svg.append(f"<circle cx='{bx:.1f}' cy='{by:.1f}' r='16' fill='{YELLOW}' stroke='#000' stroke-width='2.5'/>"
                       f"<path d='M0 -11 L7.5 9 L0 4.5 L-7.5 9 Z' fill='#000' transform='translate({bx:.1f} {by:.1f}) rotate({rot})'/>")
        svg.append("<g transform='translate(764 112)'><circle r='20' fill='#fff' stroke='#000' stroke-width='2'/>"
                   "<path d='M0 -14 L6 6 L0 2 L-6 6 Z' fill='#000'/>"
                   "<text y='17' font-family='Plex Cond' font-size='14' font-weight='700' text-anchor='middle' fill='#000'>N</text></g>")
        svg.append("</svg>")
        parts += svg

        y1, y2 = v["yellow"]
        parts.append(f"<div class='abs' style='left:16px;top:16px;background:{YELLOW};border:3px solid #000;padding:8px 14px 10px'>"
                     f"<div style='font-size:22px;font-weight:700;line-height:1.1;white-space:nowrap'>{esc(y1)}</div>"
                     f"<div style='font-size:16px;line-height:1.2;white-space:nowrap'>{esc(y2)}</div></div>")

        c = v["cartouche"]
        parts.append("<div class='abs' style='left:16px;bottom:16px;width:300px;background:#fff;border:6px double #000;"
                     "padding:8px 16px 10px;display:flex;flex-direction:column;gap:3px'>"
                     f"<div style='font-size:14px;font-weight:600;letter-spacing:.18em;text-transform:uppercase;white-space:nowrap'>{esc(c[0])}</div>"
                     f"<div style='font-size:28px;font-weight:700;line-height:1.05;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{esc(c[1])}</div>"
                     f"<div style='font-size:16px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{esc(c[2])}</div>"
                     f"<div style=\"font-family:'Plex Serif';font-style:italic;font-size:15px;white-space:nowrap\">{esc(c[3])}</div></div>")

        cells = "".join(f"<div><div class='k'>{esc(t)}</div><div class='v'>{esc(val)} <span class='u'>{esc(u)}</span></div></div>"
                        for t, val, u in v["instruments"])
        parts.append("<div class='abs' style='right:16px;bottom:16px;width:268px;background:#fff;border:3px solid #000;"
                     "padding:10px 14px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:14px;row-gap:8px'>"
                     f"{cells}</div>")

    stt = v["status"]
    color = RED if stt["alert"] else "#000"
    weight = 700 if stt["alert"] else 400
    style = "font-family:'Plex Cond';font-style:normal" if stt["alert"] else "font-family:'Plex Serif';font-style:italic"
    parts.append(f"<div class='abs' style='left:318px;width:196px;bottom:8px;text-align:center;font-size:14px;"
                 f"{style};font-weight:{weight};color:{color};white-space:nowrap;"
                 f"text-shadow:-2px 0 #fff,2px 0 #fff,0 -2px #fff,0 2px #fff'>{esc(stt['text'])}</div>")
    parts.append("</div></body></html>")
    return "".join(parts)


# ---------- capture et publication ----------

def screenshot(html_text: str, workdir: Path) -> bytes:
    from playwright.sync_api import sync_playwright

    page_file = workdir / "frame.html"
    page_file.write_text(html_text, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(200)
        png = page.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H}, type="png")
        browser.close()
    return png


def quantize(png: bytes) -> bytes:
    """Ramène chaque pixel à la couleur pure la plus proche (sans tramage) : aplats nets."""
    import io

    from PIL import Image

    import numpy as np

    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int32)
    pal = np.array(PALETTE, dtype=np.int32)
    # Couleur la plus proche… (distance pondérée selon la sensibilité de l'œil)
    wts = np.array([3, 4, 2], dtype=np.int32)
    d = (((a[:, :, None, :] - pal[None, None, :, :]) ** 2) * wts).sum(-1)
    idx = d.argmin(-1)
    # …sauf pour les pixels quasi gris (lissage du noir sur blanc) : seuil de luminosité,
    # sinon les hachures et le texte noir lissés tomberaient sur le bleu.
    chroma = a.max(-1) - a.min(-1)
    lum = (a * np.array([299, 587, 114])).sum(-1) // 1000
    grey = chroma < 48
    idx = np.where(grey, np.where(lum < 140, 0, 1), idx)
    q = Image.fromarray(pal[idx].astype(np.uint8), "RGB")
    out = io.BytesIO()
    q.save(out, format="PNG", optimize=True)
    return out.getvalue()


def write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Génère l'image du cadre e-ink")
    ap.add_argument("--state", default="data/state.json", type=Path)
    ap.add_argument("--out", default="public", type=Path)
    ap.add_argument("--now", type=int, default=None, help="horodatage unix (tests)")
    args = ap.parse_args(argv)

    now = args.now or int(time.time())
    try:
        state = json.loads(args.state.read_text())
    except (OSError, ValueError):
        state = {}
    args.out.mkdir(parents=True, exist_ok=True)

    view = build_view(state, now)
    raw = screenshot(to_html(view), args.out)
    (args.out / "frame.html").unlink(missing_ok=True)
    changed = write_if_changed(args.out / "frame.png", quantize(raw))
    write_if_changed(args.out / "frame-preview.png", raw)

    meta = {
        "rendered_at": now,
        "mode": "vide" if view.get("empty") else view["mode"],
        "position_time": (state.get("track") or {}).get("updated"),
        "stale": view["status"]["alert"],
        "sources": {k: v.get("status") for k, v in (state.get("sources") or {}).items() if "status" in v},
        "next_update_hint_s": 3600,
    }
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    (args.out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Littorina</title>"
        "<body style='margin:24px;font-family:sans-serif;background:#eee'>"
        "<img src='frame.png' width='800' height='480' style='border:1px solid #999;background:#fff'>"
        "<p><a href='frame-preview.png'>aperçu lissé</a> · <a href='meta.json'>meta.json</a></p>",
        encoding="utf-8")
    print(f"rendu {meta['mode']} — frame.png {'modifiée' if changed else 'inchangée'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
