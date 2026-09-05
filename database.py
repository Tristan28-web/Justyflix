import os
import json
import shutil
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from config import Config, setup_logger

logger = setup_logger('database')

# Reentrant lock for thread-safe database operations
_db_lock = threading.RLock()


class JSONDatabase:
    """Thread-safe JSON Database for storing movie metadata with automatic backup."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.DATABASE_FILE
        self.backup_dir = Config.BACKUP_DIR
        self.init_db()

    def _default_schema(self) -> Dict[str, Any]:
        """Returns the initial empty database structure."""
        return {
            "movies": {},
            "stats": {
                "total": 0,
                "available": 0,
                "pending": 0,
                "last_scrape": None
            }
        }

    def init_db(self) -> None:
        """Create movies.json if it does not exist."""
        with _db_lock:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            os.makedirs(self.backup_dir, exist_ok=True)
            
            if not os.path.exists(self.db_path):
                logger.info(f"Initializing empty JSON database at {self.db_path}")
                initial_data = self._default_schema()
                self._atomic_write(initial_data)

    def _read_data(self) -> Dict[str, Any]:
        """Reads and parses JSON database safely."""
        with _db_lock:
            if not os.path.exists(self.db_path):
                return self._default_schema()
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "movies" not in data:
                        data["movies"] = {}
                    if "stats" not in data:
                        data["stats"] = {"total": len(data["movies"]), "available": 0, "pending": 0, "last_scrape": None}
                    return data
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"Error reading {self.db_path}: {e}. Attempting recovery from backup.")
                backup_file = os.path.join(self.backup_dir, 'movies.backup.json')
                if os.path.exists(backup_file):
                    try:
                        with open(backup_file, 'r', encoding='utf-8') as bf:
                            return json.load(bf)
                    except Exception as be:
                        logger.error(f"Failed recovery from backup: {be}")
                return self._default_schema()

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        """Performs atomic write with pre-write backup."""
        with _db_lock:
            # Update stats dynamically
            movies = data.get("movies", {})
            total = len(movies)
            available = sum(1 for m in movies.values() if m.get("status") == "available")
            pending = sum(1 for m in movies.values() if m.get("status") == "pending")

            data["stats"] = {
                "total": total,
                "available": available,
                "pending": pending,
                "last_scrape": data.get("stats", {}).get("last_scrape", datetime.utcnow().isoformat())
            }

            # Backup existing file before write
            if os.path.exists(self.db_path):
                try:
                    backup_file = os.path.join(self.backup_dir, 'movies.backup.json')
                    shutil.copy2(self.db_path, backup_file)
                except Exception as ex:
                    logger.warning(f"Failed to create pre-write backup: {ex}")

            # Atomic write to .tmp file then rename
            tmp_path = f"{self.db_path}.tmp"
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                # Atomic replace
                if os.path.exists(self.db_path):
                    os.replace(tmp_path, self.db_path)
                else:
                    os.rename(tmp_path, self.db_path)
            except Exception as e:
                logger.error(f"Failed atomic write to {self.db_path}: {e}")
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                raise

    def save_movie(self, movie: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add or update movie in JSON database.
        Ensures proper timestamp and status fields.
        """
        if not movie or "id" not in movie:
            raise ValueError("Movie data must contain an 'id'")

        with _db_lock:
            data = self._read_data()
            movie_id = str(movie["id"])
            now_iso = datetime.utcnow().isoformat()

            existing = data["movies"].get(movie_id, {})

            # Prepare structured movie entry
            entry = {
                "id": movie_id,
                "title": movie.get("title", existing.get("title", "Untitled")),
                "year": str(movie.get("year", existing.get("year", ""))),
                "genre": movie.get("genre", existing.get("genre", "General")),
                "director": movie.get("director", existing.get("director", "Unknown")),
                "cast": movie.get("cast", existing.get("cast", "Unknown")),
                "description": movie.get("description", existing.get("description", "")),
                "poster": movie.get("poster", existing.get("poster", "")),
                "source_url": movie.get("source_url", existing.get("source_url", "")),
                "download_links": movie.get("download_links", existing.get("download_links", [])),
                "status": movie.get("status", existing.get("status", "available")),
                "scraped_at": existing.get("scraped_at", now_iso),
                "last_checked": now_iso
            }

            # If download links exist, default status to available unless specified
            if entry["download_links"] and entry["status"] == "pending":
                entry["status"] = "available"

            data["movies"][movie_id] = entry
            data["stats"]["last_scrape"] = now_iso
            self._atomic_write(data)
            logger.info(f"Saved movie: {entry['title']} (ID: {movie_id})")
            return entry

    def get_movie(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a movie by its ID."""
        with _db_lock:
            data = self._read_data()
            return data["movies"].get(str(movie_id))

    def get_all_movies(
        self,
        status: Optional[str] = None,
        query: Optional[str] = None,
        genre: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all movies with optional filters.
        Returns sorted by scraped_at / last_checked descending.
        """
        with _db_lock:
            data = self._read_data()
            movie_list = list(data["movies"].values())

            if status:
                movie_list = [m for m in movie_list if m.get("status") == status]

            if genre and genre.lower() != 'all':
                g_lower = genre.lower()
                movie_list = [m for m in movie_list if g_lower in m.get("genre", "").lower()]

            if query:
                q_lower = query.lower()
                movie_list = [
                    m for m in movie_list
                    if q_lower in m.get("title", "").lower()
                    or q_lower in m.get("genre", "").lower()
                    or q_lower in m.get("cast", "").lower()
                    or q_lower in m.get("director", "").lower()
                    or q_lower in m.get("year", "")
                ]

            # Sort latest first
            movie_list.sort(key=lambda x: x.get("scraped_at", ""), reverse=True)
            return movie_list

    def update_status(self, movie_id: str, status: str) -> bool:
        """Update movie status (e.g. available, pending, error)."""
        with _db_lock:
            data = self._read_data()
            movie_id = str(movie_id)
            if movie_id in data["movies"]:
                data["movies"][movie_id]["status"] = status
                data["movies"][movie_id]["last_checked"] = datetime.utcnow().isoformat()
                self._atomic_write(data)
                logger.info(f"Updated movie {movie_id} status to {status}")
                return True
            return False

    def delete_movie(self, movie_id: str) -> bool:
        """Deletes a movie by ID."""
        with _db_lock:
            data = self._read_data()
            movie_id = str(movie_id)
            if movie_id in data["movies"]:
                del data["movies"][movie_id]
                self._atomic_write(data)
                return True
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Return database statistics."""
        with _db_lock:
            data = self._read_data()
            stats = data.get("stats", {})
            total = len(data.get("movies", {}))
            available = sum(1 for m in data.get("movies", {}).values() if m.get("status") == "available")
            pending = sum(1 for m in data.get("movies", {}).values() if m.get("status") == "pending")
            return {
                "total": total,
                "available": available,
                "pending": pending,
                "last_scrape": stats.get("last_scrape")
            }


# Global database instance
db = JSONDatabase()
