import os
import re
import threading
from datetime import datetime
from typing import Dict, Any
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort, Response, stream_with_context, send_from_directory
from flask_cors import CORS
from config import Config, setup_logger
from database import db, is_series
from scraper import MWLBDScraper
from download_resolver import resolve_movie_direct_download, stream_mkv_with_auto_audio, download_cache, verify_r2_url

logger = setup_logger('website')

app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
CORS(app)


def seed_initial_data_if_empty():
    """Populates database with initial 2026 showcase releases if empty."""
    stats = db.get_stats()
    if stats.get('total', 0) == 0:
        logger.info("Database is empty. Populating with initial verified 2026 showcase releases...")
        sample_movies = [
            {
                "id": "bhooth-bangla-2026-2026",
                "title": "Bhooth Bangla",
                "year": "2026",
                "genre": "Bollywood, Comedy, Horror",
                "director": "Priyadarshan",
                "cast": "Akshay Kumar",
                "description": "Bhooth Bangla (2026) Hindi Full Movie Download Direct Google Drive",
                "poster": "https://fojik.site/wp-content/uploads/79RBp8afL4u4z3nVGR78z6eIvBB-185x278.jpg",
                "source_url": "https://fojik.site/movie/bhooth-bangla-2026/",
                "download_links": [
                    {
                        "url": "https://drive.google.com/uc?export=download&id=1a2B3c4D5e6F7g8H9i0J_SAMPLE_1080p",
                        "original_url": "https://drive.google.com/file/d/1a2B3c4D5e6F7g8H9i0J_SAMPLE_1080p/view?usp=sharing",
                        "preview_url": "https://drive.google.com/file/d/1a2B3c4D5e6F7g8H9i0J_SAMPLE_1080p/preview",
                        "type": "gdrive",
                        "file_id": "1a2B3c4D5e6F7g8H9i0J_SAMPLE_1080p",
                        "label": "Download Direct (1080p WEB-DL)",
                        "quality": "1080p",
                        "size": "2.1 GB"
                    }
                ],
                "status": "available"
            }
        ]
        for m in sample_movies:
            db.save_movie(m)


# Perform initial seed check
seed_initial_data_if_empty()


# Global background crawler tracking state
crawler_state = {
    "is_running": False,
    "start_page": 1,
    "num_pages": 1,
    "pages_completed": 0,
    "total_scraped": 0,
    "message": "Crawler is idle.",
    "last_run": None
}
_crawler_lock = threading.Lock()


def run_crawl_task(start_page: int = 1, num_pages: int = 1, concurrency: int = 4, target: str = "2026_archive"):
    """Executes crawler in background thread."""
    global crawler_state
    with _crawler_lock:
        if crawler_state["is_running"]:
            logger.warning("Crawl task requested while another is already active.")
            return
        crawler_state["is_running"] = True
        crawler_state["start_page"] = start_page
        crawler_state["num_pages"] = num_pages
        crawler_state["pages_completed"] = 0
        crawler_state["total_scraped"] = 0
        crawler_state["message"] = f"Crawling 2026+ releases (target={target})..."

    def _progress_cb(processed, total_pages, scraped_count):
        with _crawler_lock:
            crawler_state["pages_completed"] = processed
            crawler_state["total_scraped"] = scraped_count
            crawler_state["message"] = f"Crawled {processed}/{total_pages} pages ({scraped_count} 2026+ movies indexed)..."

    try:
        from scraper import get_active_scrapers
        from datetime import datetime

        scrapers = get_active_scrapers()
        total_scraped_all = 0
        total_pages_all = 0

        for sc in scrapers:
            try:
                logger.info(f"Running multi-source crawl for provider: {sc.provider_name}")
                for p in range(start_page, start_page + num_pages):
                    catalog_items = sc.scrape_catalog_page(p)
                    for item in catalog_items:
                        movie_url = item.get("url") or item.get("source_url")
                        if movie_url:
                            details = sc.scrape_movie_details(movie_url)
                            if details:
                                db.add_or_merge_movie(details)
                                total_scraped_all += 1
                    total_pages_all += 1
                    _progress_cb(total_pages_all, num_pages * len(scrapers), total_scraped_all)
            except Exception as sc_err:
                logger.warning(f"Error during {sc.provider_name} crawl: {sc_err}")

        with _crawler_lock:
            crawler_state["is_running"] = False
            crawler_state["pages_completed"] = total_pages_all
            crawler_state["total_scraped"] = total_scraped_all
            crawler_state["message"] = (
                f"Multi-source crawl completed! "
                f"Indexed/merged {total_scraped_all} 2026+ movies across sources."
            )
            crawler_state["last_run"] = datetime.utcnow().isoformat()
    except Exception as e:
        logger.error(f"Background crawl task encountered an error: {e}", exc_info=True)
        with _crawler_lock:
            crawler_state["is_running"] = False
            crawler_state["message"] = f"Crawl error: {e}"


def start_background_auto_sync_daemon(interval_seconds: int = 1800):
    """
    Background daemon thread that periodically crawls page 1 of all active scrapers
    every 30 minutes to discover and auto-index newly released movies and TV series.
    Runs silently in the background without blocking the web application.
    """
    def _auto_sync_loop():
        import time
        logger.info(f"Background multi-source auto-sync daemon started (interval={interval_seconds}s).")
        time.sleep(15)  # Short delay after startup
        while True:
            try:
                logger.info("Auto-sync daemon: Checking multi-source providers for new 2026 releases...")
                from scraper import get_active_scrapers

                scrapers = get_active_scrapers()
                new_releases_count = 0

                for sc in scrapers:
                    try:
                        latest_items = sc.scrape_catalog_page(1)
                        for item in latest_items[:10]:  # Top 10 newest
                            movie_url = item.get("url") or item.get("source_url")
                            if movie_url:
                                details = sc.scrape_movie_details(movie_url)
                                if details:
                                    res = db.add_or_merge_movie(details)
                                    if res:
                                        new_releases_count += 1
                    except Exception as ex_sc:
                        logger.warning(f"Auto-sync error for provider {sc.provider_name}: {ex_sc}")

                logger.info(f"Auto-sync cycle finished. Processed/merged {new_releases_count} titles across sources.")
            except Exception as ex:
                logger.error(f"Error in auto-sync daemon loop: {ex}")

            time.sleep(interval_seconds)

    t = threading.Thread(target=_auto_sync_loop, daemon=True)
    t.start()


# Start background auto-sync daemon on server startup
start_background_auto_sync_daemon(interval_seconds=1800)



# Standard MWLBD Navigation Categories
MWLBD_CATEGORIES = [
    {"label": "Bollywood Hindi", "genre": "Bollywood"},
    {"label": "Hollywood English", "genre": "Hollywood"},
    {"label": "Dual Audio", "genre": "Dual Audio"},
    {"label": "Hindi Dubbed", "genre": "Hindi Dubbed"},
    {"label": "South Indian", "genre": "Tamil"},
    {"label": "TV & Web Series", "genre": "Series"},
    {"label": "Anime & Cartoon", "genre": "Anime"},
    {"label": "Action", "genre": "Action"},
    {"label": "Horror", "genre": "Horror"},
    {"label": "Comedy", "genre": "Comedy"},
    {"label": "4K UHD / 1080p", "genre": "1080p"},
]


# Justyflix UI Navigation & Genre Pills
NETFLIX_GENRES = [
    "All", "Hollywood", "Bollywood", "Action", "Adventure", "Comedy", "Crime",
    "Drama", "Dual Audio", "Horror", "Sci-Fi", "Thriller", "Anime", "Series"
]


# Context processor to expose active navbar state across all templates
@app.context_processor
def inject_nav_context():
    nav = request.args.get('nav', '').strip()
    status = request.args.get('status', '').strip()
    if not nav:
        if status == 'available':
            nav = 'downloads'
        else:
            nav = 'home'
    return {'nav': nav}


# Web Routes
@app.route('/')
def home():
    """Home page displaying Justyflix cinematic hero, category carousels, and movie grid."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    genre = request.args.get('genre', '').strip()
    nav = request.args.get('nav', '').strip()
    view = request.args.get('view', '').strip()

    if not nav:
        if status == 'available':
            nav = 'downloads'
        else:
            nav = 'home'

    effective_genre = None
    effective_status = status or None

    if genre and genre != "All":
        effective_genre = genre
    elif nav == 'hollywood':
        effective_genre = 'Hollywood'
    elif nav == 'bollywood':
        effective_genre = 'Bollywood'
    elif nav == 'tv_shows':
        effective_genre = 'Series'
    elif nav == 'dual_audio':
        effective_genre = 'Dual Audio'
    elif nav == 'downloads':
        effective_status = 'available'
    elif view == 'latest_movies':
        effective_genre = 'Movie'

    pagination = db.get_paginated_movies(
        page=page,
        per_page=per_page,
        status=effective_status,
        query=query or None,
        genre=effective_genre
    )
    stats = db.get_stats()
    all_movies = db.get_all_movies()

    # Curate top featured movies for Justyflix Billboard Hero Slider
    def sanitize_synopsis(desc, title):
        if not desc:
            return f"{title} (2026) - A brand new cinematic release with instant streaming and high-speed cloud access."
        import re
        s = re.sub(r'available for direct download[^\.]*', 'available for streaming in ultra-high definition', desc, flags=re.IGNORECASE)
        s = re.sub(r'direct downloads?', 'fast downloads', s, flags=re.IGNORECASE)
        s = re.sub(r'direct ready', 'instant ready', s, flags=re.IGNORECASE)
        return s.strip()

    def enrich_movie(m):
        m_copy = dict(m)
        scraped = m_copy.get('scraped_at')
        formatted_date = None
        if scraped:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(scraped)
                formatted_date = dt.strftime('%b. %d, %Y')
            except Exception:
                pass
        if not formatted_date:
            yr = m_copy.get('year', '2026')
            formatted_date = f"Sep. 04, {yr}" if yr else "2026"
        m_copy['formatted_date'] = formatted_date

        t = (m_copy.get('title') or '') + ' ' + (m_copy.get('description') or '')
        g = (m_copy.get('genre') or '')
        t_lower = t.lower()
        g_lower = g.lower()

        if 'dual audio' in t_lower or 'dual audio' in g_lower:
            tag = 'Dual Audio ORG'
        elif 'season' in t_lower or 'series' in g_lower:
            tag = 'Series [HD]'
        elif 'hindi org' in t_lower or 'hindi dub' in t_lower:
            tag = 'Hindi Dub [HD]'
        elif 'pre-hdrip' in t_lower:
            tag = 'PRE-HDRip'
        elif 'pre-hd' in t_lower:
            tag = 'PRE-HD [Hindi]'
        elif 'hindi' in t_lower or 'bollywood' in g_lower:
            tag = 'PRE-HD [Hindi]'
        elif 'hollywood' in g_lower or 'english' in t_lower:
            tag = 'WEB-DL [HD]'
        else:
            tag = 'WEB-DL [HD]'
        m_copy['quality_tag'] = tag
        return m_copy

    featured_movies = []
    seen_titles = set()
    for candidate in all_movies:
        t = candidate.get("title", "").strip()
        poster = candidate.get("poster")
        if poster and t not in seen_titles:
            c_copy = enrich_movie(candidate)
            c_copy["clean_description"] = sanitize_synopsis(c_copy.get("description", ""), t)
            featured_movies.append(c_copy)
            seen_titles.add(t)
            if len(featured_movies) >= 6:
                break

    if not featured_movies and all_movies:
        featured_movies = [enrich_movie(dict(m, clean_description=sanitize_synopsis(m.get("description", ""), m.get("title", "")))) for m in all_movies[:5]]

    featured_movie = featured_movies[0] if featured_movies else None

    # Curate pure movies only (strictly exclude any TV Series or Web Series)
    pure_movies = [m for m in all_movies if not is_series(m) and m.get("poster")]

    # Curate pure TV & WEB Series only
    pure_series = [m for m in all_movies if is_series(m) and m.get("poster")]

    # Curate Featured Carousel Movies (20 pure movies with poster)
    featured_carousel_movies = [enrich_movie(m) for m in pure_movies][:20]

    # Curate Latest Movies Grid Movies (45 pure movies = 5 horizontal x 9 vertical, NO SERIES!)
    homepage_latest_movies = [enrich_movie(m) for m in pure_movies][:45]

    # Curate TV & WEB Series Grid Movies (25 pure series = 5 horizontal x 5 vertical)
    homepage_series_movies = [enrich_movie(m) for m in pure_series][:25]

    # Enrich paginated items for Dedicated Catalog Grid
    pagination["items"] = [enrich_movie(m) for m in pagination["items"]]

    is_filtered = bool((genre and genre != "All") or query or effective_status or (nav and nav != 'home') or (view and view != 'home'))

    return render_template(
        'index.html',
        movies=pagination["items"],
        pagination=pagination,
        stats=stats,
        query=query,
        status=status,
        nav=nav,
        view=view,
        genre=genre or "All",
        effective_genre=effective_genre,
        is_filtered=is_filtered,
        featured_movie=featured_movie,
        featured_movies=featured_movies,
        featured_carousel_movies=featured_carousel_movies,
        latest_carousel_movies=homepage_latest_movies,
        homepage_latest_movies=homepage_latest_movies,
        series_movies=homepage_series_movies,
        homepage_series_movies=homepage_series_movies,
        netflix_genres=NETFLIX_GENRES,
        categories=MWLBD_CATEGORIES
    )


# In-memory cache for resolved YouTube trailer video IDs
_TRAILER_CACHE: Dict[str, str] = {}


@app.route('/favicon.ico')
def favicon():
    """Serves the Justyflix capital J red square favicon."""
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/api/trailer')
def api_trailer():
    """
    Dynamically resolves or retrieves the official YouTube trailer embed URL
    for any movie or TV series.
    """
    import requests
    from urllib.parse import quote_plus

    title = request.args.get('title', '').strip()
    year = request.args.get('year', '').strip()
    movie_id = request.args.get('id', '').strip()

    if not title and movie_id:
        m = db.get_movie(movie_id)
        if m:
            title = m.get('title', '')
            year = m.get('year', '')

    if not title:
        return jsonify({'status': 'error', 'message': 'Movie title is required'}), 400

    # Sanitize title for clean trailer search query
    clean_title = re.sub(r'\[.*?\]|\(.*?\)|Dual Audio.*|Season\s*\d+.*|Hindi.*|ENG.*|V\d+.*', '', title, flags=re.IGNORECASE).strip(' -:|')
    if not clean_title:
        clean_title = title.strip()

    cache_key = f"{clean_title.lower()}_{year}"

    # Return cached YouTube video ID if available
    if cache_key in _TRAILER_CACHE:
        video_id = _TRAILER_CACHE[cache_key]
        return jsonify({
            'status': 'success',
            'video_id': video_id,
            'embed_url': f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&rel=0&modestbranding=1",
            'clean_title': clean_title
        })

    # Search YouTube for the official trailer
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    query = f"{clean_title} {year} official trailer".strip()
    yt_search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"

    try:
        r = requests.get(yt_search_url, headers=headers, timeout=5)
        if r.status_code == 200:
            video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
            if not video_ids:
                video_ids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', r.text)

            if video_ids:
                # Find first valid video ID
                video_id = video_ids[0]
                _TRAILER_CACHE[cache_key] = video_id
                logger.info(f"Resolved trailer for '{clean_title}': YouTube ID {video_id}")
                return jsonify({
                    'status': 'success',
                    'video_id': video_id,
                    'embed_url': f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&rel=0&modestbranding=1",
                    'clean_title': clean_title
                })
    except Exception as e:
        logger.warning(f"Error querying YouTube trailer for '{clean_title}': {e}")

    # Fallback to direct search embed if YouTube search scrape didn't match
    fallback_embed = f"https://www.youtube-nocookie.com/embed?listType=search&list={quote_plus(query)}&autoplay=1"
    return jsonify({
        'status': 'fallback',
        'video_id': '',
        'embed_url': fallback_embed,
        'clean_title': clean_title
    })


@app.route('/movie/<movie_id>')
def movie_detail(movie_id):
    """Detailed view for a single movie with direct Google Drive downloads and stream preview."""
    movie = db.get_movie(movie_id)
    if not movie:
        abort(404)

    # On-the-fly detail and Google Drive links enrichment if not yet deep-scraped
    if (not movie.get('download_links') or len(movie['download_links']) == 0) and movie.get('source_url'):
        try:
            scraper = MWLBDScraper()
            details = scraper.get_movie_details(movie['source_url'])
            if details:
                details['id'] = movie_id
                movie = db.save_movie(details)
        except Exception as ex:
            logger.warning(f"On-demand detail scrape failed for {movie_id}: {ex}")

    return render_template('movie_detail.html', movie=movie, categories=MWLBD_CATEGORIES)


@app.route('/download/<movie_id>/<int:link_idx>')
def download_movie(movie_id, link_idx):
    """
    Automatic audio-configured download route:
    Resolves intermediate tokens and streams the video file with on-the-fly MKV
    header patching so that:
    - English is the default audio track for Hollywood/Dual Audio releases.
    - Hindi is the default audio track for Bollywood releases.
    Directly downloaded to the user's PC with zero manual player configuration.
    """
    movie = db.get_movie(movie_id)
    if not movie:
        abort(404)

    links = movie.get('download_links', [])
    if not links or link_idx < 0 or link_idx >= len(links):
        return redirect(url_for('movie_detail', movie_id=movie_id))

    target_link = links[link_idx]
    quality = target_link.get('quality') or target_link.get('label') or '1080p'
    source_url = movie.get('source_url') or target_link.get('url', '')
    fallback_url = target_link.get('url') or target_link.get('original_url', '')
    file_id = target_link.get('file_id', '')

    res = resolve_movie_direct_download(
        source_url=source_url,
        target_quality=quality,
        fallback_url=fallback_url,
        file_id=file_id
    )

    if not res.get('success') or not res.get('download_url'):
        # Attempt one forced refresh before giving up
        res = resolve_movie_direct_download(
            source_url=source_url,
            target_quality=quality,
            fallback_url=fallback_url,
            file_id=file_id,
            force_refresh=True
        )

    if not res.get('success') or not res.get('download_url'):
        logger.warning(f"Download resolution failed for {movie_id} [{quality}]. Falling back safely.")
        return redirect(url_for('movie_detail', movie_id=movie_id))

    r2_url = res['download_url']
    filename = res.get('filename') or f"{movie.get('title', 'Movie')}_{quality}.mkv"

    # Determine if movie is pure Bollywood (Hindi by default) or Hollywood/Dual Audio (English by default)
    genre_str = str(movie.get('genre', '')).lower()
    desc_str = str(movie.get('description', '')).lower()
    is_bollywood = ('bollywood' in genre_str or 'hindi' in genre_str) and not any(
        k in desc_str or k in genre_str for k in ['dual audio', 'hollywood', 'english', '[hindi org & eng]', 'eng & hindi']
    )

    try:
        range_header = request.headers.get('Range')
        gen, status, resp_hdrs = stream_mkv_with_auto_audio(
            r2_url=r2_url,
            is_bollywood=is_bollywood,
            range_header=range_header
        )
        resp_hdrs['Content-Disposition'] = f'attachment; filename="{filename}"'
        return Response(stream_with_context(gen), status=status, headers=resp_hdrs)
    except Exception as ex:
        logger.warning(f"Streaming auto-audio failed ({ex}). Evicting cache and attempting fresh re-resolution...")
        cache_key = f"{source_url}:{quality}:{file_id}"
        download_cache.delete(cache_key)

        fresh_res = resolve_movie_direct_download(
            source_url=source_url,
            target_quality=quality,
            fallback_url=fallback_url,
            file_id=file_id,
            force_refresh=True
        )
        if fresh_res.get('success') and fresh_res.get('download_url'):
            fresh_url = fresh_res['download_url']
            try:
                gen, status, resp_hdrs = stream_mkv_with_auto_audio(
                    r2_url=fresh_url,
                    is_bollywood=is_bollywood,
                    range_header=request.headers.get('Range')
                )
                resp_hdrs['Content-Disposition'] = f'attachment; filename="{filename}"'
                return Response(stream_with_context(gen), status=status, headers=resp_hdrs)
            except Exception as ex2:
                logger.warning(f"Fresh stream retry failed ({ex2}). Checking direct R2 before redirect...")
                if verify_r2_url(fresh_url, timeout=3.0):
                    return redirect(fresh_url, code=302)

        # NEVER redirect to an expired or unverified URL!
        logger.error(f"Cannot resolve valid stream for {movie_id}. Returning safely to movie detail page.")
        return redirect(url_for('movie_detail', movie_id=movie_id))


@app.route('/api/resolve-download/<movie_id>/<int:link_idx>', methods=['GET'])
def api_resolve_download(movie_id, link_idx):
    """
    AJAX endpoint for instant download preparation with frontend spinner.
    Returns direct auto-audio download URL with Content-Disposition for local PC download.
    """
    movie = db.get_movie(movie_id)
    if not movie:
        return jsonify({"status": "error", "message": "Movie not found"}), 404

    links = movie.get('download_links', [])
    if not links or link_idx < 0 or link_idx >= len(links):
        return jsonify({"status": "error", "message": "Download link not found"}), 404

    target_link = links[link_idx]
    quality = target_link.get('quality') or target_link.get('label') or '1080p'
    source_url = movie.get('source_url') or target_link.get('url', '')
    fallback_url = target_link.get('url') or target_link.get('original_url', '')
    file_id = target_link.get('file_id', '')

    res = resolve_movie_direct_download(
        source_url=source_url,
        target_quality=quality,
        fallback_url=fallback_url,
        file_id=file_id
    )

    # If resolved URL fails live verification, force a fresh scrape
    if res.get('success') and res.get('download_url') and not verify_r2_url(res['download_url'], timeout=3.0):
        cache_key = f"{source_url}:{quality}:{file_id}"
        download_cache.delete(cache_key)
        res = resolve_movie_direct_download(
            source_url=source_url,
            target_quality=quality,
            fallback_url=fallback_url,
            file_id=file_id,
            force_refresh=True
        )

    if res.get('success') and res.get('download_url'):
        download_endpoint = url_for('download_movie', movie_id=movie_id, link_idx=link_idx)
        return jsonify({
            "status": "success",
            "download_url": download_endpoint,
            "direct_cdn_url": res['download_url'],
            "filename": res.get('filename', f"{movie.get('title', 'Movie')}_{quality}.mkv"),
            "quality": quality,
            "source": "Justyflix Auto-Audio Stream (English Default)"
        }), 200
    else:
        return jsonify({
            "status": "fallback",
            "download_url": fallback_url or source_url,
            "filename": f"{movie.get('title', 'Movie')}_{quality}.mkv",
            "quality": quality,
            "message": res.get('error', 'Download server link is currently refreshing. Please try again in a moment.')
        }), 200


@app.route('/scrape')
def scrape_view():
    """Manual scrape trigger and deep crawler dashboard view."""
    stats = db.get_stats()
    movies = db.get_all_movies()[:15]
    scraper = MWLBDScraper()
    total_pages = scraper.get_total_catalog_pages()
    return render_template(
        'scrape.html',
        stats=stats,
        recent_movies=movies,
        crawler_state=crawler_state,
        total_site_pages=total_pages,
        categories=MWLBD_CATEGORIES
    )


@app.route('/scrape-details/<path:url>')
def scrape_details_view(url):
    """Scrapes on-demand metadata for a specific MWLBD URL."""
    try:
        scraper = MWLBDScraper()
        movie_data = scraper.get_movie_details(url)
        if movie_data:
            saved = db.save_movie(movie_data)
            return redirect(url_for('movie_detail', movie_id=saved['id']))
        else:
            return render_template(
                'scrape.html',
                stats=db.get_stats(),
                error_message=f"Could not extract details from {url}",
                recent_movies=db.get_all_movies()[:10]
            )
    except Exception as e:
        logger.error(f"Failed on-demand scrape for {url}: {e}")
        return render_template(
            'scrape.html',
            stats=db.get_stats(),
            error_message=str(e),
            recent_movies=db.get_all_movies()[:10]
        )


# API Endpoints
@app.route('/api/movies', methods=['GET'])
def api_get_movies():
    """Returns JSON list of all movies with optional filter parameters."""
    status = request.args.get('status')
    query = request.args.get('query')
    genre = request.args.get('genre')

    movies = db.get_all_movies(status=status, query=query, genre=genre)
    return jsonify({
        "status": "success",
        "count": len(movies),
        "movies": movies
    }), 200


@app.route('/api/movie/<movie_id>', methods=['GET'])
def api_get_movie(movie_id):
    """Returns single movie details as JSON."""
    movie = db.get_movie(movie_id)
    if not movie:
        return jsonify({"status": "error", "message": "Movie not found"}), 404
    return jsonify({"status": "success", "movie": movie}), 200


@app.route('/api/scrape', methods=['POST'])
def api_trigger_scrape():
    """Triggers background scraping task."""
    limit = request.args.get('limit', default=15, type=int)
    worker_thread = threading.Thread(target=run_scrape_task, kwargs={'limit': limit}, daemon=True)
    worker_thread.start()

    return jsonify({
        "status": "success",
        "message": f"Scraper initiated in background (limit={limit}).",
        "timestamp": db.get_stats().get("last_scrape")
    }), 202


@app.route('/api/crawl', methods=['POST'])
def api_trigger_crawl():
    """Triggers multi-page background crawling."""
    start_page = request.args.get('start_page', default=1, type=int)
    num_pages = request.args.get('num_pages', default=2, type=int)
    concurrency = request.args.get('concurrency', default=4, type=int)
    target = request.args.get('target', default='2026_archive')

    thread = threading.Thread(
        target=run_crawl_task,
        kwargs={'start_page': start_page, 'num_pages': num_pages, 'concurrency': concurrency, 'target': target},
        daemon=True
    )
    thread.start()

    return jsonify({
        "status": "success",
        "message": f"2026+ crawler started (target={target}) for {num_pages} page(s) starting at page {start_page}.",
        "crawler": crawler_state
    }), 202


@app.route('/api/crawl/status', methods=['GET'])
def api_crawl_status():
    """Returns status of the active or completed crawl task."""
    return jsonify({
        "status": "success",
        "crawler": crawler_state,
        "database": db.get_stats()
    }), 200


@app.route('/api/notifications', methods=['GET'])
def api_notifications():
    """
    Returns latest scraped movies and future releases for real-time notification bell.
    Detects newly scraped titles, 2026 releases, and future releases.
    """
    all_movies = db.get_all_movies()
    recent_movies = all_movies[:20]
    now = datetime.utcnow()

    notifications = []
    for m in recent_movies:
        scraped_at_str = m.get("scraped_at")
        time_ago = "2026 Release"
        if scraped_at_str:
            try:
                clean_ts = scraped_at_str.replace('Z', '').split('.')[0]
                dt = datetime.fromisoformat(clean_ts)
                diff_sec = max(0, (now - dt).total_seconds())
                if diff_sec < 60:
                    time_ago = "Just now"
                elif diff_sec < 3600:
                    mins = int(diff_sec // 60)
                    time_ago = f"{mins}m ago"
                elif diff_sec < 86400:
                    hrs = int(diff_sec // 3600)
                    time_ago = f"{hrs}h ago"
                elif diff_sec < 604800:
                    days = int(diff_sec // 86400)
                    time_ago = f"{days}d ago"
                else:
                    time_ago = "Recently Added"
            except Exception:
                time_ago = "2026 Release"

        year_str = str(m.get("year", "2026")).strip()
        is_future = False
        try:
            m_year = re.search(r'\b(20\d\d)\b', year_str)
            year_int = int(m_year.group(1)) if m_year else 2026
            is_future = (year_int >= 2026)
        except Exception:
            is_future = True

        status = m.get("status", "pending")
        if status == "available":
            badge_text = "Available"
        elif str(m.get("year")) > "2026":
            badge_text = f"Upcoming {m.get('year')}"
        else:
            badge_text = "New 2026"

        notifications.append({
            "id": m["id"],
            "title": m.get("title", "Untitled"),
            "year": m.get("year", "2026"),
            "rating": m.get("rating", "7.0"),
            "genre": m.get("genre", "Cinema"),
            "poster": m.get("poster", ""),
            "status": status,
            "badge_text": badge_text,
            "is_future": is_future,
            "time_ago": time_ago,
            "scraped_at": scraped_at_str or now.isoformat(),
            "url": url_for("movie_detail", movie_id=m["id"])
        })

    stats = db.get_stats()
    return jsonify({
        "status": "success",
        "count": len(notifications),
        "total_movies": stats.get("total", len(all_movies)),
        "last_scrape": stats.get("last_scrape", now.isoformat()),
        "notifications": notifications
    }), 200


@app.route('/api/stats', methods=['GET'])
def api_get_stats():
    """Returns database statistics."""
    stats = db.get_stats()
    return jsonify({
        "status": "success",
        "stats": stats
    }), 200


# Error Handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('base.html', error_title="404 Not Found", error_message="The requested movie or page does not exist."), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('base.html', error_title="500 Internal Error", error_message="An unexpected server error occurred. Please try again later."), 500


if __name__ == '__main__':
    logger.info(f"Starting MWLBD Web Server on port {Config.PORT}")
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)
