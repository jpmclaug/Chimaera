"""
Chimaera MTG Market Surveillance // Standalone Background Worker
================================================================
Independent background service that scans vendor prices (Scryfall/TCGplayer,
Mighty Meeple, eBay), triggers Discord deal notifications, and synchronizes
telemetry with the database.

Can be deployed on VPS, Docker, Render Worker, Fly.io, Railway, Kubernetes,
or executed as a serverless cron job (AWS Lambda / GitHub Actions).

Usage:
    python worker.py                 # Runs in continuous surveillance daemon mode
    python worker.py --once          # Runs a single scan cycle and exits (cron mode)
    python worker.py --interval 1.5  # Overrides polling cadence (in hours)
    python worker.py --no-notify     # Suppresses Discord webhook deal dispatches
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from app import create_app
from config import Config
from deal_engine import DealEngine
from models import db, SystemSetting

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WORKER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chimaera.worker")

# Global stop flag for graceful shutdown
running = True


def signal_handler(signum, frame):
    """Graceful termination handler for SIGINT/SIGTERM."""
    global running
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.info(f"Received termination signal {sig_name}. Initiating graceful shutdown...")
    running = False


def setup_signal_handlers():
    """Binds OS termination signals."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def run_worker_cycle(deal_engine: DealEngine, notify: bool = True) -> dict:
    """Executes a single surveillance sweep across all watchlist targets."""
    start_time = datetime.now(timezone.utc)
    logger.info("Executing surveillance cycle across monitored target registry...")
    
    try:
        # Update worker heartbeat in DB
        SystemSetting.set_val("worker_heartbeat", start_time.isoformat())
        SystemSetting.set_val("worker_status", "running")

        results = deal_engine.poll_all_cards(notify=notify)

        # MicroCenter Charlotte Surveillance Sync
        mc_summary = None
        if SystemSetting.get_bool("microcenter_poll_enabled", default=True):
            try:
                logger.info("Executing MicroCenter Charlotte inventory sweep in worker...")
                mc_summary = deal_engine.sync_microcenter(notify=notify)
                logger.info(f"MicroCenter sync finished: {mc_summary.get('message', 'Complete')}")
            except Exception as mc_err:
                logger.error(f"Error syncing MicroCenter in worker cycle: {mc_err}")

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        deals_count = sum(1 for r in results if r.get("is_deal"))
        logger.info(
            f"Surveillance cycle completed in {duration:.2f}s: "
            f"{len(results)} targets scanned, {deals_count} active deals detected."
        )

        SystemSetting.set_val("worker_status", "idle")
        return {
            "success": True,
            "count": len(results),
            "deals": deals_count,
            "microcenter": mc_summary,
            "duration": duration,
        }
    except Exception as e:
        logger.error(f"Error during surveillance cycle: {e}", exc_info=True)
        try:
            SystemSetting.set_val("worker_status", "error")
            SystemSetting.set_val("last_poll_status", f"Worker error: {str(e)}")
        except Exception:
            pass
        return {"success": False, "error": str(e)}


def main():
    """Main worker entry point."""
    parser = argparse.ArgumentParser(
        description="Chimaera MTG Market Surveillance - Standalone Daemon / Cron Worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single surveillance sweep and exit immediately (ideal for serverless cron).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Override polling interval in hours (default: dynamically read from database).",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Disable Discord webhook notifications during price polling.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed debug-level logging output.",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    setup_signal_handlers()

    logger.info("=" * 65)
    logger.info("CHIMAERA // STANDALONE TACTICAL INTELLIGENCE WORKER")
    logger.info("Sector: MTG-Core | Version: 1.0")
    logger.info("=" * 65)

    # Initialize Flask app context for DB access
    app = create_app()
    notify = not args.no_notify

    with app.app_context():
        deal_engine = DealEngine(app=app)

        # Single cycle mode
        if args.once:
            logger.info("Operating Mode: SINGLE SWEEP (--once)")
            res = run_worker_cycle(deal_engine, notify=notify)
            sys.exit(0 if res.get("success") else 1)

        # Continuous daemon mode
        logger.info("Operating Mode: CONTINUOUS DAEMON (Adaptive Database Cadence)")
        SystemSetting.set_val("worker_status", "online")

        last_run_timestamp = 0.0

        while running:
            with app.app_context():
                # Check dynamic database settings
                auto_enabled = SystemSetting.get_bool("auto_poll_enabled", default=True)
                
                # Determine interval (CLI override > DB setting > Config fallback)
                if args.interval is not None and args.interval > 0:
                    interval_hours = args.interval
                else:
                    interval_hours = SystemSetting.get_float(
                        "poll_interval_hours",
                        default=Config.POLL_INTERVAL_HOURS,
                    )
                
                # Ensure minimum sanity interval (0.01 hours = ~36 seconds)
                interval_hours = max(0.01, interval_hours)
                interval_seconds = interval_hours * 3600.0

                current_time = time.time()
                time_since_last_run = current_time - last_run_timestamp

                # Check if manual poll requested via DB flag
                manual_requested = SystemSetting.get_val("manual_poll_requested") == "true"

                should_run = False
                if manual_requested:
                    logger.info("Manual surveillance trigger detected in database.")
                    SystemSetting.set_val("manual_poll_requested", "false")
                    should_run = True
                elif auto_enabled and (time_since_last_run >= interval_seconds or last_run_timestamp == 0.0):
                    should_run = True

                if should_run:
                    run_worker_cycle(deal_engine, notify=notify)
                    last_run_timestamp = time.time()

                # Update worker heartbeat
                SystemSetting.set_val("worker_heartbeat", datetime.now(timezone.utc).isoformat())

            # Sleep in responsive slices (5 seconds) to allow graceful shutdown
            # and rapid detection of UI cadence/manual trigger changes
            sleep_slice = 5
            for _ in range(int(sleep_slice)):
                if not running:
                    break
                time.sleep(1)

        logger.info("Worker process terminated cleanly.")
        with app.app_context():
            try:
                SystemSetting.set_val("worker_status", "offline")
            except Exception:
                pass


if __name__ == "__main__":
    main()
