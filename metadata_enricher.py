import urllib.request, urllib.parse, json, re, logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}

_METADATA_CACHE: Dict[str, Dict[str, Any]] = {}

def clean_movie_title(raw_title: str) -> str:
    """Removes file release, codec, audio tags, and year brackets to yield the pure title."""
    t = re.sub(r'\[.*?\]|\(.*?\)', '', raw_title)
    t = re.sub(
        r'\b(Dual Audio|Hindi ORG|ORG|ENG|Hindi Dub|Dubbed|WEB-DL|BluRay|HDRip|HD|PRE-HD|480p|720p|1080p|2160p|4K|HEVC|x264|x265|ESub|ESubs|V\d+|Season\s*\d+|S\d+|Complete|Collection)\b',
        '', t, flags=re.IGNORECASE
    )
    t = re.sub(r'\b(202\d|201\d)\b', '', t)
    t = re.sub(r'[:\-_.]', ' ', t)
    return ' '.join(t.split())


def is_placeholder_description(desc: Optional[str]) -> bool:
    """Detects whether synopsis is a scraped file release string or placeholder."""
    if not desc or len(desc.strip()) < 30:
        return True
    desc_l = desc.lower()
    bad_tokens = [
        '480p, 720p', 'web-dl 480p', '1080p |', 'web-dl 1080p', '[hindi org',
        '(2026 new release)', 'resume not supported', 'direct cloud download'
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
    candidates = [
        f"{clean_t.replace(' ', '_')}_(film)",
        f"{clean_t.replace(' ', '_')}_({year}_film)",
        f"{clean_t.replace(' ', '_')}"
    ]
    try:
        sr_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(clean_t + ' film')}&utf8=&format=json"
        sr_req = urllib.request.Request(sr_url, headers=HEADERS)
        sr_data = json.loads(urllib.request.urlopen(sr_req, timeout=3.5).read())
        hits = sr_data.get('query', {}).get('search', [])
        if hits:
            candidates.insert(0, hits[0]['title'].replace(' ', '_'))
    except Exception:
        pass

    for cand in candidates:
        try:
            sum_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(cand)}"
            sum_req = urllib.request.Request(sum_url, headers=HEADERS)
            sum_data = json.loads(urllib.request.urlopen(sum_req, timeout=3.5).read())
            extract = sum_data.get('extract', '')
            if extract and len(extract) > 60 and 'may refer to:' not in extract:
                # Extract director if missing
                if not director:
                    dm = re.search(r'directed(?:,\s*[\w\-]+)*\s*(?:and\s*[\w\-]+\s*)?by\s+([A-Z][a-zA-Z0-9\.\- ]+)', extract)
                    if dm:
                        raw_d = dm.group(1).strip()
                        clean_d = re.split(
                            r'\.\s+(?:The|It|He|She|This|In|A|An)\b|,\s*(?:who|written|and|produced)|\s+and\s+written|\s+written\s+by',
                            raw_d
                        )[0].strip(' ,.')
                        if clean_d and len(clean_d) > 2 and len(clean_d) < 40:
                            director = clean_d
                # Extract cast if missing
                if not cast:
                    cm = re.search(r'starring\s+([A-Z][a-zA-Z\.\- ]+(?:,\s+[A-Z][a-zA-Z\.\- ]+)*(?:\s+and\s+[A-Z][a-zA-Z\.\- ]+)?)', extract)
                    if cm:
                        raw_c = cm.group(1).split('. In')[0].split('. The')[0].strip(' ,.')
                        if raw_c and len(raw_c) > 2 and len(raw_c) < 80:
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
    """Enriches a single movie dict in-place if director, cast, or synopsis need updating."""
    if not movie:
        return movie

    cur_dir = str(movie.get('director') or '').strip()
    cur_cast = str(movie.get('cast') or '').strip()
    cur_desc = str(movie.get('description') or '').strip()

    needs_dir = not cur_dir or cur_dir.lower() in ('unknown', 'n/a', 'none')
    needs_cast = not cur_cast or cur_cast.lower() in ('unknown', 'n/a', 'none')
    needs_desc = is_placeholder_description(cur_desc)

    if needs_dir or needs_cast or needs_desc:
        meta = enrich_movie_metadata(
            title=movie.get('title', ''),
            year=str(movie.get('year') or '2026'),
            current_genre=movie.get('genre', ''),
            current_desc=cur_desc
        )
        if needs_dir:
            movie['director'] = meta['director']
        if needs_cast:
            movie['cast'] = meta['cast']
        if needs_desc:
            movie['description'] = meta['description']

    return movie
