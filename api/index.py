import traceback
from app import create_app

app = None
init_error = None

try:
    app = create_app()
except Exception as e:
    init_error = traceback.format_exc()
    print("CRITICAL FLASK INIT ERROR:\n", init_error)
    
    from flask import Flask
    app = Flask(__name__)
    
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def error_fallback(path):
        return f"""
        <html>
            <body style="font-family: monospace; background: #121212; color: #ff6b6b; padding: 30px;">
                <h2>Deployment Startup Error Detected:</h2>
                <pre>{init_error}</pre>
                <p style="color: #ffffff;">Check your PostgreSQL database connection string (DATABASE_URL) in Vercel environment variables.</p>
            </body>
        </html>
        """, 500
