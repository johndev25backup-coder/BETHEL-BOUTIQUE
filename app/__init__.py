import os
from flask import Flask, jsonify
from app.config import Config
from app.extensions import limiter, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret or secret == "dev-secret-key":
        raise RuntimeError("Set a strong SECRET_KEY in Vercel Environment Variables.")
    app.config["SECRET_KEY"] = secret

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(os.environ.get("VERCEL") == "1"),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    )

    login_manager.init_app(app)
    limiter.init_app(app)

    from app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    @app.get("/")
    def health():
        return jsonify({
            "success": True,
            "application": "Bethel Boutique",
            "message": "Backend is running. Staff authentication endpoint is available.",
        })

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    return app
