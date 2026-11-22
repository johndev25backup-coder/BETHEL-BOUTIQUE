
import os
from flask import Flask, render_template
from app.config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Serverless upload directory
    upload_folder = app.config.get("UPLOAD_FOLDER", "/tmp/uploads")

    try:
        os.makedirs(upload_folder, exist_ok=True)
    except OSError:
        app.config["UPLOAD_FOLDER"] = "/tmp/uploads"
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Register authentication blueprint.
    # The blueprint itself defines /api/auth.
    from app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    # Register admin blueprint.
    # The blueprint itself defines /api/admin.
    from app.admin.routes import admin_bp
    app.register_blueprint(admin_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    # Temporary diagnostic endpoint
    @app.route("/api/health", methods=["GET"])
    def health():
        return {
            "success": True,
            "message": "Bethel Boutique API is running",
            "routes": [
                str(rule)
                for rule in app.url_map.iter_rules()
            ]
        }, 200

    return app
