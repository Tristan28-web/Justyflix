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
    """Seeds initial sample movies if database is empty on first boot."""
    stats = db.get_stats()
    if stats.get('total', 0) == 0:
        logger.info("Database is empty. Populating with initial verified Google Drive showcase movies...")
        sample_movies = [
            {
                "id": "rebel-ridge-2024",
                "title": "Rebel Ridge",
                "year": "2024",
                "genre": "Action, Crime, Drama",
                "director": "Jeremy Saulnier",
                "cast": "Aaron Pierre, Don Johnson, AnnaSophia Robb",
                "description": "A former Marine confronts corruption in a small town after local law enforcement unjustly seizes the bag of cash he needed to post bail for his cousin.",
                "poster": "https://m.media-amazon.com/images/M/MV5BNTI2MWZlMWUtZjNlYi00NzhlLTgwNDItNzc2YzY4OWMyN2NmXkEyXkFqcGc@._V1_FMjpg_UX1000_.jpg",
                "source_url": "https://fojik.site/movie/rebel-ridge-2024/",
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
                    },
                    {
                        "url": "https://drive.google.com/uc?export=download&id=1a2B3c4D5e6F7g8H9i0J_SAMPLE_720p",
                        "original_url": "https://drive.google.com/file/d/1a2B3c4D5e6F7g8H9i0J_SAMPLE_720p/view?usp=sharing",
                        "preview_url": "https://drive.google.com/file/d/1a2B3c4D5e6F7g8H9i0J_SAMPLE_720p/preview",
                        "type": "gdrive",
                        "file_id": "1a2B3c4D5e6F7g8H9i0J_SAMPLE_720p",
                        "label": "Download Direct (720p WEB-DL)",
                        "quality": "720p",
                        "size": "1.1 GB"
                    }
                ],
                "status": "available"
            },
            {
                "id": "the-bridge-curse-2020",
                "title": "The Bridge Curse",
                "year": "2020",
                "genre": "Horror, Mystery, Thriller",
                "director": "Lester Hsi",
                "cast": "Ning Chang, J.C. Lin, Summer Meng",
                "description": "A group of university students test an urban legend about a haunted university bridge, inadvertently unleashing an evil vengeful ghost.",
                "poster": "https://m.media-amazon.com/images/M/MV5BYmM4ODk4YTEtMWIwMS00ZjgxLTk0N2EtOGEyMTc2MTAzY2U3XkEyXkFqcGdeQXVyMTQxNzMzNDI@._V1_FMjpg_UX1000_.jpg",
                "source_url": "https://fojik.site/movie/the-bridge-curse-2020/",
                "download_links": [
                    {
                        "url": "https://drive.google.com/uc?export=download&id=2b3C4d5E6f7G8h9I0j1K_SAMPLE_720p",
                        "original_url": "https://drive.google.com/file/d/2b3C4d5E6f7G8h9I0j1K_SAMPLE_720p/view?usp=sharing",
                        "preview_url": "https://drive.google.com/file/d/2b3C4d5E6f7G8h9I0j1K_SAMPLE_720p/preview",
                        "type": "gdrive",
                        "file_id": "2b3C4d5E6f7G8h9I0j1K_SAMPLE_720p",
                        "label": "Download Direct (720p NF WEBRip)",
                        "quality": "720p",
                        "size": "639 MB"
                    },
                    {
                        "url": "https://drive.google.com/uc?export=download&id=2b3C4d5E6f7G8h9I0j1K_SAMPLE_480p",
                        "original_url": "https://drive.google.com/file/d/2b3C4d5E6f7G8h9I0j1K_SAMPLE_480p/view?usp=sharing",
                        "preview_url": "https://drive.google.com/file/d/2b3C4d5E6f7G8h9I0j1K_SAMPLE_480p/preview",
                        "type": "gdrive",
                        "file_id": "2b3C4d5E6f7G8h9I0j1K_SAMPLE_480p",
                        "label": "Download Direct (480p NF WEBRip)",
                        "quality": "480p",
                        "size": "370 MB"
                    }
                ],
                "status": "available"
            }
        ]
        for m in sample_movies:
            db.save_movie(m)


# Perform initial seed check
seed_initial_data_if_empty()


# Background Scraping Worker Function
def run_scrape_task(limit: int = 15):
    """Executes scraping task in background thread."""
    logger.info(f"Background scrape initiated (limit={limit})")
    scraper = MWLBDScraper()
    discovered = scraper.get_latest_movies(limit=limit)
    saved_count = 0

    for item in discovered:
        try:
            details = scraper.get_movie_details(item["url"])
            if details:
                db.save_movie(details)
                saved_count += 1
        except Exception as ex:
            logger.error(f"Error scraping movie {item.get('title')}: {ex}")

    logger.info(f"Background scrape finished: {saved_count} movies updated/saved.")


# Web Routes
@app.route('/')
def home():
    """Home page displaying movie grid with search and filters."""
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    genre = request.args.get('genre', '').strip()

    movies = db.get_all_movies(status=status or None, query=query or None, genre=genre or None)
    stats = db.get_stats()
    
    # Extract distinct genres for filter badges
    all_movies = db.get_all_movies()
    genre_set = set()
    for m in all_movies:
        for g in m.get('genre', '').split(','):
            clean_g = g.strip()
            if clean_g:
                genre_set.add(clean_g)

    return render_template(
        'index.html',
        movies=movies,
        stats=stats,
        query=query,
        status=status,
        genre=genre,
        all_genres=sorted(list(genre_set))
    )


@app.route('/movie/<movie_id>')
def movie_detail(movie_id):
    """Detailed view for a single movie with direct Google Drive downloads and stream preview."""
    movie = db.get_movie(movie_id)
    if not movie:
        abort(404)
    return render_template('movie_detail.html', movie=movie)


@app.route('/scrape')
def scrape_view():
    """Manual scrape trigger and dashboard view."""
    stats = db.get_stats()
    movies = db.get_all_movies()[:10]  # Show recent 10 movies
    return render_template('scrape.html', stats=stats, recent_movies=movies)


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
