"""Start the web UI: python run.py  ->  http://127.0.0.1:8000"""
import webbrowser
import uvicorn
from app import config

if __name__ == "__main__":
    host, port = config.host(), config.port()
    if host not in ("127.0.0.1", "localhost"):
        # The app drives your job-site accounts and holds your documents; it is built for this machine only.
        raise SystemExit(f"APP_HOST={host} would expose the app to other machines. Use 127.0.0.1 (see README).")
    webbrowser.open(f"http://{host}:{port}")
    uvicorn.run("app.web:app", host=host, port=port, log_level="warning")
