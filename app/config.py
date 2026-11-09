import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    
    # DATABASE_URL is optional now that you are using Google Sheets
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///fallback.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Google Sheets Configuration
    GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "AppDatabase")
    GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    
    # File upload storage path
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static/uploads")
