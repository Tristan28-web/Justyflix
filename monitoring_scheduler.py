import os
import time
import json
import threading
from datetime import datetime
from typing import Dict, Any
from config import Config, setup_logger
from analytics_tracker import tracker
from telegram_notifier import send_daily_summary_report, is_telegram_configured

logger = setup_logger('monitoring_scheduler')

REPORT_STATE_FILE = os.path.join(os.path.dirname(Config.DATABASE_FILE), 'analytics', 'report_state.json')

_scheduler_running = False
_scheduler_lock = threading.Lock()


def _get_last_report_time() -> float:
    """Reads the timestamp of the last dispatched 24h report."""
    if os.path.exists(REPORT_STATE_FILE):
        try:
            with open(REPORT_STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return float(data.get('last_report_time', 0.0))
        except Exception:
            pass
    return 0.0


def _save_last_report_time(ts: float):
    """Saves the timestamp of the last dispatched 24h report."""
    try:
        os.makedirs(os.path.dirname(REPORT_STATE_FILE), exist_ok=True)
        with open(REPORT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'last_report_time': ts,
                'last_report_iso': datetime.utcfromtimestamp(ts).isoformat()
            }, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save report state: {e}")


def trigger_immediate_daily_report() -> Dict[str, Any]:
    """Manually triggers a full 24h summary report to Telegram."""
    try:
        from telegram_notifier import is_telegram_configured
        if not is_telegram_configured():
            return {
                "success": False,
                "error": "Telegram bot is not configured. Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment."
            }
        stats = tracker.get_summary_stats()
        ok = send_daily_summary_report(stats)
        if ok:
            _save_last_report_time(time.time())
            return {"success": True, "message": "24-hour summary report dispatched successfully to Telegram."}
        else:
            return {"success": False, "error": "Telegram API rejected report dispatch. Check bot permissions."}
    except Exception as e:
        logger.error(f"Manual 24h report trigger error: {e}")
        return {"success": False, "error": str(e)}


def _scheduler_loop():
    """Background monitoring daemon checking for 24-hour cycle completion."""
    logger.info("24-hour Telegram monitoring report scheduler started.")
    
    # Initialize report timestamp on first boot if never set
    last_ts = _get_last_report_time()
    if last_ts == 0.0:
        _save_last_report_time(time.time())

    while True:
        try:
            interval_seconds = getattr(Config, 'DAILY_REPORT_INTERVAL_HOURS', 24) * 3600
            now = time.time()
            last_report = _get_last_report_time()
            elapsed = now - last_report

            if elapsed >= interval_seconds:
                logger.info(f"24 hours elapsed ({elapsed:.0f}s >= {interval_seconds}s). Dispatching daily summary report to Telegram...")
                stats = tracker.get_summary_stats()
                success = send_daily_summary_report(stats)
                if success:
                    _save_last_report_time(now)
                    logger.info("24-hour automated Telegram summary report delivered successfully.")
                else:
                    logger.warning("Could not deliver 24-hour Telegram report. Will retry in 15 minutes.")
                    time.sleep(900)
                    continue

        except Exception as ex:
            logger.error(f"Error in monitoring scheduler loop: {ex}")

        # Sleep 60 seconds before next check
        time.sleep(60)


def start_monitoring_scheduler():
    """Starts the 24-hour monitoring daemon thread if not already running."""
    global _scheduler_running
    with _scheduler_lock:
        if not _scheduler_running:
            _scheduler_running = True
            t = threading.Thread(
                target=_scheduler_loop,
                daemon=True,
                name="Justyflix24hReportScheduler"
            )
            t.start()
            logger.info("Successfully launched Justyflix 24-hour report scheduler thread.")
