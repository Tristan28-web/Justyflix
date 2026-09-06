"""
Unified Scraper Gateway & Module Exporter.
Exports MWLBDScraper, Bolly4uScraper, and helper functions for multi-source site crawling.
"""

from scrapers.base_scraper import BaseScraper
from scrapers.mwlbd_scraper import MWLBDScraper
from scrapers.bolly4u_scraper import Bolly4uScraper
from scrapers.vegamovies_scraper import VegaMoviesScraper
from scrapers.x1337_scraper import X1337Scraper


def get_active_scrapers() -> list:
    """Returns list of instantiated active source site scrapers."""
    return [
        MWLBDScraper(),
        Bolly4uScraper(),
        VegaMoviesScraper(),
        X1337Scraper()
    ]
