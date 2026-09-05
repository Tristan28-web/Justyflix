import os
import sys
import time
import signal
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from config import Config, setup_logger
from database import db
from scraper import MWLBDScraper

logger = setup_logger('worker')


def scrape_only(pages: int = 2, concurrency: int = 4):
    """
    Background worker job:
    1. Crawls the 2026 archive and newest catalog drops
    2. Concurrently extracts full metadata and Google Drive links
    3. Saves directly to JSON database
    STRICTLY ZERO FILE DOWNLOADS TO SERVER.
    """
    start_time = datetime.utcnow()
    logger.info(f"=== Starting Scheduled 2026+ Scrape Job at {start_time.isoformat()} ===")

    try:
        from scrapers.mwlbd_scraper import MWLBDScraper
        from scrapers.bolly4u_scraper import Bolly4uScraper

        scrapers = [MWLBDScraper(), Bolly4uScraper()]
        total_scraped = 0

        for sc in scrapers:
            try:
                logger.info(f"Worker running scrape for provider: {sc.provider_name}")
                for p in range(1, pages + 1):
                    catalog_items = sc.scrape_catalog_page(p)
                    for item in catalog_items:
                        movie_url = item.get("url") or item.get("source_url")
                        if movie_url:
                            details = sc.scrape_movie_details(movie_url)
                            if details:
                                db.add_or_merge_movie(details)
                                total_scraped += 1
            except Exception as sc_err:
                logger.warning(f"Worker error for provider {sc.provider_name}: {sc_err}")

        stats = db.get_stats()
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"=== Completed Multi-Source Scrape Job in {duration:.2f}s! "
            f"Indexed/Merged: {total_scraped}. "
            f"Total 2026+ movies in DB: {stats.get('total')} ==="
        )
    except Exception as ex:
        logger.error(f"Scheduled scrape job encountered an unexpected error: {ex}", exc_info=True)


def main():
    logger.info("Initializing MWLBD Background Scraper Worker (2026+ Releases)...")
    logger.info(f"Configured scrape interval: every {Config.SCRAPE_INTERVAL_HOURS} hours")
    logger.info(f"Database target: {Config.DATABASE_FILE}")

    scheduler = BlockingScheduler()

    # Schedule the recurring job
    scheduler.add_job(
        func=scrape_only,
        trigger='interval',
        hours=Config.SCRAPE_INTERVAL_HOURS,
        id='mwlbd_scrape_job',
        name='MWLBD 2026 Releases Scraper',
        replace_existing=True,
        max_instances=1
    )

    # Signal handlers for graceful container termination on Render
    def shutdown_handler(signum, frame):
        logger.info(f"Received termination signal ({signum}). Shutting down worker...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Run initial scrape immediately on startup
    logger.info("Executing initial startup scrape...")
    scrape_only(pages=2, concurrency=4)

    logger.info(f"Worker scheduler started. Next run in {Config.SCRAPE_INTERVAL_HOURS} hours.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped.")


if __name__ == '__main__':
    main()
