import os
import re
import time
import json
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from config import Config, setup_logger
from database import db
from telegram_notifier import send_new_visitor_alert

logger = setup_logger('analytics_tracker')

# Local fallback storage path
ANALYTICS_DATA_DIR = os.path.join(os.path.dirname(Config.DATABASE_FILE), 'analytics')
os.makedirs(ANALYTICS_DATA_DIR, exist_ok=True)
ANALYTICS_CACHE_FILE = os.path.join(ANALYTICS_DATA_DIR, 'analytics_cache.json')

# In-memory session and metrics state
_state_lock = threading.RLock()
_seen_visitor_hashes: set = set()
_active_sessions: Dict[str, float] = {}  # visitor_hash -> last_active_timestamp
_local_downloads: List[Dict[str, Any]] = []
_local_visitors: Dict[str, Dict[str, Any]] = {}
_pageviews_24h_counter: int = 0
_last_pageview_reset: float = time.time()


def _load_local_cache():
    """Loads cached analytics data from local disk if available."""
    global _local_visitors, _local_downloads, _seen_visitor_hashes
    if os.path.exists(ANALYTICS_CACHE_FILE):
        try:
            with open(ANALYTICS_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _local_visitors = data.get('visitors', {})
                _local_downloads = data.get('downloads', [])
                _seen_visitor_hashes = set(_local_visitors.keys())
                logger.info(f"Loaded {len(_local_visitors)} visitors and {len(_local_downloads)} downloads from local analytics cache.")
        except Exception as e:
            logger.warning(f"Could not load analytics cache: {e}")


def _save_local_cache():
    """Saves analytics state to local disk asynchronously."""
    try:
        data = {
            'visitors': _local_visitors,
            'downloads': _local_downloads[-500:],  # keep last 500
            'saved_at': datetime.utcnow().isoformat()
        }
        with open(ANALYTICS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save analytics cache: {e}")


# Initialize cache at import time
_load_local_cache()


def mask_ip(ip: str) -> str:
    """Masks IP address for user privacy (e.g. 124.106.12.34 -> 124.106.***.***)."""
    if not ip or ip == '127.0.0.1' or ip == 'localhost':
        return '127.0.0.1'
    if ':' in ip:  # IPv6
        parts = ip.split(':')
        return ':'.join(parts[:2]) + ':****:****'
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.***.***"
    return ip


def get_client_ip(req) -> str:
    """Extracts genuine client IP address considering proxy headers."""
    # 1. Cloudflare
    cf_ip = req.headers.get('CF-Connecting-IP')
    if cf_ip:
        return cf_ip.split(',')[0].strip()

    # 2. X-Forwarded-For
    xff = req.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[0].strip()

    # 3. X-Real-IP
    x_real = req.headers.get('X-Real-IP')
    if x_real:
        return x_real.split(',')[0].strip()

    # 4. Standard remote address
    return req.remote_addr or '127.0.0.1'


def parse_client_device(ua_str: str) -> Tuple[str, str, str]:
    """
    Parses user-agent string into (device_type, os_name, browser_name).
    """
    ua = (ua_str or '').lower()
    
    # Device
    if any(k in ua for k in ['ipad', 'tablet', 'kindle', 'playbook']):
        device = 'Tablet'
    elif any(k in ua for k in ['mobile', 'iphone', 'android', 'phone', 'ipod']):
        device = 'Mobile'
    else:
        device = 'Desktop'

    # OS
    if 'windows' in ua:
        os_name = 'Windows'
    elif 'android' in ua:
        os_name = 'Android'
    elif 'iphone' in ua or 'ipad' in ua or 'ios' in ua:
        os_name = 'iOS'
    elif 'macintosh' in ua or 'mac os' in ua:
        os_name = 'macOS'
    elif 'linux' in ua:
        os_name = 'Linux'
    else:
        os_name = 'Other'

    # Browser
    if 'edg' in ua:
        browser = 'Edge'
    elif 'chrome' in ua and 'edg' not in ua:
        browser = 'Chrome'
    elif 'safari' in ua and 'chrome' not in ua:
        browser = 'Safari'
    elif 'firefox' in ua:
        browser = 'Firefox'
    elif 'opera' in ua or 'opr' in ua:
        browser = 'Opera'
    else:
        browser = 'Web Browser'

    return device, os_name, browser


def parse_referrer_domain(ref_url: str) -> str:
    """Extracts clean source domain from HTTP Referer."""
    if not ref_url:
        return 'Direct / Social Media'
    ref_l = ref_url.lower()
    if 'facebook.com' in ref_l or 'fb.me' in ref_l:
        return 'Facebook'
    if 't.co' in ref_l or 'twitter.com' in ref_l or 'x.com' in ref_l:
        return 'X (Twitter)'
    if 'instagram.com' in ref_l:
        return 'Instagram'
    if 'reddit.com' in ref_l:
        return 'Reddit'
    if 'tiktok.com' in ref_l:
        return 'TikTok'
    if 'google.' in ref_l:
        return 'Google Search'
    if 'bing.com' in ref_l:
        return 'Bing Search'
    if 'youtube.com' in ref_l:
        return 'YouTube'
    
    # Extract hostname
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(ref_url)
        return parsed.netloc or 'External Link'
    except Exception:
        return 'External Link'


def generate_visitor_hash(ip: str, ua: str, vid_cookie: Optional[str] = None) -> str:
    """Computes a stable anonymized hash for a visitor."""
    if vid_cookie and len(vid_cookie) > 8:
        return vid_cookie[:24]
    raw = f"{ip}:{ua}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def is_static_or_monitoring_request(path: str) -> bool:
    """Filters out internal polling, health checks, and static asset requests."""
    p = path.lower()
    if any(p.startswith(pref) for pref in ['/static', '/favicon', '/robots.txt', '/api/monitor', '/api/resolve-status']):
        return True
    if any(p.endswith(ext) for ext in ['.css', '.js', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.svg', '.map']):
        return True
    return False


class AnalyticsTracker:
    """Real-time site analytics and monitoring engine with Supabase persistence."""

    def __init__(self):
        self._lock = threading.RLock()

    def record_visit(self, req, vid_cookie: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Records a visitor event.
        Returns (is_new, visitor_hash, visitor_record).
        Dispatches instant Telegram alert if is_new is True.
        """
        global _pageviews_24h_counter, _last_pageview_reset
        path = req.path

        if is_static_or_monitoring_request(path):
            return False, "", {}

        now_ts = time.time()
        now_iso = datetime.utcnow().isoformat()
        now_dt_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

        # Reset 24h pageview counter if 24 hours elapsed
        if now_ts - _last_pageview_reset > 86400:
            _pageviews_24h_counter = 0
            _last_pageview_reset = now_ts

        ip = get_client_ip(req)
        ip_masked = mask_ip(ip)
        ua = req.headers.get('User-Agent', '')
        country = req.headers.get('CF-IPCountry') or req.headers.get('X-Country-Code') or 'Global'
        ref_raw = req.headers.get('Referer', '')
        referrer = parse_referrer_domain(ref_raw)
        device, os_name, browser = parse_client_device(ua)
        v_hash = generate_visitor_hash(ip, ua, vid_cookie)

        is_new = False
        visitor_entry = None

        with self._lock:
            _pageviews_24h_counter += 1
            _active_sessions[v_hash] = now_ts

            # Clean active sessions older than 15 minutes (900s)
            cutoff = now_ts - 900
            expired_sessions = [k for k, v in _active_sessions.items() if v < cutoff]
            for k in expired_sessions:
                del _active_sessions[k]

            if v_hash not in _seen_visitor_hashes:
                is_new = True
                _seen_visitor_hashes.add(v_hash)
                visitor_entry = {
                    'visitor_hash': v_hash,
                    'first_seen': now_iso,
                    'last_seen': now_iso,
                    'visit_count': 1,
                    'ip_masked': ip_masked,
                    'country': country,
                    'device': device,
                    'os': os_name,
                    'browser': browser,
                    'referrer': referrer,
                    'landing_page': path,
                    'timestamp': now_dt_str
                }
                _local_visitors[v_hash] = visitor_entry
                _save_local_cache()
            else:
                if v_hash in _local_visitors:
                    _local_visitors[v_hash]['last_seen'] = now_iso
                    _local_visitors[v_hash]['visit_count'] = _local_visitors[v_hash].get('visit_count', 1) + 1
                    visitor_entry = _local_visitors[v_hash]

        # Supabase Persistence (Asynchronous background task)
        threading.Thread(
            target=self._sync_visitor_to_supabase,
            args=(visitor_entry, is_new),
            daemon=True,
            name="SupabaseVisitorSync"
        ).start()

        # Immediate Telegram Trigger on NEW user arrival
        if is_new and visitor_entry:
            send_new_visitor_alert(visitor_entry)

        return is_new, v_hash, visitor_entry or {}

    def record_download(self, movie_id: str, movie_title: str = "", quality: str = "1080p", source: str = "Cloud Direct", req=None, request_obj=None, title: str = None) -> None:
        """Records a movie download event."""
        req = req or request_obj
        resolved_title = title or movie_title or movie_id
        now_iso = datetime.utcnow().isoformat()
        now_dt_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        ip = get_client_ip(req) if req else '127.0.0.1'
        ip_masked = mask_ip(ip)
        v_hash = generate_visitor_hash(ip, req.headers.get('User-Agent', '') if req else '')

        dl_event = {
            'movie_id': movie_id,
            'movie_title': resolved_title,
            'quality': quality or '1080p',
            'source': source or 'Cloud Direct',
            'ip_masked': ip_masked,
            'visitor_hash': v_hash,
            'timestamp': now_iso,
            'timestamp_str': now_dt_str
        }

        with self._lock:
            _local_downloads.append(dl_event)
            _save_local_cache()

        # Supabase Persistence
        threading.Thread(
            target=self._sync_download_to_supabase,
            args=(dl_event,),
            daemon=True,
            name="SupabaseDownloadSync"
        ).start()

    def _sync_visitor_to_supabase(self, visitor: Optional[Dict[str, Any]], is_new: bool) -> None:
        """Syncs visitor record to Supabase table `site_analytics_visitors`."""
        if not visitor or not hasattr(db, 'client') or not db.client:
            return
        try:
            entry = {
                'visitor_hash': str(visitor.get('visitor_hash', '')),
                'first_seen': visitor.get('first_seen'),
                'last_seen': visitor.get('last_seen'),
                'visit_count': visitor.get('visit_count', 1),
                'ip_masked': visitor.get('ip_masked', ''),
                'country': visitor.get('country', 'Global'),
                'device': visitor.get('device', 'Desktop'),
                'os': visitor.get('os', 'Unknown'),
                'browser': visitor.get('browser', 'Web Browser'),
                'referrer': visitor.get('referrer', 'Direct'),
                'landing_page': visitor.get('landing_page', '/')
            }
            db.client.table('site_analytics_visitors').upsert(entry).execute()
        except Exception as ex:
            logger.debug(f"Supabase visitor sync notice: {ex}")

    def _sync_download_to_supabase(self, dl_event: Dict[str, Any]) -> None:
        """Syncs download event to Supabase table `site_analytics_downloads`."""
        if not hasattr(db, 'client') or not db.client:
            return
        try:
            entry = {
                'movie_id': dl_event.get('movie_id', ''),
                'movie_title': dl_event.get('movie_title', ''),
                'quality': dl_event.get('quality', '1080p'),
                'source': dl_event.get('source', 'Cloud CDN'),
                'visitor_hash': dl_event.get('visitor_hash', ''),
                'created_at': dl_event.get('timestamp')
            }
            db.client.table('site_analytics_downloads').insert(entry).execute()
        except Exception as ex:
            logger.debug(f"Supabase download sync notice: {ex}")

    def get_summary_stats(self) -> Dict[str, Any]:
        """
        Calculates aggregate analytical stats for dashboard rendering
        and 24-hour Telegram report delivery.
        """
        now = datetime.utcnow()
        day_ago = now - timedelta(hours=24)
        day_ago_iso = day_ago.isoformat()

        with self._lock:
            # Active now (last 15m)
            cutoff_15m = time.time() - 900
            active_now = sum(1 for ts in _active_sessions.values() if ts >= cutoff_15m)

            # Visitors in last 24h
            visitors_24h = [
                v for v in _local_visitors.values()
                if v.get('last_seen', '') >= day_ago_iso or v.get('first_seen', '') >= day_ago_iso
            ]
            unique_24h = len(visitors_24h) if visitors_24h else max(1, len(_local_visitors))
            total_unique = len(_local_visitors)

            # Downloads in last 24h & total
            downloads_24h_list = [
                d for d in _local_downloads
                if d.get('timestamp', '') >= day_ago_iso
            ]
            total_downloads = len(_local_downloads)
            downloads_24h = len(downloads_24h_list)

            # Top downloaded movies (24h)
            movie_dl_counts: Dict[str, Dict[str, Any]] = {}
            source_dl_pool = downloads_24h_list if downloads_24h_list else _local_downloads[-50:]
            for d in source_dl_pool:
                m_id = d.get('movie_id', 'unknown')
                m_title = d.get('movie_title') or m_id
                q = d.get('quality', '1080p')
                if m_id not in movie_dl_counts:
                    movie_dl_counts[m_id] = {'movie_id': m_id, 'title': m_title, 'quality': q, 'count': 0}
                movie_dl_counts[m_id]['count'] += 1

            top_downloads_24h = sorted(movie_dl_counts.values(), key=lambda x: x['count'], reverse=True)[:6]

            # Device Breakdown (24h)
            device_pool = visitors_24h if visitors_24h else list(_local_visitors.values())
            mobile_cnt = sum(1 for v in device_pool if v.get('device') == 'Mobile')
            desktop_cnt = sum(1 for v in device_pool if v.get('device') == 'Desktop')
            tablet_cnt = sum(1 for v in device_pool if v.get('device') == 'Tablet')
            total_dev = max(1, mobile_cnt + desktop_cnt + tablet_cnt)

            device_breakdown = {
                'mobile_count': mobile_cnt,
                'desktop_count': desktop_cnt,
                'tablet_count': tablet_cnt,
                'mobile_pct': round((mobile_cnt / total_dev) * 100),
                'desktop_pct': round((desktop_cnt / total_dev) * 100),
                'tablet_pct': round((tablet_cnt / total_dev) * 100)
            }

            # Top Referrers (24h)
            ref_counts: Dict[str, int] = {}
            for v in device_pool:
                r = v.get('referrer', 'Direct')
                ref_counts[r] = ref_counts.get(r, 0) + 1
            top_referrers = sorted([{'domain': k, 'count': v} for k, v in ref_counts.items()], key=lambda x: x['count'], reverse=True)[:5]

            # Recent Visitors (last 10)
            recent_visitors = sorted(
                list(_local_visitors.values()),
                key=lambda x: x.get('last_seen', ''),
                reverse=True
            )[:10]

            # Recent Downloads (last 10)
            recent_downloads = list(reversed(_local_downloads[-10:]))

        return {
            'active_now': max(1, active_now),
            'unique_visitors_24h': unique_24h,
            'pageviews_24h': max(unique_24h, _pageviews_24h_counter),
            'total_unique_visitors': total_unique,
            'downloads_24h': downloads_24h,
            'total_downloads': total_downloads,
            'top_downloads_24h': top_downloads_24h,
            'device_breakdown_24h': device_breakdown,
            'top_referrers_24h': top_referrers,
            'recent_visitors': recent_visitors,
            'recent_downloads': recent_downloads,
            'server_time': now.strftime('%Y-%m-%d %H:%M:%S UTC')
        }


# Global Tracker Instance
tracker = AnalyticsTracker()
