import os
from flask import Flask, render_template
from app.config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure upload directory exists for serverless runtime
    upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/uploads")
    try:
        os.makedirs(upload_folder, exist_ok=True)
    except OSError:
        app.config["UPLOAD_FOLDER"] = "/tmp/uploads"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # --- Register Blueprints with /api prefixes ---
    try:
        from app.auth.routes import auth_bp
        app.register_blueprint(auth_bp, url_prefix="/api/auth")
    except ImportError:
        pass

    try:
        from app.admin.routes import admin_bp
        app.register_blueprint(admin_bp, url_prefix="/api/admin")
    except ImportError:
        pass

    # --- Root View Route ---
    @app.route("/")
    def index():
        return render_template("index.html")

    return app
