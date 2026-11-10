#!/usr/bin/env python3
"""
Local cache for GasBuddy results to handle rate limiting
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

print(f"DEBUG: gas_finder_cache loaded from {__file__}", file=sys.stderr)

CACHE_DIR = Path.home() / '.cache' / 'gas_finder'
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / 'gasbuddy_cache.json'
CACHE_TTL = 86400  # 1 hour

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
        self._cache: Dict[str, List[CachedPrice]] = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    data = json.load(f)
                for key, prices in data.items():
                    self._cache[key] = [CachedPrice(**p) for p in prices]
                print(f"DEBUG: Loaded cache with {len(self._cache)} keys from {self.cache_file}", file=sys.stderr)
            except Exception as e:
                print(f"DEBUG: Cache load failed: {e}", file=sys.stderr)
                self._cache = {}
        else:
            print(f"DEBUG: Cache file not found: {self.cache_file}", file=sys.stderr)

    def _save(self):
        try:
            data = {k: [asdict(p) for p in v] for k, v in self._cache.items()}
            with open(self.cache_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"DEBUG: Cache save failed: {e}", file=sys.stderr)

    def _make_key(self, lat: float, lon: float, radius_km: float, fuel_types: List[str]) -> str:
        # Round coordinates to ~1km grid for cache key
        lat_key = round(lat * 100) / 100
        lon_key = round(lon * 100) / 100
        fuels = ','.join(sorted(f.lower() for f in fuel_types)) if fuel_types else 'all'
        key = f"{lat_key},{lon_key},{int(radius_km)},{fuels}"
        print(f"DEBUG: Cache key: {key} (lat={lat}, lon={lon}, radius={radius_km}, fuels={fuel_types})", file=sys.stderr)
        return key

    def get(self, lat: float, lon: float, radius_km: float, fuel_types: List[str]) -> Optional[List[CachedPrice]]:
        key = self._make_key(lat, lon, radius_km, fuel_types)
        print(f"DEBUG: Cache lookup for key: {key}", file=sys.stderr)
        print(f"DEBUG: Available keys: {list(self._cache.keys())}", file=sys.stderr)
        if key in self._cache:
            prices = self._cache[key]
            # Check if cache is still valid
            if prices and time.time() - prices[0].cached_at < self.cache_ttl:
                print(f"DEBUG: Cache HIT for {key} ({len(prices)} prices, age={time.time() - prices[0].cached_at:.0f}s)", file=sys.stderr)
                return prices
            else:
                # Expired - remove
                print(f"DEBUG: Cache EXPIRED for {key} (age={time.time() - prices[0].cached_at:.0f}s)", file=sys.stderr)
                del self._cache[key]
                self._save()
        else:
            print(f"DEBUG: Cache MISS for {key}", file=sys.stderr)
        return None

    def set(self, lat: float, lon: float, radius_km: float, fuel_types: List[str], prices: List):
        key = self._make_key(lat, lon, radius_km, fuel_types)
        cached_prices = []
        for p in prices:
            # Handle both FuelPrice objects and dicts
            if hasattr(p, 'station'):
                cached_prices.append(CachedPrice(
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
                    cached_at=time.time()
                ))
        if cached_prices:
            self._cache[key] = cached_prices
            self._save()
            print(f"DEBUG: Cached {len(cached_prices)} prices with key {key}", file=sys.stderr)

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
