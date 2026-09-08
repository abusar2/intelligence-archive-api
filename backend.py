# backend.py
# Intelligence Archive API Proxy
# Flask backend for Blogger frontend

import os
import json
import base64
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
UPSTREAM_BASE_URL = os.getenv('UPSTREAM_BASE_URL', 'https://zezsoft.eu')
UPSTREAM_USER = os.getenv('UPSTREAM_USER', '')
UPSTREAM_PASS = os.getenv('UPSTREAM_PASS', '')
UPSTREAM_TIMEOUT = int(os.getenv('UPSTREAM_TIMEOUT', '20'))
PORT = int(os.getenv('PORT', '8080'))

# Allowed origins (CORS)
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)

# CORS configuration
CORS(app, origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ['*'] else '*',
     methods=['GET', 'POST', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'])

# ============================================================
# ROUTE MAPPING
# ============================================================
ROUTE_MAP = {
    'name_family': {
        'endpoint': '/PSApp/PsArchive/getNF.php',
        'params': ['name', 'family']
    },
    'name_father': {
        'endpoint': '/PSApp/PsArchive/getNFather.php',
        'params': ['name', 'father']
    },
    'father_family': {
        'endpoint': '/PSApp/PsArchive/getFF.php',
        'params': ['father', 'family']
    },
    'name_father_gfather_family': {
        'endpoint': '/PSApp/PsArchive/getNFGF.php',
        'params': ['name', 'father', 'gfather', 'family']
    },
    'name_father_family': {
        'endpoint': '/PSApp/PsArchive/getPFF.php',
        'params': ['name', 'father', 'family']
    },
    'name_gfather_family': {
        'endpoint': '/PSApp/PsArchive/getPFgF.php',
        'params': ['name', 'gfather', 'family']
    },
    'family': {
        'endpoint': '/PSApp/PsArchive/getFamily.php',
        'params': ['family']
    },
    'birth': {
        'endpoint': '/PSApp/PsArchive/getDateBD.php',
        'params': ['datePBD']
    },
    'gfather_family': {
        'endpoint': '/PSApp/PsArchive/getGFF.php',
        'params': ['gfather', 'family']
    },
    'father_gfather': {
        'endpoint': '/PSApp/PsArchive/getNFNGF.php',
        'params': ['father', 'gfather']
    },
    'father_mother': {
        'endpoint': '/PSApp/PsArchive/getNFNM.php',
        'params': ['father', 'mother']
    },
    'name_mother': {
        'endpoint': '/PSApp/PsArchive/getNPNM.php',
        'params': ['name', 'mother']
    },
    'name_gfather': {
        'endpoint': '/PSApp/PsArchive/getNPNGF.php',
        'params': ['name', 'gfather']
    },
    'mother_family': {
        'endpoint': '/PSApp/PsArchive/getNMF.php',
        'params': ['mother', 'family']
    }
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def build_auth_header():
    """Build Basic Auth header from environment credentials."""
    if not UPSTREAM_USER or not UPSTREAM_PASS:
        logger.error('UPSTREAM_USER or UPSTREAM_PASS not configured')
        return None
    credentials = f"{UPSTREAM_USER}:{UPSTREAM_PASS}"
    encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    return f"Basic {encoded}"

def normalize_response(upstream_data):
    """Normalize upstream response into a clean format."""
    if not upstream_data:
        return []

    results = upstream_data.get('psarchive', [])
    if not results:
        if isinstance(upstream_data, list):
            results = upstream_data
        else:
            results = [upstream_data]

    normalized = []
    for item in results:
        if not isinstance(item, dict):
            continue
        clean = {}
        for key, val in item.items():
            if val is not None and val != '':
                clean[key] = val
        if clean:
            normalized.append(clean)

    return normalized

# ============================================================
# ROUTES
# ============================================================
@app.route('/api/health', methods=['GET', 'OPTIONS'])
def health_check():
    """Health check endpoint for diagnostics."""
    if request.method == 'OPTIONS':
        return '', 200

    auth = build_auth_header()
    if not auth:
        return jsonify({
            'status': 'degraded',
            'message': 'API credentials not configured'
        }), 200

    try:
        test_url = f"{UPSTREAM_BASE_URL}/PSApp/PsArchive/getFamily.php"
        response = requests.post(
            test_url,
            data={'family': 'test'},
            headers={'Authorization': auth},
            timeout=5
        )
        upstream_ok = response.status_code == 200
    except Exception as e:
        logger.warning(f"Health check upstream ping failed: {e}")
        upstream_ok = False

    return jsonify({
        'status': 'online',
        'timestamp': datetime.utcnow().isoformat(),
        'upstream': 'online' if upstream_ok else 'degraded',
        'version': '1.0.0'
    }), 200

@app.route('/api/search', methods=['POST', 'OPTIONS'])
def search():
    """Main search proxy endpoint."""
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json()
    if not data:
        return jsonify({
            'success': False,
            'error': 'Invalid request: JSON body required'
        }), 400

    route = data.get('route')
    params = data.get('params', {})

    if not route or route not in ROUTE_MAP:
        return jsonify({
            'success': False,
            'error': f'Unknown search route: {route}'
        }), 400

    route_config = ROUTE_MAP[route]
    required_params = route_config['params']

    has_value = False
    filtered_params = {}
    for key in required_params:
        val = params.get(key, '').strip()
        if val:
            has_value = True
            filtered_params[key] = val

    if not has_value:
        return jsonify({
            'success': False,
            'error': f'At least one parameter required for {route}'
        }), 400

    auth = build_auth_header()
    if not auth:
        return jsonify({
            'success': False,
            'error': 'API credentials not configured on server'
        }), 500

    url = f"{UPSTREAM_BASE_URL}{route_config['endpoint']}"

    logger.info(f"Search: route={route}, params={filtered_params}")

    try:
        response = requests.post(
            url,
            data=filtered_params,
            headers={
                'Authorization': auth,
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            timeout=UPSTREAM_TIMEOUT
        )

        if response.status_code == 401:
            return jsonify({
                'success': False,
                'error': 'Upstream authentication failed'
            }), 401
        elif response.status_code == 404:
            return jsonify({
                'success': False,
                'error': 'Upstream endpoint not found'
            }), 404
        elif response.status_code >= 500:
            return jsonify({
                'success': False,
                'error': f'Upstream server error: {response.status_code}'
            }), response.status_code

        try:
            upstream_data = response.json()
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from upstream: {response.text[:200]}")
            return jsonify({
                'success': False,
                'error': 'Invalid response from upstream server'
            }), 502

        results = normalize_response(upstream_data)

        if not results:
            return jsonify({
                'success': True,
                'results': [],
                'count': 0,
                'message': 'No records found'
            }), 200

        return jsonify({
            'success': True,
            'results': results,
            'count': len(results)
        }), 200

    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to upstream: {url}")
        return jsonify({
            'success': False,
            'error': 'Upstream request timeout'
        }), 504
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error to upstream: {url}")
        return jsonify({
            'success': False,
            'error': 'Cannot connect to upstream server'
        }), 503
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/diagnose', methods=['GET'])
def diagnose():
    """Extended diagnostics endpoint."""
    auth = build_auth_header()
    config_status = 'configured' if auth else 'missing'

    results = {
        'timestamp': datetime.utcnow().isoformat(),
        'config': {
            'upstream_base': UPSTREAM_BASE_URL,
            'auth': config_status,
            'timeout': UPSTREAM_TIMEOUT
        },
        'routes': list(ROUTE_MAP.keys()),
        'server': 'running'
    }
    return jsonify(results), 200

# ============================================================
# ERROR HANDLERS
# ============================================================
@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'success': False, 'error': 'Method not allowed'}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    logger.info(f"Starting Intelligence Archive API Proxy on port {PORT}")
    logger.info(f"Upstream base URL: {UPSTREAM_BASE_URL}")
    logger.info(f"Auth configured: {'YES' if UPSTREAM_USER and UPSTREAM_PASS else 'NO'}")
    logger.info(f"Routes available: {len(ROUTE_MAP)}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
