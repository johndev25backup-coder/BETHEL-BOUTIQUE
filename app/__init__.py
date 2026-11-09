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
        app.config["UPLOAD_FOLDER"] = "/tmp/uploads"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # --- Register Blueprints ---
    try:
        from app.admin.routes import admin_bp
        app.register_blueprint(admin_bp)
    except ImportError:
        pass

    try:
        from app.auth.routes import auth_bp
        app.register_blueprint(auth_bp, url_prefix="/auth")
    except ImportError:
        pass

    try:
        from app.main.routes import main_bp
        app.register_blueprint(main_bp)
    except ImportError:
        pass

    # --- Root Route ---
    @app.route("/")
    def index():
        return "Bethel Boutique Flask App is online and running successfully!"

    return app
