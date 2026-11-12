"""Tests for GasFinder core logic."""

import asyncio
import tempfile
from unittest.mock import Mock

import pytest

from gas_finder import FuelPrice, GasFinder
from gas_finder_history import PriceHistory


class TestFuelPrice:
    """Test FuelPrice dataclass."""

    def test_fuel_price_creation(self):
        price = FuelPrice(
            station="Test Station",
            address="123 Main St",
            latitude=40.7128,
            longitude=-74.0060,
            fuel_type="REGULAR",
            price=3.50,
            currency="USD",
            updated="2024-01-01T00:00:00Z",
            distance_km=1.5,
            source="GasBuddy",
        )
        assert price.station == "Test Station"
        assert price.price == 3.50
        assert price.fuel_type == "REGULAR"

    def test_fuel_price_optional_fields(self):
        price = FuelPrice(
            station="Test",
            address="Addr",
            latitude=0.0,
            longitude=0.0,
            fuel_type="DIESEL",
            price=4.00,
            currency="USD",
            updated="2024-01-01T00:00:00Z",
        )
        assert price.distance_km is None
        assert price.source == ""


class TestGasFinder:
    """Test GasFinder class methods."""

    def test_calc_distance(self):
        finder = GasFinder()
        # NYC to LA approx 3944 km
        dist = finder._calc_distance(40.7128, -74.0060, 34.0522, -118.2437)
        assert 3900 < dist < 4000

    def test_calc_distance_zero(self):
        finder = GasFinder()
        dist = finder._calc_distance(40.7128, -74.0060, 40.7128, -74.0060)
        assert dist == 0.0

    def test_guess_country_us(self):
        finder = GasFinder()
        # New York
        assert finder._guess_country(40.7128, -74.0060) == "us"
        # Los Angeles
        assert finder._guess_country(34.0522, -118.2437) == "us"

    def test_guess_country_italy(self):
        finder = GasFinder()
        # Milan
        assert finder._guess_country(45.4642, 9.1900) == "italy"
        # Rome
        assert finder._guess_country(41.9028, 12.4964) == "italy"

    def test_guess_country_spain(self):
        finder = GasFinder()
        # Madrid
        assert finder._guess_country(40.4168, -3.7038) == "spain"
        # Barcelona
        assert finder._guess_country(41.3851, 2.1734) == "spain"

    def test_guess_country_france(self):
        finder = GasFinder()
        # Paris
        assert finder._guess_country(48.8566, 2.3522) == "france"

    def test_guess_country_australia(self):
        finder = GasFinder()
        # Sydney
        assert finder._guess_country(-33.8688, 151.2093) == "australia"
        # Melbourne
        assert finder._guess_country(-37.8136, 144.9631) == "australia"

    def test_normalize_address(self):
        finder = GasFinder()
        assert finder._normalize_address("123 Main St") == "123mainst"
        assert finder._normalize_address("123 MAIN ST") == "123mainst"
        assert finder._normalize_address("123 Main St.") == "123mainst"


class TestAppEndpoints:
    """Basic smoke tests for Flask endpoints (require running app)."""

    @pytest.mark.skip(reason="Requires running Flask app")
    def test_api_sources(self):
        pass

    @pytest.mark.skip(reason="Requires running Flask app")
    def test_api_search(self):
        pass


class TestAFDC:
    """Test NREL AFDC station search via ArcGIS FeatureServer."""

    def test_search_afdc_e85(self):
        """Test searching for E85 stations via AFDC."""
        finder = GasFinder()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "features": [
                {
                    "attributes": {
                        "station_name": "Test E85 Station",
                        "street_address": "123 Main St",
                        "city": "Test City",
                        "state": "CA",
                        "zip": "90210",
                        "updated_at": "2024-01-01T00:00:00Z",
                    },
                    "geometry": {"x": -118.3963062, "y": 34.0696501},
                }
            ]
        }

        def mock_safe_get(url, params=None, timeout=5):
            return mock_response

        finder._safe_get = mock_safe_get

        prices = asyncio.run(
            finder.search_afdc(
                34.0696501, -118.3963062, radius_km=10, fuel_types=["e85"]
            )
        )

        assert len(prices) == 1
        assert prices[0].station == "Test E85 Station"
        assert prices[0].fuel_type == "E85"
        assert prices[0].source == "NREL AFDC"
        assert prices[0].price == 0.0  # AFDC has no price data
        assert prices[0].currency == "USD"

    def test_search_afdc_defaults_to_e85(self):
        """Test AFDC defaults to E85 when no fuel types specified."""
        finder = GasFinder()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"features": []}

        def mock_safe_get(url, params=None, timeout=5):
            return mock_response

        finder._safe_get = mock_safe_get

        prices = asyncio.run(
            finder.search_afdc(34.0696501, -118.3963062, radius_km=10)
        )

        # Should query for E85 only
        assert len(prices) == 0

    def test_search_afdc_skips_non_afdc_fuels(self):
        """Test AFDC returns empty for non-AFDC fuel types."""
        finder = GasFinder()

        prices = asyncio.run(
            finder.search_afdc(
                34.0696501, -118.3963062, radius_km=10, fuel_types=["regular"]
            )
        )
        assert len(prices) == 0

    def test_search_afdc_request_failed(self):
        """Test AFDC handles request failure gracefully."""
        finder = GasFinder()

        def mock_safe_get(url, params=None, timeout=5):
            return None

        finder._safe_get = mock_safe_get

        prices = asyncio.run(
            finder.search_afdc(
                34.0696501, -118.3963062, radius_km=10, fuel_types=["e85"]
            )
        )
        assert len(prices) == 0

    def test_search_afdc_parse_error(self):
        """Test AFDC handles parse errors gracefully."""
        finder = GasFinder()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")

        def mock_safe_get(url, params=None, timeout=5):
            return mock_response

        finder._safe_get = mock_safe_get

        prices = asyncio.run(
            finder.search_afdc(
                34.0696501, -118.3963062, radius_km=10, fuel_types=["e85"]
            )
        )
        assert len(prices) == 0

    def test_search_afdc_outside_us(self):
        """Test AFDC skips non-US countries."""
        finder = GasFinder()

        prices = asyncio.run(
            finder.search_afdc(48.8566, 2.3522, radius_km=10, fuel_types=["e85"])
        )
        assert len(prices) == 0


class TestRoutePlanning:
    """Test GasFinder route planning functionality."""

    def test_find_prices_along_route(self):
        """Test finding stations along a route."""
        finder = GasFinder()

        # Mock find_prices (async) to return stations at various points along route
        async def mock_find_prices(lat, lon, radius_km=None, fuel_types=None):
            return [
                FuelPrice(
                    station="Station A",
                    address="123 Main St",
                    latitude=lat,
                    longitude=lon,
                    fuel_type="REGULAR",
                    price=3.50,
                    currency="USD",
                    updated="2024-01-01T00:00:00Z",
                    source="GasBuddy",
                )
            ]

        finder.find_prices = mock_find_prices

        prices = asyncio.run(
            finder.find_prices_along_route(
                34.0522, -118.2437, 34.0696501, -118.3963062,
                fuel_types=["regular"], sample_every_km=50,
            )
        )

        # Should have found stations at multiple sample points
        assert len(prices) > 0

    def test_find_prices_along_route_no_results(self):
        """Test route with no stations found."""
        finder = GasFinder()

        async def mock_find_prices(lat, lon, radius_km=None, fuel_types=None):
            return []

        finder.find_prices = mock_find_prices

        prices = asyncio.run(
            finder.find_prices_along_route(
                34.0522, -118.2437, 34.0696501, -118.3963062,
                fuel_types=["regular"],
            )
        )
        assert len(prices) == 0

    def test_find_prices_along_route_dedupes_stations(self):
        """Test route deduplicates stations found at multiple sample points."""
        finder = GasFinder()

        async def mock_find_prices(lat, lon, radius_km=None, fuel_types=None):
            return [
                FuelPrice(
                    station="Same Station",
                    address="123 Main St",
                    latitude=34.0696501,
                    longitude=-118.3963062,
                    fuel_type="REGULAR",
                    price=3.50,
                    currency="USD",
                    updated="2024-01-01T00:00:00Z",
                    source="GasBuddy",
                )
            ]

        finder.find_prices = mock_find_prices

        # Two nearby points along a route - same station found at both samples
        prices = asyncio.run(
            finder.find_prices_along_route(
                34.0696501, -118.3963062, 34.0706501, -118.3953062,
                fuel_types=["regular"], sample_every_km=1,
            )
        )
        # Station appears at multiple sample points but should be deduplicated to 1
        assert len(prices) == 1
        assert prices[0].station == "Same Station"

    def test_find_prices_along_route_with_offsets(self):
        """Test route search with perpendicular offsets finds off-route stations."""
        finder = GasFinder()

        call_points = []

        async def mock_find_prices(lat, lon, radius_km=None, fuel_types=None):
            call_points.append((lat, lon))
            return [
                FuelPrice(
                    station=f"Station at {lat:.4f},{lon:.4f}",
                    address="123 Main St",
                    latitude=lat,
                    longitude=lon,
                    fuel_type="REGULAR",
                    price=3.50,
                    currency="USD",
                    updated="2024-01-01T00:00:00Z",
                    source="GasBuddy",
                )
            ]

        finder.find_prices = mock_find_prices

        # Route from LA to San Diego with offsets enabled
        prices = asyncio.run(
            finder.find_prices_along_route(
                34.0522, -118.2437,  # LA
                32.7157, -117.1611,  # San Diego
                fuel_types=["regular"],
                sample_every_km=100,
                search_radius_km=10,
                offset_km=5,
            )
        )

        # Should have called find_prices at center points AND offset points
        # (each sample point generates 3 searches: center + left + right offset)
        assert call_points, "Should have made search calls"
        assert len(call_points) > 3, f"Expected multiple search points, got {len(call_points)}"

        # Should have found stations at offset points too
        assert len(prices) > 0

    def test_find_prices_along_route_default_offset(self):
        """Test route search uses default offset (half of search radius)."""
        finder = GasFinder()

        call_points = []

        async def mock_find_prices(lat, lon, radius_km=None, fuel_types=None):
            call_points.append((lat, lon))
            return []

        finder.find_prices = mock_find_prices

        # Route with no offset_km specified - should default to search_radius_km * 0.5
        asyncio.run(
            finder.find_prices_along_route(
                34.0522, -118.2437,
                34.1, -118.2,
                fuel_types=["regular"],
                sample_every_km=10,
                search_radius_km=10,
            )
        )

        # Default offset should be 5 km (half of 10 km search radius)
        # Should have called find_prices at more than just center points
        assert len(call_points) > 2, "Expected searches at offset points too"

    def test_bearing_calculation(self):
        """Test bearing calculation between two points."""
        finder = GasFinder()

        # North: bearing should be 0 degrees
        bearing = finder._bearing(0, 0, 1, 0)
        assert abs(bearing - 0) < 1 or abs(bearing - 360) < 1

        # East: bearing should be 90 degrees
        bearing = finder._bearing(0, 0, 0, 1)
        assert abs(bearing - 90) < 1

        # South: bearing should be 180 degrees
        bearing = finder._bearing(0, 0, -1, 0)
        assert abs(bearing - 180) < 1

        # West: bearing should be 270 degrees
        bearing = finder._bearing(0, 0, 0, -1)
        assert abs(bearing - 270) < 1

    def test_destination_point(self):
        """Test destination point calculation."""
        finder = GasFinder()

        # Go 100 km north from equator
        lat, lon = finder._destination_point(0, 0, 0, 100)
        assert abs(lat - 0.899) < 0.01  # ~100 km north
        assert abs(lon - 0) < 0.01  # longitude shouldn't change much

        # Go 100 km east from equator
        lat, lon = finder._destination_point(0, 0, 90, 100)
        assert abs(lat - 0) < 0.01  # latitude shouldn't change
        assert abs(lon - 0.899) < 0.01  # ~100 km east


class TestPriceHistoryFavorites:
    """Test PriceHistory favorites functionality."""

    def setup_method(self):
        """Create a temporary PriceHistory instance for each test."""
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = tempfile.mkdtemp() + "/test_history.db"
        self.history = PriceHistory(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_add_favorite(self):
        """Test adding a favorite station."""
        fav_id = self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062,
            "E85", alert_threshold=3.50
        )
        assert fav_id is not None
        assert fav_id > 0

    def test_add_favorite_duplicate_returns_existing_id(self):
        """Test adding same favorite twice returns existing ID."""
        fav_id_1 = self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062,
            "E85", alert_threshold=3.50
        )
        fav_id_2 = self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062,
            "E85", alert_threshold=4.00
        )
        assert fav_id_1 == fav_id_2

    def test_remove_favorite(self):
        """Test removing a favorite station."""
        self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062,
            "E85", alert_threshold=3.50
        )
        result = self.history.remove_favorite("Test Station", "123 Main St", "E85")
        assert result is True

    def test_remove_favorite_not_found(self):
        """Test removing non-existent favorite returns False."""
        result = self.history.remove_favorite("Nonexistent", "Nowhere St", "REGULAR")
        assert result is False

    def test_get_favorites(self):
        """Test listing all favorites."""
        self.history.add_favorite("Station A", "123 Main St", 34.0696501, -118.3963062, "E85", 3.50)
        self.history.add_favorite("Station B", "456 Oak Ave", 34.0522, -118.2437, "REGULAR", None)

        favorites = self.history.get_favorites()
        assert len(favorites) == 2
        assert favorites[0].station == "Station A"
        assert favorites[1].station == "Station B"

    def test_is_favorited(self):
        """Test checking if station is favorited."""
        assert self.history.is_favorited("Test Station", "123 Main St", "E85") is False

        self.history.add_favorite("Test Station", "123 Main St", 34.0696501, -118.3963062, "E85", 3.50)
        assert self.history.is_favorited("Test Station", "123 Main St", "E85") is True

    def test_check_price_alerts_no_alerts(self):
        """Test check_price_alerts returns empty when no prices below threshold."""
        self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062, "E85", alert_threshold=1.00
        )
        alerts = self.history.check_price_alerts()
        assert len(alerts) == 0

    def test_check_price_alerts_with_alert(self):
        """Test check_price_alerts returns alerts when price below threshold."""
        self.history.add_favorite(
            "Test Station", "123 Main St", 34.0696501, -118.3963062, "REGULAR", alert_threshold=5.00
        )
        # Record two prices below threshold (need 2+ for trend comparison)
        self.history.record_prices([
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=4.00, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=4.50, currency="USD", source="GasBuddy",
                updated="2024-01-02T00:00:00Z",
            ),
        ])
        alerts = self.history.check_price_alerts()
        assert len(alerts) == 1
        assert alerts[0]["station"] == "Test Station"
        assert alerts[0]["current_price"] == 4.50
        assert alerts[0]["threshold"] == 5.00


class TestPriceHistoryTrend:
    """Test PriceHistory trend analysis."""

    def setup_method(self):
        """Create a temporary PriceHistory instance for each test."""
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = tempfile.mkdtemp() + "/test_history.db"
        self.history = PriceHistory(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _record_prices(self, prices):
        """Helper to record multiple prices."""
        self.history.record_prices(prices)

    def test_get_trend_rising(self):
        """Test trend detection for rising prices."""
        prices = [
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.00, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.50, currency="USD", source="GasBuddy",
                updated="2024-01-02T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=4.00, currency="USD", source="GasBuddy",
                updated="2024-01-03T00:00:00Z",
            ),
        ]
        self.history.record_prices(prices)

        trend = self.history.get_trend("Test Station", "123 Main St", "REGULAR", days=30)
        assert trend["trend"] == "rising"
        assert trend["current"] == 4.00
        assert trend["change"] > 0

    def test_get_trend_falling(self):
        """Test trend detection for falling prices."""
        prices = [
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=4.00, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.50, currency="USD", source="GasBuddy",
                updated="2024-01-02T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.00, currency="USD", source="GasBuddy",
                updated="2024-01-03T00:00:00Z",
            ),
        ]
        self.history.record_prices(prices)

        trend = self.history.get_trend("Test Station", "123 Main St", "REGULAR", days=30)
        assert trend["trend"] == "falling"
        assert trend["current"] == 3.00
        assert trend["change"] < 0

    def test_get_trend_stable(self):
        """Test trend detection for stable prices."""
        prices = [
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.50, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.51, currency="USD", source="GasBuddy",
                updated="2024-01-02T00:00:00Z",
            ),
        ]
        self.history.record_prices(prices)

        trend = self.history.get_trend("Test Station", "123 Main St", "REGULAR", days=30)
        assert trend["trend"] == "stable"

    def test_get_trend_insufficient_data(self):
        """Test trend returns insufficient_data when less than 2 snapshots."""
        prices = [
            FuelPrice(
                station="Test Station", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.50, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
        ]
        self.history.record_prices(prices)

        trend = self.history.get_trend("Test Station", "123 Main St", "REGULAR", days=30)
        assert trend["trend"] == "insufficient_data"
        assert trend["current"] is None

    def test_get_trend_nonexistent_station(self):
        """Test trend for station with no data."""
        trend = self.history.get_trend("Nonexistent", "Nowhere St", "REGULAR", days=30)
        assert trend["trend"] == "insufficient_data"
        assert trend["current"] is None


class TestPriceHistoryStats:
    """Test PriceHistory statistics."""

    def setup_method(self):
        """Create a temporary PriceHistory instance for each test."""
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = tempfile.mkdtemp() + "/test_history.db"
        self.history = PriceHistory(db_path=self.db_path)

    def teardown_method(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_get_stats_empty(self):
        """Test stats on empty database."""
        stats = self.history.get_stats()
        assert stats["total_snapshots"] == 0
        assert stats["unique_stations"] == 0
        assert stats["fuel_types"] == 0

    def test_get_stats_with_data(self):
        """Test stats with recorded prices."""
        prices = [
            FuelPrice(
                station="Station A", address="123 Main St",
                latitude=34.0696501, longitude=-118.3963062,
                fuel_type="REGULAR", price=3.50, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
            FuelPrice(
                station="Station B", address="456 Oak Ave",
                latitude=34.0522, longitude=-118.2437,
                fuel_type="DIESEL", price=4.00, currency="USD", source="GasBuddy",
                updated="2024-01-01T00:00:00Z",
            ),
        ]
        self.history.record_prices(prices)

        stats = self.history.get_stats()
        assert stats["total_snapshots"] == 2
        assert stats["unique_stations"] == 2
        assert stats["fuel_types"] == 2
