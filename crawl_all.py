import os
import sys
import json
import time
import argparse
from datetime import datetime
from config import Config, setup_logger
from database import db
from scraper import MWLBDScraper

logger = setup_logger('crawl_all')

PROGRESS_FILE = os.path.join(os.path.dirname(Config.DATABASE_FILE), 'crawler_progress.json')


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_page": 0, "total_movies": 0, "timestamp": None}


def save_progress(current_page: int, total_movies: int):
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "last_page": current_page,
                "total_movies": total_movies,
                "timestamp": datetime.utcnow().isoformat()
            }, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save crawler progress: {e}")


def crawl_site(start_page: int = 1, max_pages: int = None, concurrency: int = 5, resume: bool = False):
    scraper = MWLBDScraper()
    total_site_pages = scraper.get_total_catalog_pages()
    
    if resume:
        prog = load_progress()
        if prog.get("last_page", 0) > 0:
            start_page = prog["last_page"] + 1
            logger.info(f"Resuming crawler from page {start_page} (last page crawled: {prog['last_page']})")

    end_page = total_site_pages if max_pages is None else min(start_page + max_pages - 1, total_site_pages)

    logger.info("=" * 65)
    logger.info(f"STARTING AUTONOMOUS MWLBD FULL-SITE CRAWLER")
    logger.info(f"Target Pages: {start_page} to {end_page} (Total: {end_page - start_page + 1} pages)")
    logger.info(f"Base Mirror: {scraper.base_url}")
    logger.info(f"Database: {Config.DATABASE_FILE}")
    logger.info("=" * 65)

    def on_progress(processed, total, count):
        current_page_num = start_page + processed - 1
        save_progress(current_page_num, db.get_stats()["total"])
        logger.info(f"[Progress] Crawled {processed}/{total} pages | DB Total: {db.get_stats()['total']} movies")

    result = scraper.crawl_all_catalog_pages(
        start_page=start_page,
        end_page=end_page,
        concurrency=concurrency,
        progress_callback=on_progress
    )

    stats = db.get_stats()
    logger.info("=" * 65)
    logger.info(f"CRAWL COMPLETE!")
    logger.info(f"Pages Crawled: {result['pages_crawled']}")
    logger.info(f"Total Movies in Database: {stats['total']}")
    logger.info("=" * 65)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="MWLBD Full-Site Autonomous Crawler")
    parser.add_argument('--start', type=int, default=1, help="Starting archive page (default: 1)")
    parser.add_argument('--pages', type=int, default=None, help="Number of pages to crawl (default: all pages)")
    parser.add_argument('--concurrency', type=int, default=5, help="Concurrent page workers (default: 5)")
    parser.add_argument('--resume', action='store_true', help="Resume from last saved progress")
    args = parser.parse_args()

    crawl_site(start_page=args.start, max_pages=args.pages, concurrency=args.concurrency, resume=args.resume)
