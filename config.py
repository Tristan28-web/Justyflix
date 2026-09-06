import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Base project directory
BASE_DIR = Path(__file__).resolve().parent

# Load .env if present
load_dotenv(dotenv_path=BASE_DIR / '.env')


def _resolve_data_path(env_var: str, default_filename: str) -> str:
    """
    Resolve data path:
    If running in Docker/Render Linux where /data exists and is writable, use it.
    Otherwise fallback to local BASE_DIR/data for Windows or non-root environments.
    """
    env_val = os.getenv(env_var)
    if env_val:
        # Check if directory of specified path is writable or can be created
        target_dir = os.path.dirname(env_val)
        if target_dir and (target_dir.startswith('/data') or target_dir.startswith('\\data')):
            try:
                os.makedirs(target_dir, exist_ok=True)
                # Test write permission
                test_file = os.path.join(target_dir, '.write_test')
                with open(test_file, 'w') as f:
                    f.write('ok')
                os.remove(test_file)
                return env_val
            except (OSError, PermissionError):
                pass
        elif target_dir:
            try:
                os.makedirs(target_dir, exist_ok=True)
                return env_val
            except (OSError, PermissionError):
                pass

    # Fallback to local BASE_DIR / 'data'
    local_dir = BASE_DIR / 'data'
    local_dir.mkdir(parents=True, exist_ok=True)
    return str(local_dir / default_filename)


class Config:
    """Application Configuration Class"""
    
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-dev-secret-key-change-in-production')
    PORT = int(os.getenv('PORT', 10000))
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')

    # Data & Logging Paths
    DATABASE_FILE = _resolve_data_path('DATABASE_FILE', 'movies.json')
    METADATA_FILE = os.getenv('METADATA_FILE', DATABASE_FILE)
    
    # Ensure backup directory exists
    BACKUP_DIR = os.path.join(os.path.dirname(DATABASE_FILE), 'backup')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    # Log File Setup
    _log_env = os.getenv('LOG_FILE')
    if _log_env and not (_log_env.startswith('/data') and sys.platform.startswith('win')):
        LOG_FILE = _log_env
    else:
        LOG_FILE = os.path.join(os.path.dirname(DATABASE_FILE), 'logs', 'app.log')
    
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Scraping Configuration
    SCRAPE_INTERVAL_HOURS = int(os.getenv('SCRAPE_INTERVAL_HOURS', 6))
    MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))
    REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', 30))
    MWLBD_BASE_URL = os.getenv('MWLBD_BASE_URL', 'https://fojik.site').rstrip('/')
    FALLBACK_MIRRORS = [
        'https://fojik.site',
        'https://mlwbd.is',
        'https://mwlbd.com'
    ]

    # Google Drive Direct Mode
    GOOGLE_DRIVE_DIRECT = os.getenv('GOOGLE_DRIVE_DIRECT', 'true').lower() in ('true', '1', 'yes')

    # Telegram Bot Monitoring & Alerts
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    MONITOR_ACCESS_KEY = os.getenv('MONITOR_ACCESS_KEY', '').strip()
    DAILY_REPORT_INTERVAL_HOURS = int(os.getenv('DAILY_REPORT_INTERVAL_HOURS', 24))


def setup_logger(name: str = 'mwlbd_app') -> logging.Logger:
    """Configures structured logger with console and rotating/file output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Stream Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File Handler
        try:
            file_handler = logging.FileHandler(Config.LOG_FILE, encoding='utf-8')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Could not initialize file logging at {Config.LOG_FILE}: {e}")

    return logger
