from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Single shared instances, initialized against the app in app/__init__.py.
# Keeping them here (rather than re-creating per blueprint) avoids
# circular-import headaches as the app grows.

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

# Rate limiting - applied explicitly to sensitive endpoints (login, OTP,
# payment initiation) rather than globally, so normal browsing isn't throttled.
limiter = Limiter(key_func=get_remote_address)
