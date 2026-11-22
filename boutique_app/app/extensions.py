from flask_login import LoginManager

# Single shared instances, initialized against the app in app/__init__.py.
login_manager = LoginManager()

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address)
except ModuleNotFoundError:
    class _NoOpLimiter:
        def init_app(self, app):
            pass

        def limit(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    limiter = _NoOpLimiter()
