#!/usr/bin/env python3
"""
Price History Tracker for FuelScope
SQLite database to store price snapshots for trend analysis
"""

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DB_DIR = Path.home() / ".cache" / "gas_finder"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "price_history.db"


@dataclass
class PriceSnapshot:
    station: str
    address: str
    latitude: float
    longitude: float
    fuel_type: str
    price: float
    currency: str
    source: str
    timestamp: datetime


@dataclass
class Favorite:
    station: str
    address: str
    latitude: float
    longitude: float
    fuel_type: str
    alert_threshold: float | None = None
    id: int | None = None
    created_at: str | None = None


class PriceHistory:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station TEXT NOT NULL,
                    address TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    fuel_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    source TEXT NOT NULL,
                    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(station, address, fuel_type, timestamp)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station TEXT NOT NULL,
                    address TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    fuel_type TEXT NOT NULL,
                    alert_threshold REAL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(station, address, fuel_type)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_station_fuel_time
                ON price_snapshots(station, address, fuel_type, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_location_time
                ON price_snapshots(latitude, longitude, timestamp)
            """)

    def record_prices(self, prices: list) -> int:
        """Record a batch of prices. Returns number of new records inserted."""
        if not prices:
            return 0

        inserted = 0
        with sqlite3.connect(self.db_path) as conn:
            for p in prices:
                # Only record prices > 0 (AFDC returns 0)
                if p.price <= 0:
                    continue
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO price_snapshots
                        (station, address, latitude, longitude, fuel_type, price, currency, source, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        p.station,
                        p.address,
                        p.latitude,
                        p.longitude,
                        p.fuel_type.upper(),
                        p.price,
                        p.currency,
                        p.source,
                        datetime.now().isoformat(),
                    ))
                    if conn.total_changes > inserted:
                        inserted += 1
                except Exception as e:
                    print(f"History: Failed to record {p.station}: {e}", file=sys.stderr)
        return inserted

    def get_history(
        self,
        station: str,
        address: str,
        fuel_type: str,
        days: int = 30
    ) -> list[PriceSnapshot]:
        """Get price history for a specific station/fuel."""
        since = datetime.now() - timedelta(days=days)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM price_snapshots
                WHERE station = ? AND address = ? AND fuel_type = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (station, address, fuel_type.upper(), since.isoformat())).fetchall()

        return [
            PriceSnapshot(
                station=r["station"],
                address=r["address"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                fuel_type=r["fuel_type"],
                price=r["price"],
                currency=r["currency"],
                source=r["source"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
            )
            for r in rows
        ]

    def get_trend(self, station: str, address: str, fuel_type: str, days: int = 7) -> dict:
        """Get trend summary for a station/fuel over N days."""
        history = self.get_history(station, address, fuel_type, days)
        if len(history) < 2:
            return {"trend": "insufficient_data", "change": 0, "current": None, "history": history}

        current = history[-1].price
        oldest = history[0].price
        change = current - oldest
        pct_change = (change / oldest * 100) if oldest > 0 else 0

        if abs(pct_change) < 1:
            trend = "stable"
        elif pct_change > 0:
            trend = "rising"
        else:
            trend = "falling"

        return {
            "trend": trend,
            "change": round(change, 3),
            "pct_change": round(pct_change, 1),
            "current": current,
            "history": history,
        }

    def get_station_summary(self, lat: float, lon: float, radius_km: float = 5) -> list[dict]:
        """Get trend summary for all stations near a location."""
        from gas_finder import GasFinder
        GasFinder()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Get unique station/fuel combos near location
            rows = conn.execute("""
                SELECT DISTINCT station, address, fuel_type
                FROM price_snapshots
                WHERE latitude BETWEEN ? AND ?
                AND longitude BETWEEN ? AND ?
                AND timestamp >= ?
            """, (
                lat - radius_km / 111.0, lat + radius_km / 111.0,
                lon - radius_km / (111.0 * abs(lat) if lat else 111.0),
                lon + radius_km / (111.0 * abs(lat) if lat else 111.0),
                (datetime.now() - timedelta(days=30)).isoformat(),
            )).fetchall()

        results = []
        for r in rows:
            trend = self.get_trend(r["station"], r["address"], r["fuel_type"])
            if trend["trend"] != "insufficient_data":
                results.append(trend)

        return sorted(results, key=lambda x: x["pct_change"])

    def add_favorite(self, station: str, address: str, latitude: float, longitude: float,
                     fuel_type: str, alert_threshold: float | None = None) -> int:
        """Add a station/fuel to favorites. Returns the favorite ID."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                cur = conn.execute("""
                    INSERT OR IGNORE INTO favorites
                    (station, address, latitude, longitude, fuel_type, alert_threshold)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (station, address, latitude, longitude, fuel_type.upper(), alert_threshold))
                if cur.lastrowid:
                    return cur.lastrowid
                # Already exists - return existing ID
                row = conn.execute("""
                    SELECT id FROM favorites
                    WHERE station = ? AND address = ? AND fuel_type = ?
                """, (station, address, fuel_type.upper())).fetchone()
                return row[0] if row else None
            except Exception as e:
                print(f"History: Failed to add favorite {station}: {e}", file=sys.stderr)
                return None

    def remove_favorite(self, station: str, address: str, fuel_type: str) -> bool:
        """Remove a station/fuel from favorites. Returns True if removed."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("""
                DELETE FROM favorites
                WHERE station = ? AND address = ? AND fuel_type = ?
            """, (station, address, fuel_type.upper()))
            return cur.rowcount > 0

    def get_favorites(self) -> list[Favorite]:
        """Get all favorited stations."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM favorites ORDER BY created_at DESC
            """).fetchall()

        return [
            Favorite(
                id=r["id"],
                station=r["station"],
                address=r["address"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                fuel_type=r["fuel_type"],
                alert_threshold=r["alert_threshold"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def is_favorited(self, station: str, address: str, fuel_type: str) -> bool:
        """Check if a station/fuel is favorited."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT 1 FROM favorites
                WHERE station = ? AND address = ? AND fuel_type = ?
                LIMIT 1
            """, (station, address, fuel_type.upper())).fetchone()
            return row is not None

    def check_price_alerts(self) -> list[dict]:
        """Check all favorites for price drops below alert threshold.
        Returns list of alerts with station, fuel_type, current_price, threshold, change."""
        alerts = []
        favorites = self.get_favorites()

        for fav in favorites:
            if fav.alert_threshold is None:
                continue

            history = self.get_history(fav.station, fav.address, fav.fuel_type, days=7)
            if len(history) < 2:
                continue

            current_price = history[-1].price
            if current_price <= fav.alert_threshold:
                prev_price = history[-2].price
                change = current_price - prev_price
                pct_change = (change / prev_price * 100) if prev_price > 0 else 0

                alerts.append({
                    "station": fav.station,
                    "address": fav.address,
                    "fuel_type": fav.fuel_type,
                    "latitude": fav.latitude,
                    "longitude": fav.longitude,
                    "current_price": current_price,
                    "threshold": fav.alert_threshold,
                    "change": round(change, 3),
                    "pct_change": round(pct_change, 1),
                    "currency": history[-1].currency,
                })

        return alerts

    def cleanup_old(self, days: int = 90):
        """Remove snapshots older than N days."""
        since = datetime.now() - timedelta(days=days)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM price_snapshots WHERE timestamp < ?", (since.isoformat(),))

    def get_stats(self) -> dict:
        """Get database statistics."""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
            stations = conn.execute("SELECT COUNT(DISTINCT station || address) FROM price_snapshots").fetchone()[0]
            fuels = conn.execute("SELECT COUNT(DISTINCT fuel_type) FROM price_snapshots").fetchone()[0]
            oldest = conn.execute("SELECT MIN(timestamp) FROM price_snapshots").fetchone()[0]
            newest = conn.execute("SELECT MAX(timestamp) FROM price_snapshots").fetchone()[0]

        return {
            "total_snapshots": total,
            "unique_stations": stations,
            "fuel_types": fuels,
            "oldest": oldest,
            "newest": newest,
        }


# Global instance
_price_history = None


def get_price_history() -> PriceHistory:
    global _price_history
    if _price_history is None:
        _price_history = PriceHistory()
    return _price_history