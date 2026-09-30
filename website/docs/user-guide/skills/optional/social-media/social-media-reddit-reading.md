---
title: "Reddit Reading — Read Reddit posts and research discussions"
sidebar_label: "Reddit Reading"
description: "Read Reddit posts and research discussions"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Reddit Reading

Read Reddit posts and research discussions.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/social-media/reddit-reading` |
| Path | `optional-skills/social-media/reddit-reading` |
| Version | `1.1.0` |
| Author | Teknium (teknium1), Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Reddit`, `Social Media`, `Research`, `Discussions`, `Community` |
| Related skills | [`rss-feeds`](../../optional/research/research-rss-feeds.md), [`grounded-citations`](../../bundled/research/research-grounded-citations.md), [`blocked-page-recovery`](../../bundled/web/web-blocked-page-recovery.md), [`xurl`](../../bundled/social-media/social-media-xurl.md) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Reddit Reading Skill

Read public Reddit posts through an existing signed-in Safari session when desktop control works.
The included script keeps anonymous feeds and app-only OAuth available for headless research.
This skill does not post, vote, or send messages.

## When to Use

- Research a subreddit, search a topic, or summarize a Reddit thread.
- Compare dated discussions, scores, linked sources, comments, and replies.
- Read a Reddit URL supplied by the user.

## Prerequisites

**None for anonymous feeds.** The public Atom route needs no account or key.
It can be slow and may omit scores, replies, and many comments.

**Signed-in Safari, optional on macOS.** Use only an existing session and an authorized desktop control route.
Create a dedicated public research window and retain its window ID for this task.
Verify page control and signed-in state through that handle before using the session.
Do not inspect unrelated tabs, export cookies, change account or security settings, or bypass denied permissions.
A control error says nothing about whether the user is logged in.

**App-only OAuth, optional.** A Reddit script app can provide scores and nested comments without a user login.
Register a free "script" app at `https://www.reddit.com/prefs/apps`.
Set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in the active profile's `.env` secret store.
Each profile has its own secrets. Check the intended profile before running `doctor`.
The script uses the `client_credentials` grant and never uses a username, password, or browser cookie.
Do not paste the secret into a chat or log. Invalid credentials trigger a stated anonymous fallback.

## How to Run

Use the Safari procedure below when authorized desktop control passes its preflight.
Keep every read within the task-created window or a public article opened from it.
If Safari control is denied, record the exact error and use an available read-only backend.

Run the fallback script through `terminal` from this skill directory:

```bash
python3 scripts/reddit.py doctor
python3 scripts/reddit.py search "waiver" --sub fantasyfootball --sort top --after 2026-09-21 --before 2026-09-24
python3 scripts/reddit.py --json thread https://www.reddit.com/r/x/comments/abc123/slug/ --limit 40
python3 scripts/reddit.py sub AskHistorians --sort new --limit 15
python3 scripts/reddit.py user spez --limit 10
```

Dates use UTC. `--after` includes that date. `--before` excludes that date.
The script filters on post publication time, never Atom update time.
It excludes posts without a known publication time from a dated result.
The script filters only returned search results. Its search cannot prove that all matching posts were found.

## Safari Procedure

Use the new window's ID as the task handle. Safari can lose a new document reference while its page loads.
This preflight reads window IDs only until it identifies the one new window:

```applescript
tell application "Safari"
    set priorWindowIDs to id of every window
    make new document with properties {URL:"https://www.reddit.com/"}
    set newWindowIDs to {}
    set currentWindowIDs to id of every window
    repeat with candidateID in currentWindowIDs
        if candidateID is not in priorWindowIDs then set end of newWindowIDs to candidateID
    end repeat
    if (count of newWindowIDs) is not 1 then error "Expected one new Safari window"
    set researchWindowID to item 1 of newWindowIDs
    delay 5
    set researchTab to current tab of (first window whose id is researchWindowID)
    set researchURL to URL of researchTab
    if researchURL does not start with "https://www.reddit.com/" then error "Research window left Reddit"
    set accountState to do JavaScript "JSON.stringify({loggedInAvatar:!!document.querySelector('#user-drawer-avatar-logged-in'),userMenu:!!document.querySelector('#expand-user-drawer-button'),loginLink:[...document.querySelectorAll('a')].some(a=>a.textContent.trim()==='Log In')})" in researchTab
    return "window_id=" & researchWindowID & linefeed & "url=" & researchURL & linefeed & "account=" & accountState
end tell
```

Keep the returned window ID for later calls. Address that window by ID and check its URL before every read.
Confirm a logged-in avatar and user menu, without a `Log In` link. If controls differ, verify login in that task tab.
Stop if account state remains unclear or JavaScript is denied.
Do not replace a lost handle with `front document`; another Safari tab may now be in front.
Do not change permissions or settings as part of this procedure.

After the preflight passes, search within the task window:

```applescript
-- Replace 0 with the preflight's window ID. Update the subreddit in targetURL and the URL check together.
set researchWindowID to 0
set targetURL to "https://www.reddit.com/r/fantasyfootball/search/?q=waiver&restrict_sr=1&sort=top&t=month"
tell application "Safari"
    set researchTab to current tab of (first window whose id is researchWindowID)
    set URL of researchTab to targetURL
    delay 5
    if URL of researchTab does not start with "https://www.reddit.com/r/fantasyfootball/search/" then error "Search left task scope"
    return do JavaScript "JSON.stringify({url:location.href,cards:[...document.querySelectorAll('[data-testid=search-post-unit]')].map(e=>({url:e.querySelector('a[data-testid=post-title]')?.href,text:e.innerText,dates:[...e.querySelectorAll('time')].map(t=>t.getAttribute('datetime'))}))})" in researchTab
end tell
```

Save that output with the UTC observation time. Repeat the same window-ID and URL check for post and article reads.
On a post, read `shreddit-post` attributes `id`, `score`, `comment-count`, and `created-timestamp`.
Read `shreddit-comment` attributes `thingid`, `parentid`, and `depth` before and after each reply expansion.
Save the text and URL of each linked article that supports the answer.

1. Search `https://www.reddit.com/r/SUBREDDIT/search/?q=QUERY&restrict_sr=1` in the task window, using the exact community and a URL-encoded query. Record the query, sort, and preset time filter.
2. Scroll through results. Record each card's post URL, ID, score, `time[datetime]`, and observation time.
3. Open each candidate in the task window and compare its `created-timestamp` with the card. Convert offset timestamps to UTC; treat absent or relative-only dates as unknown. Apply the date window to creation time, never edit time. Deduplicate post IDs and record excluded or undated candidates.
4. Save the text and URL of selected posts and linked public sources. Read comment `thingid`, `parentid`, and `depth` when present; compare IDs before and after expanding relevant replies. Record hidden or failed controls and retrieved counts without claiming complete coverage.

Reddit may offer preset time ranges. This procedure does not assume a custom date-range control exists.
Apply the requested dates by checking each post's publication time.
If the page cannot show an absolute publication time, leave that post's date unverified.

## Quick Reference

| Need | Safari when authorized | Script fallback |
|---|---|---|
| Search one subreddit | Search in that subreddit | `search "q" --sub NAME` |
| Date window | Verify each post's publication time in UTC | `search "q" --after DATE --before DATE` |
| Popularity | Read visible numeric post scores | OAuth gives scores; feeds do not |
| Thread | Open post, comments, and relevant replies | `thread URL --limit N` |
| Linked article | Open the public source from the post | Use an available read-only web tool |
| Backend | Confirm signed-in state in research tab | `doctor` |

## Procedure

1. Verify the topic's date range from a live source. Separate posts about future events from reactions after them.
2. Search with both topic and subreddit filters. Apply the date window and record the exact candidate set.
3. Deduplicate candidates by Reddit post ID. Rank observed scores and record each source and observation time.
4. Open each selected post and any relevant linked article. Read the post before summarizing its comments.
5. Expand relevant comment and reply branches when the interface permits it. Record branches that remain hidden.
6. Count unique retrieved comment IDs or permalinks. Record reported totals, limits, truncation, and failed reads separately.
7. Cite post permalinks and linked articles. State search limits and never claim complete comment coverage without proof.

For anonymous feeds, plan requests before running them. Follow rate limits and stop after repeated throttling.
The script waits for one 429 reset and retries once. It does not evade blocks.

## Pitfalls

- A successful Safari navigation does not verify login. A denied automation route does not prove logout.
- Reddit scores change. Record when and where each score was observed.
- Search ranking, date windows, and result limits can omit eligible posts.
- An Atom feed gives `published` and `updated` separately when Reddit supplies them. `created` mirrors `published` for older readers.
- A feed returns a limited view. It cannot show reliable scores or reply nesting.
- OAuth may return `more` branches and may stop at a requested depth or count.
- The script reports retrieved comments and unresolved branches. It does not claim all comments were read.
- The plain-text thread view shortens bodies. Use `--json` for all text returned by the backend.
- Open linked articles before using their claims. A link title or Reddit summary is not the article.
- Do not post, vote, message, or collect browser credentials.

## Verification

`python3 scripts/reddit.py doctor` reports the active script backend.
`thread` returns `comment_coverage` with retrieved and unresolved counts where available.
For Safari, verify the dedicated tab's URL, readable content, and signed-in state.
If macOS denies access, stop that route and report its exact error.
