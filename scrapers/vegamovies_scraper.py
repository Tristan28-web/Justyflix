import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, parse_qs, urlparse
from bs4 import BeautifulSoup
from config import Config, setup_logger
from scrapers.base_scraper import BaseScraper

logger = setup_logger('vegamovies_scraper')


class VegaMoviesScraper(BaseScraper):
    """
    Scraper adapter for VegaMovies / HDHub4u direct HTTP/GDrive movie platforms.
    Extracts 4K UHD, 1080p, 720p, 480p Dual Audio releases and direct stream links.
    """

    def __init__(self, base_url: Optional[str] = None):
        target_url = (base_url or getattr(Config, 'VEGAMOVIES_BASE_URL', 'https://vegamovies.pages.dev')).rstrip('/')
        super().__init__(provider_name="VegaMovies", base_url=target_url)

    def get_total_catalog_pages(self) -> int:
        """Determines max catalog pages."""
        html = self._fetch_url(f"{self.base_url}/")
        if not html:
            return 20
        soup = BeautifulSoup(html, 'html.parser')
        pages = []
        for a in soup.find_all('a', href=True):
            m = re.search(r'/page/(\d+)', a['href'])
            if m:
                pages.append(int(m.group(1)))
        return max(pages) if pages else 20

    def scrape_catalog_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """Scrapes movie summaries from catalog page."""
        page_url = f"{self.base_url}/" if page_num <= 1 else f"{self.base_url}/page/{page_num}/"
        logger.info(f"Scraping VegaMovies catalog page {page_num}: {page_url}")
        html = self._fetch_url(page_url)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        from database import is_2026_or_future

        posts = soup.select('.post, article, .item, .post-article')
        if not posts:
            posts = soup.find_all('div', class_=re.compile(r'post|entry|item', re.I))

        for post in posts:
            a = post.find('a', href=True)
            if not a:
                continue
            href = a['href']
            if href in seen_urls or not href.startswith('http'):
                continue

            raw_text = a.text.strip() or post.get_text().strip()
            if not raw_text or len(raw_text) < 4:
                continue

            img = post.find('img')
            poster_url = (img.get('src') or img.get('data-src') or "") if img else ""

            clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|4k.*|\[.*?\]|\(.*?\)', '', raw_text, flags=re.IGNORECASE).strip(' -:|')
            if not clean_title:
                clean_title = href.strip('/').split('/')[-1].replace('-', ' ').title()

            year_m = re.search(r'\b(202[4-9]|203[0-9])\b', raw_text)
            year = year_m.group(1) if year_m else "2026"

            candidate = {
                "title": clean_title,
                "url": href,
                "source_url": href,
                "poster": poster_url,
                "year": year,
                "source_site": "VegaMovies"
            }

            if not is_2026_or_future(candidate):
                continue

            seen_urls.add(href)
            discovered.append(candidate)

        return discovered

    def scrape_movie_details(self, movie_url: str) -> Optional[Dict[str, Any]]:
        """Scrapes full metadata and download links for specific movie."""
        html = self._fetch_url(movie_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')

        title_elem = soup.find('h1') or soup.find('h2', class_='entry-title')
        raw_title = title_elem.text.strip() if title_elem else ""

        clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|4k.*|\[.*?\]|\(.*?\)', '', raw_title, flags=re.IGNORECASE).strip(' -:|')
        if not clean_title:
            slug = movie_url.strip('/').split('/')[-1]
            clean_title = slug.replace('-', ' ').title()

        year_match = re.search(r'\b(202[4-9]|203[0-9])\b', raw_title)
        year = year_match.group(1) if year_match else "2026"

        genres = []
        for a in soup.find_all('a', href=True):
            if any(k in a['href'] for k in ['/category/', '/genre/']):
                genres.append(a.text.strip())

        desc_elem = soup.find('div', class_=re.compile(r'entry-content|post-content|content', re.I))
        description = desc_elem.text.strip() if desc_elem else f"{clean_title} ({year}) full movie details and high-speed download links."
        description = ' '.join(description.split()[:80])

        poster_url = ""
        img_elem = soup.select_one('.entry-content img') or soup.select_one('.post img')
        if img_elem:
            poster_url = img_elem.get('src') or img_elem.get('data-src') or ""

        download_links: List[Dict[str, Any]] = []
        seen_links = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.text.strip()
            text_lower = text.lower()

            if any(k in text_lower or k in href.lower() for k in ['download', 'drive', 'vcloud', 'gdrive', '1080p', '720p', '480p', '4k']):
                if any(bad in href.lower() for bad in ['facebook', 'twitter', 'telegram', 'whatsapp', 'how-to']):
                    continue
                if href in seen_links:
                    continue

                seen_links.add(href)

                q_match = re.search(r'\b(2160p|1080p|720p|480p|4k)\b', text + " " + href, re.I)
                quality = q_match.group(1).upper() if q_match else "1080p"

                download_links.append({
                    "url": href,
                    "original_url": href,
                    "preview_url": "",
                    "type": "direct_hub",
                    "file_id": "",
                    "label": f"Download {quality} ({self.provider_name})",
                    "quality": quality,
                    "size": "",
                    "source_site": "VegaMovies"
                })

        movie_id = self.generate_slug_id(clean_title) + f"-{year}"

        from database import calculate_real_or_authentic_rating, is_series

        movie_record = {
            "id": movie_id,
            "title": clean_title,
            "year": year,
            "genre": ", ".join(list(set(genres))) if genres else "Action, Drama, Sci-Fi",
            "rating": "7.5",
            "duration": "2h 05m",
            "description": description,
            "poster": poster_url,
            "banner": poster_url,
            "source_url": movie_url,
            "source_site": "VegaMovies",
            "is_series": False,
            "download_links": download_links
        }

        movie_record['rating'] = calculate_real_or_authentic_rating(movie_record)
        movie_record['is_series'] = is_series(movie_record)

        return movie_record
