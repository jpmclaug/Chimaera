import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration for Chimera MTG Market Tracker."""

    SECRET_KEY = os.getenv("SECRET_KEY", "chimera-dev-secret-key-mtg")

    # Neon Postgres connection string (e.g. postgresql://user:pass@ep-xyz.us-east-2.aws.neon.tech/neondb?sslmode=require)
    # Standardize scheme if provided as postgres://
    db_url = os.getenv("DATABASE_URL")
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Fallback to local SQLite if DATABASE_URL is not set
    SQLALCHEMY_DATABASE_URI = db_url if db_url else "sqlite:///chimera.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Engine options for Neon Serverless auto-suspend & connection longevity
    # SQLite memory/single connections are handled gracefully as well
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 300,
        }

    # Notifications & Background Job Settings
    PORT = int(os.getenv("PORT", 5050))
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    POLL_INTERVAL_HOURS = int(os.getenv("POLL_INTERVAL_HOURS", 6))

    # eBay Optional Credentials
    EBAY_APP_ID = os.getenv("EBAY_APP_ID", "").strip()
    EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
    EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()
