"""Settings and environment configuration for ATLAS Home.

Handles reading configuration from environment variables and an optional .env file
without requiring third-party dependencies.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Union


def load_env_file(env_path: Path | str | None = None) -> dict[str, str]:
    """Parse a simple .env file into key-value pairs without overriding existing env vars."""
    if env_path is None:
        # Default to finding .env in the project root (parent of atlas/)
        current = Path(__file__).resolve().parent.parent.parent
        env_path = current / ".env"
    else:
        env_path = Path(env_path)

    loaded = {}
    if not env_path.is_file():
        return loaded

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip wrapping quotes if any
                    if len(val) >= 2 and (
                        (val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")
                    ):
                        val = val[1:-1]
                    loaded[key] = val
                    if key not in os.environ:
                        os.environ[key] = val
    except Exception as exc:
        print(f"[config] Warning: Failed to read .env file at {env_path}: {exc}")

    return loaded


class Settings:
    """ATLAS Home configuration object."""

    def __init__(self, env_path: Path | str | None = None) -> None:
        load_env_file(env_path)

        raw_camera_url = os.getenv("ATLAS_CAMERA_URL", "0").strip()
        # Interpret integer string (e.g. "0") safely as integer device index
        try:
            self.camera_source: Union[int, str] = int(raw_camera_url)
        except ValueError:
            self.camera_source = raw_camera_url

        self.raw_camera_url = raw_camera_url
        self.device_id: str = os.getenv("ATLAS_DEVICE_ID", "atlas_home_01").strip()
        self.api_host: str = os.getenv("ATLAS_API_HOST", "127.0.0.1").strip()
        self.api_port: int = int(os.getenv("ATLAS_API_PORT", "5000"))
        self.environment: str = os.getenv("ATLAS_ENV", "development").strip()
        self.camera_retry_interval: float = float(os.getenv("ATLAS_CAMERA_RETRY_INTERVAL_SEC", "3.0"))
        self.camera_buffer_size: int = int(os.getenv("ATLAS_CAMERA_BUFFER_SIZE", "1"))

        # LLM Verification parameters
        self.llm_provider: str = os.getenv("ATLAS_LLM_PROVIDER", "openai_compatible").strip().lower()
        raw_key = os.getenv("ATLAS_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.llm_api_key: str | None = raw_key.strip() if raw_key else None

        default_base_url = "http://localhost:11434" if self.llm_provider == "ollama" else "https://api.openai.com/v1"
        default_model = "qwen2.5:3b" if self.llm_provider == "ollama" else "gpt-4o-mini"
        default_timeout = 120.0 if self.llm_provider == "ollama" else 10.0

        self.llm_base_url: str = os.getenv("ATLAS_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or default_base_url
        self.llm_model: str = os.getenv("ATLAS_LLM_MODEL") or os.getenv("OPENAI_MODEL") or default_model
        self.llm_timeout_seconds: float = float(os.getenv("ATLAS_LLM_TIMEOUT_SEC", str(default_timeout)))
        self.analysis_cooldown_seconds: float = float(os.getenv("ATLAS_ANALYSIS_COOLDOWN_SEC", "15.0"))

        # Step 7: Notifications & Escalation configuration
        self.notifications_enabled: bool = os.getenv("ATLAS_NOTIFICATIONS_ENABLED", "true").strip().lower() in ("true", "1", "yes")
        self.escalation_enabled: bool = os.getenv("ATLAS_ESCALATION_ENABLED", "true").strip().lower() in ("true", "1", "yes")
        self.escalation_interval_seconds: float = float(os.getenv("ATLAS_ESCALATION_INTERVAL_SEC", "60.0"))
        self.max_escalation_level: int = int(os.getenv("ATLAS_MAX_ESCALATION_LEVEL", "3"))
        self.notification_retry_count: int = int(os.getenv("ATLAS_NOTIFICATION_RETRY_COUNT", "3"))
        self.notification_retry_delay_seconds: float = float(os.getenv("ATLAS_NOTIFICATION_RETRY_DELAY_SEC", "5.0"))
        # External channels explicitly default to False (NOT_CONFIGURED)
        self.mobile_push_enabled: bool = os.getenv("ATLAS_MOBILE_PUSH_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        self.mobile_push_endpoint: str = os.getenv("ATLAS_MOBILE_PUSH_ENDPOINT", "").strip()
        self.mobile_push_api_key: str = os.getenv("ATLAS_MOBILE_PUSH_API_KEY", "").strip()
        self.email_notifications_enabled: bool = os.getenv("ATLAS_EMAIL_NOTIFICATIONS_ENABLED", "false").strip().lower() in ("true", "1", "yes")

    def to_dict(self, hide_sensitive: bool = False) -> dict[str, Any]:
        api_key_repr = None
        if self.llm_provider == "ollama":
            api_key_repr = None
        elif self.llm_api_key:
            api_key_repr = "***REDACTED***" if hide_sensitive else f"{self.llm_api_key[:4]}..."

        is_configured = True if self.llm_provider == "ollama" else bool(self.llm_api_key)

        return {
            "device_id": self.device_id,
            "camera_source": self.raw_camera_url,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "environment": self.environment,
            "camera_retry_interval": self.camera_retry_interval,
            "camera_buffer_size": self.camera_buffer_size,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "llm_configured": is_configured,
            "llm_api_key": api_key_repr,
            "analysis_cooldown_seconds": self.analysis_cooldown_seconds,
            "notifications_enabled": self.notifications_enabled,
            "escalation_enabled": self.escalation_enabled,
            "escalation_interval_seconds": self.escalation_interval_seconds,
            "max_escalation_level": self.max_escalation_level,
            "notification_retry_count": self.notification_retry_count,
            "mobile_push_enabled": self.mobile_push_enabled,
            "email_notifications_enabled": self.email_notifications_enabled,
        }


_settings_instance: Settings | None = None


def get_settings(reload: bool = False) -> Settings:
    """Return singleton Settings instance."""
    global _settings_instance
    if _settings_instance is None or reload:
        _settings_instance = Settings()
    return _settings_instance
