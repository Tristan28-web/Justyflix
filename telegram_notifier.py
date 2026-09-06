import os
import time
import json
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Dict, Any, Optional
from config import Config, setup_logger

logger = setup_logger('telegram_notifier')

_last_send_time = 0.0
_send_lock = threading.Lock()


_auto_detected_chat_id: Optional[str] = None


def get_telegram_config() -> tuple[str, str]:
    """
    Dynamically retrieves current Telegram Bot Token and Chat ID.
    If chat_id is not explicitly set in environment, automatically queries getUpdates
    to discover the admin chat ID as soon as the user starts a conversation with the bot.
    """
    global _auto_detected_chat_id
    token = (os.environ.get('TELEGRAM_BOT_TOKEN') or Config.TELEGRAM_BOT_TOKEN or '').strip()
    chat_id = (os.environ.get('TELEGRAM_CHAT_ID') or Config.TELEGRAM_CHAT_ID or _auto_detected_chat_id or '').strip()

    # Attempt automatic discovery if bot token is present but chat_id is missing
    if token and not chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            req = urllib.request.Request(url, headers={'User-Agent': 'JustFlixMonitor/1.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                results = data.get('result', [])
                if results:
                    for update in reversed(results):
                        c = update.get('message', {}).get('chat') or update.get('channel_post', {}).get('chat')
                        if c and c.get('id'):
                            _auto_detected_chat_id = str(c['id'])
                            chat_id = _auto_detected_chat_id
                            logger.info(f"Automatically detected Telegram chat ID: {chat_id}")
                            break
        except Exception as ex:
            logger.debug(f"Could not auto-detect Telegram chat ID: {ex}")

    return token, chat_id


def is_telegram_configured() -> bool:
    """Returns True if both bot token and chat ID are populated."""
    token, chat_id = get_telegram_config()
    return bool(token and chat_id)


def send_telegram_message(text: str, parse_mode: str = 'HTML', timeout: int = 10) -> bool:
    """
    Sends a message via official Telegram Bot API (synchronous).
    Includes rate-limiting guard to prevent 429 Too Many Requests.
    """
    global _last_send_time
    token, chat_id = get_telegram_config()

    if not token or not chat_id:
        logger.debug("Telegram credentials not configured. Skipping notification dispatch.")
        return False

    with _send_lock:
        now = time.time()
        elapsed = now - _last_send_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_send_time = time.time()

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True
    }

    try:
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'JustFlixMonitor/1.0'
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode('utf-8')
            res_json = json.loads(resp_body)
            if res_json.get('ok'):
                logger.info("Successfully dispatched Telegram notification.")
                return True
            else:
                logger.warning(f"Telegram API responded with error: {res_json}")
                return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore') if hasattr(e, 'read') else ''
        logger.error(f"Telegram API HTTP error {e.code}: {err_body}")
        return False
    except Exception as ex:
        logger.error(f"Telegram notification exception: {ex}")
        return False


def send_telegram_message_async(text: str, parse_mode: str = 'HTML') -> None:
    """Dispatches a Telegram message asynchronously in a background daemon thread."""
    t = threading.Thread(
        target=send_telegram_message,
        args=(text, parse_mode),
        daemon=True,
        name="TelegramNotifierWorker"
    )
    t.start()


def send_new_visitor_alert(visitor_data: Dict[str, Any]) -> None:
    """
    Immediately alerts the Telegram bot when a genuine NEW user lands on JustFlix.
    Runs asynchronously to ensure zero impact on user request latency.
    """
    if not is_telegram_configured():
        return

    ip = visitor_data.get('ip_masked') or visitor_data.get('ip') or 'Unknown'
    country = visitor_data.get('country') or 'Global'
    device = visitor_data.get('device') or 'Desktop'
    os_name = visitor_data.get('os') or 'Unknown'
    browser = visitor_data.get('browser') or 'Web Browser'
    landing_page = visitor_data.get('landing_page') or '/'
    referrer = visitor_data.get('referrer') or 'Direct'
    timestamp = visitor_data.get('timestamp') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    msg = (
        f"<b>[JUSTFLIX TELEMETRY] NEW VISITOR DETECTED</b>\n"
        f"───────────────────────────────\n"
        f"<b>IP Address:</b> <code>{ip}</code> ({country})\n"
        f"<b>Client:</b> {device} | {os_name} | {browser}\n"
        f"<b>Landing:</b> <code>{landing_page}</code>\n"
        f"<b>Referrer:</b> {referrer}\n"
        f"<b>Timestamp:</b> {timestamp}\n"
        f"───────────────────────────────\n"
        f"<i>Automated alert dispatched by JustFlix Analytics Service.</i>"
    )
    send_telegram_message_async(msg)


def send_daily_summary_report(stats: Dict[str, Any]) -> bool:
    """
    Sends the full 24-hour comprehensive analytics report to Telegram.
    Summarizes unique users, page views, downloads, top releases, and devices.
    """
    if not is_telegram_configured():
        logger.info("Telegram not configured. Skipping 24h summary report dispatch.")
        return False

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    unique_24h = stats.get('unique_visitors_24h', 0)
    pageviews_24h = stats.get('pageviews_24h', 0)
    downloads_24h = stats.get('downloads_24h', 0)
    active_now = stats.get('active_now', 0)
    total_unique = stats.get('total_unique_visitors', 0)
    total_downloads = stats.get('total_downloads', 0)

    # Top downloaded titles list
    top_downloads = stats.get('top_downloads_24h', [])
    top_dl_lines = []
    if top_downloads:
        for idx, item in enumerate(top_downloads[:5], 1):
            title = item.get('title') or item.get('movie_id') or 'Movie'
            count = item.get('count', 1)
            quality = item.get('quality', '')
            q_str = f" [{quality}]" if quality else ""
            top_dl_lines.append(f"  {idx}. <b>{title}</b>{q_str}: {count} download(s)")
    else:
        top_dl_lines.append("  <i>No downloads recorded in this reporting period.</i>")
    top_dl_text = "\n".join(top_dl_lines)

    # Device breakdown
    devices = stats.get('device_breakdown_24h', {})
    mobile_pct = devices.get('mobile_pct', 0)
    desktop_pct = devices.get('desktop_pct', 0)
    tablet_pct = devices.get('tablet_pct', 0)

    # Top referrers
    referrers = stats.get('top_referrers_24h', [])
    ref_lines = []
    if referrers:
        for ref in referrers[:3]:
            dom = ref.get('domain', 'Direct')
            cnt = ref.get('count', 0)
            ref_lines.append(f"  • {dom}: {cnt} visit(s)")
    else:
        ref_lines.append("  • Direct / Social Media")
    ref_text = "\n".join(ref_lines)

    msg = (
        f"<b>[JUSTFLIX] 24-HOUR ANALYTICS SUMMARY REPORT</b>\n"
        f"<b>Reporting Period:</b> 24 Hours Ending {now_str}\n"
        f"───────────────────────────────\n"
        f"<b>TRAFFIC & USER METRICS</b>\n"
        f"• Active Sessions (Current): {active_now}\n"
        f"• Unique Visitors (24h): {unique_24h:,}\n"
        f"• Total Page Views (24h): {pageviews_24h:,}\n"
        f"• Total Unique Visitors (All-Time): {total_unique:,}\n\n"
        f"<b>DOWNLOAD ACTIVITY</b>\n"
        f"• Downloads (24h): {downloads_24h:,}\n"
        f"• Downloads (All-Time): {total_downloads:,}\n\n"
        f"<b>TOP DOWNLOADED TITLES (24H)</b>\n"
        f"{top_dl_text}\n\n"
        f"<b>CLIENT ENVIRONMENT BREAKDOWN</b>\n"
        f"• Desktop: {desktop_pct}%\n"
        f"• Mobile: {mobile_pct}%\n"
        f"• Tablet: {tablet_pct}%\n\n"
        f"<b>PRIMARY TRAFFIC SOURCES</b>\n"
        f"{ref_text}\n"
        f"───────────────────────────────\n"
        f"<i>Automated scheduled report. Next delivery in 24 hours.</i>"
    )
    return send_telegram_message(msg)


def test_telegram_connection() -> Dict[str, Any]:
    """Tests the Telegram Bot connection and sends an immediate test ping."""
    token, chat_id = get_telegram_config()
    if not token:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN is not configured."}
    if not chat_id:
        return {
            "success": False,
            "error": "No Telegram chat detected yet. Please open @justyflixmonitor_bot in your Telegram app, click 'Start' (or send any message), then click 'Test Telegram Ping' again to establish the link."
        }

    test_msg = (
        f"<b>[JUSTFLIX TELEMETRY] CONNECTION VERIFICATION</b>\n"
        f"───────────────────────────────\n"
        f"<b>Status:</b> Operational\n"
        f"<b>Timestamp:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"<b>Gateway:</b> Telegram Bot API\n"
        f"───────────────────────────────\n"
        f"<i>Diagnostic ping verified. Telemetry services are ready for deployment.</i>"
    )
    ok = send_telegram_message(test_msg)
    if ok:
        return {"success": True, "message": "Connection verified. Test message delivered to your Telegram chat."}
    else:
        return {"success": False, "error": "Failed to send message via Telegram API. Check bot token and chat ID."}
