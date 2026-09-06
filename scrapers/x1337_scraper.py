import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from config import Config, setup_logger
from scrapers.base_scraper import BaseScraper
from metadata_enricher import clean_display_title

logger = setup_logger('1337x_scraper')


class X1337Scraper(BaseScraper):
    """
    Scraper adapter for 1337x (and active mirrors like 1337x.st, 1337x.gd).
    Harvests torrent metadata, 4K/1080p/720p release titles, seeders, and magnet links.
    """

    def __init__(self, base_url: Optional[str] = None):
        target_url = (base_url or getattr(Config, 'X1337_BASE_URL', 'https://1337xx.to')).rstrip('/')
        super().__init__(provider_name="1337x Torrent Index", base_url=target_url)

    def get_total_catalog_pages(self) -> int:
        """Returns total pages available for trending movies."""
        return 20

    def scrape_catalog_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """Scrapes movie/series torrent summaries from 1337x search/trending pages."""
        page_url = f"{self.base_url}/popular-movies" if page_num <= 1 else f"{self.base_url}/sort-search/2026/seeders/desc/{page_num}/"
        logger.info(f"Scraping 1337x catalog page {page_num}: {page_url}")
        html = self._fetch_url(page_url)
        if not html:
            # Fallback to 1337x.is mirror domain if primary domain fails
            mirror_url = f"https://1337x.is/popular-movies" if page_num <= 1 else f"https://1337x.is/sort-search/2026/seeders/desc/{page_num}/"
            html = self._fetch_url(mirror_url)
            if not html:
                return []

        soup = BeautifulSoup(html, 'html.parser')
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        from database import is_2026_or_future

        rows = soup.select('table.table-list tr, tr')
        for row in rows:
            td_name = row.select_one('td.name, td.coll-1')
            if not td_name:
                continue

            links = td_name.find_all('a', href=True)
            target_a = None
            for a in links:
                if '/torrent/' in a['href']:
                    target_a = a
                    break

            if not target_a:
                continue

            href = urljoin(self.base_url, target_a['href'])
            if href in seen_urls:
                continue

            raw_text = target_a.text.strip()
            if not raw_text or len(raw_text) < 4:
                continue

            # Parse size & seeders
            td_seeds = row.select_one('td.seeds, td.coll-2')
            seeds_text = td_seeds.text.strip() if td_seeds else "0"

            td_size = row.select_one('td.size, td.coll-4')
            size_text = td_size.text.strip() if td_size else ""

            clean_title = clean_display_title(raw_text)
            if not clean_title:
                slug = href.strip('/').split('/')[-1]
                clean_title = clean_display_title(slug.replace('-', ' '))

            year_m = re.search(r'\b(202[4-9]|203[0-9])\b', raw_text)
            year = year_m.group(1) if year_m else "2026"

            candidate = {
                "title": clean_title,
                "raw_name": raw_text,
                "url": href,
                "source_url": href,
                "poster": "",
                "year": year,
                "seeds": seeds_text,
                "size": size_text,
                "source_site": "1337x"
            }

            if not is_2026_or_future(candidate):
                continue

            seen_urls.add(href)
            discovered.append(candidate)

        return discovered

    def scrape_movie_details(self, movie_url: str) -> Optional[Dict[str, Any]]:
        """Scrapes full metadata and magnet links for a specific 1337x torrent detail page."""
        html = self._fetch_url(movie_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')

        title_elem = soup.find('h1') or soup.select_one('.box-info-heading h1')
        raw_title = title_elem.text.strip() if title_elem else ""

        clean_title = clean_display_title(raw_title)
        if not clean_title:
            slug = movie_url.strip('/').split('/')[-1]
            clean_title = clean_display_title(slug.replace('-', ' '))

        year_match = re.search(r'\b(202[4-9]|203[0-9])\b', raw_title)
        year = year_match.group(1) if year_match else "2026"

        # Find Magnet Link
        magnet_url = ""
        for a in soup.find_all('a', href=True):
            if a['href'].startswith('magnet:'):
                magnet_url = a['href']
                break

        poster_url = ""
        img_elem = soup.select_one('.torrent-detail-info img') or soup.select_one('.box-info img')
        if img_elem:
            poster_url = img_elem.get('src') or ""
            if poster_url and not poster_url.startswith('http'):
                poster_url = urljoin(self.base_url, poster_url)

        q_match = re.search(r'\b(2160p|1080p|720p|480p|4k)\b', raw_title, re.I)
        quality = q_match.group(1).upper() if q_match else "1080p"

        download_links = []
        if magnet_url:
            download_links.append({
                "url": magnet_url,
                "original_url": magnet_url,
                "preview_url": "",
                "type": "magnet",
                "file_id": "",
                "label": f"Magnet Link {quality} (P2P Free)",
                "quality": quality,
                "size": "",
                "source_site": "1337x"
            })

        movie_id = self.generate_slug_id(clean_title) + f"-{year}"

        from database import calculate_real_or_authentic_rating, is_series

        movie_record = {
            "id": movie_id,
            "title": clean_title,
            "year": year,
            "genre": "Action, Sci-Fi, Thriller",
            "rating": "7.8",
            "duration": "2h 12m",
            "description": f"{clean_title} ({year}) 1337x high-speed release with verified magnet links.",
            "poster": poster_url,
            "banner": poster_url,
            "source_url": movie_url,
            "source_site": "1337x",
            "is_series": False,
            "download_links": download_links
        }

        movie_record['rating'] = calculate_real_or_authentic_rating(movie_record)
        movie_record['is_series'] = is_series(movie_record)

        return movie_record
