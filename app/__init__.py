import os
from flask import Flask
from app.config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure upload folder exists, with fallback for Vercel's read-only file system
    upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/uploads")
    try:
        os.makedirs(upload_folder, exist_ok=True)
    except OSError:
        # Automatically fallback to /tmp if the file system is read-only
        app.config["UPLOAD_FOLDER"] = "/tmp/uploads"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Initialize extensions, blueprints, and database routes here
    # (Keep your existing extension initializations like login_manager, db, etc.)

    return app
