import re
import unicodedata
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, parse_qs, urlparse
from bs4 import BeautifulSoup
from config import Config, setup_logger
from scrapers.base_scraper import BaseScraper

logger = setup_logger('mwlbd_scraper')


class MWLBDScraper(BaseScraper):
    """
    Scraper adapter for MWLBD (and active mirrors like fojik.site).
    Inherits from BaseScraper.
    """

    def __init__(self, base_url: Optional[str] = None):
        target_url = (base_url or getattr(Config, 'MWLBD_BASE_URL', 'https://fojik.site')).rstrip('/')
        super().__init__(provider_name="MWLBD", base_url=target_url)

    def _extract_gdrive_id(self, url: str) -> Optional[str]:
        """Extracts Google Drive file ID from supported URL patterns."""
        patterns = [
            r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/uc\?export=download&id=([a-zA-Z0-9_-]+)',
            r'docs\.google\.com/file/d/([a-zA-Z0-9_-]+)'
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            if 'id' in qs and qs['id']:
                return qs['id'][0]
        except Exception:
            pass
        return None

    def _format_gdrive_link(self, raw_url: str, label: str = "Download", quality: str = "", size: str = "") -> Dict[str, Any]:
        """Formats link into standard Google Drive direct download representation."""
        file_id = self._extract_gdrive_id(raw_url) or ""
        direct_url = f"https://drive.google.com/uc?export=download&id={file_id}" if file_id else raw_url
        embed_url = f"https://drive.google.com/file/d/{file_id}/preview" if file_id else ""

        full_label = label
        if quality and quality.lower() not in full_label.lower():
            full_label = f"{label} ({quality})"
        if size:
            full_label += f" [{size}]"

        return {
            "url": direct_url,
            "original_url": raw_url,
            "preview_url": embed_url,
            "type": "gdrive",
            "file_id": file_id,
            "label": full_label,
            "quality": quality,
            "size": size,
            "source_site": "MWLBD"
        }

    def _extract_download_links(self, soup: BeautifulSoup, page_html: str) -> List[Dict[str, Any]]:
        """Extract Google Drive download links from soup and page content."""
        links: List[Dict[str, Any]] = []
        seen_urls = set()

        # 1. Direct Google Drive URLs
        gdrive_urls = re.findall(
            r'https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?)[^\s"\'<>]+',
            page_html
        )
        for u in gdrive_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                links.append(self._format_gdrive_link(u, label="Download"))

        # 2. Parse download tables
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all(['td', 'th'])
                if not cols or cols[0].name == 'th':
                    continue

                col_texts = [c.text.strip() for c in cols]
                quality = col_texts[1] if len(col_texts) >= 2 else ""
                size = col_texts[3] if len(col_texts) >= 4 else ""

                found_row_link = False
                for a in row.find_all('a', href=True):
                    href = a['href']
                    if 'drive.google.com' in href or 'docs.google.com' in href:
                        if href not in seen_urls:
                            seen_urls.add(href)
                            links.append(self._format_gdrive_link(
                                href, label="Download", quality=quality, size=size
                            ))
                            found_row_link = True

                if not found_row_link:
                    form = row.find('form')
                    if form:
                        form_action = form.get('action', '')
                        link_id = form.get('id', '')
                        row_key = f"{link_id}-{quality}"
                        if row_key not in seen_urls:
                            seen_urls.add(row_key)
                            links.append({
                                "url": form_action or self.base_url,
                                "original_url": form_action or self.base_url,
                                "preview_url": "",
                                "type": "gdrive",
                                "file_id": link_id,
                                "label": f"Download ({quality})" if quality else "Download",
                                "quality": quality or "HD",
                                "size": size,
                                "source_site": "MWLBD"
                            })

        # 3. Extra <a> tags
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('drive.google.com' in href or 'docs.google.com' in href) and href not in seen_urls:
                seen_urls.add(href)
                text = a.text.strip() or "Download Google Drive"
                links.append(self._format_gdrive_link(href, label=text))

        return links

    def get_total_catalog_pages(self) -> int:
        """Determines max catalog pages on MWLBD."""
        html = self._fetch_url(f"{self.base_url}/movie/")
        if not html:
            return 50
        soup = BeautifulSoup(html, 'html.parser')
        pages = []
        for a in soup.find_all('a', href=True):
            m = re.search(r'/page/(\d+)', a['href'])
            if m:
                pages.append(int(m.group(1)))
        return max(pages) if pages else 30

    def get_movies_from_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """Scrapes movie summaries from catalog page."""
        page_url = f"{self.base_url}/movie/" if page_num <= 1 else f"{self.base_url}/movie/page/{page_num}/"
        logger.info(f"Scraping MWLBD catalog page {page_num}: {page_url}")
        html = self._fetch_url(page_url)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        from database import is_2026_or_future

        articles = soup.select('article.item')
        if articles:
            for article in articles:
                a = article.select_one('h3 a') or article.select_one('.poster a') or article.select_one('a[href*="/movie/"]')
                if not a or not a.get('href'):
                    continue
                href = a['href']
                if href in seen_urls:
                    continue

                full_url = urljoin(self.base_url, href)
                raw_text = a.text.strip()
                if any(bad in raw_text.lower() for bad in ['see all', 'view all', 'all movies']):
                    continue

                year_span = article.select_one('.data span')
                year_text = year_span.text.strip() if year_span else ""

                img = article.select_one('.poster img')
                poster_url = (img.get('src') or img.get('data-src') or "") if img else ""

                clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|Google Drive.*|\[.*?\]|\(.*?\)', '', raw_text, flags=re.IGNORECASE).strip(' -:|')
                if not clean_title:
                    clean_title = href.strip('/').split('/')[-1].replace('-', ' ').title()

                candidate = {
                    "title": clean_title,
                    "url": full_url,
                    "source_url": full_url,
                    "poster": poster_url,
                    "year": year_text or "2026",
                    "source_site": "MWLBD"
                }

                if not is_2026_or_future(candidate):
                    continue

                seen_urls.add(href)
                discovered.append(candidate)

        if not discovered and not articles:
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/movie/' in href and href not in seen_urls:
                    slug = href.strip('/').split('/')[-1]
                    if not slug or slug in ('movie', 'movies') or any(x in href for x in ['/genre/', '/category/', '/tag/', '/page/']):
                        continue
                    raw_text = a.text.strip()
                    if any(bad in raw_text.lower() for bad in ['see all', 'view all', 'all movies']):
                        continue

                    full_url = urljoin(self.base_url, href)
                    clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|Google Drive.*|\[.*?\]|\(.*?\)', '', raw_text, flags=re.IGNORECASE).strip(' -:|')
                    if not clean_title:
                        clean_title = slug.replace('-', ' ').title()

                    candidate = {
                        "title": clean_title,
                        "url": full_url,
                        "source_url": full_url,
                        "poster": "",
                        "year": "2026",
                        "source_site": "MWLBD"
                    }

                    if not is_2026_or_future(candidate):
                        continue

                    seen_urls.add(href)
                    discovered.append(candidate)

        return discovered

    def scrape_catalog_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """Alias for BaseScraper implementation."""
        return self.get_movies_from_page(page_num)

    def scrape_movie_details(self, movie_url: str) -> Optional[Dict[str, Any]]:
        """Scrapes full metadata and download links for specific movie."""
        html = self._fetch_url(movie_url)
        if not html:
            return None

        soup = BeautifulSoup(html, 'html.parser')

        title_elem = soup.find('h1') or soup.find('h2', class_='entry-title')
        raw_title = title_elem.text.strip() if title_elem else ""

        clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|Google Drive.*|\[.*?\]|\(.*?\)', '', raw_title, flags=re.IGNORECASE).strip(' -:|')
        if not clean_title:
            slug = movie_url.strip('/').split('/')[-1]
            clean_title = slug.replace('-', ' ').title()

        year_match = re.search(r'\b(202[4-9]|203[0-9])\b', raw_title)
        year = year_match.group(1) if year_match else "2026"

        genres = []
        for a in soup.find_all('a', href=True):
            if '/genre/' in a['href']:
                genres.append(a.text.strip())

        desc_elem = soup.find('div', class_='wp-content') or soup.find('div', class_='entry-content')
        description = desc_elem.text.strip() if desc_elem else f"{clean_title} ({year}) full movie details and direct links."
        description = ' '.join(description.split()[:80])

        poster_url = ""
        img_elem = soup.select_one('.poster img') or soup.select_one('.entry-content img')
        if img_elem:
            poster_url = img_elem.get('src') or img_elem.get('data-src') or ""

        download_links = self._extract_download_links(soup, html)

        movie_id = self.generate_slug_id(clean_title) + f"-{year}"

        from database import calculate_real_or_authentic_rating, is_series

        movie_record = {
            "id": movie_id,
            "title": clean_title,
            "year": year,
            "genre": ", ".join(list(set(genres))) if genres else "Action, Drama, Thriller",
            "rating": "7.2",
            "duration": "2h 15m",
            "description": description,
            "poster": poster_url,
            "banner": poster_url,
            "source_url": movie_url,
            "source_site": "MWLBD",
            "is_series": False,
            "download_links": download_links
        }

        movie_record['rating'] = calculate_real_or_authentic_rating(movie_record)
        movie_record['is_series'] = is_series(movie_record)

        return movie_record
