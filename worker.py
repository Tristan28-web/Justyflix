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
    1. Crawls multiple catalog archive pages from MWLBD (pages 1 to N)
    2. Concurrently extracts full metadata and Google Drive links
    3. Saves directly to JSON database
    STRICTLY ZERO FILE DOWNLOADS TO SERVER.
    """
    start_time = datetime.utcnow()
    logger.info(f"=== Starting Scheduled Multi-Page Scrape Job at {start_time.isoformat()} ({pages} pages) ===")

    try:
        scraper = MWLBDScraper()
        result = scraper.crawl_catalog(start_page=1, num_pages=pages, concurrency=concurrency)
        stats = db.get_stats()
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"=== Completed Scrape Job in {duration:.2f}s! "
            f"Successfully saved/updated: {result.get('total_scraped')}, Errors: {result.get('total_errors')}. "
            f"Total movies in DB: {stats.get('total')} ==="
        )
    except Exception as ex:
        logger.error(f"Scheduled scrape job encountered an unexpected error: {ex}", exc_info=True)


def main():
    logger.info("Initializing MWLBD Background Scraper Worker...")
    logger.info(f"Configured scrape interval: every {Config.SCRAPE_INTERVAL_HOURS} hours")
    logger.info(f"Database target: {Config.DATABASE_FILE}")

    scheduler = BlockingScheduler()

    # Schedule the recurring job
    scheduler.add_job(
        func=scrape_only,
        trigger='interval',
        hours=Config.SCRAPE_INTERVAL_HOURS,
        id='mwlbd_scrape_job',
        name='MWLBD Periodic Metadata Scraper',
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
    scrape_only(limit=10)

    logger.info(f"Worker scheduler started. Next run in {Config.SCRAPE_INTERVAL_HOURS} hours.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped.")


if __name__ == '__main__':
    main()
