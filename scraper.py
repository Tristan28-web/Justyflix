import re
import unicodedata
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, parse_qs, urlparse
import requests
from bs4 import BeautifulSoup
from config import Config, setup_logger

logger = setup_logger('scraper')


class MWLBDScraper:
    """
    Scraper for MWLBD (and its active mirrors like fojik.site).
    Extracts metadata and Google Drive download links.
    STRICTLY NO SERVER-SIDE FILE DOWNLOADS.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or Config.MWLBD_BASE_URL).rstrip('/')
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
        self.timeout = Config.REQUEST_TIMEOUT
        self.max_retries = Config.MAX_RETRIES

    def _fetch_url(self, url: str) -> Optional[str]:
        """Fetch page content with retries and timeout."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"Fetch {url} attempt {attempt} returned status {resp.status_code}")
            except Exception as e:
                logger.warning(f"Fetch {url} attempt {attempt} failed: {e}")
        return None

    def _generate_id(self, title: str) -> str:
        """Create clean slug ID from movie title."""
        title = unicodedata.normalize('NFKD', title)
        title = title.encode('ascii', 'ignore').decode('ascii')
        title = re.sub(r'[^\w\s-]', '', title.lower()).strip()
        slug = re.sub(r'[-\s]+', '-', title)
        return slug[:80] if slug else "movie"

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
        # Try parse_qs for 'id' parameter
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
            "size": size
        }

    def _extract_download_links(self, soup: BeautifulSoup, page_html: str) -> List[Dict[str, Any]]:
        """
        Extract Google Drive download links from soup and page content.
        Parses direct drive links, tables, and buttons.
        """
        links: List[Dict[str, Any]] = []
        seen_urls = set()

        # 1. Regex search for any direct Google Drive URLs embedded in page
        gdrive_urls = re.findall(
            r'https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?)[^\s"\'<>]+',
            page_html
        )
        for u in gdrive_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                links.append(self._format_gdrive_link(u, label="Direct Google Drive Download"))

        # 2. Parse download tables on MWLBD
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all(['td', 'th'])
                if not cols or cols[0].name == 'th':
                    continue

                col_texts = [c.text.strip() for c in cols]
                quality = ""
                size = ""
                # Table columns: Download (0), Quality (1), Language (2), Size (3)
                if len(col_texts) >= 2:
                    quality = col_texts[1]
                if len(col_texts) >= 4:
                    size = col_texts[3]

                # Look for links inside this row
                for a in row.find_all('a', href=True):
                    href = a['href']
                    if 'drive.google.com' in href or 'docs.google.com' in href:
                        if href not in seen_urls:
                            seen_urls.add(href)
                            links.append(self._format_gdrive_link(
                                href,
                                label="Download",
                                quality=quality,
                                size=size
                            ))

        # 3. Look for any other <a> tags containing drive.google.com
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('drive.google.com' in href or 'docs.google.com' in href) and href not in seen_urls:
                seen_urls.add(href)
                text = a.text.strip() or "Download Google Drive"
                links.append(self._format_gdrive_link(href, label=text))

        return links

    def get_latest_movies(self, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Scrapes movie listings from the homepage or catalog.
        Returns basic list of {title, url, poster, year}.
        """
        logger.info(f"Scraping latest movies from {self.base_url} (limit={limit})")
        html = self._fetch_url(self.base_url)
        if not html:
            # Try fallback mirrors if configured base_url fails
            for mirror in Config.FALLBACK_MIRRORS:
                if mirror != self.base_url:
                    logger.info(f"Attempting fallback mirror: {mirror}")
                    html = self._fetch_url(mirror)
                    if html:
                        self.base_url = mirror
                        break

        if not html:
            logger.error("Failed to retrieve listings from base URL and fallback mirrors.")
            return []

        soup = BeautifulSoup(html, 'html.parser')
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        # MWLBD lists movie links inside <h3><a href="..."> or <article><a>
        candidate_links = soup.find_all('a', href=True)
        for a in candidate_links:
            href = a['href']
            # Match movie URLs e.g. /movie/title-year/
            if '/movie/' in href and href not in seen_urls:
                # Exclude category or pagination links
                if any(x in href for x in ['/genre/', '/category/', '/tag/', '/page/']):
                    continue

                full_url = urljoin(self.base_url, href)
                raw_text = a.text.strip()

                # Find associated poster image if nearby
                poster_url = ""
                parent_container = a.find_parent(['article', 'div', 'li'])
                if parent_container:
                    img = parent_container.find('img')
                    if img:
                        poster_url = img.get('src') or img.get('data-src') or ""

                # Extract year if present
                year_match = re.search(r'\b(20\d\d|19\d\d)\b', raw_text or href)
                year = year_match.group(1) if year_match else ""

                clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|\[.*?\]|\(.*?\)', '', raw_text)
                clean_title = clean_title.strip(' -:|')
                if not clean_title:
                    # Derivation from URL slug
                    slug = href.strip('/').split('/')[-1]
                    clean_title = slug.replace('-', ' ').title()

                seen_urls.add(href)
                discovered.append({
                    "title": clean_title,
                    "url": full_url,
                    "poster": poster_url,
                    "year": year
                })

                if len(discovered) >= limit:
                    break

        logger.info(f"Discovered {len(discovered)} movie listings.")
        return discovered

    def get_movie_details(self, movie_url: str) -> Optional[Dict[str, Any]]:
        """
        Scrapes detailed movie metadata and Google Drive download links.
        Returns complete movie metadata dictionary.
        """
        logger.info(f"Scraping movie details from {movie_url}")
        html = self._fetch_url(movie_url)
        if not html:
            logger.error(f"Failed to fetch movie details from {movie_url}")
            return None

        soup = BeautifulSoup(html, 'html.parser')

        # 1. Title
        title_el = soup.find(['h1', 'h2'])
        raw_title = title_el.text.strip() if title_el else (soup.title.text.strip() if soup.title else "")
        # Clean title
        clean_title = re.sub(
            r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|\[Complete\]|\(.*?\)',
            '',
            raw_title
        ).strip(' -:|')
        if not clean_title:
            slug = movie_url.strip('/').split('/')[-1]
            clean_title = slug.replace('-', ' ').title()

        # 2. Release Year
        year_match = re.search(r'\b(20\d\d|19\d\d)\b', raw_title)
        year = year_match.group(1) if year_match else ""

        # 3. Poster Image
        poster = ""
        # Look for OpenGraph image first
        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content'):
            poster = og_img['content']
        if not poster:
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src') or ""
                if src and any(k in src.lower() for k in ['tmdb', 'amazon', 'upload', 'poster', 'wp-content/uploads']):
                    poster = src
                    break

        # 4. Genre
        genre_tags = []
        for g in soup.find_all('a', href=True):
            if '/genre/' in g['href']:
                genre_tags.append(g.text.strip())
        genre = ", ".join(dict.fromkeys(genre_tags)) if genre_tags else "Action, Drama"

        # 5. Director & Cast
        director = "Not Available"
        cast = "Not Available"
        # Check headings or text
        for h in soup.find_all(['h2', 'h3', 'h4', 'strong', 'b']):
            txt = h.text.strip().lower()
            if 'director' in txt:
                nxt = h.find_next_sibling()
                if nxt:
                    director = nxt.text.strip()
            elif 'cast' in txt:
                nxt = h.find_next_sibling()
                if nxt:
                    cast = nxt.text.strip()

        # 6. Synopsis / Description
        description = ""
        desc_h2 = soup.find(lambda tag: tag.name in ['h2', 'h3'] and 'synopsis' in tag.text.lower())
        if desc_h2:
            p = desc_h2.find_next('p')
            if p:
                description = p.text.strip()
        if not description:
            article = soup.find(['article', '.entry-content'])
            if article:
                p = article.find('p')
                if p and len(p.text.strip()) > 30:
                    description = p.text.strip()
        if not description:
            description = f"{clean_title} ({year}) available for direct download from Google Drive in high definition."

        # 7. Extract Google Drive download links
        download_links = self._extract_download_links(soup, html)

        # 8. Generate unique ID
        movie_id = self._generate_id(f"{clean_title} {year}")

        status = "available" if download_links else "pending"

        movie_data = {
            "id": movie_id,
            "title": clean_title,
            "year": year,
            "genre": genre,
            "director": director,
            "cast": cast,
            "description": description,
            "poster": poster,
            "source_url": movie_url,
            "download_links": download_links,
            "status": status
        }

        logger.info(f"Extracted metadata for '{clean_title}': {len(download_links)} GDrive links found.")
        return movie_data
