
from flask_login import LoginManager

login_manager = LoginManager()


class _Limiter:
    def init_app(self, app):
        pass

    def limit(self, *args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


limiter = _Limiter()
