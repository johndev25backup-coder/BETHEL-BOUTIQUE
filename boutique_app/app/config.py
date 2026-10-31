import os
import secrets
import warnings


class Config:
    """
    All secrets are read from environment variables, never hardcoded.
    Load a real .env file (see .env.example) before running the app.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        # Auto-generates a throwaway key so the app can start immediately
        # for local demo/testing. This key changes every restart, which
        # logs everyone out each time - fine for testing, not for
        # production. Set a real SECRET_KEY in .env before going live.
        warnings.warn(
            "SECRET_KEY not set - using a temporary generated key for this "
            "run only. Set SECRET_KEY in your .env before production."
        )
        SECRET_KEY = secrets.token_hex(32)

    # Falls back to a local SQLite file if DATABASE_URL isn't set, so the
    # app can run immediately for local testing with zero database setup.
    # Switch to a real DATABASE_URL (PostgreSQL) before going anywhere near
    # production - SQLite doesn't handle concurrent writes safely enough
    # for the stock-locking logic once real customers are hitting it.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///boutique_demo.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Demo/testing mode: when true, payment "initiation" instantly succeeds
    # with a fake reference instead of calling MTN/Airtel/M-Pesa - lets you
    # test the full checkout -> payment -> points -> tracking flow before
    # you have real sandbox credentials. MUST be false in production.
    PAYMENT_MOCK_MODE = os.environ.get("PAYMENT_MOCK_MODE", "true").lower() == "true"

    # --- Admin console key: server-side only, see .env.example for rules ---
    # Same demo-convenience pattern as SECRET_KEY above: auto-generated if
    # missing so the app starts, but a real fixed value belongs in .env
    # before this is exposed to the internet.
    ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY") or secrets.token_hex(32)
    PUBLIC_API_KEY = os.environ.get("PUBLIC_API_KEY") or secrets.token_hex(16)

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

    # --- MTN MoMo ---
    MTN_MOMO_BASE_URL = os.environ.get("MTN_MOMO_BASE_URL")
    MTN_MOMO_SUBSCRIPTION_KEY = os.environ.get("MTN_MOMO_SUBSCRIPTION_KEY")
    MTN_MOMO_API_USER = os.environ.get("MTN_MOMO_API_USER")
    MTN_MOMO_API_KEY = os.environ.get("MTN_MOMO_API_KEY")
    MTN_MOMO_TARGET_ENVIRONMENT = os.environ.get("MTN_MOMO_TARGET_ENVIRONMENT", "sandbox")

    # --- Airtel Money ---
    AIRTEL_BASE_URL = os.environ.get("AIRTEL_BASE_URL")
    AIRTEL_CLIENT_ID = os.environ.get("AIRTEL_CLIENT_ID")
    AIRTEL_CLIENT_SECRET = os.environ.get("AIRTEL_CLIENT_SECRET")

    # --- M-Pesa Daraja ---
    MPESA_BASE_URL = os.environ.get("MPESA_BASE_URL")
    MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY")
    MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET")
    MPESA_SHORTCODE = os.environ.get("MPESA_SHORTCODE")
    MPESA_PASSKEY = os.environ.get("MPESA_PASSKEY")
    MPESA_CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL")

    # Business rules (owner-configurable defaults; move to a settings table
    # later if you want the owner to edit these from the dashboard)
    LOYALTY_MIN_PURCHASE_UGX = 20000
    LOYALTY_MIN_POINTS_AWARDED = 4000
    STOCK_RESERVATION_MINUTES = 45
    RETURN_WINDOW_DAYS = 2
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    
