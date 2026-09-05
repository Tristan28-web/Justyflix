import re
import unicodedata
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import requests
from config import Config, setup_logger

logger = setup_logger('base_scraper')


class BaseScraper(ABC):
    """
    Abstract Base Class for all movie/series source site scrapers.
    Defines a unified contract for metadata extraction, category crawling,
    and link normalization across multiple movie provider sites.
    """

    def __init__(self, provider_name: str, base_url: str):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self.timeout = getattr(Config, 'REQUEST_TIMEOUT', 15)
        self.max_retries = getattr(Config, 'MAX_RETRIES', 3)

    def _fetch_url(self, url: str) -> Optional[str]:
        """Fetch page content with retries and custom timeout."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"[{self.provider_name}] Fetch {url} attempt {attempt} returned status {resp.status_code}")
            except Exception as e:
                logger.warning(f"[{self.provider_name}] Fetch {url} attempt {attempt} failed: {e}")
        return None

    def generate_slug_id(self, title: str) -> str:
        """Generates clean slug ID from title."""
        title = unicodedata.normalize('NFKD', title)
        title = title.encode('ascii', 'ignore').decode('ascii')
        title = re.sub(r'[^\w\s-]', '', title.lower()).strip()
        slug = re.sub(r'[-\s]+', '-', title)
        return slug[:80] if slug else "movie"

    @abstractmethod
    def get_total_catalog_pages(self) -> int:
        """Returns total available pages in the site's catalog."""
        pass

    @abstractmethod
    def scrape_catalog_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """
        Scrapes a page from the main catalog.
        Returns a list of movie summaries with source detail URLs.
        """
        pass

    @abstractmethod
    def scrape_movie_details(self, movie_url: str) -> Optional[Dict[str, Any]]:
        """
        Scrapes full metadata and download links for a specific movie detail page.
        Returns standardized movie dict.
        """
        pass
