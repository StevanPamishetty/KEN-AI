# weather.py
"""
Advanced Weather Engine (Option B)
- OpenWeatherMap geocoding + weather + forecast + air pollution
- Safe TTL caching
- Robust forecast aggregation
- Clean packet for LLM injection
"""

from dotenv import load_dotenv
load_dotenv()

import os
import time
import requests
from typing import Optional, Dict, Any, List, Tuple

OWM_KEY = os.getenv("WEATHER_API_KEY")

# Separate caches (cleaner & safer)
_geo_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_current_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
_forecast_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
_aqi_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

# TTLs
GEO_TTL = 24 * 3600       # 24 hours
CURRENT_TTL = 5 * 60      # 5 min
FORECAST_TTL = 10 * 60    # 10 min
AQI_TTL = 60 * 60         # 1 hour

HEADERS = {"User-Agent": "KEN-Assistant-Weather/1.0"}


def _now_ts() -> float:
    return time.time()


# --------------------------------------------------------
# GEOCODING
# --------------------------------------------------------
def geocode_location(location: str) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    key = location.strip().lower()

    cached = _geo_cache.get(key)
    if cached and _now_ts() - cached[1] < GEO_TTL:
        return cached[0]

    try:
        r = requests.get(
            "http://api.openweathermap.org/geo/1.0/direct",
            params={"q": location, "limit": 1, "appid": OWM_KEY},
            timeout=8,
            headers=HEADERS
        )

        if r.status_code != 200:
            return None

        data = r.json()
        if not data:
            return None

        top = data[0]
        result = {
            "name": top.get("name"),
            "lat": top.get("lat"),
            "lon": top.get("lon"),
            "country": top.get("country"),
            "state": top.get("state"),
        }

        _geo_cache[key] = (result, _now_ts())
        return result

    except Exception:
        return None


# --------------------------------------------------------
# CURRENT WEATHER
# --------------------------------------------------------
def _fetch_current_weather_by_coord(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    key = f"cur:{lat:.4f},{lon:.4f}"
    cached = _current_cache.get(key)
    if cached and _now_ts() - cached[1] < CURRENT_TTL:
        return cached[0]

    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": OWM_KEY, "units": "metric"},
            timeout=8,
            headers=HEADERS
        )

        if r.status_code != 200:
            return None

        d = r.json()
        res = {
            "provider": "openweathermap",
            "raw": d,
            "name": d.get("name"),
            "description": d.get("weather", [{}])[0].get("description"),
            "temp_c": d.get("main", {}).get("temp"),
            "feels_like_c": d.get("main", {}).get("feels_like"),
            "humidity": d.get("main", {}).get("humidity"),
            "pressure": d.get("main", {}).get("pressure"),
            "wind_speed_m_s": d.get("wind", {}).get("speed"),
            "clouds_pct": d.get("clouds", {}).get("all"),
        }

        _current_cache[key] = (res, _now_ts())
        return res

    except Exception:
        return None


# --------------------------------------------------------
# FORECAST (DAILY AGGREGATE)
# --------------------------------------------------------
def _fetch_forecast_by_coord(lat: float, lon: float, days: int = 5) -> Optional[List[Dict[str, Any]]]:
    if not OWM_KEY:
        return None

    key = f"fc:{lat:.4f},{lon:.4f}:{days}"
    cached = _forecast_cache.get(key)
    if cached and _now_ts() - cached[1] < FORECAST_TTL:
        return cached[0]

    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"lat": lat, "lon": lon, "appid": OWM_KEY, "units": "metric"},
            timeout=10,
            headers=HEADERS
        )

        if r.status_code != 200:
            return None

        d = r.json()
        items = d.get("list", [])

        daily = {}
        for it in items:
            ts = it.get("dt")
            day_str = time.strftime("%Y-%m-%d", time.gmtime(ts))

            slot = daily.setdefault(day_str, {"temps": [], "desc": [], "winds": [], "hum": []})
            main = it.get("main", {})

            slot["temps"].append(main.get("temp"))
            slot["desc"].append(it.get("weather", [{}])[0].get("description"))
            slot["winds"].append(it.get("wind", {}).get("speed"))
            slot["hum"].append(main.get("humidity"))

        result = []
        for day in sorted(daily.keys())[:days]:
            e = daily[day]

            temps = [t for t in e["temps"] if t is not None]
            winds = [w for w in e["winds"] if w is not None]
            hums =  [h for h in e["hum"] if h is not None]

            result.append({
                "date": day,
                "temp_min_c": min(temps) if temps else None,
                "temp_max_c": max(temps) if temps else None,
                "temp_avg_c": sum(temps)/len(temps) if temps else None,
                "common_description": max(set(e["desc"]), key=e["desc"].count) if e["desc"] else None,
                "wind_avg_m_s": sum(winds)/len(winds) if winds else None,
                "humidity_avg": sum(hums)/len(hums) if hums else None,
            })

        _forecast_cache[key] = (result, _now_ts())
        return result

    except Exception:
        return None


# --------------------------------------------------------
# AIR QUALITY
# --------------------------------------------------------
def _fetch_aqi_by_coord(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    if not OWM_KEY:
        return None

    key = f"aqi:{lat:.4f},{lon:.4f}"
    cached = _aqi_cache.get(key)
    if cached and _now_ts() - cached[1] < AQI_TTL:
        return cached[0]

    try:
        r = requests.get(
            "http://api.openweathermap.org/data/2.5/air_pollution",
            params={"lat": lat, "lon": lon, "appid": OWM_KEY},
            timeout=8,
            headers=HEADERS
        )

        if r.status_code != 200:
            return None

        d = r.json()
        if "list" in d and d["list"]:
            el = d["list"][0]
            result = {
                "aqi_index": el.get("main", {}).get("aqi"),
                "components": el.get("components", {})
            }

            _aqi_cache[key] = (result, _now_ts())
            return result

        return None

    except Exception:
        return None


# --------------------------------------------------------
# FINAL PACKET
# --------------------------------------------------------
def build_weather_packet(location: str, forecast_days: int = 5) -> Optional[Dict[str, Any]]:
    if not location:
        return None

    geo = geocode_location(location)
    if not geo:
        return None

    lat, lon = geo.get("lat"), geo.get("lon")
    if lat is None or lon is None:
        return None

    current = _fetch_current_weather_by_coord(lat, lon)
    forecast = _fetch_forecast_by_coord(lat, lon, days=forecast_days)
    aqi = _fetch_aqi_by_coord(lat, lon)

    return {
        "location": geo.get("name") or location,
        "country": geo.get("country"),
        "state": geo.get("state"),
        "lat": lat,
        "lon": lon,
        "current": current,
        "forecast": forecast,
        "aqi": aqi,
        "fetched_at": _now_ts(),
    }


# --------------------------------------------------------
# FORMAT FOR LLM
# --------------------------------------------------------
def format_packet_for_prompt(packet: Dict[str, Any]) -> str:
    if not packet:
        return "Weather data unavailable."

    lines = []

    loc = packet.get("location")
    country = packet.get("country")
    lines.append(f"{loc}{', ' + country if country else ''}")

    cur = packet.get("current") or {}
    if cur:
        lines.append(
            f"Current: {cur.get('description','N/A')}, "
            f"{cur.get('temp_c','N/A')}°C (feels like {cur.get('feels_like_c','N/A')}°C)"
        )
        lines.append(
            f"Humidity: {cur.get('humidity','N/A')}% | "
            f"Wind: {cur.get('wind_speed_m_s','N/A')} m/s | "
            f"Clouds: {cur.get('clouds_pct','N/A')}%"
        )

    aqi = packet.get("aqi")
    if aqi:
        aqi_val = aqi.get("aqi_index")
        aqi_text = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}.get(aqi_val, "Unknown")
        lines.append(f"AQI: {aqi_val} ({aqi_text})")

    fc = packet.get("forecast") or []
    for day in fc[:3]:
        lines.append(
            f"{day.get('date')}: {day.get('common_description','N/A')}, "
            f"{day.get('temp_min_c','N/A')}°C - {day.get('temp_max_c','N/A')}°C"
        )

    return " | ".join(lines)


# --------------------------------------------------------
# UTILITY FOR MAIN.PY
# --------------------------------------------------------
def get_weather_summary_for_prompt(location: str, forecast_days: int = 5) -> Optional[Dict[str, Any]]:
    pkt = build_weather_packet(location, forecast_days)
    if not pkt:
        return None

    return {
        "packet": pkt,
        "summary": format_packet_for_prompt(pkt)
    }
