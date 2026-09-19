"""LOCAL-PATCH gemini-studio-host: an explicit GEMINI_BASE_URL on the AI Studio host keeps an
express-shaped (``AQ.``) key on that host instead of the Vertex express redirect."""

from agent.gemini_native_adapter import (
    DEFAULT_GEMINI_BASE_URL,
    VERTEX_EXPRESS_BASE_URL,
    normalize_gemini_base_url,
)

STUDIO = "https://generativelanguage.googleapis.com/v1beta"


def test_express_key_redirects_to_vertex_without_a_pin(monkeypatch):
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
    assert normalize_gemini_base_url(STUDIO, "AQ.abc") == VERTEX_EXPRESS_BASE_URL
    assert normalize_gemini_base_url("", "AQ.abc") == VERTEX_EXPRESS_BASE_URL


def test_express_key_stays_on_studio_host_when_pinned(monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", STUDIO)
    assert normalize_gemini_base_url(STUDIO, "AQ.abc") == STUDIO
    assert normalize_gemini_base_url("", "AQ.abc") == DEFAULT_GEMINI_BASE_URL


def test_studio_key_is_unaffected(monkeypatch):
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
    assert normalize_gemini_base_url(STUDIO, "AIzaXYZ") == STUDIO
    assert normalize_gemini_base_url("https://generativelanguage.googleapis.com", "AIzaXYZ") == STUDIO
