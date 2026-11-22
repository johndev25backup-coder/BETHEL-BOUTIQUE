
import os

from flask import Flask, render_template
from app.config import Config
from app.extensions import limiter, login_manager


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static"
    )

    # Load application configuration
    app.config.from_object(config_class)

    # Require a secure production secret
    secret = os.environ.get("SECRET_KEY", "").strip()

    if not secret or secret == "dev-secret-key":
        raise RuntimeError(
            "Set a strong SECRET_KEY in Vercel Environment Variables."
        )

    app.config["SECRET_KEY"] = secret

    # Secure session configuration
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(os.environ.get("VERCEL") == "1"),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    )

    # Initialize Flask extensions
    login_manager.init_app(app)
    limiter.init_app(app)

    # IMPORTANT:
    # The current staff authentication uses Flask sessions and Google Sheets.
    # It does not yet use Flask-Login user objects.
    # Returning None allows unauthenticated visitors to view the homepage.
    @login_manager.user_loader
    def load_user(user_id):
        return None

    # Register staff authentication routes
    from app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    # Homepage: render the original storefront interface
    @app.route("/", methods=["GET"])
    def home():
        return render_template("index.html")

    # Health-check endpoint
    @app.route("/health", methods=["GET"])
    def health():
        return {
            "success": True,
            "application": "Bethel Boutique",
            "message": "Backend is running."
        }, 200

    # Security response headers
    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        response.headers["Cache-Control"] = "no-store"

        return response

    return app
