from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager

# Single shared instances, initialized against the app in app/__init__.py.
# Keeping them here (rather than re-creating per blueprint) avoids
# circular-import headaches as the app grows.

login_manager = LoginManager()

# Rate limiting - applied explicitly to sensitive endpoints (login, OTP,
# payment initiation) rather than globally, so normal browsing isn't throttled.
limiter = Limiter(key_func=get_remote_address)
