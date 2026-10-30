from __future__ import annotations

import asyncio

import tools.web_tools as wt


class Vendor:
    def __init__(self, name):
        self.name = name


def run(monkeypatch, fallback, primary_result):
    calls = []

    async def fake_extract(provider, urls, fmt):
        calls.append((provider.name, list(urls)))
        if provider.name == "firecrawl":
            return primary_result(urls)
        return [{"url": u, "title": "t", "content": "from exa"} for u in urls]

    monkeypatch.setattr(wt, "_extract_safe_urls", fake_extract)
    monkeypatch.setattr(wt, "_configured_backend", lambda key="backend": fallback if key == "extract_fallback_backend" else "")
    monkeypatch.setattr(wt, "_resolve_extract_provider", lambda name: (Vendor(name), None))
    results = asyncio.run(wt._extract_with_fallback(Vendor("firecrawl"), ["https://a.test", "https://b.test"], None))
    return results, calls


def half_failed(urls):
    return [{"url": urls[0], "content": "ok"}, {"url": urls[1], "content": "", "error": "Payment Required"}]


def test_only_failed_urls_go_to_the_fallback(monkeypatch):
    results, calls = run(monkeypatch, "exa", half_failed)
    assert calls[1] == ("exa", ["https://b.test"])
    assert [r["content"] for r in results] == ["ok", "from exa"]
    assert "error" not in results[1]


def test_no_fallback_configured_keeps_the_vendor_error(monkeypatch):
    results, calls = run(monkeypatch, "", half_failed)
    assert len(calls) == 1
    assert results[1]["error"] == "Payment Required"


def test_a_raising_primary_still_reaches_the_fallback(monkeypatch):
    def boom(urls):
        raise RuntimeError("402")
    results, _ = run(monkeypatch, "exa", boom)
    assert [r["content"] for r in results] == ["from exa", "from exa"]


def test_a_raising_primary_without_fallback_still_raises(monkeypatch):
    def boom(urls):
        raise RuntimeError("402")
    try:
        run(monkeypatch, "", boom)
    except RuntimeError:
        return
    raise AssertionError("expected the vendor error to surface")
