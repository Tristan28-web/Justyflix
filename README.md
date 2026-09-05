# MWLBD Google Drive Direct Download Movie Automation

A complete movie automation web application that scrapes movie metadata and Google Drive download links from MWLBD (and its active mirrors like Fojik), serving direct downloads from Google Drive with **zero local server storage** and **no SQLite database**.

Optimized for **Render.com** deployment with online access.

---

## Key Features

- **Direct Google Drive Download**: Movies are served directly from Google Drive's worldwide CDN. Users experience maximum download speeds with zero server bandwidth usage.
- **Zero Local Server Storage**: No movie files are ever saved on the server (0 disk cost).
- **JSON-Only Database (`movies.json`)**: Lightweight, thread-safe database with atomic writes and automatic pre-write backups. No SQLite required.
- **Background Worker**: APScheduler automatically scrapes new movie releases and updates Google Drive links every 6 hours.
- **Render Ready**: Includes complete `render.yaml` Blueprint, `Dockerfile`, and `Procfile` configured for Render Web and Worker services.
- **Modern Responsive Dark UI**: Neon-accented Bootstrap 5 interface with real-time search filtering, genre tags, and stream preview player.

---

## Directory Structure

```text
├── config.py             # Configuration & cross-platform path resolution
├── database.py           # Thread-safe JSON database with atomic writes & backup
├── scraper.py            # MWLBDScraper extracting metadata & Google Drive links
├── website.py            # Flask web application & REST API endpoints
├── worker.py             # APScheduler background worker daemon
├── requirements.txt      # Pinned Python dependencies
├── render.yaml           # Render.com Blueprint configuration
├── Dockerfile            # Container configuration
├── Procfile              # Process definitions for web & worker
├── .env.example          # Environment variables template
├── static/
│   ├── css/style.css     # Custom dark neon stylesheet
│   └── js/main.js        # Real-time search, clipboard copy & AJAX scraper
└── templates/
    ├── base.html         # Base HTML layout & navigation
    ├── index.html        # Responsive movie grid & search filter
    ├── movie_detail.html # Full movie metadata & direct download buttons
    └── scrape.html       # Scraper control dashboard & discovered links
```

---

## Google Drive Direct Link Formats

The application automatically parses and normalizes Google Drive URLs:

- **Standard View**: `https://drive.google.com/file/d/FILE_ID/view`
- **Share Link**: `https://drive.google.com/file/d/FILE_ID/view?usp=sharing`
- **Direct Download**: `https://drive.google.com/uc?export=download&id=FILE_ID`
- **Embedded Stream**: `https://drive.google.com/file/d/FILE_ID/preview`

---

## Deployment on Render.com

### Option 1: Render Blueprint (Recommended)
1. Push this repository to GitHub or GitLab.
2. In your Render Dashboard, click **New +** -> **Blueprint**.
3. Connect your repository. Render will automatically read `render.yaml` and create:
   - **Web Service (`mwlbd-gdrive-web`)**: Flask + Gunicorn on port `$PORT`.
   - **Worker Service (`mwlbd-gdrive-worker`)**: Standalone background scraper running every 6 hours.
   - **Persistent Disk (`mwlbd-data`)**: 1 GB disk mounted at `/data` for `movies.json` and logs.

### Option 2: Docker Deployment
1. Build container:
   ```bash
   docker build -t mwlbd-gdrive .
   ```
2. Run container:
   ```bash
   docker run -p 10000:10000 mwlbd-gdrive
   ```

---

## Local Development

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Configure environment**:
   ```bash
   cp .env.example .env
   ```
3. **Start the Web Interface**:
   ```bash
   python website.py
   ```
   Open your browser at `http://localhost:10000`.

4. **Start the Background Worker (optional)**:
   ```bash
   python worker.py
   ```

---

## API Documentation

- `GET /api/movies`: Returns all indexed movies as JSON (supports `?status=available`, `?query=...`, `?genre=...`).
- `GET /api/movie/<movie_id>`: Returns full metadata for an individual movie.
- `POST /api/scrape?limit=15`: Initiates a background scraping task.
- `GET /api/stats`: Returns database statistics (`total`, `available`, `pending`, `last_scrape`).
