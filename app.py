#!/usr/bin/env python3
"""
Gas Price Finder Web App
Flask backend + Leaflet map frontend
"""

import asyncio
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

# Add parent directory to import gas_finder
sys.path.insert(0, str(Path(__file__).parent.parent))
from gas_finder import GasFinder
from gas_finder_history import get_price_history

app = Flask(__name__, template_folder="templates", static_folder="static")

def get_finder():
    # Create new GasFinder instance for each search to avoid stale GasBuddy token cache
    return GasFinder()


def get_loop():
    """Get or create an event loop for async operations."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json()

    lat = data.get("lat")
    lon = data.get("lon")
    location = data.get("location")
    radius = data.get("radius", 10)
    fuel_types = data.get("fuel_types", [])
    limit = data.get("limit", 20)

    finder = get_finder()
    loop = get_loop()

    # Determine location
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        location_name = f"coordinates ({lat}, {lon})"
    elif location:
        geo = finder.geocode_address(location)
        if not geo:
            return jsonify({"error": "Failed to geocode location"}), 400
        lat, lon = geo["lat"], geo["lon"]
        location_name = geo["display_name"]
    else:
        ip_loc = finder.get_location_from_ip()
        if not ip_loc:
            return jsonify({"error": "Failed to detect location"}), 400
        lat, lon = ip_loc["lat"], ip_loc["lon"]
        location_name = f"{ip_loc['city']}, {ip_loc['region']} ({ip_loc['country']})"

    # Search for prices
    try:
        import logging

        logging.basicConfig(filename="/tmp/flask_search.log", level=logging.DEBUG)
        logging.info(
            f"Searching for prices: lat={lat}, lon={lon}, radius={radius}, fuel_types={fuel_types}"
        )
        prices = loop.run_until_complete(
            finder.find_prices(lat, lon, radius, fuel_types if fuel_types else None)
        )
        logging.info(f"Found {len(prices)} prices")
        for p in prices[:5]:
            logging.info(rf"  {p.station}: {p.fuel_type} \${p.price} ({p.source})")
    except Exception as e:
        import traceback

        logging.error(f"Search error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        # Clean up GasFinder to clear GasBuddy cache
        loop.run_until_complete(finder.close())

    if not prices:
        return jsonify(
            {
                "location": location_name,
                "lat": lat,
                "lon": lon,
                "prices": [],
                "message": "No prices found. Try increasing radius or different location.",
                "sources": [],
            }
        )

    # Limit results
    prices = prices[:limit]

    # Convert to dict for JSON
    results = []
    sources_seen = set()
    for p in prices:
        sources_seen.add(p.source)
        results.append(
            {
                "station": p.station,
                "address": p.address,
                "lat": p.latitude,
                "lon": p.longitude,
                "fuel_type": p.fuel_type,
                "price": p.price,
                "currency": p.currency,
                "updated": p.updated,
                "distance_km": p.distance_km,
                "source": p.source,
            }
        )

    return jsonify(
        {
            "location": location_name,
            "lat": lat,
            "lon": lon,
            "radius": radius,
            "fuel_types": fuel_types,
            "count": len(results),
            "prices": results,
            "sources": list(sources_seen),
        }
    )


@app.route("/api/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    address = data.get("address", "")

    finder = get_finder()
    geo = finder.geocode_address(address)

    if not geo:
        return jsonify({"error": "Failed to geocode"}), 400

    return jsonify(
        {"lat": geo["lat"], "lon": geo["lon"], "display_name": geo["display_name"]}
    )


@app.route("/api/sources")
def sources():
    """Get info about data sources"""
    return jsonify(
        {
            "sources": [
                {
                    "id": "afdc",
                    "name": "NREL AFDC",
                    "description": "Official DOE Alternative Fuels Data Center - canonical station locations",
                    "url": "https://afdc.energy.gov",
                    "coverage": "US nationwide",
                    "fuel_types": ["e85", "cng", "lng", "lpg", "electric", "hydrogen"],
                },
                {
                    "id": "gasbuddy",
                    "name": "GasBuddy",
                    "description": "Community-reported gas prices for US/Canada",
                    "url": "https://www.gasbuddy.com",
                    "coverage": "US, Canada",
                    "fuel_types": ["regular", "midgrade", "premium", "diesel", "e85"],
                },
                {
                    "id": "fuelwide",
                    "name": "FuelWide",
                    "description": "Official government open data for fuel prices",
                    "url": "https://fuelwide.com",
                    "coverage": "France, Italy, Spain, Australia",
                    "fuel_types": [
                        "diesel",
                        "petrol95",
                        "petrol98",
                        "lpg",
                        "e5",
                        "e10",
                    ],
                },
            ]
        }
    )


@app.route("/api/history/station", methods=["POST"])
def station_history():
    """Get price history for a specific station/fuel combination"""
    data = request.get_json()
    station = data.get("station", "")
    address = data.get("address", "")
    fuel_type = data.get("fuel_type", "")
    days = data.get("days", 30)

    if not station or not address or not fuel_type:
        return jsonify({"error": "station, address, and fuel_type required"}), 400

    try:
        history = get_price_history()
        trend = history.get_trend(station, address, fuel_type, days)
        return jsonify(trend)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history/nearby", methods=["POST"])
def nearby_history():
    """Get price trends for stations near a location"""
    data = request.get_json()
    lat = data.get("lat")
    lon = data.get("lon")
    radius_km = data.get("radius_km", 5)

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    try:
        history = get_price_history()
        trends = history.get_station_summary(float(lat), float(lon), float(radius_km))
        return jsonify({"trends": trends})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history/stats")
def history_stats():
    """Get database statistics"""
    try:
        history = get_price_history()
        return jsonify(history.get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/static/<path:path>")
def serve_static(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    # Disable debug reloader to avoid event loop issues
    # Bind to 0.0.0.0 to allow external connections (needed for Docker)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
