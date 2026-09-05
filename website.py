import os
import threading
from typing import Dict, Any
from flask import Flask, render_template, request, jsonify, redirect, url_for, abort
from flask_cors import CORS
from config import Config, setup_logger
from database import db
from scraper import MWLBDScraper

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
        scraper = MWLBDScraper()
        from datetime import datetime

        if target == "2026_archive":
            result = scraper.crawl_2026_archive(
                start_page=start_page,
                end_page=start_page + num_pages - 1 if num_pages > 1 else None,
                concurrency=concurrency,
                progress_callback=_progress_cb
            )
            scraped = result.get("total_movies", 0)
            pages = result.get("pages_crawled", num_pages)
        elif num_pages >= 5:
            # High-throughput multi-page catalog crawler
            result = scraper.crawl_all_catalog_pages(
                start_page=start_page,
                end_page=start_page + num_pages - 1,
                concurrency=concurrency,
                progress_callback=_progress_cb
            )
            scraped = result.get("total_movies", 0)
            pages = result.get("pages_crawled", num_pages)
        else:
            # Deep detail crawler
            result = scraper.crawl_catalog(
                start_page=start_page,
                num_pages=num_pages,
                concurrency=concurrency
            )
            scraped = result.get("total_scraped", 0)
            pages = result.get("pages_crawled", num_pages)

        with _crawler_lock:
            crawler_state["is_running"] = False
            crawler_state["pages_completed"] = pages
            crawler_state["total_scraped"] = scraped
            crawler_state["message"] = (
                f"Successfully crawled {pages} page(s)! "
                f"Saved/updated {scraped} 2026+ movies into database."
            )
            crawler_state["last_run"] = datetime.utcnow().isoformat()
    except Exception as e:
        logger.error(f"Background crawl task encountered an error: {e}", exc_info=True)
        with _crawler_lock:
            crawler_state["is_running"] = False
            crawler_state["message"] = f"Crawl error: {e}"


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


# Netflix UI Navigation & Genre Pills
NETFLIX_GENRES = [
    "All", "Action", "Adventure", "Anime", "Bollywood", "Comedy", "Crime",
    "Documentary", "Drama", "Dual Audio", "Horror", "Sci-Fi", "Thriller"
]


# Web Routes
@app.route('/')
def home():
    """Home page displaying Netflix-style cinematic hero, category carousels, and movie grid."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    genre = request.args.get('genre', '').strip()

    pagination = db.get_paginated_movies(
        page=page,
        per_page=per_page,
        status=status or None,
        query=query or None,
        genre=genre if genre and genre != "All" else None
    )
    stats = db.get_stats()
    all_movies = db.get_all_movies()

    # Pick top featured movie for Netflix Billboard Hero
    featured_movie = None
    for candidate in all_movies:
        if candidate.get("poster") and len(candidate.get("description", "")) > 15:
            featured_movie = candidate
            break
    if not featured_movie and all_movies:
        featured_movie = all_movies[0]

    # Curate Netflix Content Rows for homepage
    trending_movies = all_movies[:18]
    bollywood_movies = [
        m for m in all_movies
        if 'bollywood' in m.get('genre', '').lower() or 'hindi' in m.get('title', '').lower()
    ][:18]
    dual_audio_movies = [
        m for m in all_movies
        if 'dual' in m.get('genre', '').lower() or 'dual audio' in m.get('title', '').lower()
    ][:18]
    series_movies = [
        m for m in all_movies
        if 'series' in m.get('genre', '').lower() or 'season' in m.get('title', '').lower()
    ][:18]

    return render_template(
        'index.html',
        movies=pagination["items"],
        pagination=pagination,
        stats=stats,
        query=query,
        status=status,
        genre=genre or "All",
        featured_movie=featured_movie,
        trending_movies=trending_movies,
        bollywood_movies=bollywood_movies,
        dual_audio_movies=dual_audio_movies,
        series_movies=series_movies,
        netflix_genres=NETFLIX_GENRES,
        categories=MWLBD_CATEGORIES
    )


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
