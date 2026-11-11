#!/usr/bin/env python3
"""
Local cache for GasBuddy results to handle rate limiting
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


CACHE_DIR = Path.home() / ".cache" / "gas_finder"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "gasbuddy_cache.json"
CACHE_TTL = 86400  # 24 hours


@dataclass
class CachedPrice:
    station: str
    address: str
    latitude: float
    longitude: float
    fuel_type: str
    price: float
    currency: str
    updated: str
    distance_km: float
    source: str
    cached_at: float


class GasBuddyCache:
    def __init__(self):
        self.cache_file = CACHE_FILE
        self.cache_ttl = CACHE_TTL
        self._cache: dict[str, list[CachedPrice]] = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    data = json.load(f)
                for key, prices in data.items():
                    self._cache[key] = [CachedPrice(**p) for p in prices]
            except Exception as e:
                self._cache = {}

    def _save(self):
        try:
            data = {k: [asdict(p) for p in v] for k, v in self._cache.items()}
            with open(self.cache_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            pass

    def _make_key(
        self, lat: float, lon: float, radius_km: float, fuel_types: list[str]
    ) -> str:
        # Round coordinates to ~1km grid for cache key
        lat_key = round(lat * 100) / 100
        lon_key = round(lon * 100) / 100
        fuels = ",".join(sorted(f.lower() for f in fuel_types)) if fuel_types else "all"
        key = f"{lat_key},{lon_key},{int(radius_km)},{fuels}"
        return key

    def get(
        self, lat: float, lon: float, radius_km: float, fuel_types: list[str]
    ) -> list[CachedPrice] | None:
        key = self._make_key(lat, lon, radius_km, fuel_types)
        if key in self._cache:
            prices = self._cache[key]
            # Check if cache is still valid
            if prices and time.time() - prices[0].cached_at < self.cache_ttl:
                return prices
            else:
                # Expired - remove
                del self._cache[key]
                self._save()
        return None

    def set(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        fuel_types: list[str],
        prices: list,
    ):
        key = self._make_key(lat, lon, radius_km, fuel_types)
        cached_prices = []
        for p in prices:
            # Handle both FuelPrice objects and dicts
            if hasattr(p, "station"):
                cached_prices.append(
                    CachedPrice(
                        station=p.station,
                        address=p.address,
                        latitude=p.latitude,
                        longitude=p.longitude,
                        fuel_type=p.fuel_type,
                        price=p.price,
                        currency=p.currency,
                        updated=p.updated,
                        distance_km=p.distance_km or 0,
                        source=p.source,
                        cached_at=time.time(),
                    )
                )
        if cached_prices:
            self._cache[key] = cached_prices
            self._save()

    def clear_expired(self):
        now = time.time()
        keys_to_delete = []
        for key, prices in self._cache.items():
            if prices and now - prices[0].cached_at >= self.cache_ttl:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del self._cache[key]
        if keys_to_delete:
            self._save()


# Global cache instance
_gasbuddy_cache = None


def get_gasbuddy_cache() -> GasBuddyCache:
    global _gasbuddy_cache
    if _gasbuddy_cache is None:
        _gasbuddy_cache = GasBuddyCache()
    return _gasbuddy_cache
