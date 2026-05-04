#!/usr/bin/env python3
"""
Cooltra Meteo — Servidor local
Fa de proxy cap a Open Meteo i serveix el dashboard.

Execució:
    python3 server.py

Dashboard:
    http://localhost:8000
"""

import json
import os
import time
import calendar
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Importa el fetcher intel·ligent (caché en disc)
try:
    import fetcher as _fetcher
    _USE_FETCHER = True
except ImportError:
    _USE_FETCHER = False

# ─── CIUTATS ───────────────────────────────────────────────────────────────────
CITIES = [
    {"name": "Barcelona", "lat": 41.3851, "lon":  2.1734, "country": "ES"},
    {"name": "Madrid",    "lat": 40.4168, "lon": -3.7038, "country": "ES"},
    {"name": "Valencia",  "lat": 39.4699, "lon": -0.3763, "country": "ES"},
    {"name": "Sevilla",   "lat": 37.3891, "lon": -5.9845, "country": "ES"},
    {"name": "Lisboa",    "lat": 38.7223, "lon": -9.1393, "country": "PT"},
    {"name": "Paris",     "lat": 48.8566, "lon":  2.3522, "country": "FR"},
    {"name": "Torino",    "lat": 45.0703, "lon":  7.6869, "country": "IT"},
    {"name": "Milano",    "lat": 45.4654, "lon":  9.1859, "country": "IT"},
    {"name": "Roma",      "lat": 41.9028, "lon": 12.4964, "country": "IT"},
    {"name": "Amsterdam", "lat": 52.3676, "lon":  4.9041, "country": "NL"},
    {"name": "Rotterdam", "lat": 51.9244, "lon":  4.4777, "country": "NL"},
    {"name": "Haarlem",   "lat": 52.3874, "lon":  4.6462, "country": "NL"},
    {"name": "Den Haag",  "lat": 52.0705, "lon":  4.3007, "country": "NL"},
    {"name": "Delft",     "lat": 52.0116, "lon":  4.3571, "country": "NL"},
    {"name": "Nijmegen",  "lat": 51.8426, "lon":  5.8546, "country": "NL"},
    {"name": "Eindhoven", "lat": 51.4416, "lon":  5.4697, "country": "NL"},
    {"name": "Bruxelles", "lat": 50.8503, "lon":  4.3517, "country": "BE"},
    {"name": "Antwerp",   "lat": 51.2194, "lon":  4.4025, "country": "BE"},
]
CITIES_BY_NAME = {c["name"]: c for c in CITIES}

# ─── CACHE ─────────────────────────────────────────────────────────────────────
_cache         = {"data": None, "ts": 0}
_history_cache = {}          # city_name -> {"data": {...}, "ts": timestamp}
CACHE_TTL      = 21600       # 6 hores (el fetcher actualitza 4x/dia)
HISTORY_TTL    = 86400       # 24 hores (historial)
DISC_CACHE_MAX_AGE = 21600   # Accepta disc si té < 6 hores

# ─── DATE HELPERS ──────────────────────────────────────────────────────────────
def fmt(d):
    return d.strftime("%Y-%m-%d")

def shift_year(d, delta):
    """Desplaça una data N anys, ajustant el dia si cal (p. ex. 29 feb)."""
    y = d.year + delta
    max_day = calendar.monthrange(y, d.month)[1]
    return d.replace(year=y, day=min(d.day, max_day))

def months_ago(d, n):
    """Retorna la data d - n mesos."""
    total = d.year * 12 + d.month - n
    y, m  = divmod(total, 12)
    if m == 0:
        m = 12
        y -= 1
    max_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, max_day))

# ─── FETCH OPEN METEO ──────────────────────────────────────────────────────────
def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CooltraMeteo/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())

def fetch_city(city):
    """Descarrega 3 anys de dades (2026, 2025, 2024) per la finestra de 16+8 dies."""
    t = date.today()
    params = "precipitation_sum,temperature_2m_max,temperature_2m_min"
    coords = f"latitude={city['lat']}&longitude={city['lon']}"

    # Any actual: forecast API amb past_days=16
    cur_url = (
        f"https://api.open-meteo.com/v1/forecast?{coords}"
        f"&daily={params}&past_days=16&forecast_days=8&timezone=auto"
    )

    # Anys anteriors: archive API
    def archive_url(year_offset):
        s = fmt(shift_year(t - timedelta(days=16), year_offset))
        e = fmt(shift_year(t + timedelta(days=7),  year_offset))
        return (
            f"https://archive-api.open-meteo.com/v1/archive?{coords}"
            f"&daily={params}&start_date={s}&end_date={e}&timezone=auto"
        ), s, e

    ly_url,  s_ly,  e_ly  = archive_url(-1)
    ly2_url, s_ly2, e_ly2 = archive_url(-2)

    # Fetch seqüencial amb petites pauses per evitar HTTP 429
    def fetch_with_retry(url, attempts=4):
        last_err = None
        for i in range(attempts):
            try:
                return fetch_url(url)
            except Exception as e:
                last_err = e
                # Backoff: 2s, 5s, 10s, 20s
                time.sleep([2, 5, 10, 20][i] if i < 4 else 20)
        raise last_err

    cur = fetch_with_retry(cur_url)
    time.sleep(0.3)
    ly  = fetch_with_retry(ly_url)
    time.sleep(0.3)
    ly2 = fetch_with_retry(ly2_url)

    return city["name"], {"cur": cur, "ly": ly, "ly2": ly2}

def _load_disc_cache():
    """Carrega weather_latest.json del disc si existeix i és prou recent."""
    if not _USE_FETCHER:
        return None
    path = _fetcher.WEATHER_LATEST
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("fetched_ts", 0)
        if age < DISC_CACHE_MAX_AGE:
            print(f"  [disc] weather_latest.json de fa {int(age)}s")
            return data
    except Exception as e:
        print(f"  [disc] error llegint caché: {e}")
    return None


def fetch_all(force=False):
    now = time.time()
    if not force and _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        print(f"  [cache] dades principals de fa {int(now-_cache['ts'])}s")
        return _cache["data"]

    # Intenta llegir del disc (escrit pel fetcher)
    if not force:
        disc = _load_disc_cache()
        if disc:
            _cache["data"] = disc
            _cache["ts"]   = now
            return disc

    # Si el fetcher està disponible, usa'l (gestiona caché intel·ligent)
    if _USE_FETCHER:
        print("  Actualitzant via fetcher intel·ligent...")
        result = _fetcher.run_fetch(verbose=True)
        _cache["data"] = result
        _cache["ts"]   = now
        return result

    print("  Descarregant dades principals (3 anys × 18 ciutats)...")
    t = date.today()
    result = {
        "cities":     CITIES,
        "data":       {},
        "fetched_at": fmt(t),
        "today":      fmt(t),
        "dates": {
            "start_cur": fmt(t - timedelta(days=16)),
            "end_cur":   fmt(t + timedelta(days=7)),
            "start_ly":  fmt(shift_year(t - timedelta(days=16), -1)),
            "end_ly":    fmt(shift_year(t + timedelta(days=7),  -1)),
            "start_ly2": fmt(shift_year(t - timedelta(days=16), -2)),
            "end_ly2":   fmt(shift_year(t + timedelta(days=7),  -2)),
        },
    }

    # Seqüencial: el retry per URL ja és dins de fetch_city
    for city in CITIES:
        try:
            name, data = fetch_city(city)
            result["data"][name] = data
            print(f"  ✓  {name}")
        except Exception as e:
            print(f"  ✗  {city['name']}: {e}")
            result["data"][city["name"]] = {"cur": None, "ly": None, "ly2": None}

    _cache["data"] = result
    _cache["ts"]   = now
    print(f"  Llest. {len(result['data'])} ciutats.")
    return result


def fetch_city_history(city_name, force=False):
    """Descarrega 18 mesos de dades d'una ciutat concreta."""
    now = time.time()
    if not force and city_name in _history_cache:
        cached = _history_cache[city_name]
        if (now - cached["ts"]) < HISTORY_TTL:
            print(f"  [cache] historial {city_name} de fa {int(now-cached['ts'])}s")
            return cached["data"]

    # Usa el fetcher intel·ligent si està disponible (caché incremental en disc)
    if _USE_FETCHER:
        print(f"  Historial {city_name} via fetcher intel·ligent...")
        result = _fetcher.fetch_city_history_smart(city_name)
        _history_cache[city_name] = {"data": result, "ts": now}
        print(f"  ✓ Historial {city_name} llest.")
        return result

    city = CITIES_BY_NAME.get(city_name)
    if not city:
        raise ValueError(f"Ciutat desconeguda: {city_name}")

    t     = date.today()
    start = months_ago(t, 18)
    end   = t

    params = "precipitation_sum,temperature_2m_max,temperature_2m_min"
    coords = f"latitude={city['lat']}&longitude={city['lon']}"

    print(f"  Descarregant historial 18 mesos: {city_name} ({fmt(start)} → {fmt(end)})...")

    arch_url = (
        f"https://archive-api.open-meteo.com/v1/archive?{coords}"
        f"&daily={params}&start_date={fmt(start)}&end_date={fmt(end)}&timezone=auto"
    )

    data = fetch_url(arch_url)

    result = {
        "city":       city_name,
        "country":    city["country"],
        "start_date": fmt(start),
        "end_date":   fmt(end),
        "fetched_at": fmt(t),
        "daily":      data.get("daily", {}),
    }

    _history_cache[city_name] = {"data": result, "ts": now}
    print(f"  ✓ Historial {city_name} llest.")
    return result


# ─── HTTP HANDLER ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt_str, *args):
        msg = fmt_str % args
        if "200" in msg and "/api" not in self.path:
            return
        print(f"  [{self.address_string()}] {msg}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)

        if path in ("/", "/index.html", "/meteo_dashboard.html"):
            self._serve_file("meteo_dashboard.html", "text/html; charset=utf-8")
        elif path == "/api/weather":
            if params.get("refresh", ["0"])[0] == "1":
                _cache["ts"] = 0
            self._serve_json(fetch_all)
        elif path == "/api/history":
            city_name = params.get("city", [""])[0]
            force     = params.get("refresh", ["0"])[0] == "1"
            if force and city_name in _history_cache:
                _history_cache[city_name]["ts"] = 0
            self._serve_json(lambda: fetch_city_history(city_name, force))
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_file(self, filename, ctype):
        fpath = os.path.join(BASE_DIR, filename)
        try:
            with open(fpath, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def _serve_json(self, fn):
        try:
            data = fn()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err)


# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    PORT = 8000
    print(f"\n🌦  Cooltra Meteo Server")
    print(f"   → http://localhost:{PORT}")
    print(f"   → Ctrl+C per aturar\n")
    try:
        HTTPServer(("", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor aturat.")
