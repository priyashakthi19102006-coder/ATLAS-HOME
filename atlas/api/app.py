"""Flask application factory for ATLAS Home."""

from __future__ import annotations

from pathlib import Path
from flask import Flask, send_from_directory
from flask_cors import CORS

from atlas.api.routes import api_bp
from atlas.config.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure the Flask app."""
    if settings is None:
        settings = get_settings()

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

    app = Flask(
        __name__,
        static_folder=str(frontend_dir),
        static_url_path="",
    )
    app.secret_key = getattr(settings, "api_key", None) or "atlas_home_local_secret_key_2026"
    CORS(app)

    # Register API blueprint
    app.register_blueprint(api_bp, url_prefix="/api")

    # Serve frontend dashboard at root
    @app.route("/")
    def index():
        return send_from_directory(str(frontend_dir), "index.html")

    @app.route("/<path:path>")
    def static_files(path: str):
        return send_from_directory(str(frontend_dir), path)

    return app
