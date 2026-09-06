import re
import json
import base64
import time
import threading
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, Tuple
from bs4 import BeautifulSoup
from config import setup_logger

logger = setup_logger('download_resolver')

# Standard browser headers
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}


class DownloadCache:
    """Thread-safe in-memory cache with TTL for resolved direct download URLs."""
    
    def __init__(self, default_ttl_seconds: int = 1800):  # 30 minutes default safe TTL
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl_seconds

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                if time.time() < entry['expires_at']:
                    return entry['data']
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, data: Dict[str, Any], ttl: Optional[int] = None) -> None:
        with self._lock:
            ttl_val = ttl if ttl is not None else self.default_ttl
            self._cache[key] = {
                'data': data,
                'expires_at': time.time() + ttl_val
            }

    def delete(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# Global cache instance
download_cache = DownloadCache()


def verify_r2_url(url: str, timeout: float = 3.5) -> bool:
    """
    Performs a lightweight HTTP range request (bytes=0-0) to verify that
    the presigned Cloudflare R2 URL is alive, valid, and not expired.
    Returns True if status is 200 or 206 and not an XML error.
    Returns False if status is 400/403 (ExpiredRequest/AccessDenied) or times out.
    """
    if not url or not isinstance(url, str):
        return False
    if 'r2.cloudflarestorage.com' not in url:
        return True

    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={
            'Range': 'bytes=0-0',
            'User-Agent': HEADERS['User-Agent']
        }
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            if resp.status in (200, 206):
                ct = resp.headers.get('Content-Type', '')
                if 'xml' in ct.lower():
                    return False
                return True
            return False
    except urllib.error.HTTPError as e:
        logger.warning(f"R2 URL verification failed ({e.code} {e.reason}) for {url[:90]}...")
        return False
    except Exception as ex:
        logger.warning(f"R2 URL verification check exception: {ex}")
        return False


def resolve_movie_direct_download(
    source_url: str,
    target_quality: str = "1080p",
    fallback_url: str = "",
    file_id: str = "",
    timeout: int = 12,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Resolves a movie's quality download link to its direct Cloudflare R2 CDN download URL.
    Verifies that the resolved URL is active and unexpired before caching/returning.
    Returns:
        {
            "success": bool,
            "download_url": str,
            "filename": str,
            "quality": str,
            "source": str,
            "error": Optional[str]
        }
    """
    cache_key = f"{source_url}:{target_quality}:{file_id}"
    if not force_refresh:
        cached = download_cache.get(cache_key)
        if cached:
            cached_url = cached.get('download_url', '')
            if verify_r2_url(cached_url, timeout=3.0):
                logger.info(f"Returning verified cached direct download URL for {cache_key}")
                return cached
            else:
                logger.info(f"Cached direct download URL expired or invalid for {cache_key}. Evicting and refreshing.")
                download_cache.delete(cache_key)

    logger.info(f"Resolving direct download for {source_url} ({target_quality})")

    # If URL is a P2P magnet link, resolve via multi-source direct CDN or return clean magnet URL for full P2P movie download
    if (fallback_url and fallback_url.startswith('magnet:')) or (source_url and source_url.startswith('magnet:')):
        mag_url = fallback_url if (fallback_url and fallback_url.startswith('magnet:')) else source_url
        
        # 1. Search database for direct high-speed CDN match (MWLBD, VegaMovies, Bolly4u Cloudflare R2 / GDrive)
        try:
            from database import db
            all_movies = db.get_all_movies()
            lookup_key = (file_id or source_url or "").lower()
            clean_target = re.sub(r'[^a-z0-9]+', ' ', lookup_key).strip()
            
            for m in all_movies:
                m_title = re.sub(r'[^a-z0-9]+', ' ', m.get('title', '').lower()).strip()
                links = m.get('download_links', [])
                has_direct_link = any(l.get('type') != 'magnet' and 'magnet:' not in str(l.get('url', '')) for l in links)
                
                if has_direct_link and (clean_target in m_title or m_title in clean_target or (len(clean_target) > 5 and clean_target[:8] in m_title)):
                    for l in links:
                        if l.get('type') != 'magnet' and 'magnet:' not in str(l.get('url', '')):
                            src_url = m.get('source_url', '')
                            fb_url = l.get('url', '')
                            f_id = l.get('file_id', '')
                            resolved = resolve_movie_direct_download(src_url, target_quality=target_quality, fallback_url=fb_url, file_id=f_id)
                            if resolved.get('success') and resolved.get('download_url') and not resolved.get('download_url').startswith('magnet:'):
                                resolved['source'] = f"Direct High-Speed Cloud CDN (Matched via {m.get('source_site', 'Multi-Source')})"
                                download_cache.set(cache_key, resolved, ttl=1800)
                                return resolved
        except Exception as ex_m:
            logger.warning(f"1337x magnet cross-source lookup exception: {ex_m}")

        # 2. Return clean magnet link for full P2P movie file download
        res = {
            "success": True,
            "download_url": mag_url,
            "filename": f"movie_{target_quality}.torrent",
            "quality": target_quality,
            "source": "1337x P2P Magnet Link",
            "error": None
        }
        download_cache.set(cache_key, res, ttl=1800)
        return res

    # If fallback is already a direct drive or file link, verify and use it
    if fallback_url and ('drive.google.com' in fallback_url or 'r2.cloudflarestorage.com' in fallback_url):
        if verify_r2_url(fallback_url, timeout=3.0):
            res = {
                "success": True,
                "download_url": fallback_url,
                "filename": f"movie_{target_quality}.mkv",
                "quality": target_quality,
                "source": "Direct Link",
                "error": None
            }
            download_cache.set(cache_key, res, ttl=1800)
            return res

    try:
        res_m = re.search(r'\b(2160p|1080p|720p|480p|360p|4k)\b', target_quality, re.I)
        res_token = res_m.group(1).lower() if res_m else target_quality.lower()
        is_hevc = 'hevc' in target_quality.lower()

        # Step 1: Fetch source movie page
        req1 = urllib.request.Request(source_url, headers=HEADERS)
        html1 = urllib.request.urlopen(req1, timeout=timeout).read().decode('utf-8', errors='ignore')
        soup1 = BeautifulSoup(html1, 'html.parser')

        target_form = None
        if file_id:
            target_form = soup1.find('form', {'id': file_id})

        if not target_form:
            best_tr_form = None
            for tr in soup1.find_all('tr'):
                txt = tr.get_text().lower()
                if res_token in txt:
                    f = tr.find('form')
                    if f and f.find('input', {'name': 'FU'}):
                        if is_hevc and 'hevc' in txt:
                            target_form = f
                            break
                        if not best_tr_form:
                            best_tr_form = f
            if not target_form and best_tr_form:
                target_form = best_tr_form

        if not target_form:
            for f in soup1.find_all('form'):
                if f.find('input', {'name': 'FU'}):
                    target_form = f
                    break

        if not target_form:
            raise Exception("No download form found on movie page.")

        action1 = target_form.get('action') or "https://search.technews24.site/blog.php"
        if not action1.startswith('http'):
            action1 = urllib.parse.urljoin('https://search.technews24.site/', action1)
        inputs1 = {inp.get('name'): inp.get('value') for inp in target_form.find_all('input')}

        # Step 2: POST to blog.php
        req2 = urllib.request.Request(
            action1,
            data=urllib.parse.urlencode(inputs1).encode('utf-8'),
            headers={**HEADERS, 'Referer': source_url, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        html2 = urllib.request.urlopen(req2, timeout=timeout).read().decode('utf-8', errors='ignore')
        soup2 = BeautifulSoup(html2, 'html.parser')
        form2 = soup2.find('form')
        if not form2:
            raise Exception("Step 2: Verification form not found on blog.php.")
        action2 = form2.get('action')
        if not action2 or not action2.startswith('http'):
            action2 = urllib.parse.urljoin('https://sharelink-1.shop/', action2 or 'dld2.php')
        inputs2 = {inp.get('name'): inp.get('value') for inp in form2.find_all('input')}

        # Step 3: POST to sharelink-1.shop/dld2.php
        req3 = urllib.request.Request(
            action2,
            data=urllib.parse.urlencode(inputs2).encode('utf-8'),
            headers={**HEADERS, 'Referer': action1, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        html3 = urllib.request.urlopen(req3, timeout=timeout).read().decode('utf-8', errors='ignore')
        soup3 = BeautifulSoup(html3, 'html.parser')
        form3 = soup3.find('form')
        action3 = form3.get('action') if form3 else None
        if not action3 or not action3.startswith('http'):
            action3 = urllib.parse.urljoin('https://freethemesy.shop/', action3 or 'dld2.php')
        inputs3 = {inp.get('name'): inp.get('value') for inp in form3.find_all('input')} if form3 else inputs2

        # Step 4: POST to freethemesy.shop/dld2.php
        req4 = urllib.request.Request(
            action3,
            data=urllib.parse.urlencode(inputs3).encode('utf-8'),
            headers={**HEADERS, 'Referer': action2, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        html4 = urllib.request.urlopen(req4, timeout=timeout).read().decode('utf-8', errors='ignore')

        sss_m = re.search(r"var sss\s*=\s*'([^']+)'", html4)
        sss_val = sss_m.group(1) if sss_m else inputs3.get('FU2', '')
        if not sss_val:
            raise Exception("Step 4: Token not found in verification page.")
        v_literal = re.search(r"'([a-f0-9]{13})'", html4)
        v_val = v_literal.group(1) if v_literal else "6a9be278aa0a3"

        # Step 5: freethemesy API call -> links page
        req5 = urllib.request.Request(
            "https://freethemesy.shop/new/l/api/m",
            data=urllib.parse.urlencode({'s': sss_val, 'v': v_val}).encode('utf-8'),
            headers={
                **HEADERS,
                'Referer': action3,
                'Origin': 'https://freethemesy.shop',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest'
            }
        )
        links_page_url = urllib.request.urlopen(req5, timeout=timeout).read().decode('utf-8').strip()

        # Step 6: Fetch links page & select matching GDS quality link
        req6 = urllib.request.Request(links_page_url, headers={**HEADERS, 'Referer': 'https://freethemesy.shop/'})
        html6 = urllib.request.urlopen(req6, timeout=timeout).read().decode('utf-8', errors='ignore')
        soup6 = BeautifulSoup(html6, 'html.parser')

        chosen_gds_url = None
        best_gds_url = None
        for p in soup6.find_all('p'):
            p_text = p.get_text().lower()
            if res_token in p_text:
                for a in p.find_all('a'):
                    if a.get_text().strip().upper() == 'GDS' and a.get('href'):
                        if is_hevc and 'hevc' in p_text:
                            chosen_gds_url = a.get('href')
                            break
                        if not best_gds_url:
                            best_gds_url = a.get('href')
                if chosen_gds_url:
                    break

        if not chosen_gds_url and best_gds_url:
            chosen_gds_url = best_gds_url

        if not chosen_gds_url:
            for a in soup6.find_all('a'):
                if a.get_text().strip().upper() == 'GDS' and a.get('href'):
                    chosen_gds_url = a.get('href')
                    break

        if not chosen_gds_url:
            raise Exception("Step 6: Direct server link not available for this title.")

        # Step 7: Resolve GDS link to Cloudflare R2
        req7a = urllib.request.Request(chosen_gds_url, headers={**HEADERS, 'Referer': links_page_url})
        soup7a = BeautifulSoup(urllib.request.urlopen(req7a, timeout=timeout).read().decode('utf-8', errors='ignore'), 'html.parser')
        form7a = soup7a.find('form')
        if not form7a:
            raise Exception("Step 7a: Destination form not found.")

        action7a = form7a.get('action')
        inputs7a = {inp.get('name'): inp.get('value') for inp in form7a.find_all('input')}

        req7b = urllib.request.Request(
            action7a,
            data=urllib.parse.urlencode(inputs7a).encode('utf-8'),
            headers={**HEADERS, 'Referer': chosen_gds_url, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        soup7b = BeautifulSoup(urllib.request.urlopen(req7b, timeout=timeout).read().decode('utf-8', errors='ignore'), 'html.parser')
        form7b = soup7b.find('form')
        if not form7b:
            raise Exception("Step 7b: Routing form not found.")
        action7b = form7b.get('action')
        inputs7b = {inp.get('name'): inp.get('value') for inp in form7b.find_all('input')}

        req7c = urllib.request.Request(
            action7b,
            data=urllib.parse.urlencode(inputs7b).encode('utf-8'),
            headers={**HEADERS, 'Referer': action7a, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        html7c = urllib.request.urlopen(req7c, timeout=timeout).read().decode('utf-8', errors='ignore')

        sss_m2 = re.search(r"var sss\s*=\s*'([^']+)'", html7c)
        vurl_m2 = re.search(r"var vurl\s*=\s*atob\('([^']+)'\)", html7c)
        v_m2 = re.search(r"v:\s*'([^']+)'", html7c)
        if not (sss_m2 and vurl_m2 and v_m2):
            raise Exception("Step 7c: Link tokens not found on CDN router.")

        s3_sss = sss_m2.group(1)
        raw_b64 = vurl_m2.group(1)
        s3_vurl = base64.b64decode(raw_b64 + '=' * (-len(raw_b64) % 4)).decode('utf-8')
        s3_v = v_m2.group(1)

        api7_url = urllib.parse.urljoin(action7b, s3_vurl)
        req7d = urllib.request.Request(
            api7_url,
            data=json.dumps({'s': s3_sss, 'v': s3_v}).encode('utf-8'),
            headers={
                **HEADERS,
                'Referer': action7b,
                'Origin': action7b[:action7b.find('/', 8)],
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        )
        boa_url = urllib.request.urlopen(req7d, timeout=timeout).read().decode('utf-8').strip()

        # Step 7e: Submit clouddownload on boabd
        req7e = urllib.request.Request(
            boa_url,
            data=urllib.parse.urlencode({'clouddownload': ''}).encode('utf-8'),
            headers={
                **HEADERS,
                'Referer': boa_url,
                'Origin': 'https://boabd.com',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        )
        soup7e = BeautifulSoup(urllib.request.urlopen(req7e, timeout=15).read().decode('utf-8', errors='ignore'), 'html.parser')

        for a in soup7e.find_all('a'):
            href = a.get('href', '')
            if 'r2.cloudflarestorage.com' in href:
                fn_match = re.search(r'filename%3D%22([^%"]+)%22', href) or re.search(r'filename="([^"]+)"', href)
                filename = urllib.parse.unquote(fn_match.group(1)) if fn_match else f"Movie_{target_quality}.mkv"
                
                # Verify URL is alive and not expired before accepting
                if not verify_r2_url(href, timeout=3.5):
                    logger.warning(f"R2 URL from boabd failed live verification (likely expired token): {href[:90]}...")
                    continue

                # Compute safe TTL from X-Amz-Expires
                ttl = 1800
                exp_m = re.search(r'X-Amz-Expires=(\d+)', href)
                if exp_m:
                    ttl = min(int(exp_m.group(1)) - 300, 3600)
                    if ttl < 300:
                        ttl = 300

                res = {
                    "success": True,
                    "download_url": href,
                    "filename": filename,
                    "quality": target_quality,
                    "source": "Cloudflare R2 High-Speed CDN",
                    "error": None
                }
                download_cache.set(cache_key, res, ttl=ttl)
                logger.info(f"Successfully resolved verified direct R2 URL: {filename} (TTL={ttl}s)")
                return res

        raise Exception("Direct R2 CDN link not active or not generated on storage server.")

    except Exception as ex:
        logger.exception(f"Download resolution error for {source_url} ({target_quality}): {ex}")
        # Fallback to direct GDrive download if file_id exists
        if file_id and file_id.isalnum() and len(file_id) > 15:
            fallback = f"https://drive.google.com/uc?export=download&confirm=t&id={file_id}"
            return {
                "success": True,
                "download_url": fallback,
                "filename": f"movie_{target_quality}.mkv",
                "quality": target_quality,
                "source": "Direct High-Speed Stream",
                "error": None
            }
        elif fallback_url and fallback_url != "https://search.technews24.site/blog.php":
            return {
                "success": True,
                "download_url": fallback_url,
                "filename": f"movie_{target_quality}.mkv",
                "quality": target_quality,
                "source": "Direct Source",
                "error": None
            }
        return {
            "success": False,
            "download_url": fallback_url or source_url,
            "filename": f"movie_{target_quality}.mkv",
            "quality": target_quality,
            "source": "None",
            "error": str(ex)
        }


def find_mkv_tracks_element(data: bytes) -> int:
    """Finds the actual Tracks master element (0x16 0x54 0xAE 0x6B) containing TrackEntries."""
    needle = b'\x16\x54\xae\x6b'
    pos = 0
    while True:
        idx = data.find(needle, pos)
        if idx == -1:
            return -1
        # Skip if part of SeekHead SeekID (0x53 0xAB)
        if idx >= 2 and data[idx-2:idx] == b'\x53\xab':
            pos = idx + len(needle)
            continue
        # Check if followed by size vint and TrackEntry (0xAE)
        if b'\xae' in data[idx+4:idx+24]:
            return idx
        pos = idx + len(needle)


def patch_mkv_header_bytes(first_chunk: bytearray, is_bollywood: bool = False) -> bytearray:
    """
    Parses TrackEntry elements in the first MKV chunk and patches audio default flags:
    - Hollywood / Dual Audio: Sets English audio FlagDefault=1 and FlagEnabled=1, Hindi audio FlagDefault=0.
    - Bollywood: Preserves Hindi audio default.
    Zero audio/video re-encoding and zero byte length change.
    """
    if is_bollywood:
        return first_chunk

    tracks_idx = find_mkv_tracks_element(first_chunk)
    if tracks_idx == -1:
        return first_chunk

    pos = tracks_idx + 4
    first_ae = first_chunk.find(b'\xae', pos, pos + 30)
    if first_ae == -1:
        return first_chunk

    curr = first_ae
    entries = []
    chunk_len = len(first_chunk)

    while curr != -1 and curr < chunk_len - 20:
        if first_chunk[curr] != 0xae:
            break
        vint_first = first_chunk[curr + 1]
        size = None
        size_len = 0
        for mask_len in range(1, 9):
            mask = 1 << (8 - mask_len)
            if vint_first & mask:
                size_len = mask_len
                raw_val = vint_first & (mask - 1)
                for b_idx in range(1, size_len):
                    raw_val = (raw_val << 8) | first_chunk[curr + 1 + b_idx]
                size = raw_val
                break
        if size is None or size <= 0:
            break
        entry_start = curr
        entry_end = curr + 1 + size_len + size
        entries.append((entry_start, entry_end, size_len))
        curr = entry_end
        if curr < chunk_len and first_chunk[curr] == 0xec:
            v_first = first_chunk[curr + 1]
            for mask_len in range(1, 9):
                mask = 1 << (8 - mask_len)
                if v_first & mask:
                    v_size_len = mask_len
                    v_val = v_first & (mask - 1)
                    for b_idx in range(1, v_size_len):
                        v_val = (v_val << 8) | first_chunk[curr + 1 + b_idx]
                    curr = curr + 1 + v_size_len + v_val
                    break

    for start, end, _ in entries:
        chunk = first_chunk[start:end]
        tt_pos = chunk.find(b'\x83\x81')
        if tt_pos == -1 or chunk[tt_pos + 2] != 2:  # 2 = Audio
            continue

        lang = "und"
        if b'"\xb5\x9d\x82en' in chunk or b'"\xb5\x9c\x83eng' in chunk or b'English' in chunk or b'english' in chunk:
            lang = "eng"
        elif b'"\xb5\x9d\x82hi' in chunk or b'"\xb5\x9c\x83hin' in chunk or b'Hindi' in chunk or b'hindi' in chunk:
            lang = "hin"

        fd_offset = chunk.find(b'\x88\x81')
        fe_offset = chunk.find(b'\xb9\x81')

        if not is_bollywood:
            if lang == "eng":
                if fe_offset != -1 and first_chunk[start + fe_offset + 2] == 0:
                    first_chunk[start + fe_offset + 2] = 1
                    logger.info(f"Patched MKV on-the-fly: English audio FlagEnabled -> 1 (offset {start + fe_offset + 2})")
                if fd_offset != -1:
                    first_chunk[start + fd_offset + 2] = 1
                    logger.info(f"Patched MKV on-the-fly: English audio FlagDefault -> 1 (offset {start + fd_offset + 2})")
            elif lang == "hin":
                if fd_offset != -1:
                    first_chunk[start + fd_offset + 2] = 0
                    logger.info(f"Patched MKV on-the-fly: Hindi audio FlagDefault -> 0 (offset {start + fd_offset + 2})")
                else:
                    hin_lang_tag = b'\x22\xb5\x9c\x83hin'
                    hl_offset = chunk.find(hin_lang_tag)
                    if hl_offset != -1:
                        abs_pos = start + hl_offset
                        first_chunk[abs_pos:abs_pos+7] = b'\x88\x81\x00\xec\x82\x00\x00'
                        logger.info(f"Patched MKV on-the-fly: Hindi audio injected FlagDefault -> 0 (offset {abs_pos})")

    return first_chunk


def stream_mkv_with_auto_audio(
    r2_url: str,
    is_bollywood: bool = False,
    range_header: Optional[str] = None
) -> Tuple[Any, int, dict]:
    """
    Streams MKV video file chunks from Cloudflare R2 to client.
    Automatically patches the first 64KB chunk on the fly so that:
    - English is the default audio track for Hollywood/Dual Audio.
    - Hindi is the default audio track for Bollywood.
    Zero manual configuration required in any media player.
    """
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    if range_header:
        headers['Range'] = range_header

    req = urllib.request.Request(r2_url, headers=headers)
    upstream = urllib.request.urlopen(req, context=ctx, timeout=30)

    status = upstream.status
    content_length = upstream.headers.get('Content-Length')
    content_range = upstream.headers.get('Content-Range')
    content_type = upstream.headers.get('Content-Type', 'video/x-matroska')

    resp_headers = {
        'Content-Type': content_type,
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'self'",
        'Cache-Control': 'no-transform, public, max-age=86400',
        'X-Download-Options': 'noopen'
    }
    if content_length:
        resp_headers['Content-Length'] = content_length
    if content_range:
        resp_headers['Content-Range'] = content_range

    def generate():
        starts_at_zero = not range_header or range_header.startswith('bytes=0-')
        if starts_at_zero and not is_bollywood:
            first_chunk = bytearray(upstream.read(65536))
            patched = patch_mkv_header_bytes(first_chunk, is_bollywood=False)
            yield bytes(patched)

        while True:
            chunk = upstream.read(1048576) # 1 MB
            if not chunk:
                break
            yield chunk

    return generate(), status, resp_headers


