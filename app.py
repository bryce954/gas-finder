#!/usr/bin/env python3
"""
Gas Price Finder Web App
Flask backend + Leaflet map frontend
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_from_directory

# Add parent directory to import gas_finder
sys.path.insert(0, str(Path(__file__).parent.parent))
from gas_finder import GasFinder

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')

# Global event loop (singleton to avoid "Event loop is closed" errors)
_loop = None


def get_finder():
    # Create new GasFinder instance for each search to avoid stale GasBuddy token cache
    return GasFinder()


def get_loop():
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def get_loop():
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/search', methods=['POST'])
def search():
    data = request.get_json()

    lat = data.get('lat')
    lon = data.get('lon')
    location = data.get('location')
    radius = data.get('radius', 10)
    fuel_types = data.get('fuel_types', [])
    limit = data.get('limit', 20)

    finder = get_finder()
    loop = get_loop()

    # Determine location
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        location_name = f"coordinates ({lat}, {lon})"
    elif location:
        geo = finder.geocode_address(location)
        if not geo:
            return jsonify({'error': 'Failed to geocode location'}), 400
        lat, lon = geo['lat'], geo['lon']
        location_name = geo['display_name']
    else:
        ip_loc = finder.get_location_from_ip()
        if not ip_loc:
            return jsonify({'error': 'Failed to detect location'}), 400
        lat, lon = ip_loc['lat'], ip_loc['lon']
        location_name = f"{ip_loc['city']}, {ip_loc['region']} ({ip_loc['country']})"

    # Search for prices
    try:
        import logging
        logging.basicConfig(filename='/tmp/flask_search.log', level=logging.DEBUG)
        logging.info(f"Searching for prices: lat={lat}, lon={lon}, radius={radius}, fuel_types={fuel_types}")
        prices = loop.run_until_complete(
            finder.find_prices(lat, lon, radius, fuel_types if fuel_types else None)
        )
        logging.info(f"Found {len(prices)} prices")
        for p in prices[:5]:
            logging.info(f"  {p.station}: {p.fuel_type} \${p.price} ({p.source})")
    except Exception as e:
        import traceback
        logging.error(f"Search error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up GasFinder to clear GasBuddy cache
        loop.run_until_complete(finder.close())

    if not prices:
        return jsonify({
            'location': location_name,
            'lat': lat,
            'lon': lon,
            'prices': [],
            'message': 'No prices found. Try increasing radius or different location.',
            'sources': []
        })

    # Limit results
    prices = prices[:limit]

    # Convert to dict for JSON
    results = []
    sources_seen = set()
    for p in prices:
        sources_seen.add(p.source)
        results.append({
            'station': p.station,
            'address': p.address,
            'lat': p.latitude,
            'lon': p.longitude,
            'fuel_type': p.fuel_type,
            'price': p.price,
            'currency': p.currency,
            'updated': p.updated,
            'distance_km': p.distance_km,
            'source': p.source
        })

    return jsonify({
        'location': location_name,
        'lat': lat,
        'lon': lon,
        'radius': radius,
        'fuel_types': fuel_types,
        'count': len(results),
        'prices': results,
        'sources': list(sources_seen)
    })


@app.route('/api/geocode', methods=['POST'])
def geocode():
    data = request.get_json()
    address = data.get('address', '')

    finder = get_finder()
    geo = finder.geocode_address(address)

    if not geo:
        return jsonify({'error': 'Failed to geocode'}), 400

    return jsonify({
        'lat': geo['lat'],
        'lon': geo['lon'],
        'display_name': geo['display_name']
    })


@app.route('/api/sources')
def sources():
    """Get info about data sources"""
    return jsonify({
        'sources': [
            {
                'id': 'afdc',
                'name': 'NREL AFDC',
                'description': 'Official DOE Alternative Fuels Data Center - canonical station locations',
                'url': 'https://afdc.energy.gov',
                'coverage': 'US nationwide',
                'fuel_types': ['e85', 'cng', 'lng', 'lpg', 'electric', 'hydrogen']
            },
            {
                'id': 'gasbuddy',
                'name': 'GasBuddy',
                'description': 'Community-reported gas prices for US/Canada',
                'url': 'https://www.gasbuddy.com',
                'coverage': 'US, Canada',
                'fuel_types': ['regular', 'midgrade', 'premium', 'diesel', 'e85']
            },
            {
                'id': 'fuelwide',
                'name': 'FuelWide',
                'description': 'Official government open data for fuel prices',
                'url': 'https://fuelwide.com',
                'coverage': 'France, Italy, Spain, Australia',
                'fuel_types': ['diesel', 'petrol95', 'petrol98', 'lpg', 'e5', 'e10']
            }
        ]
    })


@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    # Disable debug reloader to avoid event loop issues
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)