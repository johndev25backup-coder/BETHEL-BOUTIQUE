import os

def _optional(name, default=""):
    return os.environ.get(name, default)

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    # DATABASE_URL is no longer required since we use Google Sheets
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///fallback.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "AppDatabase")
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
