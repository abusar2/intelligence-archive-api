# backend.py
# Intelligence Archive API Proxy with Iploop Residential Proxy

import os
import json
import base64
import logging
import socket
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import urllib3.util.connection
from dotenv import load_dotenv

# ✅ استيراد مكتبة Iploop
from iploop import IPLoop

# إجبار IPv4 (احتياطي)
urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

# Load environment variables
load_dotenv()

# Configuration
UPSTREAM_BASE_URL = os.getenv('UPSTREAM_BASE_URL', 'https://zezsoft.eu')
UPSTREAM_USER = os.getenv('UPSTREAM_USER', '')
UPSTREAM_PASS = os.getenv('UPSTREAM_PASS', '')
UPSTREAM_TIMEOUT = int(os.getenv('UPSTREAM_TIMEOUT', '20'))
PORT = int(os.getenv('PORT', '8080'))
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')

# ✅ [جديد] مفتاح Iploop (يجب إضافته إلى متغيرات البيئة في Render)
IPLOOP_API_KEY = os.getenv('IPLOOP_API_KEY', '')
# ✅ [جديد] الدولة المستهدفة (فلسطين)
IPLOOP_COUNTRY = os.getenv('IPLOOP_COUNTRY', 'PS')

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)
CORS(app, origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ['*'] else '*',
     methods=['GET', 'POST', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'])

# ✅ [جديد] تهيئة عميل Iploop
iploop_client = None
if IPLOOP_API_KEY:
    try:
        iploop_client = IPLoop(IPLOOP_API_KEY)
        logger.info(f"Iploop client initialized (country={IPLOOP_COUNTRY})")
    except Exception as e:
        logger.error(f"Failed to initialize Iploop client: {e}")
else:
    logger.warning("IPLOOP_API_KEY not set — proxy is disabled")

# ============================================================
# ROUTE MAPPING
# ============================================================
ROUTE_MAP = {
    'name_family': {'endpoint': '/PSApp/PsArchive/getNF.php', 'params': ['name', 'family']},
    'name_father': {'endpoint': '/PSApp/PsArchive/getNFather.php', 'params': ['name', 'father']},
    'father_family': {'endpoint': '/PSApp/PsArchive/getFF.php', 'params': ['father', 'family']},
    'name_father_gfather_family': {'endpoint': '/PSApp/PsArchive/getNFGF.php', 'params': ['name', 'father', 'gfather', 'family']},
    'name_father_family': {'endpoint': '/PSApp/PsArchive/getPFF.php', 'params': ['name', 'father', 'family']},
    'name_gfather_family': {'endpoint': '/PSApp/PsArchive/getPFgF.php', 'params': ['name', 'gfather', 'family']},
    'family': {'endpoint': '/PSApp/PsArchive/getFamily.php', 'params': ['family']},
    'birth': {'endpoint': '/PSApp/PsArchive/getDateBD.php', 'params': ['datePBD']},
    'gfather_family': {'endpoint': '/PSApp/PsArchive/getGFF.php', 'params': ['gfather', 'family']},
    'father_gfather': {'endpoint': '/PSApp/PsArchive/getNFNGF.php', 'params': ['father', 'gfather']},
    'father_mother': {'endpoint': '/PSApp/PsArchive/getNFNM.php', 'params': ['father', 'mother']},
    'name_mother': {'endpoint': '/PSApp/PsArchive/getNPNM.php', 'params': ['name', 'mother']},
    'name_gfather': {'endpoint': '/PSApp/PsArchive/getNPNGF.php', 'params': ['name', 'gfather']},
    'mother_family': {'endpoint': '/PSApp/PsArchive/getNMF.php', 'params': ['mother', 'family']},
    'id': {'endpoint': '/PSApp/PsArchive/getIDP.php', 'params': ['id', 'type']}
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def build_auth_header():
    if not UPSTREAM_USER or not UPSTREAM_PASS:
        return None
    credentials = f"{UPSTREAM_USER}:{UPSTREAM_PASS}"
    encoded = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
    return f"Basic {encoded}"

def normalize_response(upstream_data):
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
        clean = {k: v for k, v in item.items() if v is not None and v != ''}
        if clean:
            normalized.append(clean)
    return normalized

# ============================================================
# ROUTES
# ============================================================
@app.route('/api/health', methods=['GET', 'OPTIONS'])
def health_check():
    if request.method == 'OPTIONS':
        return '', 200
    auth = build_auth_header()
    if not auth:
        return jsonify({'status': 'degraded', 'message': 'API credentials not configured'}), 200

    try:
        test_url = f"{UPSTREAM_BASE_URL}/PSApp/PsArchive/getFamily.php"
        headers = {'Authorization': auth, 'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'family': 'test'}
        if iploop_client:
            # ✅ استخدام Iploop
            resp = iploop_client.post(test_url, data=data, headers=headers, country=IPLOOP_COUNTRY, timeout=15)
            upstream_ok = resp.status_code == 200
        else:
            resp = requests.post(test_url, data=data, headers=headers, timeout=10)
            upstream_ok = resp.status_code == 200
    except Exception as e:
        logger.warning(f"Health check upstream ping failed: {e}")
        upstream_ok = False

    return jsonify({
        'status': 'online',
        'timestamp': datetime.utcnow().isoformat(),
        'upstream': 'online' if upstream_ok else 'degraded',
        'proxy': 'iploop' if iploop_client else 'direct',
        'version': '2.0.0'
    }), 200

@app.route('/api/search', methods=['POST', 'OPTIONS'])
def search():
    if request.method == 'OPTIONS':
        return '', 200
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Invalid request: JSON body required'}), 400

    route = data.get('route')
    params = data.get('params', {})
    if not route or route not in ROUTE_MAP:
        return jsonify({'success': False, 'error': f'Unknown search route: {route}'}), 400

    route_config = ROUTE_MAP[route]
    required_params = route_config['params']
    has_value = False
    filtered_params = {}
    for key in required_params:
        val = params.get(key, '').strip()
        if val:
            has_value = True
            filtered_params[key] = val

    if route == 'id' and 'id' in filtered_params:
        filtered_params['type'] = params.get('type', '1').strip() or '1'
    if not has_value:
        return jsonify({'success': False, 'error': f'At least one parameter required for {route}'}), 400

    auth = build_auth_header()
    if not auth:
        return jsonify({'success': False, 'error': 'API credentials not configured on server'}), 500

    url = f"{UPSTREAM_BASE_URL}{route_config['endpoint']}"
    headers = {
        'Authorization': auth,
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
    }

    logger.info(f"Search: route={route}, params={filtered_params}, proxy={'iploop' if iploop_client else 'direct'}")

    try:
        if iploop_client:
            # ✅ [جديد] استخدام Iploop مع إعادة المحاولة التلقائية
            response = iploop_client.post(
                url,
                data=filtered_params,
                headers=headers,
                country=IPLOOP_COUNTRY,
                timeout=UPSTREAM_TIMEOUT,
                retries=3 # إعادة المحاولة حتى 3 مرات عناوين IP مختلفة
            )
        else:
            response = requests.post(url, data=filtered_params, headers=headers, timeout=UPSTREAM_TIMEOUT)

        if response.status_code == 401:
            return jsonify({'success': False, 'error': 'Upstream authentication failed'}), 401
        elif response.status_code == 404:
            return jsonify({'success': False, 'error': 'Upstream endpoint not found'}), 404
        elif response.status_code >= 500:
            return jsonify({'success': False, 'error': f'Upstream server error: {response.status_code}'}), response.status_code

        try:
            upstream_data = response.json()
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': 'Invalid response from upstream server'}), 502

        results = normalize_response(upstream_data)
        if not results:
            return jsonify({'success': True, 'results': [], 'count': 0, 'message': 'No records found'}), 200
        return jsonify({'success': True, 'results': results, 'count': len(results)}), 200

    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({'success': False, 'error': f'Proxy or network error: {str(e)[:150]}'}), 502

@app.route('/api/diagnose', methods=['GET'])
def diagnose():
    auth = build_auth_header()
    return jsonify({
        'timestamp': datetime.utcnow().isoformat(),
        'config': {'upstream_base': UPSTREAM_BASE_URL, 'auth': 'configured' if auth else 'missing', 'proxy': 'iploop' if iploop_client else 'direct'},
        'routes': list(ROUTE_MAP.keys()),
        'server': 'running'
    }), 200

@app.route('/api/outbound-ip', methods=['GET'])
def outbound_ip():
    """Returns outbound IP — will show Iploop residential IP when enabled."""
    try:
        target = 'https://api.ipify.org?format=json'
        if iploop_client:
            r = iploop_client.get(target, country=IPLOOP_COUNTRY, timeout=15)
            ip = r.json().get('ip') if r.headers.get('content-type','').startswith('application/json') else r.text
        else:
            r = requests.get(target, timeout=10)
            ip = r.json().get('ip')
        return jsonify({'outbound_ip': ip, 'proxy': 'iploop' if iploop_client else 'direct', 'status': 'ok'}), 200
    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

# ============================================================
# ERROR HANDLERS & MAIN
# ============================================================
@app.errorhandler(404)
def not_found(e): return jsonify({'success': False, 'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e): return jsonify({'success': False, 'error': 'Internal server error'}), 500

if __name__ == '__main__':
    logger.info(f"Starting on port {PORT}, proxy={'iploop' if iploop_client else 'disabled'}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
