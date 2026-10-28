# backend.py
# Intelligence Archive API Proxy — with Iploop Residential Proxy
# Version 2.0.0

import os
import json
import base64
import logging
import socket
import time
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import urllib3.util.connection
from dotenv import load_dotenv

# ✅ إجبار الاتصال عبر IPv4 فقط (احتياطي)
urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

# Load environment variables
load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================
UPSTREAM_BASE_URL = os.getenv('UPSTREAM_BASE_URL', 'https://zezsoft.eu')
UPSTREAM_USER = os.getenv('UPSTREAM_USER', '')
UPSTREAM_PASS = os.getenv('UPSTREAM_PASS', '')
UPSTREAM_TIMEOUT = int(os.getenv('UPSTREAM_TIMEOUT', '25'))
PORT = int(os.getenv('PORT', '8080'))
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')

# ✅ [جديد] إعدادات Iploop
IPLOOP_API_KEY = os.getenv('IPLOOP_API_KEY', '')
IPLOOP_COUNTRY = os.getenv('IPLOOP_COUNTRY', 'PS')
IPLOOP_PROXY_TYPE = os.getenv('IPLOOP_PROXY_TYPE', 'residential')
IPLOOP_CACHE_TTL = int(os.getenv('IPLOOP_CACHE_TTL', '300'))  # 5 دقائق

# ✅ [جديد] محاولة استيراد SDK (اختياري — الكود يعمل حتى بدونه)
try:
    from iploop import IPLoop as _IPLoopSDK
    IPLOOP_SDK_AVAILABLE = True
except ImportError:
    _IPLoopSDK = None
    IPLOOP_SDK_AVAILABLE = False

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app
app = Flask(__name__)
CORS(app, origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ['*'] else '*',
     methods=['GET', 'POST', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'])

# ============================================================
# IPLOOP PROXY MANAGER
# ============================================================
class IploopProxyManager:
    """يدير جلب البروكسي السكني من Iploop مع التخزين المؤقت."""

    def __init__(self):
        self._cached_proxies = None
        self._cache_time = 0
        self._sdk_client = None
        self._sdk_initialized = False

    def _init_sdk(self):
        if self._sdk_initialized:
            return
        self._sdk_initialized = True
        if not IPLOOP_SDK_AVAILABLE or not IPLOOP_API_KEY:
            return
        try:
            self._sdk_client = _IPLoopSDK(api_key=IPLOOP_API_KEY)
            logger.info("Iploop SDK initialized")
        except Exception as e:
            logger.warning(f"Iploop SDK init failed: {e}")
            self._sdk_client = None

    def _extract_proxy_url(self, data):
        """يستخرج عنوان البروكسي من أشكال الردود المختلفة."""
        if not data:
            return None
        # سلسلة مباشرة
        if isinstance(data, str):
            return data if ':' in data else None
        # قاموس
        if isinstance(data, dict):
            if 'proxy' in data and isinstance(data['proxy'], str):
                return data['proxy']
            if 'data' in data:
                items = data['data']
                if isinstance(items, list) and items:
                    item = items[0]
                    if isinstance(item, str):
                        return item
                    if isinstance(item, dict):
                        ip = item.get('ip')
                        port = item.get('port')
                        user = item.get('username') or item.get('user')
                        pw = item.get('password') or item.get('pass')
                        if ip and port:
                            if user and pw:
                                return f'http://{user}:{pw}@{ip}:{port}'
                            return f'http://{ip}:{port}'
            if 'ip' in data and 'port' in data:
                ip = data['ip']
                port = data['port']
                user = data.get('username') or data.get('user')
                pw = data.get('password') or data.get('pass')
                if user and pw:
                    return f'http://{user}:{pw}@{ip}:{port}'
                return f'http://{ip}:{port}'
        # قائمة
        if isinstance(data, list) and data:
            return self._extract_proxy_url(data[0])
        return None

    def _fetch_via_sdk(self):
        """يحاول جلب البروكسي عبر SDK."""
        self._init_sdk()
        if not self._sdk_client:
            return None
        # محاولة استدعاء دوال مختلفة (تختلف حسب إصدار SDK)
        method_names = ['fetch_proxy', 'get_proxy', 'get_proxies', 'fetch_proxies']
        for name in method_names:
            if not hasattr(self._sdk_client, name):
                continue
            method = getattr(self._sdk_client, name)
            # محاولات متعددة لتوقيع الدالة
            for kwargs in [
                {'country': IPLOOP_COUNTRY, 'type': IPLOOP_PROXY_TYPE},
                {'country': IPLOOP_COUNTRY},
                {'country_code': IPLOOP_COUNTRY},
                {},
            ]:
                try:
                    result = method(**kwargs)
                    proxy_url = self._extract_proxy_url(result)
                    if proxy_url:
                        logger.info(f"Iploop proxy fetched via SDK ({name})")
                        return proxy_url
                except TypeError:
                    continue
                except Exception as e:
                    logger.debug(f"SDK method {name} with {kwargs} failed: {e}")
        return None

    def _fetch_via_api(self):
        """يحاول جلب البروكسي عبر HTTP API مباشرة."""
        endpoints = [
            'https://api.iploop.io/api/v1/fetch-proxy',
            'https://api.iploop.io/api/fetch-proxy',
            'https://api.iploop.io/v1/proxy',
        ]
        for endpoint in endpoints:
            try:
                r = requests.get(
                    endpoint,
                    headers={'API-KEY': IPLOOP_API_KEY, 'Authorization': f'Bearer {IPLOOP_API_KEY}'},
                    params={
                        'country': IPLOOP_COUNTRY,
                        'type': IPLOOP_PROXY_TYPE,
                        'count': 1,
                    },
                    timeout=10
                )
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except Exception:
                        data = r.text.strip()
                    proxy_url = self._extract_proxy_url(data)
                    if proxy_url:
                        logger.info(f"Iploop proxy fetched via API ({endpoint})")
                        return proxy_url
            except Exception as e:
                logger.debug(f"API endpoint {endpoint} failed: {e}")
        return None

    def get_proxies(self, force_refresh=False):
        """يرجع قاموس proxies لاستخدامه مع requests، أو None."""
        if not IPLOOP_API_KEY:
            return None

        now = time.time()
        if not force_refresh and self._cached_proxies and (now - self._cache_time) < IPLOOP_CACHE_TTL:
            return self._cached_proxies

        proxy_url = self._fetch_via_sdk() or self._fetch_via_api()

        if proxy_url:
            self._cached_proxies = {'http': proxy_url, 'https': proxy_url}
            self._cache_time = now
            return self._cached_proxies

        logger.warning("Could not fetch Iploop proxy — falling back to direct connection")
        return None

    def invalidate(self):
        """يبطل التخزين المؤقت (لجلب بروكسي جديد)."""
        self._cached_proxies = None
        self._cache_time = 0


# كائن عام لإدارة البروكسي
iploop_manager = IploopProxyManager()

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
        logger.error('UPSTREAM_USER or UPSTREAM_PASS not configured')
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


def do_upstream_request(url, data, headers, timeout):
    """
    ينفذ طلب POST إلى upstream، مستخدماً Iploop إن أمكن.
    يعيد كائن response من requests.
    """
    proxies = iploop_manager.get_proxies()

    if proxies:
        try:
            logger.info(f"Using Iploop proxy for: {url}")
            response = requests.post(
                url, data=data, headers=headers,
                timeout=timeout, proxies=proxies
            )
            # إذا فشل البروكسي، نجرب بروكسي جديد
            if response.status_code in (403, 502, 503, 504):
                logger.warning(f"Proxy returned {response.status_code}, refreshing...")
                iploop_manager.invalidate()
                new_proxies = iploop_manager.get_proxies(force_refresh=True)
                if new_proxies and new_proxies != proxies:
                    logger.info("Retrying with fresh Iploop proxy")
                    return requests.post(
                        url, data=data, headers=headers,
                        timeout=timeout, proxies=new_proxies
                    )
            return response
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            logger.warning(f"Proxy connection failed: {e} — retrying with fresh proxy")
            iploop_manager.invalidate()
            new_proxies = iploop_manager.get_proxies(force_refresh=True)
            if new_proxies:
                try:
                    return requests.post(
                        url, data=data, headers=headers,
                        timeout=timeout, proxies=new_proxies
                    )
                except Exception as e2:
                    logger.error(f"Fresh proxy also failed: {e2}")

    # Fallback: اتصال مباشر
    logger.info("Using direct connection (no proxy)")
    return requests.post(url, data=data, headers=headers, timeout=timeout)


# ============================================================
# ROUTES
# ============================================================
@app.route('/api/health', methods=['GET', 'OPTIONS'])
def health_check():
    if request.method == 'OPTIONS':
        return '', 200

    auth = build_auth_header()
    if not auth:
        return jsonify({
            'status': 'degraded',
            'message': 'API credentials not configured'
        }), 200

    proxy_status = 'disabled'
    upstream_ok = False

    try:
        test_url = f"{UPSTREAM_BASE_URL}/PSApp/PsArchive/getFamily.php"
        test_headers = {
            'Authorization': auth,
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        # اختبار سريع (5 ثوانٍ) بدون انتظار جلب بروكسي جديد
        proxies = None
        if IPLOOP_API_KEY and iploop_manager._cached_proxies:
            proxies = iploop_manager._cached_proxies
            proxy_status = 'iploop (cached)'
        elif IPLOOP_API_KEY:
            proxy_status = 'iploop (not fetched yet)'

        resp = requests.post(
            test_url, data={'family': 'test'},
            headers=test_headers, timeout=8, proxies=proxies
        )
        upstream_ok = resp.status_code == 200
    except Exception as e:
        logger.warning(f"Health check upstream ping failed: {e}")

    return jsonify({
        'status': 'online',
        'timestamp': datetime.utcnow().isoformat(),
        'upstream': 'online' if upstream_ok else 'degraded',
        'proxy': proxy_status,
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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
    }

    logger.info(f"Search: route={route}, params={filtered_params}, proxy={'iploop' if IPLOOP_API_KEY else 'direct'}")

    try:
        response = do_upstream_request(url, filtered_params, headers, UPSTREAM_TIMEOUT)

        if response.status_code == 401:
            return jsonify({'success': False, 'error': 'Upstream authentication failed'}), 401
        elif response.status_code == 403:
            return jsonify({
                'success': False,
                'error': 'Upstream blocked request (403) — proxy IP may be blocked'
            }), 403
        elif response.status_code == 404:
            return jsonify({'success': False, 'error': 'Upstream endpoint not found'}), 404
        elif response.status_code >= 500:
            return jsonify({
                'success': False,
                'error': f'Upstream server error: {response.status_code}'
            }), response.status_code

        try:
            upstream_data = response.json()
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from upstream: {response.text[:200]}")
            return jsonify({'success': False, 'error': 'Invalid response from upstream server'}), 502

        results = normalize_response(upstream_data)

        if not results:
            return jsonify({
                'success': True, 'results': [], 'count': 0,
                'message': 'No records found'
            }), 200

        return jsonify({
            'success': True,
            'results': results,
            'count': len(results)
        }), 200

    except requests.exceptions.Timeout:
        logger.error(f"Timeout: {url}")
        return jsonify({'success': False, 'error': 'Upstream request timeout'}), 504
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return jsonify({'success': False, 'error': 'Cannot connect to upstream or proxy'}), 503
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@app.route('/api/diagnose', methods=['GET'])
def diagnose():
    auth = build_auth_header()
    return jsonify({
        'timestamp': datetime.utcnow().isoformat(),
        'config': {
            'upstream_base': UPSTREAM_BASE_URL,
            'auth': 'configured' if auth else 'missing',
            'timeout': UPSTREAM_TIMEOUT,
            'iploop_enabled': bool(IPLOOP_API_KEY),
            'iploop_country': IPLOOP_COUNTRY,
            'iploop_sdk_available': IPLOOP_SDK_AVAILABLE,
        },
        'routes': list(ROUTE_MAP.keys()),
        'server': 'running'
    }), 200


@app.route('/api/outbound-ip', methods=['GET'])
def outbound_ip():
    """يعرض IP الصادر — سيكون IP سكني عند تفعيل Iploop."""
    try:
        target = 'https://api.ipify.org?format=json'
        proxies = iploop_manager.get_proxies(force_refresh=True)

        if proxies:
            r = requests.get(target, timeout=15, proxies=proxies)
            proxy_used = 'iploop'
        else:
            r = requests.get(target, timeout=10)
            proxy_used = 'direct'

        try:
            ip = r.json().get('ip')
        except Exception:
            ip = r.text.strip()

        return jsonify({
            'outbound_ip': ip,
            'proxy': proxy_used,
            'iploop_country': IPLOOP_COUNTRY,
            'status': 'ok'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'failed',
            'error': str(e),
            'proxy': 'iploop' if IPLOOP_API_KEY else 'direct'
        }), 500


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
    logger.info(f"Starting Intelligence Archive API Proxy v2.0.0")
    logger.info(f"Upstream: {UPSTREAM_BASE_URL}")
    logger.info(f"Auth: {'configured' if (UPSTREAM_USER and UPSTREAM_PASS) else 'MISSING'}")
    logger.info(f"Routes: {len(ROUTE_MAP)}")
    logger.info(f"Iploop: {'ENABLED' if IPLOOP_API_KEY else 'DISABLED'}")
    logger.info(f"Iploop SDK available: {IPLOOP_SDK_AVAILABLE}")
    logger.info(f"Iploop country: {IPLOOP_COUNTRY}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
