import os
import urllib.request, urllib.parse, json, re, logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SITE_URL = os.getenv('RENDER_EXTERNAL_URL', 'https://justflix-0rxf.onrender.com')
HEADERS = {
    'User-Agent': f'JustFlix/1.0 ({SITE_URL}; contact@justflix.com)'
}

_METADATA_CACHE: Dict[str, Dict[str, Any]] = {}

def wiki_title_format(title: str) -> str:
    """Formats title into Wikipedia-compliant title casing (lowercase minor words)."""
    minor = {'of', 'the', 'and', 'in', 'a', 'an', 'to', 'for', 'on', 'with', 'at', 'by', 'from', 'vs'}
    words = title.split()
    if not words:
        return ''
    res = [words[0].capitalize()]
    for w in words[1:]:
        res.append(w.lower() if w.lower() in minor else w.capitalize())
    return ' '.join(res)


def clean_display_title(raw_title: str) -> str:
    """Cleans dot-delimited torrent releases and raw file titles into clean human titles."""
    if not raw_title:
        return ""
    t = raw_title.strip()
    # Strip bracketed tags
    t = re.sub(r'\[.*?\]|\(.*?\)', '', t)
    # Strip dot year suffixes like .2026. or .2025.
    t = re.sub(r'\.(202\d|201\d)\.?', ' ', t)
    # Remove standalone years at the end
    t = re.sub(r'\b(202\d|201\d)\b\s*$', '', t)
    # Replace dots, underscores, hyphens between words with spaces (except Dr., Mr., etc.)
    if '.' in t and not re.search(r'^(Dr\.|Mr\.|Mrs\.|Ms\.)\b', t):
        t = re.sub(r'[:\-_.]', ' ', t)
    # Strip release codec tags
    t = re.sub(
        r'\b(Dual Audio|Hindi ORG|ORG|ENG|Hindi Dub|Dubbed|WEB-DL|BluRay|HDRip|HD|PRE-HD|480p|720p|1080p|2160p|4K|HEVC|x264|x265|ESub|ESubs|V\d+)\b',
        '', t, flags=re.IGNORECASE
    )
    # Clean series formatting: preserve S01E03 uppercase
    t = re.sub(r'\b[sS]\d+(?:[eE]\d+)?\b', lambda m: m.group(0).upper(), t)
    # Format vs.
    words = t.split()
    if not words:
        return raw_title.strip()
    clean_words = []
    for w in words:
        if w.lower() in ('vs', 'vs.'):
            clean_words.append('vs')
        else:
            clean_words.append(w)
    formatted = wiki_title_format(' '.join(clean_words))
    formatted = re.sub(r'\b[vV]s\b', 'vs.', formatted)
    formatted = re.sub(r'\.{2,}', '.', formatted)
    # Ensure S01E03 remains uppercase
    formatted = re.sub(r'\b(s\d+)(e\d+)\b', lambda m: m.group(1).upper() + m.group(2).upper(), formatted, flags=re.I)
    return formatted.strip()


def clean_search_title(raw_title: str) -> str:
    """Strips episode codes, year, and codecs to yield pure search title for wiki/imdb."""
    t = clean_display_title(raw_title)
    t = re.sub(r'\bS\d+(?:E\d+)?\b|\bSeason\s*\d+\b|\bEpisode\s*\d+\b', '', t, flags=re.I)
    t = re.sub(r'[:\-_.]', ' ', t)
    return ' '.join(t.split())


def clean_movie_title(raw_title: str) -> str:
    return clean_search_title(raw_title)


def is_placeholder_description(desc: Optional[str]) -> bool:
    """Detects whether synopsis is a scraped file release string or placeholder."""
    if not desc or len(desc.strip()) < 30:
        return True
    desc_l = desc.lower()
    bad_tokens = [
        '480p, 720p', 'web-dl 480p', '1080p |', 'web-dl 1080p', '[hindi org',
        '(2026 new release)', 'resume not supported', 'direct cloud download',
        '1337x', 'magnet link', 'high-speed release', 'verified magnet',
        'p2p free', 'download direct', 'full movie download', 'full movie details',
        'fast downloads', 'instant ready', 'seeders', 'torrent', 'download link',
        'direct link', 'vegamovies', 'bolly4u', 'mwlbd', 'fojik',
        'directed by acclaimed', 'an intense', 'screenshots', 'available for direct download'
    ]
    return any(bad in desc_l for bad in bad_tokens)




def enrich_movie_metadata(title: str, year: str = '2026', current_genre: str = '', current_desc: str = '') -> Dict[str, str]:
    """
    Fetches real Director, real Starring cast, and real Plot Synopsis from authoritative sources:
    1. IMDb Suggestions API (for authentic starring cast)
    2. Wikipedia REST API & Search (for authentic director and plot synopsis)
    3. Context-rich fallback synthesis if no public record exists.
    """
    clean_t = clean_movie_title(title)
    if not clean_t:
        clean_t = title.strip()

    cache_key = f"{clean_t.lower()}_{year}"
    if cache_key in _METADATA_CACHE:
        return _METADATA_CACHE[cache_key]

    director = None
    cast = None
    synopsis = None

    # Step 1: IMDb Suggestion API for official cast
    slug = re.sub(r'[^a-zA-Z0-9 ]', '', clean_t).strip().lower()
    try:
        imdb_url = f"https://v3.sg.media-imdb.com/suggestion/x/{urllib.parse.quote(slug)}.json"
        req = urllib.request.Request(imdb_url, headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=3.5).read())
        items = data.get('d', [])
        for it in items:
            it_title = it.get('l', '')
            norm_it = re.sub(r'[^a-zA-Z0-9 ]', '', it_title).lower()
            if slug in norm_it or norm_it in slug:
                if it.get('s'):
                    cast = it.get('s').strip()
                break
    except Exception as ex:
        logger.debug(f"IMDb suggestion lookup failed for '{clean_t}': {ex}")

    # Step 2: Wikipedia Summary & Search API for real Director and Plot Synopsis
    clean_under = clean_t.replace(' ', '_')
    wiki_fmt = wiki_title_format(clean_t).replace(' ', '_')
    is_tv = bool(re.search(r'S\d+|Season|Episode|series|show', title, re.I))

    candidates = []
    if is_tv:
        candidates.extend([
            f"{wiki_fmt}_(TV_series)",
            f"{clean_under}_(TV_series)",
            f"{wiki_fmt}_(series)",
            f"{wiki_fmt}_(miniseries)",
            wiki_fmt,
            clean_under
        ])
    else:
        candidates.extend([
            f"{wiki_fmt}_({year}_film)",
            f"{wiki_fmt}_(film)",
            f"{clean_under}_({year}_film)",
            f"{clean_under}_(film)",
            f"{wiki_fmt.replace('_vs_', '_vs._')}",
            f"{wiki_fmt}",
            f"{clean_under}",
            f"{wiki_fmt}_(TV_series)"
        ])

    try:
        q_search = f'"{clean_t}" {year} film' if not is_tv else f'"{clean_t}" series'
        sr_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q_search)}&utf8=&format=json"
        sr_req = urllib.request.Request(sr_url, headers=HEADERS)
        sr_data = json.loads(urllib.request.urlopen(sr_req, timeout=3.5).read())
        hits = sr_data.get('query', {}).get('search', [])
        for h in hits[:3]:
            h_title = h['title']
            words = set(clean_t.lower().split())
            h_words = set(re.sub(r'[^a-z0-9 ]', ' ', h_title.lower()).split())
            if words.intersection(h_words):
                candidates.append(h_title.replace(' ', '_'))
    except Exception:
        pass

    for cand in candidates:
        try:
            sum_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(cand)}"
            sum_req = urllib.request.Request(sum_url, headers=HEADERS)
            sum_data = json.loads(urllib.request.urlopen(sum_req, timeout=3.5).read())
            extract = sum_data.get('extract', '')
            if extract and len(extract) > 60 and 'may refer to:' not in extract:
                extract_l = extract.lower()
                # Reject songs, albums, and musical tracks
                if any(bad_m in extract_l for bad_m in ['is a song by', 'is a single by', 'is an album by', 'is an ep by', 'is a track by', 'is a band from', 'is a rock band']):
                    continue
                # Reject films released before 2025
                if re.search(r'\bis an? (?:19\d\d|20[0-1]\d|202[0-4])\b', extract_l):
                    continue

                # Relevance check: at least one word from title must appear in extract or cand
                core_words = [w.lower() for w in clean_t.split() if len(w) > 3]
                if core_words and not any(w in extract.lower() or w in cand.lower() for w in core_words):
                    continue

                # Extract director/creator if missing
                if not director:
                    dm = re.search(r'(?:written and )?(?:directed|created)(?: and written)?\s+by\s+([A-Z][a-zA-Z\.\- ]+)', extract)
                    if dm:
                        raw_d = dm.group(1).strip()
                        clean_d = re.split(
                            r'\.\s*(?:It|The|He|She|In|A|An)\b|\b(?:in\s+(?:his|her|their)|from\s+a|based\s+on|who|and\s+(?:produced|written|stars|starring))\b|[,.]',
                            raw_d, flags=re.IGNORECASE
                        )[0].strip(' ,.')
                        if clean_d and 2 < len(clean_d) < 45:
                            director = clean_d

                # Extract cast if missing
                if not cast:
                    cm = re.search(r'(?:starring|stars)\s+([A-Z][a-zA-Z\.\- ]+(?:,\s+[A-Z][a-zA-Z\.\- ]+)*(?:\s+and\s+[A-Z][a-zA-Z\.\- ]+)?)', extract)
                    if cm:
                        raw_c = cm.group(1).split('. In')[0].split('. The')[0].strip(' ,.')
                        if raw_c and 2 < len(raw_c) < 90:
                            cast = raw_c

                # Clean up extract to remove trailing producer boilerplate
                clean_extract = re.sub(r'It is produced by.*?(\.|\n|$)', '', extract).strip()
                clean_extract = re.sub(r'It was released on.*?(\.|\n|$)', '', clean_extract).strip()
                synopsis = clean_extract if len(clean_extract) > 50 else extract
                break
        except Exception:
            continue

    # Fallbacks if still missing
    if not director or director.lower() in ('unknown', 'n/a', 'none', ''):
        director = "Acclaimed Director"
    if not cast or cast.lower() in ('unknown', 'n/a', 'none', ''):
        cast = "Ensemble Cast"
    if not synopsis or is_placeholder_description(synopsis):
        genre_name = current_genre.split(',')[0].strip() if current_genre else "Action"
        synopsis = (
            f"An intense {genre_name.lower()} release starring {cast}, directed by {director}. "
            f"Featuring gripping performances, heart-pounding suspense, and high-stakes cinematic action."
        )

    result = {
        'director': director,
        'cast': cast,
        'description': synopsis
    }
    _METADATA_CACHE[cache_key] = result
    return result


def enrich_movie_record(movie: Dict[str, Any]) -> Dict[str, Any]:
    """Enriches a single movie dict in-place if title, director, cast, or synopsis need updating."""
    if not movie:
        return movie

    cur_title = str(movie.get('title') or '').strip()
    cur_dir = str(movie.get('director') or '').strip()
    cur_cast = str(movie.get('cast') or '').strip()
    cur_desc = str(movie.get('description') or '').strip()

    # Clean title from torrent dots, year suffixes, and codec tags
    clean_t = clean_display_title(cur_title)
    if clean_t and clean_t != cur_title:
        movie['title'] = clean_t
        cur_title = clean_t

    # Clean trailing 'Director' string accidentally scraped
    if cur_dir.endswith('Director') and cur_dir.lower() not in ('director', 'acclaimed director'):
        cur_dir = cur_dir[:-8].strip()
        movie['director'] = cur_dir

    # Fix concatenated cast strings (e.g. 'Alan RitchsonDennis QuaidStephan James')
    if re.search(r'[a-z][A-Z]', cur_cast) and ',' not in cur_cast:
        cleaned_cast = re.sub(r'([a-z])([A-Z])', r'\1, \2', cur_cast)
        cleaned_cast = re.sub(r'\d+', '', cleaned_cast).strip(' ,')
        movie['cast'] = cleaned_cast
        cur_cast = cleaned_cast

    needs_dir = not cur_dir or cur_dir.lower() in ('unknown', 'n/a', 'none', 'acclaimed director', 'director', 'acclaimed')
    needs_cast = not cur_cast or cur_cast.lower() in ('unknown', 'n/a', 'none', 'ensemble cast')
    needs_desc = is_placeholder_description(cur_desc)

    if needs_dir or needs_cast or needs_desc:
        meta = enrich_movie_metadata(
            title=cur_title,
            year=str(movie.get('year') or '2026'),
            current_genre=movie.get('genre', ''),
            current_desc=cur_desc
        )
        if meta.get('director') and (needs_dir or meta['director'].lower() not in ('acclaimed director', 'director')):
            movie['director'] = meta['director']
        if meta.get('cast') and (needs_cast or meta['cast'].lower() != 'ensemble cast'):
            movie['cast'] = meta['cast']
        if needs_desc or is_placeholder_description(cur_desc):
            movie['description'] = meta['description']

    return movie

