import os
import secrets
import warnings

# Vercel sets the VERCEL environment variable automatically on every deployment.
ON_VERCEL = bool(os.environ.get("VERCEL"))


def _require(name, local_default=None):
    """
    Return the environment variable `name`.

    On Vercel, a missing value raises a clear error so the deployment logs say
    exactly what is wrong. Locally, fall back to `local_default` (a value or a
    zero-argument callable) so the app still starts for demo/testing.
    """
    value = os.environ.get(name)
    if value:
        return value
    if ON_VERCEL:
        raise RuntimeError(
            f"{name} must be set in Vercel -> Project Settings -> Environment Variables"
        )
    warnings.warn(
        f"{name} not set - using a local-only fallback. "
        f"Set {name} in your .env before production."
    )
    return local_default() if callable(local_default) else local_default


def _bool(name, default="false"):
    return os.environ.get(name, default).strip().lower() == "true"


class Config:
    """
    All secrets are read from environment variables, never hardcoded.
    Locally: load a real .env file (see .env.example) before running the app.
    On Vercel: set them under Project Settings -> Environment Variables.
    """

    # --- Core security ---
    # Random fallbacks are fine locally but NOT on serverless (each instance
    # would get a different key), so on Vercel these are required.
    SECRET_KEY = _require("SECRET_KEY", lambda: secrets.token_hex(32))
    ADMIN_API_KEY = _require("ADMIN_API_KEY", lambda: secrets.token_hex(32))
    PUBLIC_API_KEY = _require("PUBLIC_API_KEY", lambda: secrets.token_hex(16))

    # --- Database ---
    # SQLite is for local testing only. Vercel's filesystem is read-only, and
    # SQLite can't safely handle concurrent writes for the stock-locking logic.
    _db_url = _require("DATABASE_URL", "sqlite:///boutique_demo.db")
    # SQLAlchemy needs "postgresql://", but some providers hand out "postgres://".
    if _db_url.startswith("postgres://"):
           _db_url = _db_url.replace("postgresql+psycopg://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Serverless functions can sit idle and lose DB connections; this checks a
    # connection is alive before using it.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- Uploads ---
    # /tmp is the only writable location on Vercel, and it is ephemeral and not
    # shared between instances. Use object storage (Vercel Blob, S3, Cloudinary)
    # for product images that must persist.
    UPLOAD_FOLDER = os.path.join("/tmp", "bethel_boutique", "uploads")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

    # --- Sessions / cookies ---
    # Vercel serves over HTTPS, so default to secure cookies there.
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", "true" if ON_VERCEL else "false")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # --- Payments: demo/testing mode ---
    # When true, payment "initiation" instantly succeeds with a fake reference
    # instead of calling MTN/Airtel/M-Pesa. Defaults to FALSE so it can never be
    # on by accident; set PAYMENT_MOCK_MODE=true explicitly for testing.
    PAYMENT_MOCK_MODE = _bool("PAYMENT_MOCK_MODE", "false")

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
    # Must be a public HTTPS URL on your deployed domain, not localhost.
    MPESA_CALLBACK_URL = os.environ.get("MPESA_CALLBACK_URL")

    # --- Cron (stock reservation cleanup) ---
    # Serverless functions can't run background timers, so expired reservations
    # are released by a Vercel Cron Job calling a protected endpoint.
    # Vercel sends "Authorization: Bearer <CRON_SECRET>" if CRON_SECRET is set.
    CRON_SECRET = os.environ.get("CRON_SECRET")

    # --- Business rules (owner-configurable defaults) ---
    # Move to a settings table later if the owner should edit these from the dashboard.
    LOYALTY_MIN_PURCHASE_UGX = 20000
    LOYALTY_MIN_POINTS_AWARDED = 4000
    STOCK_RESERVATION_MINUTES = 45
    RETURN_WINDOW_DAYS = 2
