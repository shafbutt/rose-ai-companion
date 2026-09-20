"""
ROSE Location & Weather Module
Provides location detection and weather information using free APIs.

Location: Uses ip-api.com (free, no key required) for IP-based geolocation.
Weather: Uses Open-Meteo API (free, no key required) for current weather + forecast.

Both features require permission checks before use.
"""
import json
import urllib.request
import urllib.error
import threading
import time
import logging

logger = logging.getLogger("rose.weather")

# Cache location to avoid repeated API calls
_location_cache = {"data": None, "timestamp": 0}
_LOCATION_CACHE_TTL = 3600  # 1 hour


def get_location() -> dict:
    """
    Get approximate location based on IP address.
    Returns dict with city, region, country, lat, lon, timezone.
    Returns None if unavailable.
    """
    now = time.time()
    if _location_cache["data"] and (now - _location_cache["timestamp"]) < _LOCATION_CACHE_TTL:
        return _location_cache["data"]

    try:
        url = "http://ip-api.com/json/?fields=status,message,country,countryCode,region,regionName,city,lat,lon,timezone"
        req = urllib.request.Request(url, headers={"User-Agent": "ROSE-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        if data.get("status") != "success":
            logger.warning(f"Location API error: {data.get('message', 'unknown')}")
            return None

        result = {
            "city": data.get("city", "Unknown"),
            "region": data.get("regionName", "Unknown"),
            "country": data.get("country", "Unknown"),
            "country_code": data.get("countryCode", ""),
            "latitude": data.get("lat"),
            "longitude": data.get("lon"),
            "timezone": data.get("timezone", "Unknown"),
        }
        _location_cache["data"] = result
        _location_cache["timestamp"] = now
        return result

    except Exception as e:
        logger.warning(f"Location fetch failed: {e}")
        return None


def get_weather(latitude: float = None, longitude: float = None) -> dict:
    """
    Get current weather from Open-Meteo API.
    If lat/lon not provided, attempts to detect from IP.
    Returns dict with temperature, conditions, humidity, wind, etc.
    """
    # Get location if not provided
    if latitude is None or longitude is None:
        loc = get_location()
        if not loc:
            return {"error": "Could not determine location. Please enable location permission."}
        latitude = loc["latitude"]
        longitude = loc["longitude"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitude}&longitude={longitude}"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            f"weather_code,wind_speed_10m,wind_direction_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
            f"&timezone=auto&forecast_days=3"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ROSE-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        current = data.get("current", {})
        daily = data.get("daily", {})

        # Decode weather condition from WMO code
        weather_code = current.get("weather_code", 0)
        condition = _decode_weather_code(weather_code)

        result = {
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "condition": condition,
            "weather_code": weather_code,
            "forecast": [],
        }

        # Parse 3-day forecast
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        precip_probs = daily.get("precipitation_probability_max", [])

        for i in range(min(len(dates), 3)):
            result["forecast"].append({
                "date": dates[i],
                "high_c": max_temps[i] if i < len(max_temps) else None,
                "low_c": min_temps[i] if i < len(min_temps) else None,
                "condition": _decode_weather_code(codes[i]) if i < len(codes) else "Unknown",
                "rain_chance": precip_probs[i] if i < len(precip_probs) else None,
            })

        return result

    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
        return {"error": f"Could not fetch weather: {e}"}


def _decode_weather_code(code: int) -> str:
    """Convert WMO weather code to human-readable condition."""
    codes = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Foggy", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
        56: "Light freezing drizzle", 57: "Dense freezing drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        66: "Light freezing rain", 67: "Heavy freezing rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
        85: "Slight snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
    }
    return codes.get(code, "Unknown")


def format_weather_reply(weather: dict, location_name: str = "") -> str:
    """Format weather data into a friendly spoken response."""
    if "error" in weather:
        return weather["error"]

    temp = weather.get("temperature_c")
    feels = weather.get("feels_like_c")
    condition = weather.get("condition", "Unknown")
    humidity = weather.get("humidity_percent")
    wind = weather.get("wind_speed_kmh")

    loc_str = f" in {location_name}" if location_name else ""
    parts = [f"It's {condition}{loc_str}."]

    if temp is not None:
        parts.append(f"Temperature is {round(temp)}°C")
        if feels is not None and abs(feels - temp) > 2:
            parts.append(f"but feels like {round(feels)}°C.")
        else:
            parts[-1] += "."
    if humidity is not None:
        parts.append(f"Humidity at {humidity}%.")
    if wind is not None:
        parts.append(f"Wind speed {round(wind)} km/h.")

    return " ".join(parts)


def format_forecast_reply(weather: dict) -> str:
    """Format 3-day forecast into a friendly response."""
    if "error" in weather:
        return weather["error"]

    forecast = weather.get("forecast", [])
    if not forecast:
        return "I don't have forecast data right now."

    parts = ["Here's the forecast:"]
    for day in forecast:
        date = day.get("date", "")
        high = day.get("high_c")
        low = day.get("low_c")
        cond = day.get("condition", "")
        rain = day.get("rain_chance")
        line = f"{date}: {cond}"
        if high is not None and low is not None:
            line += f", {round(low)}–{round(high)}°C"
        if rain is not None:
            line += f", {rain}% rain chance"
        parts.append(line)

    return " ".join(parts)


# ---- Voice command patterns ----
WEATHER_PATTERNS = [
    r".*\b(weather|temperature|how.*(hot|cold|warm)|forecast|will\s*it\s*rain|is\s*it\s*raining)\b.*",
]

FORECAST_PATTERNS = [
    r".*\b(forecast|next\s*few\s*days|coming\s*days|tomorrow.*weather|week.*weather)\b.*",
]


def check_weather_query(text: str) -> bool:
    """Check if user is asking about weather."""
    import re
    lower = text.lower()
    return any(re.match(pat, lower) for pat in WEATHER_PATTERNS)


def check_forecast_query(text: str) -> bool:
    """Check if user is asking about forecast."""
    import re
    lower = text.lower()
    return any(re.match(pat, lower) for pat in FORECAST_PATTERNS)


def handle_weather_query(text: str, permission_checker=None) -> str:
    """
    Handle weather queries. Returns friendly reply string or None if not a weather query.
    permission_checker: optional callable(name) -> bool for permission checks.
    """
    if not check_weather_query(text):
        return None

    # Check location permission
    if permission_checker and not permission_checker("location"):
        return "I don't have location permission. Please enable it in Settings → Permissions to get weather information."

    # Check internet permission
    if permission_checker and not permission_checker("internet"):
        return "I don't have internet permission. I need it to fetch weather data."

    # Check if forecast
    if check_forecast_query(text):
        weather = get_weather()
        if "error" in weather:
            return weather["error"]
        return format_forecast_reply(weather)

    # Current weather
    loc = get_location()
    weather = get_weather()
    if "error" in weather:
        return weather["error"]
    loc_name = loc["city"] if loc else ""
    return format_weather_reply(weather, loc_name)
