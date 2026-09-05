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
                found_row_link = False
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
                            found_row_link = True

                # If no direct link was found, inspect form in table row (MWLBD standard)
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
                                "size": size
                            })

        # 3. Look for any other <a> tags containing drive.google.com
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('drive.google.com' in href or 'docs.google.com' in href) and href not in seen_urls:
                seen_urls.add(href)
                text = a.text.strip() or "Download Google Drive"
                links.append(self._format_gdrive_link(href, label=text))

        return links

    def get_movies_from_page(self, page_num: int = 1) -> List[Dict[str, Any]]:
        """
        Scrapes all movie listings from a specific archive page number.
        E.g. /movie/ (page 1) or /movie/page/2/ etc.
        """
        page_url = f"{self.base_url}/movie/" if page_num <= 1 else f"{self.base_url}/movie/page/{page_num}/"
        logger.info(f"Scraping catalog page {page_num}: {page_url}")
        html = self._fetch_url(page_url)
        if not html:
            logger.warning(f"Failed to fetch catalog page {page_num}")
            return []

        soup = BeautifulSoup(html, 'html.parser')
        discovered: List[Dict[str, Any]] = []
        seen_urls = set()

        from database import is_2026_or_future

        # 1. First parse structured article items if available
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

                clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|\[.*?\]|\(.*?\)', '', raw_text).strip(' -:|')
                if not clean_title:
                    clean_title = href.strip('/').split('/')[-1].replace('-', ' ').title()

                candidate = {
                    "title": clean_title,
                    "url": full_url,
                    "source_url": full_url,
                    "poster": poster_url,
                    "year": year_text or "2026"
                }

                if not is_2026_or_future(candidate):
                    continue

                seen_urls.add(href)
                discovered.append(candidate)

        # 2. Fallback to general link parsing if articles weren't found
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
                    poster_url = ""
                    parent_container = a.find_parent(['article', 'div', 'li'])
                    if parent_container:
                        img = parent_container.find('img')
                        if img:
                            poster_url = img.get('src') or img.get('data-src') or ""

                    year_match = re.search(r'\b(20\d\d|19\d\d)\b', raw_text or href)
                    year = year_match.group(1) if year_match else ""

                    clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|\[.*?\]|\(.*?\)', '', raw_text).strip(' -:|')
                    if not clean_title:
                        clean_title = slug.replace('-', ' ').title()

                    candidate = {
                        "title": clean_title,
                        "url": full_url,
                        "source_url": full_url,
                        "poster": poster_url,
                        "year": year
                    }

                    if not is_2026_or_future(candidate):
                        continue

                    seen_urls.add(href)
                    discovered.append(candidate)

        logger.info(f"Page {page_num}: discovered {len(discovered)} 2026+ movies.")
        return discovered

    def get_total_catalog_pages(self) -> int:
        """Determines the maximum number of archive pages on MWLBD."""
        catalog_url = f"{self.base_url}/movie/"
        html = self._fetch_url(catalog_url)
        if not html:
            return 1
        soup = BeautifulSoup(html, 'html.parser')
        span = soup.select_one('.pagination span')
        if span:
            match = re.search(r'Page\s+\d+\s+of\s+(\d+)', span.text, re.I)
            if match:
                return int(match.group(1))

        # Fallback to checking page numbers in links
        page_nums = []
        for a in soup.find_all('a', href=True):
            if '/movie/page/' in a['href']:
                m = re.search(r'/page/(\d+)', a['href'])
                if m:
                    page_nums.append(int(m.group(1)))
        return max(page_nums) if page_nums else 487

    def get_total_2026_pages(self) -> int:
        """Determines the number of pages in the dedicated /release/2026/ archive."""
        url = f"{self.base_url}/release/2026/"
        html = self._fetch_url(url)
        if not html:
            return 13
        soup = BeautifulSoup(html, 'html.parser')
        span = soup.select_one('.pagination span')
        if span:
            match = re.search(r'Page\s+\d+\s+of\s+(\d+)', span.text, re.I)
            if match:
                return int(match.group(1))
        return 13

    def crawl_2026_archive(
        self,
        start_page: int = 1,
        end_page: Optional[int] = None,
        concurrency: int = 5,
        progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Crawls every page in the dedicated /release/2026/ archive.
        Ingests all 2026 new releases into movies.json with posters, titles, and URLs.
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from database import db, is_2026_or_future

        if not end_page:
            end_page = self.get_total_2026_pages()

        logger.info(f"Starting crawl of dedicated 2026 releases: Pages {start_page} to {end_page}...")
        total_movies_indexed = 0
        pages_processed = 0

        def scrape_page(p: int) -> List[Dict[str, Any]]:
            url = f"{self.base_url}/release/2026/page/{p}/" if p > 1 else f"{self.base_url}/release/2026/"
            p_html = self._fetch_url(url)
            if not p_html:
                return []
            p_soup = BeautifulSoup(p_html, 'html.parser')
            items = []
            for article in p_soup.select('article.item'):
                title_el = article.select_one('h3 a')
                year_el = article.select_one('.data span')
                img_el = article.select_one('.poster img')
                if not title_el:
                    continue

                raw_title = title_el.text.strip()
                movie_url = urljoin(self.base_url, title_el.get('href', ''))
                poster_url = (img_el.get('src') or img_el.get('data-src') or "") if img_el else ""
                clean_title = re.sub(r'Dual Audio.*|WEB-DL.*|HDRip.*|NF.*|480p.*|720p.*|1080p.*|GDRive.*|\[.*?\]|\(.*?\)', '', raw_title).strip(' -:|')
                if not clean_title:
                    clean_title = movie_url.strip('/').split('/')[-1].replace('-', ' ').title()

                slug_id = self._generate_id(f"{clean_title} 2026")

                # Detect genre keywords accurately
                detected_genres = []
                title_lower = raw_title.lower()
                
                # Check for Hollywood / Dual Audio English
                if re.search(r'\[hindi org & eng\]|\[eng & hindi\]|\[hindi & eng\]|\[hindi dubbed\]', title_lower):
                    detected_genres.append("Hollywood English")
                    detected_genres.append("Dual Audio")
                elif re.search(r'\[hindi org & tamil\]|\[tamil & hindi\]', title_lower):
                    detected_genres.append("Tamil")
                    detected_genres.append("Dual Audio")
                elif re.search(r'\[hindi org & telugu\]|\[telugu & hindi\]', title_lower):
                    detected_genres.append("Telugu")
                    detected_genres.append("Dual Audio")
                elif re.search(r'\[hindi org & korean\]|\[korean & hindi\]', title_lower):
                    detected_genres.append("Korean")
                    detected_genres.append("Dual Audio")
                elif "hindi" in title_lower and not any(k in title_lower for k in ['org & eng', 'org & tamil', 'org & telugu', 'org & korean']):
                    detected_genres.append("Bollywood Hindi")

                for kw, g in [("action", "Action"), ("adventure", "Adventure"), ("horror", "Horror"), ("comedy", "Comedy"), ("drama", "Drama"), ("crime", "Crime"), ("thriller", "Thriller"), ("series", "TV Series"), ("anime", "Anime")]:
                    if kw in title_lower and g not in detected_genres:
                        detected_genres.append(g)
                        
                genre = ", ".join(detected_genres) if detected_genres else "General"

                candidate = {
                    "id": slug_id,
                    "title": clean_title,
                    "year": "2026",
                    "genre": genre,
                    "director": "Unknown",
                    "cast": "Unknown",
                    "description": f"{raw_title} (2026 New Release)",
                    "poster": poster_url,
                    "source_url": movie_url,
                    "download_links": [],
                    "status": "pending"
                }
                if is_2026_or_future(candidate):
                    items.append(candidate)
            return items

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_page = {executor.submit(scrape_page, p): p for p in range(start_page, end_page + 1)}
            for future in as_completed(future_to_page):
                try:
                    movies = future.result()
                    if movies:
                        saved = db.save_movies_batch(movies)
                        total_movies_indexed += saved
                    pages_processed += 1
                    if progress_callback:
                        progress_callback(pages_processed, end_page - start_page + 1, total_movies_indexed)
                except Exception as e:
                    logger.error(f"Error crawling 2026 page: {e}")

        logger.info(f"Completed 2026 archive crawl: {pages_processed} pages, {total_movies_indexed} movies indexed. Total in DB: {db.get_stats()['total']}")
        return {
            "pages_crawled": pages_processed,
            "total_movies": total_movies_indexed
        }

    def crawl_all_catalog_pages(
        self,
        start_page: int = 1,
        end_page: Optional[int] = None,
        concurrency: int = 5,
        progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Crawls every catalog page across the entire MWLBD archive (up to end_page / 487+ pages).
        Ingests all movies in high-throughput batches into movies.json.
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from database import db

        if not end_page:
            end_page = self.get_total_catalog_pages()

        logger.info(f"Starting FULL SITE CRAWL: Pages {start_page} to {end_page} (~{end_page * 45} movies)...")
        total_movies_indexed = 0
        pages_processed = 0

        # Crawl pages in batches of 5
        batch_size = 5
        for p_start in range(start_page, end_page + 1, batch_size):
            p_end = min(p_start + batch_size - 1, end_page)
            pages_to_fetch = list(range(p_start, p_end + 1))

            page_movies = []
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_to_page = {executor.submit(self.get_movies_from_page, p): p for p in pages_to_fetch}
                for future in as_completed(future_to_page):
                    try:
                        movies_on_page = future.result()
                        # Enrich each movie card with slug id and metadata
                        for m in movies_on_page:
                            slug_id = self._generate_id(f"{m['title']} {m['year']}")
                            m["id"] = slug_id
                            if "genre" not in m or not m["genre"]:
                                # Infer genre from title keywords
                                title_lower = m["title"].lower()
                                detected_genres = []
                                for kw, g in [("hindi", "Bollywood"), ("dual audio", "Dual Audio"), ("dubbed", "Hindi Dubbed"), ("action", "Action"), ("horror", "Horror"), ("comedy", "Comedy"), ("series", "TV Series"), ("anime", "Anime")]:
                                    if kw in title_lower:
                                        detected_genres.append(g)
                                m["genre"] = ", ".join(detected_genres) if detected_genres else "General"
                        page_movies.extend(movies_on_page)
                    except Exception as e:
                        logger.error(f"Error fetching page batch: {e}")

            if page_movies:
                saved = db.save_movies_batch(page_movies)
                total_movies_indexed += saved

            pages_processed += len(pages_to_fetch)
            if progress_callback:
                progress_callback(pages_processed, end_page - start_page + 1, total_movies_indexed)

            logger.info(f"Progress: {pages_processed}/{end_page - start_page + 1} pages scraped. Total movies in catalog: {db.get_stats()['total']}")
            time.sleep(0.2)  # Respectful pacing between page batches

        return {
            "pages_crawled": pages_processed,
            "total_movies": total_movies_indexed
        }

    def get_latest_movies(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Scrapes movie listings from homepage and catalog.
        If limit is None, returns all listings on page 1.
        """
        movies = self.get_movies_from_page(1)
        return movies[:limit] if limit else movies

    def crawl_catalog(
        self,
        start_page: int = 1,
        num_pages: int = 1,
        concurrency: int = 4,
        on_movie_saved: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Crawls multiple catalog pages concurrently and saves metadata & GDrive links into database.
        Zero movie files are stored locally.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from database import db

        total_scraped = 0
        total_errors = 0
        all_movies_to_fetch = []

        logger.info(f"Starting catalog crawl from page {start_page} for {num_pages} page(s)...")

        for page in range(start_page, start_page + num_pages):
            page_listings = self.get_movies_from_page(page)
            if not page_listings:
                logger.warning(f"No listings found on page {page}. Halting crawl.")
                break
            all_movies_to_fetch.extend(page_listings)

        logger.info(f"Discovered {len(all_movies_to_fetch)} unique movies across pages. Fetching full details concurrently...")

        def _fetch_and_save(item):
            try:
                details = self.get_movie_details(item["url"])
                if details:
                    db.save_movie(details)
                    if on_movie_saved:
                        on_movie_saved(details)
                    return True
            except Exception as e:
                logger.error(f"Error crawling {item.get('title')}: {e}")
            return False

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_movie = {executor.submit(_fetch_and_save, m): m for m in all_movies_to_fetch}
            for future in as_completed(future_to_movie):
                if future.result():
                    total_scraped += 1
                else:
                    total_errors += 1

        logger.info(f"Catalog crawl completed: {total_scraped} saved, {total_errors} errors.")
        return {
            "total_scraped": total_scraped,
            "total_errors": total_errors,
            "pages_crawled": num_pages
        }

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
        sg = soup.find('div', class_='sgeneros')
        if sg:
            for a in sg.find_all('a'):
                t = a.get_text().strip()
                if t and not any(res in t for res in ['1080p', '4K', '720p', 'HEVC', 'Full HD', '2K']):
                    genre_tags.append(t)
        if not genre_tags:
            for g in soup.find_all('a', href=True):
                if '/genre/' in g['href']:
                    t = g.text.strip()
                    if t and not any(res in t for res in ['1080p', '4K', '720p', 'HEVC', 'Full HD', '2K']):
                        genre_tags.append(t)
        genre = ", ".join(dict.fromkeys(genre_tags)) if genre_tags else "General"

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
