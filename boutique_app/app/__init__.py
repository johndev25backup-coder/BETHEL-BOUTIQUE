from flask import Flask

from app.config import Config
from app.extensions import db, limiter, login_manager, migrate


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)

    # Session cookie hardening
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app.config["SESSION_COOKIE_SECURE"],
    )
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return db.session.get(User, int(user_id))

    login_manager.login_view = None  # this is an API; return 401 JSON instead of redirecting

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
        # Basic hardening headers - a proper reverse proxy (nginx/Render/etc)
        # should also enforce HTTPS in front of this.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    from app.jobs.scheduler import init_scheduler
    init_scheduler(app)

    return app
