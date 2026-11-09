import os
import bcrypt

from flask import Flask, render_template, request, send_from_directory
from sqlalchemy import inspect, text

from app.config import Config
from app.extensions import db, limiter, login_manager, migrate


def create_app(config_class=Config):
    app = Flask(__name__, template_folder=".")
    app.config.from_object(config_class)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    from app import models as _models
    from app.models import User, UserRole

    with app.app_context():
        db.create_all()
        
        # Schema migration checks
        for table_name, column_name, column_sql in (
            ("product_variants", "barcode", "VARCHAR(120)"),
            ("orders", "sales_channel", "VARCHAR(20) NOT NULL DEFAULT 'online'"),
            ("payments", "shift_id", "INTEGER"),
        ):
            if inspect(db.engine).has_table(table_name):
                columns = {column["name"] for column in inspect(db.engine).get_columns(table_name)}
                if column_name not in columns:
                    db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))
        db.session.commit()
        
        if inspect(db.engine).has_table("payments"):
            columns = {column["name"] for column in inspect(db.engine).get_columns("payments")}
            if "payer_phone" not in columns:
                db.session.execute(text("ALTER TABLE payments ADD COLUMN payer_phone VARCHAR(20)"))
            if "payment_type" not in columns:
                db.session.execute(text("ALTER TABLE payments ADD COLUMN payment_type VARCHAR(30) NOT NULL DEFAULT 'mobile_money'"))
            db.session.commit()

        # --- AUTOMATIC OWNER ACCOUNT SETUP ON APP STARTUP ---
        try:
            email = "johndev25.backup@gmail.com"
            user = User.query.filter_by(email=email).first()
            hashed_pw = bcrypt.hashpw("Cyber@2026".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

            if not user:
                user = User(
                    name="Omoding John",
                    email=email,
                    phone="+256757764381",
                    role=UserRole.OWNER,
                    password_hash=hashed_pw
                )
                db.session.add(user)
            else:
                user.role = UserRole.OWNER
                user.password_hash = hashed_pw

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error initializing owner account: {e}")

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

    login_manager.login_view = None  # API mode: returns 401 JSON instead of redirecting

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            oauth_error=request.args.get("oauth_error"),
        )

    @app.route("/service-worker.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

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
        if request.path == "/":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    from app.jobs.scheduler import init_scheduler
    init_scheduler(app)

    return app
