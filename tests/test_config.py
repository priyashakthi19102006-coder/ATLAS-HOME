"""Tests for ATLAS Home Configuration."""

import os
import tempfile
from atlas.config.settings import Settings, load_env_file


def test_default_settings():
    settings = Settings()
    assert settings.device_id is not None
    assert settings.api_port == 5000
    assert settings.api_host == "127.0.0.1"


def test_custom_env_loading():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("ATLAS_CAMERA_URL=http://10.0.0.99:4747/video\n")
        f.write("ATLAS_DEVICE_ID=atlas_test_device\n")
        f.write("ATLAS_API_PORT=6001\n")
        temp_path = f.name

    try:
        loaded = load_env_file(temp_path)
        assert loaded["ATLAS_CAMERA_URL"] == "http://10.0.0.99:4747/video"
        assert loaded["ATLAS_DEVICE_ID"] == "atlas_test_device"
        assert loaded["ATLAS_API_PORT"] == "6001"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_numeric_device_index_parsing():
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("ATLAS_CAMERA_URL=0\n")
        temp_path = f.name

    try:
        # Clear existing env var if set
        os.environ.pop("ATLAS_CAMERA_URL", None)
        settings = Settings(env_path=temp_path)
        assert settings.camera_source == 0
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
