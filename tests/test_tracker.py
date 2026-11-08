import json

import pytest
import requests

from tracker import predictwind as pw
from tracker import run

T0 = 1715799600  # 2024-05-15, premier point réel de Littorina


def make_route(n=200, start=T0, last_without_bsp=True):
    route = []
    for i in range(n):
        p = {"p": {"lat": 18.0672 - i * 0.01, "lon": -63.0904 + i * 0.01},
             "t": start + i * 3600, "bearing": 180, "bsp": 5.2}
        route.append(p)
    if last_without_bsp:
        del route[-1]["bsp"]
    return route


def make_html(route, updated=None, live=None, extra=None):
    boat = {
        "route": route,
        "images": [],
        "live": live or {"twd": None, "tws": None, "gust": None},
        "updated": updated if updated is not None else (route[-1].get("t") if route else None),
        "trackDisplayId": "Littorina",
        "boatInfo": {"name": "Littorina", "visible_duration": 0,
                     "profilePublic": True, "boatType": "SailCatamaran"},
    }
    if extra:
        boat.update(extra)
    data = {"props": {"pageProps": {"boatData": boat, "_nextI18Next": {}}}}
    return ('<html><head></head><body><div id="__next"></div>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
            '</body></html>')


# ---------- parse ----------

def test_parse_nominal():
    tr = pw.parse(make_html(make_route()))
    assert tr.name == "Littorina" and tr.boat_type == "SailCatamaran"
    assert len(tr.points) == 200
    assert tr.points[-1].bsp is None          # bsp absent toléré
    assert tr.points[0].bsp == 5.2
    assert tr.updated == tr.points[-1].t
    assert tr.live == {"twd": None, "tws": None, "gust": None}


def test_updated_in_milliseconds_is_normalised():
    route = make_route()
    tr = pw.parse(make_html(route, updated=route[-1]["t"] * 1000))
    assert tr.updated == route[-1]["t"]


def test_unsorted_and_duplicates():
    route = make_route(50)
    route = list(reversed(route)) + [route[0]]
    tr = pw.parse(make_html(route))
    ts = [p.t for p in tr.points]
    assert ts == sorted(ts) and len(ts) == 50


def test_few_bad_points_are_dropped():
    route = make_route(200)
    route[10]["p"]["lat"] = 999
    route[20] = {"oops": 1}
    tr = pw.parse(make_html(route))
    assert len(tr.points) == 198 and tr.dropped_points == 2


@pytest.mark.parametrize("html", [
    "<html>no data</html>",
    '<script id="__NEXT_DATA__">{not json</script>',
    make_html([]),
])
def test_format_changes_detected(html):
    with pytest.raises(pw.FormatChangedError):
        pw.parse(html)


def test_renamed_point_fields_detected():
    route = [{"position": {"latitude": 1, "longitude": 2}, "time": T0} for _ in range(10)]
    with pytest.raises(pw.FormatChangedError):
        pw.parse(make_html(route))


def test_moved_boatdata_detected():
    html = make_html(make_route()).replace('"boatData"', '"vessel"')
    with pytest.raises(pw.FormatChangedError, match="boatData"):
        pw.parse(html)


def test_sanity_check_truncated_track():
    tr = pw.parse(make_html(make_route(100)))
    with pytest.raises(pw.FormatChangedError, match="tronquée"):
        pw.sanity_check(tr, previous_points=8600, previous_last_t=None)


# ---------- orchestrateur ----------

class FakeResp:
    def __init__(self, status=200, text="", js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        if self._js is None:
            raise ValueError
        return self._js


class FakeSession:
    """Simule PredictWind + Open-Meteo ; chaque source peut être mise en panne."""

    def __init__(self, pw_html=None, pw_down=False, meteo_down=False):
        self.pw_html, self.pw_down, self.meteo_down = pw_html, pw_down, meteo_down
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if "predictwind" in url:
            if self.pw_down:
                raise requests.ConnectionError("down")
            return FakeResp(text=self.pw_html)
        if self.meteo_down:
            return FakeResp(status=503, text="unavailable")
        if "marine" in url:
            return FakeResp(js={"current": {"time": "2026-11-08T19:00", "wave_height": 1.4,
                                            "wave_direction": 230, "wave_period": 8.1,
                                            "swell_wave_height": 1.1, "swell_wave_direction": 220,
                                            "swell_wave_period": 11.0,
                                            "sea_surface_temperature": 18.2}})
        return FakeResp(js={"utc_offset_seconds": 46800, "timezone": "Pacific/Auckland",
                            "timezone_abbreviation": "NZDT",
                            "current": {"time": "2026-11-09T08:00", "wind_speed_10m": 12.3,
                                        "wind_direction_10m": 250, "wind_gusts_10m": 18.0,
                                        "temperature_2m": 17.5, "weather_code": 2, "is_day": 1}})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PW_TRACKING_URL", "https://www.predictwind.com/tracking/Littorina")
    return tmp_path / "state.json"


def _run(monkeypatch, state_path, session, *args):
    monkeypatch.setattr(run.requests, "Session", lambda: session)
    return run.main(["--state", str(state_path), *args])


def test_full_run_ok(monkeypatch, env):
    s = FakeSession(pw_html=make_html(make_route()))
    assert _run(monkeypatch, env, s) == 0
    st = json.loads(env.read_text())
    assert st["sources"]["predictwind"]["status"] == "ok"
    assert len(st["track"]["points"]) == 200
    assert st["weather"]["forecast"]["timezone"] == "Pacific/Auckland"
    assert st["weather"]["marine"]["water_temp_c"] == 18.2


def test_network_failure_keeps_last_state(monkeypatch, env):
    _run(monkeypatch, env, FakeSession(pw_html=make_html(make_route())))
    s = FakeSession(pw_down=True, meteo_down=True)
    assert _run(monkeypatch, env, s, "--force") == 0
    st = json.loads(env.read_text())
    assert st["sources"]["predictwind"]["status"] == "fetch_error"
    assert len(st["track"]["points"]) == 200            # ancien état conservé
    assert st["weather"]["forecast"]["wind_kn"] == 12.3  # même position → météo conservée
    assert st["track"]["stale"] is True                  # données de 2024 → signalées anciennes


def test_format_change_keeps_last_state_and_strict_exit(monkeypatch, env):
    _run(monkeypatch, env, FakeSession(pw_html=make_html(make_route())))
    s = FakeSession(pw_html="<html>new site</html>")
    assert _run(monkeypatch, env, s, "--force", "--strict") == 3
    st = json.loads(env.read_text())
    assert st["sources"]["predictwind"]["status"] == "format_changed"
    assert len(st["track"]["points"]) == 200


def test_throttle_one_call_per_hour(monkeypatch, env):
    _run(monkeypatch, env, FakeSession(pw_html=make_html(make_route())))
    s = FakeSession(pw_html=make_html(make_route()))
    _run(monkeypatch, env, s)
    assert not any("predictwind" in c for c in s.calls)


def test_missing_url(monkeypatch, tmp_path):
    monkeypatch.delenv("PW_TRACKING_URL", raising=False)
    assert run.main(["--state", str(tmp_path / "s.json")]) == 2


def test_corrupted_state_file_recovers(monkeypatch, env):
    env.write_text("{garbage")
    assert _run(monkeypatch, env, FakeSession(pw_html=make_html(make_route()))) == 0
    assert json.loads(env.read_text())["sources"]["predictwind"]["status"] == "ok"
