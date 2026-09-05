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


def scrape_only(limit: int = 20):
    """
    Background worker job:
    1. Scrapes latest movie metadata from MWLBD
    2. Extracts Google Drive links
    3. Saves directly to JSON database
    STRICTLY ZERO FILE DOWNLOADS TO SERVER.
    """
    start_time = datetime.utcnow()
    logger.info(f"=== Starting Scheduled Scrape Job at {start_time.isoformat()} (Limit: {limit}) ===")

    try:
        scraper = MWLBDScraper()
        latest_movies = scraper.get_latest_movies(limit=limit)
        logger.info(f"Worker discovered {len(latest_movies)} movies.")

        successful_count = 0
        error_count = 0

        for movie_info in latest_movies:
            url = movie_info.get("url")
            title = movie_info.get("title", "Unknown")
            try:
                details = scraper.get_movie_details(url)
                if details:
                    db.save_movie(details)
                    successful_count += 1
                else:
                    error_count += 1
                    logger.warning(f"Could not extract details for {title} ({url})")
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing movie '{title}': {e}")

        stats = db.get_stats()
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"=== Completed Scrape Job in {duration:.2f}s! "
            f"Successfully updated: {successful_count}, Errors: {error_count}. "
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
