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
        scraper = MWLBDScraper()
        # Always sync latest 2026 releases
        result_2026 = scraper.crawl_2026_archive(start_page=1, end_page=pages, concurrency=concurrency)
        stats = db.get_stats()
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"=== Completed 2026 Scrape Job in {duration:.2f}s! "
            f"Indexed: {result_2026.get('total_movies')}. "
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
