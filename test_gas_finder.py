"""Tests for GasFinder core logic."""

import pytest

from gas_finder import FuelPrice, GasFinder


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
