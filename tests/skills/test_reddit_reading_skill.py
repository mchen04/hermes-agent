"""Tests for optional-skills/social-media/reddit-reading/scripts/reddit.py — backend selection and throttle handling."""

import io
import json
import sys
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "optional-skills" / "social-media" / "reddit-reading" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import reddit  # noqa: E402

THREAD_ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/op</name></author><title>Post title</title>
  <link href="https://www.reddit.com/r/test/comments/abc123/post_title/"/><published>2026-08-31T23:30:00-04:00</published><updated>2026-09-01T09:00:00+00:00</updated>
  <content type="html">&lt;div&gt;body text&lt;/div&gt; submitted by /u/op [link] [comments]</content></entry>
<entry><author><name>/u/c1</name></author><title>/u/c1 on Post title</title>
  <link href="https://www.reddit.com/r/test/comments/abc123/post_title/k1/"/><published>2026-09-01T01:00:00+00:00</published><updated>2026-09-01T02:00:00+00:00</updated>
  <content type="html">&lt;p&gt;first comment&lt;/p&gt;</content></entry>
</feed>"""


def _http_error(code, headers):
    return urllib.error.HTTPError("https://www.reddit.com/x", code, "msg", headers, io.BytesIO(b""))


def test_anonymous_thread_uses_atom_feed_and_waits_out_a_429_exactly_once(monkeypatch):
    """Without OAuth credentials the .rss endpoint is used; a 429 sleeps for x-ratelimit-reset and retries once,
    and the parsed thread separates the post from its comments with feed noise stripped."""
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    assert reddit.oauth_credentials() is None

    calls = []
    sleeps = []

    class Resp(io.BytesIO):
        headers = {"x-ratelimit-remaining": "0.0"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise _http_error(429, {"x-ratelimit-reset": "7"})
        return Resp(THREAD_ATOM)

    with mock.patch.object(reddit.urllib.request, "urlopen", fake_urlopen), \
         mock.patch.object(reddit.time, "sleep", sleeps.append):
        post = reddit.atom_thread("test", "abc123", limit=10)

    assert calls[0].startswith("https://www.reddit.com/r/test/comments/abc123/.rss") and len(calls) == 2
    assert sleeps == [8]  # reset + 1s margin, one retry only
    assert post["title"] == "Post title" and post["author"] == "op"
    assert post["body"] == "body text"  # "submitted by … [link] [comments]" footer stripped
    assert post["published"] == "2026-08-31T23:30:00-04:00"
    assert post["updated"] == "2026-09-01T09:00:00+00:00"
    assert post["created"] == post["published"]
    assert [c["author"] for c in post["comments"]] == ["c1"]
    assert post["comment_coverage"]["retrieved_unique"] == 1
    assert post["comment_coverage"]["reply_nesting_available"] is False

    # a second 429 after the retry propagates instead of looping
    with mock.patch.object(reddit.urllib.request, "urlopen", side_effect=_http_error(429, {})), \
         mock.patch.object(reddit.time, "sleep", lambda s: None), pytest.raises(urllib.error.HTTPError):
        reddit._get("https://www.reddit.com/r/test/.rss")


def test_anonymous_json_preserves_escaped_comparisons_in_post_and_comment(monkeypatch, capsys):
    feed = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Bid ranges</title><link href="https://www.reddit.com/r/test/comments/abc123/bids/"/>
<content type="html">&lt;p&gt;FAAB &amp;lt;10% here and &amp;gt;5% there&lt;/p&gt;</content></entry>
<entry><title>Reply</title><link href="https://www.reddit.com/r/test/comments/abc123/bids/c1/"/>
<content type="html">&lt;p&gt;I bid &amp;lt;8% and won &amp;gt;4% back&lt;/p&gt;</content></entry>
</feed>'''
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    with mock.patch.object(reddit, "_get", return_value=(feed, {})):
        assert reddit.main(["--json", "thread", "https://www.reddit.com/r/test/comments/abc123/bids/"]) == 0

    thread = json.loads(capsys.readouterr().out)
    assert thread["body"] == "FAAB <10% here and >5% there"
    assert thread["comments"][0]["body"] == "I bid <8% and won >4% back"


def test_oauth_credentials_route_to_oauth_host_and_flatten_nested_comments(monkeypatch):
    """With REDDIT_CLIENT_ID/SECRET the script talks to oauth.reddit.com with a bearer token and
    returns nested comments flattened with depth and scores — data the anonymous path cannot provide."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "sec")
    assert reddit.oauth_credentials() == ("cid", "sec")

    listing = [
        {"data": {"children": [{"kind": "t3", "data": {"title": "T", "author": "op", "subreddit": "test", "score": 42,
                                                      "num_comments": 2, "created_utc": 1.0, "permalink": "/r/test/comments/abc123/t/",
                                                      "is_self": True, "url": "https://www.reddit.com/r/test/comments/abc123/t/", "selftext": "s"}}]}},
        {"data": {"children": [{"kind": "t1", "data": {"id": "c1", "author": "a", "score": 5, "body": "top", "permalink": "/p/1",
                                                      "replies": {"data": {"children": [{"kind": "t1", "data": {"id": "c2", "author": "b", "score": 1, "body": "reply", "replies": ""}}]}}}},
                               {"kind": "t1", "data": {"id": "c1", "author": "a", "score": 5, "body": "duplicate"}},
                               {"kind": "more", "data": {"count": 3}}]}},
    ]
    seen = {}

    def fake_api(path, token, **params):
        seen["path"], seen["token"] = path, token
        return listing

    with mock.patch.object(reddit, "_api", fake_api):
        post = reddit.api_thread("tok", "test", "abc123", limit=10)

    assert seen == {"path": "/r/test/comments/abc123", "token": "tok"}
    assert post["score"] == 42 and post["url"] == "https://www.reddit.com/r/test/comments/abc123/t/"
    assert [(c["author"], c["depth"], c["score"]) for c in post["comments"]] == [("a", 0, 5), ("b", 1, 1)]
    assert post["comment_coverage"]["retrieved_unique"] == 2
    assert post["comment_coverage"]["unresolved_branches"] == 1
    assert post["comment_coverage"]["unresolved_comment_count"] == 3

    # the bearer header actually reaches the OAuth host
    captured = {}

    def fake_get(url, headers=None, retry_on_429=True):
        captured["url"], captured["headers"] = url, headers
        return b'{"data": {"children": []}}', {}

    with mock.patch.object(reddit, "_get", fake_get):
        reddit._api("/r/test/hot", "tok", limit=1)
    assert captured["url"].startswith("https://oauth.reddit.com/r/test/hot?")
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_search_filters_dates_in_subreddit_and_deduplicates_results(capsys):
    args = SimpleNamespace(query="roads", sub="AskHistorians", sort="top", time="all", limit=10,
                           after=date(2026, 4, 1), before=date(2026, 5, 1))
    posts = [
        {"url": "https://www.reddit.com/r/AskHistorians/comments/abc123/one/", "published": "2026-04-03T12:00:00+00:00"},
        {"url": "https://www.reddit.com/r/AskHistorians/comments/abc123/other_slug/", "published": "2026-04-03T12:00:00+00:00"},
        {"url": "https://www.reddit.com/r/AskHistorians/comments/b/two/", "published": "2026-05-01T00:00:00+00:00"},
    ]
    with mock.patch.object(reddit, "atom_listing", return_value=posts) as listing:
        found = reddit.cmd_search(args, None)
    assert listing.call_args.args == ("/r/AskHistorians/search", 10)
    assert listing.call_args.kwargs["restrict_sr"] == 1
    assert len(found) == 1 and found[0]["url"] == posts[0]["url"]
    assert "not an exhaustive search" in capsys.readouterr().err
    epoch = datetime(2026, 4, 3, tzinfo=timezone.utc).timestamp()
    assert reddit._in_date_window({"created_utc": epoch}, args.after, args.before)


def test_search_excludes_edited_posts_without_a_publication_in_window():
    args = SimpleNamespace(query="waiver", sub="fantasyfootball", sort="top", time="all", limit=10,
                           after=date(2026, 9, 21), before=date(2026, 9, 24))
    posts = [
        {"url": "https://www.reddit.com/r/fantasyfootball/comments/old/edited/",
         "published": "2026-09-20T12:00:00+00:00", "updated": "2026-09-22T12:00:00+00:00",
         "created": "2026-09-22T12:00:00+00:00"},
        {"url": "https://www.reddit.com/r/fantasyfootball/comments/unknown/unknown/",
         "published": None, "updated": "2026-09-22T12:00:00+00:00",
         "created": "2026-09-22T12:00:00+00:00"},
        {"url": "https://www.reddit.com/r/fantasyfootball/comments/new/new/",
         "published": "2026-09-22T12:00:00+00:00", "updated": "2026-09-23T12:00:00+00:00"},
    ]
    with mock.patch.object(reddit, "atom_listing", return_value=posts):
        found = reddit.cmd_search(args, None)
    assert [post["url"] for post in found] == [posts[2]["url"]]


def test_publication_window_converts_offset_midnight_to_utc():
    after, before = date(2026, 9, 21), date(2026, 9, 22)
    assert reddit._in_date_window({"published": "2026-09-20T23:30:00-02:00"}, after, before)
    assert not reddit._in_date_window({"published": "2026-09-21T00:30:00+02:00"}, after, before)
    assert not reddit._in_date_window({"published": "2026-09-21T12:00:00"}, after, before)
    assert not reddit._in_date_window({"created_utc": "invalid"}, after, before)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
