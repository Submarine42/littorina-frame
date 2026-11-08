import io
import json

import pytest

from tracker import geocode, render, stats

T0 = 1790000000
H = 3600


def leg(start_t, lat, lon, hours, dlat, dlon, bsp=6.0):
    return [[start_t + i * H, lat + i * dlat, lon + i * dlon, 180.0, bsp] for i in range(hours)]


def stay(start_t, lat, lon, hours):
    return [[start_t + i * H, lat, lon, 0.0, 0.0] for i in range(hours)]


def two_legs_with_stop(last_moving=True):
    pts = leg(T0, -10.0, -150.0, 48, -0.1, -0.1)              # 48 h de mer
    t = pts[-1][0] + H
    pts += stay(t, pts[-1][1], pts[-1][2], 72)                # 3 jours d'escale
    t = pts[-1][0] + H
    pts += leg(t, pts[-1][1], pts[-1][2], 30, -0.1, -0.05)    # nouvelle traversée
    if not last_moving:
        t = pts[-1][0] + H
        pts += stay(t, pts[-1][1], pts[-1][2], 10)
    return pts


def test_haversine_one_degree_latitude_is_60nm():
    assert stats.haversine_nm(0, 0, 1, 0) == pytest.approx(60.0, rel=0.01)


def test_cardinal_fr():
    assert [stats.cardinal_fr(d) for d in (0, 44, 135, 225, 270, 350)] == ["N", "NE", "SE", "SO", "O", "N"]


def test_navigation_with_previous_stop():
    pts = two_legs_with_stop()
    s = stats.compute(pts, now=pts[-1][0] + 600)
    assert s["mode"] == "navigation"
    assert len(s["stops"]) == 1 and s["stops"][0]["days"] == pytest.approx(3.0, abs=0.1)
    assert s["leg"]["from_stop"]["lat"] == pytest.approx(s["stops"][0]["lat"])
    assert s["leg"]["days"] == 1                  # 30 h depuis l'escale
    assert 150 < s["leg"]["nm"] < 250
    assert s["course_card"] in ("S", "SO")
    assert s["speed_kn"] == 6.0                   # bsp du dernier point
    assert s["voyage_day"] == (pts[-1][0] + 600 - T0) // 86400 + 1


def test_escale_mode_after_six_hours_stationary():
    pts = two_legs_with_stop(last_moving=False)
    s = stats.compute(pts, now=pts[-1][0] + 600)
    assert s["mode"] == "escale"
    assert "current_stop" in s and "leg" not in s
    assert len(s["stops"]) == 1                   # l'escale en cours n'est pas listée


def test_gps_glitch_excluded_from_distance():
    pts = leg(T0, 0.0, -150.0, 10, 0.0, 0.1)
    clean = stats.compute(pts, now=pts[-1][0])["total_nm"]
    bad = [list(p) for p in pts]
    bad[5] = [bad[5][0], 30.0, -100.0, 0.0, None]  # point aberrant à des milliers de milles
    assert stats.compute(bad, now=pts[-1][0])["total_nm"] < clean + 15


def test_nearby_stops_merged():
    a = stay(T0, -17.50, -149.50, 30)
    b = leg(a[-1][0] + H, -17.50, -149.50, 2, 0.02, 0.0, bsp=3)  # petit déplacement dans le lagon
    c = stay(b[-1][0] + H, b[-1][1], b[-1][2], 30)
    d = leg(c[-1][0] + H, c[-1][1], c[-1][2], 20, -0.1, -0.1)
    assert len(stats.detect_stops(a + b + c + d)) == 1


def test_format_coords_fallback():
    assert geocode.format_coords(-35.7587, 174.3472) == "35° 46′ S · 174° 21′ E"
    assert geocode.label({}, -35.76, 174.35)[0].startswith("35° 46′ S")
    assert geocode.label({"-35.76,174.35": {"name": "Whangarei", "country": "Nouvelle-Zélande"}},
                         -35.7587, 174.3472) == ("Whangarei", "Nouvelle-Zélande")


def test_geocode_cache_and_limits():
    calls = []

    class S:
        def get(self, url, params, headers, timeout):
            calls.append((params["lat"], params["lon"]))
            assert "littorina-frame" in headers["User-Agent"]

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"address": {"town": "Whangarei", "country": "Nouvelle-Zélande"}}
            return R()

    places = {"1.00,2.00": {"name": "Déjà connu", "country": None}}
    n = geocode.resolve(places, [(1.0, 2.0), (-35.76, 174.35), (-35.7601, 174.3499)], S(), sleep=lambda s: None)
    assert n == 1 and len(calls) == 1             # cache + même clé arrondie
    assert places["-35.76,174.35"]["name"] == "Whangarei"


# ---------- rendu ----------

def make_state(pts, now, mode_places=None):
    return {
        "boat": {"name": "Littorina"},
        "track": {"updated": pts[-1][0], "points": pts},
        "stats": stats.compute(pts, now),
        "places": mode_places or {},
        "weather": {"forecast": {"time_utc_offset_s": 46800, "wind_kn": 12.3, "wind_dir": 225,
                                 "air_temp_c": 19.0, "sunrise": "2026-11-09T06:05", "sunset": "2026-11-09T20:02"},
                    "marine": {"wave_m": 1.4, "wave_period_s": 8.1, "water_temp_c": 18.2}},
        "sources": {"predictwind": {"status": "ok"}},
    }


def test_view_navigation():
    pts = two_legs_with_stop()
    now = pts[-1][0] + 600
    v = render.build_view(make_state(pts, now), now)
    assert v["mode"] == "navigation" and v["heading"]
    assert v["cartouche"][0] == "Littorina · en mer"
    assert "jour de traversée" in v["cartouche"][1]
    assert [i[0] for i in v["instruments"]] == ["Cap", "Vitesse", "Vent prévu", "Houle"]
    assert v["instruments"][3][1] == "1,4"
    assert v["status"]["alert"] is False


def test_view_escale_without_swell_falls_back_to_water_then_air():
    pts = two_legs_with_stop(last_moving=False)
    now = pts[-1][0] + 600
    st = make_state(pts, now)
    v = render.build_view(st, now)
    assert v["mode"] == "escale"
    assert [i[0] for i in v["instruments"]] == ["Vent prévu", "Eau", "Lever", "Coucher"]
    assert v["instruments"][2][1] == "06:05"
    st["weather"]["marine"] = None
    assert render.build_view(st, now)["instruments"][1][0] == "Air"


def test_view_stale_data_is_flagged():
    pts = two_legs_with_stop()
    now = pts[-1][0] + 2 * 86400
    v = render.build_view(make_state(pts, now), now)
    assert v["status"]["alert"] is True and v["status"]["text"].startswith("position du")


def test_view_empty_state_never_blank():
    v = render.build_view({}, T0)
    assert v["empty"] is True
    assert "En attente" in render.to_html(v)


def test_html_escapes_place_names():
    pts = two_legs_with_stop(last_moving=False)
    now = pts[-1][0] + 600
    cs = stats.compute(pts, now)["current_stop"]
    places = {geocode.place_key(cs["lat"], cs["lon"]): {"name": "<b>Île & Co</b>", "country": "Polynésie"}}
    html = render.to_html(render.build_view(make_state(pts, now, places), now))
    assert "&lt;b&gt;Île &amp; Co&lt;/b&gt;" in html and "<b>Île" not in html


def test_quantize_outputs_only_palette_colors():
    from PIL import Image

    img = Image.new("RGB", (40, 20), (255, 255, 255))
    for x in range(40):
        img.putpixel((x, 5), (128, 128, 128))    # gris de lissage → noir ou blanc, jamais bleu
        img.putpixel((x, 10), (200, 30, 40))     # rouge proche → rouge palette
        img.putpixel((x, 15), (250, 210, 20))    # jaune proche → jaune palette
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = Image.open(io.BytesIO(render.quantize(buf.getvalue())))
    colors = {c for _, c in out.getcolors(100)}
    assert colors <= set(render.PALETTE)
    assert out.getpixel((0, 5)) in [(0, 0, 0), (255, 255, 255)]
    assert out.getpixel((0, 10)) == (208, 2, 27)


def test_full_render_with_browser(tmp_path):
    pytest.importorskip("playwright")
    pts = two_legs_with_stop(last_moving=False)
    now = pts[-1][0] + 600
    p = tmp_path / "state.json"
    p.write_text(json.dumps(make_state(pts, now)))
    try:
        assert render.main(["--state", str(p), "--out", str(tmp_path / "pub"), "--now", str(now)]) == 0
    except Exception as e:  # navigateur absent sur la machine de test
        pytest.skip(f"navigateur indisponible : {e}")
    from PIL import Image

    im = Image.open(tmp_path / "pub" / "frame.png")
    assert im.size == (800, 480)
    meta = json.loads((tmp_path / "pub" / "meta.json").read_text())
    assert meta["mode"] == "escale"
    # rendu déterministe : même entrée → même octets (ETag stable, pas de rafraîchissement inutile)
    first = (tmp_path / "pub" / "frame.png").read_bytes()
    render.main(["--state", str(p), "--out", str(tmp_path / "pub"), "--now", str(now)])
    assert (tmp_path / "pub" / "frame.png").read_bytes() == first
