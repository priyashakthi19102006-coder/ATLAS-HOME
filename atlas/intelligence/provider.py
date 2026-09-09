"""LLM Provider abstraction layer for ATLAS Home.

Decouples the verifier from any specific AI provider or SDK.
Supports OpenAI-compatible REST endpoints, offline fallback, and test-only deterministic mocks.
"""

from __future__ import annotations

import abc
import json
import logging
from typing import Any
import urllib.error
import urllib.request

logger = logging.getLogger("atlas.intelligence.provider")


class BaseLLMProvider(abc.ABC):
    """Abstract base class for ATLAS LLM providers."""

    @abc.abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Generate structured response.
        
        Returns:
            (parsed_json_dict, error_message_or_none)
        """
        pass

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Name of the provider implementation."""
        pass

    @abc.abstractmethod
    def check_health(self) -> dict[str, Any]:
        """Perform a harmless connectivity and authentication health check.
        
        Returns:
            dict containing:
            - network: 'CONNECTED' | 'DISCONNECTED' | 'UNCONFIGURED'
            - authenticated: bool
            - model_available: bool
            - status_code: int | None
            - error: str | None
        """
        pass


class OpenAICompatibleProvider(BaseLLMProvider):
    """Standard HTTP provider calling OpenAI-compatible /chat/completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key.strip() if api_key else None
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return f"openai_compatible({self.model})"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def check_health(self) -> dict[str, Any]:
        """Verify provider network reachability and authentication validity."""
        if not self.is_configured:
            return {
                "network": "UNCONFIGURED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": "No API key configured.",
            }

        endpoint = f"{self.base_url}/models"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            req = urllib.request.Request(endpoint, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=min(5.0, self.timeout_seconds)) as resp:
                if resp.getcode() == 200:
                    return {
                        "network": "CONNECTED",
                        "authenticated": True,
                        "model_available": True,
                        "status_code": 200,
                        "error": None,
                    }
                return {
                    "network": "CONNECTED",
                    "authenticated": False,
                    "model_available": False,
                    "status_code": resp.getcode(),
                    "error": f"Unexpected HTTP status {resp.getcode()}",
                }
        except urllib.error.HTTPError as e:
            if e.code == 401:
                err_msg = "HTTP 401 Unauthorized: Invalid or revoked API key"
            else:
                err_msg = f"HTTP {e.code} {e.reason}"
            return {
                "network": "CONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": e.code,
                "error": err_msg,
            }
        except urllib.error.URLError as e:
            return {
                "network": "DISCONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": f"Connection failed: {e.reason}",
            }
        except Exception as e:
            return {
                "network": "DISCONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": f"{type(e).__name__}: {e}",
            }

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self.is_configured:
            return None, "LLM provider is unconfigured: missing API key."

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                status_code = resp.getcode()
                raw_body = resp.read().decode("utf-8")

                if status_code != 200:
                    return None, f"HTTP Error {status_code} from LLM provider."

                response_json = json.loads(raw_body)
                choices = response_json.get("choices", [])
                if not choices:
                    return None, "Empty choices returned from LLM provider."

                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    return None, "Empty message content in LLM response."

                # Parse JSON content with safe fence stripping
                parsed = extract_and_parse_json(content)
                return parsed, None

        except urllib.error.HTTPError as e:
            if e.code == 401:
                err_msg = "HTTP 401 Unauthorized: Invalid or revoked API key"
            else:
                err_msg = f"HTTP {e.code}: {e.reason}"
            logger.warning("LLM call failed: %s", err_msg)
            return None, err_msg
        except urllib.error.URLError as e:
            err_msg = f"URLError (connection/timeout): {e.reason}"
            logger.warning("LLM call failed: %s", err_msg)
            return None, err_msg
        except json.JSONDecodeError as e:
            err_msg = f"Failed to parse LLM response JSON: {e}"
            logger.warning("LLM call returned invalid JSON: %s", err_msg)
            return None, err_msg
        except Exception as e:
            err_msg = f"Unexpected error during LLM invocation: {type(e).__name__}: {e}"
            logger.warning("LLM unexpected failure: %s", err_msg)
            return None, err_msg


def extract_and_parse_json(raw: str) -> dict[str, Any]:
    """Safely parse JSON from LLM output, stripping markdown code fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object/dict from LLM, got {type(parsed).__name__}")
    return parsed


def _model_matches(target: str, candidate: str) -> bool:
    """Helper to match model names ignoring casing and default ':latest' tag."""
    t = target.lower().strip()
    c = candidate.lower().strip()
    if t == c:
        return True
    if t.endswith(":latest") and t[:-7] == c:
        return True
    if c.endswith(":latest") and c[:-7] == t:
        return True
    return False


class OllamaProvider(BaseLLMProvider):
    """Local Ollama LLM provider calling http://localhost:11434/api/chat."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:3b",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def provider_name(self) -> str:
        return f"ollama({self.model})"

    @property
    def is_configured(self) -> bool:
        return True

    def check_health(self) -> dict[str, Any]:
        """Verify local Ollama reachability and configured model presence."""
        endpoint = f"{self.base_url}/api/tags"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=min(5.0, self.timeout_seconds)) as resp:
                if resp.getcode() != 200:
                    return {
                        "network": "CONNECTED",
                        "authenticated": True,
                        "model_available": False,
                        "status_code": resp.getcode(),
                        "error": f"Ollama HTTP error {resp.getcode()}",
                    }
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                model_found = any(
                    _model_matches(self.model, m.get("name", ""))
                    or _model_matches(self.model, m.get("model", ""))
                    for m in models
                )
                if model_found:
                    return {
                        "network": "CONNECTED",
                        "authenticated": True,
                        "model_available": True,
                        "status_code": 200,
                        "error": None,
                    }
                return {
                    "network": "CONNECTED",
                    "authenticated": True,
                    "model_available": False,
                    "status_code": 200,
                    "error": f"Model '{self.model}' not found in Ollama (MODEL_NOT_FOUND)",
                }
        except urllib.error.URLError as e:
            return {
                "network": "DISCONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": f"Ollama unavailable: {e.reason}",
            }
        except Exception as e:
            return {
                "network": "DISCONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": f"{type(e).__name__}: {e}",
            }

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        endpoint = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "num_predict": 600,
            },
        }
        headers = {
            "Content-Type": "application/json",
        }

        _max_attempts = 2
        _last_json_err: str | None = None

        for attempt in range(1, _max_attempts + 1):
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    status_code = resp.getcode()
                    raw_body = resp.read().decode("utf-8")

                    if status_code != 200:
                        return None, f"HTTP Error {status_code} from Ollama provider."

                    response_json = json.loads(raw_body)
                    content = response_json.get("message", {}).get("content", "")
                    if not content:
                        return None, "Empty message content in Ollama response."

                    parsed = extract_and_parse_json(content)
                    return parsed, None

            except urllib.error.HTTPError as e:
                err_msg = f"HTTP {e.code}: {e.reason}"
                logger.warning("Ollama call failed: %s", err_msg)
                return None, err_msg
            except urllib.error.URLError as e:
                err_msg = f"URLError (connection/timeout): {e.reason}"
                logger.warning("Ollama call failed: %s", err_msg)
                return None, err_msg
            except TimeoutError:
                err_msg = f"TimeoutError: Ollama request timed out ({self.timeout_seconds}s)"
                logger.warning("Ollama request timed out: %s", err_msg)
                return None, err_msg
            except json.JSONDecodeError as e:
                _last_json_err = f"Failed to parse Ollama JSON response: {e}"
                if attempt < _max_attempts:
                    logger.warning(
                        "Ollama returned unparseable JSON (attempt %d/%d), retrying: %s",
                        attempt, _max_attempts, e,
                    )
                    continue
                logger.warning("Ollama call returned invalid JSON after %d attempts: %s", _max_attempts, e)
                return None, _last_json_err
            except Exception as e:
                err_msg = f"Unexpected error during Ollama invocation: {type(e).__name__}: {e}"
                logger.warning("Ollama unexpected failure: %s", err_msg)
                return None, err_msg

        return None, _last_json_err or "Ollama generate failed with no error captured."



class MockTestLLMProvider(BaseLLMProvider):
    """Deterministic mock provider explicitly for test fixtures.
    
    WARNING: For test use only. Never used in live camera execution.
    """

    def __init__(
        self,
        default_response: dict[str, Any] | None = None,
        should_fail: bool = False,
        error_message: str = "Simulated provider failure",
    ) -> None:
        self.default_response = default_response
        self.should_fail = should_fail
        self.error_message = error_message
        self.call_count = 0
        self.last_prompts: tuple[str, str] | None = None

    @property
    def provider_name(self) -> str:
        return "mock_test_provider"

    @property
    def is_configured(self) -> bool:
        return True

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        self.call_count += 1
        self.last_prompts = (system_prompt, user_prompt)

        if self.should_fail:
            return None, self.error_message

        if self.default_response is not None:
            return self.default_response, None

        # Fallback safe minimal structured test response
        return {
            "situation": "Test observed scene activity",
            "interpretation": "Test evidence-grounded interpretation",
            "supporting_event_ids": [],
            "evidence_summary": ["Test event observed"],
            "confidence": 0.75,
            "uncertainty": "moderate",
            "verification_required": True,
            "possible_conditions": ["test_condition"],
            "contradicting_evidence": [],
        }, None

    def check_health(self) -> dict[str, Any]:
        if self.should_fail:
            return {
                "network": "DISCONNECTED",
                "authenticated": False,
                "model_available": False,
                "status_code": None,
                "error": self.error_message,
            }
        return {
            "network": "CONNECTED",
            "authenticated": True,
            "model_available": True,
            "status_code": 200,
            "error": None,
        }
