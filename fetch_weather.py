#!/usr/bin/env python3
"""
Cooltra Meteo — Fetcher
Descarrega les dades d'Open-Meteo per a les 18 ciutats i desa'les a weather_latest.json.

Execució manual:
    python fetch_weather.py
"""

import json
import os
import time
import calendar
import urllib.request
from datetime import date, datetime, timedelta, timezone

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

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_latest.json")


def fmt(d):
    return d.strftime("%Y-%m-%d")


def shift_year(d, delta):
    y = d.year + delta
    max_day = calendar.monthrange(y, d.month)[1]
    return d.replace(year=y, day=min(d.day, max_day))


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "CooltraMeteo/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_with_retry(url, attempts=4):
    last_err = None
    for i in range(attempts):
        try:
            return fetch_url(url)
        except Exception as e:
            last_err = e
            wait = [2, 5, 10, 20][i]
            print(f"    Reintent {i+1}/{attempts} en {wait}s: {e}")
            time.sleep(wait)
    raise last_err


def fetch_city(city):
    t = date.today()
    params = "precipitation_sum,temperature_2m_max,temperature_2m_min"
    coords = f"latitude={city['lat']}&longitude={city['lon']}"

    cur_url = (
        f"https://api.open-meteo.com/v1/forecast?{coords}"
        f"&daily={params}&past_days=16&forecast_days=8&timezone=auto"
    )

    def archive_url(year_offset):
        s = fmt(shift_year(t - timedelta(days=16), year_offset))
        e = fmt(shift_year(t + timedelta(days=7),  year_offset))
        return (
            f"https://archive-api.open-meteo.com/v1/archive?{coords}"
            f"&daily={params}&start_date={s}&end_date={e}&timezone=auto"
        )

    cur = fetch_with_retry(cur_url)
    time.sleep(0.3)
    ly  = fetch_with_retry(archive_url(-1))
    time.sleep(0.3)
    ly2 = fetch_with_retry(archive_url(-2))

    return city["name"], {"cur": cur, "ly": ly, "ly2": ly2}


def main():
    t       = date.today()
    now_utc = datetime.now(timezone.utc)

    result = {
        "cities":     CITIES,
        "data":       {},
        "fetched_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    print(f"Descarregant dades Open-Meteo ({len(CITIES)} ciutats)...")
    for city in CITIES:
        try:
            name, data = fetch_city(city)
            result["data"][name] = data
            print(f"  ✓  {name}")
        except Exception as e:
            print(f"  ✗  {city['name']}: {e}")
            result["data"][city["name"]] = {"cur": None, "ly": None, "ly2": None}

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\nGuardat: {OUT_FILE}")
    print(f"Llest. {len(result['data'])} ciutats · {result['fetched_at']}")


if __name__ == "__main__":
    main()
