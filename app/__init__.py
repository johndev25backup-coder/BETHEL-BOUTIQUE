import os
from flask import Flask
from app.config import Config
from app.extensions import limiter, login_manager  # Remove db, migrate if fully on Google Sheets


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure a static SECRET_KEY is set in Vercel Environment Variables
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-fallback-key")

    login_manager.init_app(app)
    limiter.init_app(app)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("VERCEL") == "1" or app.config.get("SESSION_COOKIE_SECURE", False),
    )

    @login_manager.user_loader
    def load_user(user_id):
        # Replace SQLAlchemy lookup with your Google Sheets user lookup
        from app.sheets_db import get_user_by_id
        return get_user_by_id(str(user_id))

    login_manager.login_view = None

    from app.auth.routes import auth_bp
    from app.products.routes import products_bp
    from app.cart.routes import cart_bp
    from app.orders.routes import orders_bp
    from app.payments.routes import payments_bp
    from app.admin.routes import admin_bp
    from app.loyalty.routes import loyalty_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(cart_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(loyalty_bp)

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # Do NOT start background schedulers inside Vercel serverless functions
    if os.environ.get("VERCEL") != "1":
        from app.jobs.scheduler import init_scheduler
        init_scheduler(app)

    return app
