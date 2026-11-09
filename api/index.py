import traceback
from flask import Flask, Response

app = Flask(__name__)

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    try:
        from app import create_app
        real_app = create_app()
        # If create_app succeeded, let's delegate or confirm
        return f"App booted successfully! Path requested: /{path}", 200
    except Exception as e:
        err = traceback.format_exc()
        return Response(f"CRITICAL BOOT ERROR:\n\n{err}", mimetype="text/plain", status=500)
