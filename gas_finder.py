#!/usr/bin/env python3
"""
Gas Price Finder - Find cheapest gas/diesel/E85 near your location
Uses: py-gasbuddy (US/Canada) + FuelWide (Europe/International)
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

# Import local cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gas_finder_cache import get_gasbuddy_cache
from gas_finder_history import get_price_history

try:
    from py_gasbuddy import GasBuddy

    GASBUDDY_AVAILABLE = True
except ImportError:
    GASBUDDY_AVAILABLE = False

try:
    import pandas  # noqa: F401

    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

GAS_BUDDY_MAX_RETRIES = 3
GAS_BUDDY_BASE_DELAY = 3
GAS_BUDDY_INTER_POINT_DELAY = 2

DEFAULT_TIMEOUT = 5
GAS_BUDDY_API_TIMEOUT = 20  # Increased timeout


@dataclass
class FuelPrice:
    station: str
    address: str
    latitude: float
    longitude: float
    fuel_type: str
    price: float
    currency: str
    updated: str
    distance_km: float | None = None
    source: str = ""


class GasFinder:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "GasFinder/1.0 (https://github.com/user/gas-finder)"}
        )
        self.gasbuddy = None
        if GASBUDDY_AVAILABLE:
            self.gasbuddy = GasBuddy()

    def _clear_token_cache(self):
        try:
            cache_path = os.path.expanduser("~/.cache/py_gasbuddy/token")
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"Cleared GasBuddy token cache: {cache_path}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to clear token cache: {e}", file=sys.stderr)

    def _recreate_gasbuddy(self):
        self._clear_token_cache()
        for attempt in range(GAS_BUDDY_MAX_RETRIES):
            try:
                self.gasbuddy = GasBuddy()
                return True
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limited = (
                    "rate limited" in error_str
                    or "cloudflare" in error_str
                    or "1015" in error_str
                    or "just a moment" in error_str
                    or "access denied" in error_str
                    or "ray id" in error_str
                    or "429" in error_str
                    or "missing token" in error_str
                )
                if is_rate_limited and attempt < GAS_BUDDY_MAX_RETRIES - 1:
                    delay = GAS_BUDDY_BASE_DELAY * (2**attempt)
                    print(
                        f"GasBuddy reinit rate limited, retrying in {delay}s...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                if attempt < GAS_BUDDY_MAX_RETRIES - 1:
                    delay = GAS_BUDDY_BASE_DELAY * (2**attempt)
                    print(
                        f"GasBuddy reinit failed: {e}, retrying in {delay}s...",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                else:
                    print(
                        f"GasBuddy reinit failed after {GAS_BUDDY_MAX_RETRIES} attempts: {e}",
                        file=sys.stderr,
                    )
        return False

    async def close(self):
        self.gasbuddy = None

    def _safe_get(
        self, url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT
    ) -> requests.Response | None:
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            return resp
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
            socket.gaierror,
        ) as e:
            print(f"Network error for {url}: {e}", file=sys.stderr)
            return None

    def get_location_from_ip(self) -> dict | None:
        resp = self._safe_get("http://ip-api.com/json/")
        if resp and resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return {
                    "lat": data["lat"],
                    "lon": data["lon"],
                    "city": data.get("city", ""),
                    "region": data.get("regionName", ""),
                    "country": data.get("countryCode", ""),
                    "zip": data.get("zip", ""),
                }
        return None

    def geocode_address(self, address: str) -> dict | None:
        resp = self._safe_get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
        )
        if resp and resp.status_code == 200:
            data = resp.json()
            if data:
                return {
                    "lat": float(data[0]["lat"]),
                    "lon": float(data[0]["lon"]),
                    "display_name": data[0]["display_name"],
                }
        return None

    def _calc_distance(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        from math import atan2, cos, radians, sin, sqrt

        R = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    async def search_gasbuddy(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        fuel_types: list[str] | None = None,
    ) -> list[FuelPrice]:
        prices: list[FuelPrice] = []
        if not self.gasbuddy:
            return prices

        cache = get_gasbuddy_cache()
        cached = cache.get(lat, lon, radius_km, fuel_types or [])
        if cached:
            print(f"Using cached GasBuddy data ({len(cached)} prices)", file=sys.stderr)
            return [
                FuelPrice(
                    station=p.station,
                    address=p.address,
                    latitude=p.latitude,
                    longitude=p.longitude,
                    fuel_type=p.fuel_type,
                    price=p.price,
                    currency=p.currency,
                    updated=p.updated,
                    distance_km=p.distance_km,
                    source=p.source + " (cached)",
                )
                for p in cached
            ]

        fuel_key_map = {
            "regular": "regular_gas",
            "midgrade": "midgrade_gas",
            "premium": "premium_gas",
            "diesel": "diesel",
            "e85": "e85",
            "e10": "regular_gas",
        }

        search_points = [(lat, lon)]
        seen_stations = set()

        for i, (search_lat, search_lon) in enumerate(search_points):
            if i > 0:
                await asyncio.sleep(GAS_BUDDY_INTER_POINT_DELAY)

            for attempt in range(GAS_BUDDY_MAX_RETRIES):
                try:
                    # Add timeout to the GasBuddy API call
                    result = await asyncio.wait_for(
                        self.gasbuddy.price_lookup_service(
                            lat=search_lat, lon=search_lon, limit=100
                        ),
                        timeout=GAS_BUDDY_API_TIMEOUT,
                    )

                    if isinstance(result, dict) and "error" in result:
                        error_msg = str(result["error"]).lower()
                        is_rate_limited = (
                            "rate limited" in error_msg
                            or "cloudflare" in error_msg
                            or "1015" in error_msg
                            or "just a moment" in error_msg
                            or "access denied" in error_msg
                            or "ray id" in error_msg
                        )
                        is_auth_error = (
                            "401" in error_msg
                            or "403" in error_msg
                            or "unauthorized" in error_msg
                            or "forbidden" in error_msg
                            or "invalid token" in error_msg
                            or "authentication" in error_msg
                        )
                        if (
                            is_auth_error
                            and attempt == 0
                            and not getattr(self, "_token_refreshed", False)
                        ):
                            print(
                                "GasBuddy auth error in response, refreshing token...",
                                file=sys.stderr,
                            )
                            self._token_refreshed = True
                            if self._recreate_gasbuddy():
                                continue
                        if is_rate_limited:
                            if attempt < GAS_BUDDY_MAX_RETRIES - 1:
                                delay = GAS_BUDDY_BASE_DELAY * (2**attempt)
                                print(
                                    f"GasBuddy rate limited, retrying in {delay}s...",
                                    file=sys.stderr,
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                print(
                                    f"GasBuddy rate limited after {GAS_BUDDY_MAX_RETRIES} attempts, skipping",
                                    file=sys.stderr,
                                )
                                break

                    if not result or not result.get("results"):
                        break

                    for station in result.get("results", []):
                        station_lat = station.get("latitude", 0)
                        station_lon = station.get("longitude", 0)

                        station_key = f"{round(station_lat, 4)},{round(station_lon, 4)}"
                        if station_key in seen_stations:
                            continue
                        seen_stations.add(station_key)

                        dist = self._calc_distance(lat, lon, station_lat, station_lon)
                        if dist > radius_km:
                            continue

                        addr = station.get("address", {})
                        address_str = (
                            f"{addr.get('line1', '')}, {addr.get('locality', '')}, "
                            f"{addr.get('region', '')} {addr.get('postalCode', '')}"
                        )
                        fuels_list = station.get("fuels", [])

                        for our_fuel, gb_key in fuel_key_map.items():
                            if fuel_types and our_fuel not in [
                                f.lower() for f in fuel_types
                            ]:
                                continue

                            fuel_data = station.get(gb_key)

                            if our_fuel == "e85" and "e85" in fuels_list:
                                price = fuel_data.get("price") if fuel_data else None
                                if price is not None and price > 0:
                                    prices.append(
                                        FuelPrice(
                                            station=station.get("name", "Unknown"),
                                            address=address_str.strip(", "),
                                            latitude=station_lat,
                                            longitude=station_lon,
                                            fuel_type=our_fuel.upper(),
                                            price=price,
                                            currency=station.get("currency", "USD"),
                                            updated=fuel_data.get(
                                                "last_updated",
                                                datetime.now().isoformat(),
                                            ),
                                            distance_km=round(dist, 1),
                                            source="GasBuddy",
                                        )
                                    )
                                else:
                                    prices.append(
                                        FuelPrice(
                                            station=station.get("name", "Unknown"),
                                            address=address_str.strip(", "),
                                            latitude=station_lat,
                                            longitude=station_lon,
                                            fuel_type=our_fuel.upper(),
                                            price=0.0,
                                            currency=station.get("currency", "USD"),
                                            updated=fuel_data.get(
                                                "last_updated",
                                                datetime.now().isoformat(),
                                            )
                                            if fuel_data
                                            else datetime.now().isoformat(),
                                            distance_km=round(dist, 1),
                                            source="GasBuddy",
                                        )
                                    )
                                continue

                            if not fuel_data:
                                continue

                            price = fuel_data.get("price")
                            if price is None or price <= 0:
                                continue

                            prices.append(
                                FuelPrice(
                                    station=station.get("name", "Unknown"),
                                    address=address_str.strip(", "),
                                    latitude=station_lat,
                                    longitude=station_lon,
                                    fuel_type=our_fuel.upper(),
                                    price=price,
                                    currency=station.get("currency", "USD"),
                                    updated=fuel_data.get(
                                        "last_updated", datetime.now().isoformat()
                                    ),
                                    distance_km=round(dist, 1),
                                    source="GasBuddy",
                                )
                            )
                    break

                except TimeoutError:
                    print(
                        f"GasBuddy API timeout ({GAS_BUDDY_API_TIMEOUT}s), "
                        f"attempt {attempt + 1}/{GAS_BUDDY_MAX_RETRIES}",
                        file=sys.stderr,
                    )
                    if attempt < GAS_BUDDY_MAX_RETRIES - 1:
                        await asyncio.sleep(GAS_BUDDY_BASE_DELAY * (2**attempt))
                        continue
                    else:
                        print(
                            f"GasBuddy API timeout after {GAS_BUDDY_MAX_RETRIES} attempts, skipping",
                            file=sys.stderr,
                        )
                        break
                except Exception as e:
                    error_str = str(e).lower()
                    is_rate_limited = (
                        "rate limited" in error_str
                        or "cloudflare" in error_str
                        or "1015" in error_str
                        or "just a moment" in error_str
                        or "access denied" in error_str
                        or "ray id" in error_str
                        or "429" in error_str
                        or "missing token" in error_str
                    )
                    is_auth_error = (
                        "401" in error_str
                        or "403" in error_str
                        or "unauthorized" in error_str
                        or "forbidden" in error_str
                        or "invalid token" in error_str
                        or "authentication" in error_str
                    )

                    if (
                        is_auth_error
                        and attempt == 0
                        and not getattr(self, "_token_refreshed", False)
                    ):
                        print(
                            "GasBuddy auth error detected, refreshing token...",
                            file=sys.stderr,
                        )
                        self._token_refreshed = True
                        if self._recreate_gasbuddy():
                            continue

                    if is_rate_limited and attempt < GAS_BUDDY_MAX_RETRIES - 1:
                        delay = GAS_BUDDY_BASE_DELAY * (2**attempt)
                        print(
                            f"GasBuddy rate limited (exception), retrying in {delay}s...",
                            file=sys.stderr,
                        )
                        await asyncio.sleep(delay)
                        continue
                    if attempt < GAS_BUDDY_MAX_RETRIES - 1:
                        delay = GAS_BUDDY_BASE_DELAY * (2**attempt)
                        print(
                            f"GasBuddy search failed: {e}, retrying in {delay}s...",
                            file=sys.stderr,
                        )
                        await asyncio.sleep(delay)
                    else:
                        print(
                            f"GasBuddy search failed after {GAS_BUDDY_MAX_RETRIES} attempts: {e}",
                            file=sys.stderr,
                        )

        if prices:
            cache = get_gasbuddy_cache()
            cache.set(lat, lon, radius_km, fuel_types or [], prices)
            print(f"Cached {len(prices)} GasBuddy prices", file=sys.stderr)

        return prices

    def search_fuelwide(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        fuel_types: list[str] | None = None,
    ) -> list[FuelPrice]:
        prices: list[FuelPrice] = []
        country = self._guess_country(lat, lon)
        if country not in ["italy", "spain", "france", "australia"]:
            return prices

        fuel_param = ""
        if fuel_types and isinstance(fuel_types, list) and len(fuel_types) > 0:
            fw_fuels = {
                "diesel": "diesel",
                "e5": "e5",
                "e10": "e10",
                "lpg": "lpg",
                "regular": "petrol95",
                "premium": "petrol98",
            }
            fuel_param = fw_fuels.get(str(fuel_types[0]).lower(), "")

        url = f"https://fuelwide.com/api/stations/{country}"
        params = {
            "lat": lat,
            "lon": lon,
            "km": radius_km * 5,
            "sort": "dist",
            "limit": 100,
        }
        if fuel_param:
            params["fuel"] = fuel_param

        resp = self._safe_get(url, params=params, timeout=8)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                for station in data.get("stations", []):
                    station_lat = station.get("lat", 0)
                    station_lon = station.get("lon", 0)
                    dist = self._calc_distance(lat, lon, station_lat, station_lon)
                    if dist > radius_km:
                        continue

                    addr_parts = [
                        station.get("address", ""),
                        station.get("town", ""),
                        station.get("postcode", ""),
                    ]
                    address_str = ", ".join([p for p in addr_parts if p])

                    station_prices = station.get("prices", {})
                    fuel_key_map = {
                        "petrol95": "REGULAR",
                        "petrol98": "PREMIUM",
                        "diesel": "DIESEL",
                        "dieselPremium": "DIESEL_PREMIUM",
                        "lpg": "LPG",
                        "e5": "E5",
                        "e10": "E10",
                    }

                    for fw_key, price in station_prices.items():
                        if price is None or price <= 0:
                            continue
                        our_fuel = fuel_key_map.get(fw_key, fw_key.upper())
                        if fuel_types and our_fuel.lower() not in [
                            f.lower() for f in fuel_types
                        ]:
                            continue

                        prices.append(
                            FuelPrice(
                                station=station.get("brand")
                                or station.get("id", "Unknown"),
                                address=address_str,
                                latitude=station_lat,
                                longitude=station_lon,
                                fuel_type=our_fuel,
                                price=price,
                                currency="EUR",
                                updated=data.get(
                                    "reportedAt", datetime.now().isoformat()
                                ),
                                distance_km=round(dist, 1),
                                source="FuelWide",
                            )
                        )
            except Exception as e:
                print(f"FuelWide parse error: {e}", file=sys.stderr)
        return prices

    def _normalize_address(self, address: str) -> str:
        import re

        return re.sub(r"[^a-z0-9]", "", address.lower())[:15]

    async def search_afdc(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        fuel_types: list[str] | None = None,
    ) -> list[FuelPrice]:
        """Search NREL AFDC stations via ArcGIS FeatureServer (fallback for DNS issues)."""
        prices: list[FuelPrice] = []
        # Only search for E85 via AFDC (other fuels better covered by GasBuddy/FuelWide)
        afdc_fuels = {"e85", "cng", "lng", "lpg", "electric", "hydrogen"}
        if fuel_types:
            requested = {f.lower() for f in fuel_types}
            if not requested & afdc_fuels:
                return prices
        else:
            # Default to E85 if no fuel types specified
            requested = {"e85"}

        # Map our fuel types to AFDC fuel_type_code
        fuel_code_map = {
            "e85": "E85",
            "cng": "CNG",
            "lng": "LNG",
            "lpg": "LPG",
            "electric": "ELEC",
            "hydrogen": "HY",
        }

        for fuel in requested:
            if fuel not in fuel_code_map:
                continue
            afdc_code = fuel_code_map[fuel]

            # ArcGIS FeatureServer query - no radius support, so filter by state
            # We'll fetch for the state containing the lat/lon
            state = self._latlon_to_state(lat, lon)
            if not state:
                continue

            url = "https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/Alternative_Fueling_Stations/FeatureServer/0/query"
            params = {
                "where": f"fuel_type_code = '{afdc_code}' AND state = '{state}'",
                "outFields": "*",
                "f": "json",
                "resultRecordCount": 200,
            }

            resp = self._safe_get(url, params=params, timeout=15)
            if not resp or resp.status_code != 200:
                print(f"AFDC ({afdc_code}): Request failed", file=sys.stderr)
                continue

            try:
                data = resp.json()
                for feature in data.get("features", []):
                    attrs = feature.get("attributes", {})
                    geom = feature.get("geometry", {})
                    station_lat = attrs.get("latitude") or geom.get("y")
                    station_lon = attrs.get("longitude") or geom.get("x")
                    if not station_lat or not station_lon:
                        continue

                    dist = self._calc_distance(lat, lon, station_lat, station_lon)
                    if dist > radius_km:
                        continue

                    addr_parts = [
                        attrs.get("street_address", ""),
                        attrs.get("city", ""),
                        attrs.get("state", ""),
                        attrs.get("zip", ""),
                    ]
                    address_str = ", ".join([p for p in addr_parts if p])

                    # AFDC doesn't have prices, only station locations
                    prices.append(
                        FuelPrice(
                            station=attrs.get("station_name", "Unknown"),
                            address=address_str,
                            latitude=station_lat,
                            longitude=station_lon,
                            fuel_type=fuel.upper(),
                            price=0.0,  # No price data from AFDC
                            currency="USD",
                            updated=attrs.get("updated_at", datetime.now().isoformat()),
                            distance_km=round(dist, 1),
                            source="NREL AFDC",
                        )
                    )
            except Exception as e:
                print(f"AFDC ({afdc_code}) parse error: {e}", file=sys.stderr)

        return prices

    def _latlon_to_state(self, lat: float, lon: float) -> str | None:
        """Approximate state from lat/lon for US coordinates."""
        # Bounding boxes for US states (simplified)
        state_bounds = {
            "AL": (30.2, 35.0, -88.5, -84.9),
            "AK": (51.2, 71.4, -179.1, -129.9),
            "AZ": (31.3, 37.0, -114.8, -109.0),
            "AR": (33.0, 36.5, -94.6, -89.6),
            "CA": (32.5, 42.0, -124.5, -114.1),
            "CO": (37.0, 41.0, -109.1, -102.0),
            "CT": (41.0, 42.1, -73.7, -71.8),
            "DE": (38.5, 39.8, -75.8, -75.0),
            "FL": (24.4, 31.0, -87.6, -80.0),
            "GA": (30.4, 35.0, -85.6, -80.8),
            "HI": (18.9, 22.2, -160.3, -154.8),
            "ID": (42.0, 49.0, -117.3, -111.0),
            "IL": (37.0, 42.5, -91.5, -87.5),
            "IN": (37.8, 41.8, -88.1, -84.8),
            "IA": (40.4, 43.5, -96.6, -90.1),
            "KS": (37.0, 40.0, -102.1, -94.6),
            "KY": (36.5, 39.2, -89.6, -81.9),
            "LA": (28.9, 33.0, -94.0, -88.8),
            "ME": (43.1, 47.5, -71.1, -66.9),
            "MD": (38.0, 39.7, -79.5, -75.0),
            "MA": (41.2, 42.9, -73.5, -69.9),
            "MI": (41.7, 48.3, -90.4, -82.1),
            "MN": (43.5, 49.4, -97.2, -89.5),
            "MS": (30.2, 35.0, -91.6, -88.1),
            "MO": (36.0, 40.6, -95.8, -89.1),
            "MT": (44.4, 49.0, -116.0, -104.0),
            "NE": (40.0, 43.0, -104.1, -95.3),
            "NV": (35.0, 42.0, -120.0, -114.0),
            "NH": (42.7, 45.2, -72.6, -70.6),
            "NJ": (38.9, 41.4, -75.6, -73.9),
            "NM": (31.3, 37.0, -109.0, -103.0),
            "NY": (40.5, 45.0, -79.8, -71.8),
            "NC": (33.8, 36.6, -84.3, -75.5),
            "ND": (45.9, 49.0, -104.0, -96.6),
            "OH": (38.4, 42.3, -84.8, -80.5),
            "OK": (33.6, 37.0, -103.0, -94.4),
            "OR": (42.0, 46.3, -124.6, -116.5),
            "PA": (39.7, 42.3, -80.5, -74.7),
            "RI": (41.1, 42.0, -71.8, -71.1),
            "SC": (32.0, 35.2, -83.4, -78.5),
            "SD": (42.5, 45.9, -104.1, -96.4),
            "TN": (35.0, 36.7, -90.3, -81.6),
            "TX": (25.8, 36.5, -106.6, -93.5),
            "UT": (37.0, 42.0, -114.1, -109.0),
            "VT": (42.7, 45.0, -73.4, -71.5),
            "VA": (36.5, 39.5, -83.7, -75.2),
            "WA": (45.5, 49.0, -124.8, -116.9),
            "WV": (37.2, 40.6, -82.6, -77.7),
            "WI": (42.5, 47.1, -92.9, -86.8),
            "WY": (41.0, 45.0, -111.1, -104.0),
        }

        for state, (min_lat, max_lat, min_lon, max_lon) in state_bounds.items():
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                return state
        return None

    def _guess_country(self, lat: float, lon: float) -> str:
        if 36 <= lat <= 48 and 6 <= lon <= 19:
            return "italy"
        elif 36 <= lat <= 44 and -10 <= lon <= 5:
            return "spain"
        elif 42 <= lat <= 51 and -5 <= lon <= 10:
            return "france"
        elif -44 <= lat <= -10 and 110 <= lon <= 155:
            return "australia"
        return "us"

    async def find_prices(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10,
        fuel_types: list[str] | None = None,
    ) -> list[FuelPrice]:
        all_prices: list[FuelPrice] = []

        if GASBUDDY_AVAILABLE:
            print("Searching GasBuddy...", file=sys.stderr)
            all_prices.extend(
                await self.search_gasbuddy(lat, lon, radius_km, fuel_types)
            )

        country = self._guess_country(lat, lon)
        if country in ["italy", "spain", "france", "australia"]:
            print("Searching FuelWide...", file=sys.stderr)
            try:
                all_prices.extend(self.search_fuelwide(lat, lon, radius_km, fuel_types))
            except Exception as e:
                print(f"FuelWide search skipped: {e}", file=sys.stderr)

        # Search AFDC for US locations (alternative fuel station locations)
        if country == "us":
            print("Searching NREL AFDC...", file=sys.stderr)
            try:
                all_prices.extend(await self.search_afdc(lat, lon, radius_km, fuel_types))
            except Exception as e:
                print(f"AFDC search skipped: {e}", file=sys.stderr)

        if fuel_types and all_prices:
            fuel_types_upper = [f.upper() for f in fuel_types]
            all_prices = [
                p for p in all_prices if p.fuel_type.upper() in fuel_types_upper
            ]

        seen = set()
        unique_prices: list[FuelPrice] = []
        for p in all_prices:
            key = f"{round(p.latitude, 4)},{round(p.longitude, 4)}"
            if key not in seen:
                seen.add(key)
                unique_prices.append(p)
            else:
                existing_idx = next(
                    i
                    for i, up in enumerate(unique_prices)
                    if f"{round(up.latitude, 4)},{round(up.longitude, 4)}" == key
                )
                if p.price > 0 and unique_prices[existing_idx].price == 0:
                    unique_prices[existing_idx] = p

        unique_prices.sort(key=lambda p: (p.price == 0, p.price))

        # Record prices to history for trend tracking
        try:
            history = get_price_history()
            inserted = history.record_prices(unique_prices)
            if inserted:
                print(f"Recorded {inserted} price snapshots to history", file=sys.stderr)
        except Exception as e:
            print(f"History recording failed: {e}", file=sys.stderr)

        return unique_prices


    async def find_prices_along_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        fuel_types: list[str] | None = None,
        sample_every_km: float = 5,
        search_radius_km: float = 5,
    ) -> list[FuelPrice]:
        """Search for fuel stations along a driving route between two points.

        Samples points along a straight line (approximating the driving route)
        and searches for stations near each sample point. Results are tagged
        with distance along the route so users can see where to stop.
        """
        import math

        all_prices: list[FuelPrice] = []

        # Calculate total distance
        total_dist = self._calc_distance(start_lat, start_lon, end_lat, end_lon)
        if total_dist == 0:
            return []

        # Number of sample points along the route
        num_samples = max(2, int(math.ceil(total_dist / sample_every_km)))
        sample_distances = [
            i * total_dist / (num_samples - 1) for i in range(num_samples)
        ]

        # Search near each sample point
        seen_stations: set[str] = set()
        for i, dist_along in enumerate(sample_distances):
            frac = dist_along / total_dist
            # Linear interpolation (great circle approximation)
            sample_lat = start_lat + frac * (end_lat - start_lat)
            sample_lon = start_lon + frac * (end_lon - start_lon)

            prices = await self.find_prices(
                sample_lat,
                sample_lon,
                radius_km=search_radius_km,
                fuel_types=fuel_types,
            )

            for p in prices:
                station_key = f"{round(p.latitude, 4)},{round(p.longitude, 4)}"
                if station_key in seen_stations:
                    continue
                seen_stations.add(station_key)

                # Distance from start along route (km)
                p.distance_km = round(dist_along, 1)
                # Add route context
                p.address = f"{p.address} ({dist_along:.1f} km from start)"
                all_prices.append(p)

        all_prices.sort(key=lambda p: (p.price == 0, p.price))
        return all_prices


def format_price(price: FuelPrice) -> str:
    dist = f" ({price.distance_km:.1f} km)" if price.distance_km else ""
    updated = f" - Updated: {price.updated[:10]}" if price.updated else ""
    return (
        f"  {price.station}{dist} [{price.source}]\n    {price.address}\n    "
        f"{price.fuel_type}: {price.price:.3f} {price.currency}{updated}"
    )


async def main_async(args):
    finder = GasFinder()

    lat, lon = None, None
    location_name = ""

    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
        location_name = f"coordinates ({lat}, {lon})"
    elif args.location:
        print(f"Geocoding: {args.location}...", file=sys.stderr)
        geo = finder.geocode_address(args.location)
        if geo:
            lat, lon = geo["lat"], geo["lon"]
            location_name = geo["display_name"]
        else:
            print(
                "Failed to geocode location. Use --lat/--lon instead.", file=sys.stderr
            )
            sys.exit(1)
    else:
        print("Detecting location from IP...", file=sys.stderr)
        ip_loc = finder.get_location_from_ip()
        if ip_loc:
            lat, lon = ip_loc["lat"], ip_loc["lon"]
            location_name = (
                f"{ip_loc['city']}, {ip_loc['region']} ({ip_loc['country']})"
            )
        else:
            print(
                "Failed to detect location. Use --location or --lat/--lon",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Searching near: {location_name}", file=sys.stderr)
    print(f"Radius: {args.radius} km", file=sys.stderr)
    if args.fuel:
        print(f"Fuel types: {', '.join(args.fuel)}", file=sys.stderr)

    prices = await finder.find_prices(lat, lon, args.radius, args.fuel)

    if not prices:
        print(
            "No prices found. Try increasing radius or different location.",
            file=sys.stderr,
        )
        sys.exit(0)

    if args.json:
        output = [asdict(p) for p in prices[: args.limit]]
        print(json.dumps(output, indent=2))
    else:
        print(
            f"\nFound {len(prices)} prices (showing top {min(args.limit, len(prices))}):\n"
        )
        for i, price in enumerate(prices[: args.limit], 1):
            print(f"{i}. {format_price(price)}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Find cheapest gas/diesel/E85 near you"
    )
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--location", "-l", type=str, help="ZIP code, city, or address")
    parser.add_argument(
        "--radius",
        "-r",
        type=float,
        default=10,
        help="Search radius in km (default: 10)",
    )
    parser.add_argument(
        "--fuel",
        "-f",
        action="append",
        choices=["regular", "midgrade", "premium", "diesel", "e85", "e10", "e5", "lpg"],
        help="Filter by fuel type (can use multiple times)",
    )
    parser.add_argument(
        "--limit", "-n", type=int, default=10, help="Max results to show (default: 10)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
