import traceback
from flask import Flask

app = None
startup_error = None

try:
    from app import create_app
    app = create_app()
except Exception:
    startup_error = traceback.format_exc()
    print("CRITICAL STARTUP ERROR:\n", startup_error)
    
    # Fallback error app so browser shows the traceback
    app = Flask(__name__)
    
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def catch_all(path):
        return f"""
        <html>
            <body style="font-family: monospace; background: #1e1e1e; color: #ff5555; padding: 20px;">
                <h2>Flask Startup Crash Traceback:</h2>
                <pre>{startup_error}</pre>
            </body>
        </html>
        """, 500
