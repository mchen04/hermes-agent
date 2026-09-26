# Forge coding routes: staged rollout

This directory stages Forge policy for review. It does not change a live profile.

## Ownership and deployment

- `skills-source.patch` targets the versioned `my-ai-skills` source at `d58bfddf61148840d3eabe9aacc321028de0cbc7`. Apply it to that clean source, run its checks, and publish it through skill sync.
- `profile.patch` targets Forge's profile-local `SOUL.md`, `memories/USER.md`, and `profile-workflows` references under `~/.hermes/profiles/forge`. After skill sync, the supervisor applies all four files together before a new Forge session. Check patch applicability first. `USER.md` enters the system prompt, so an active session keeps its current policy and settings. Do not edit an active session or a synced release.
- `route.py` is the reviewable source for `skills/supervise/scripts/forge_route.py` in the skill patch. The default Hermes profile route and its Sol medium override stay unchanged. Research, Claude cleanup, and explicit Claude review routes remain available.

## Use

For a new session on the default Codex route, run `forge_route.py implementation` with the actual scope, verification path, hazard, and task evidence. State its level and reason. Pass its explicit model and effort to `fleetturn.py` or `fleetstart.py`. Verify the launched Codex session's `turn_context` model and effort; the helper's `verified` field checks session identity, not model settings. Pass a resumed session's recorded settings unchanged.

Run `forge_route.py review` with the checkout, a blind charter, the artifact, and an output directory outside the checkout. The default plan selects fresh GPT-6 Sol through `freshctx.py dispatch` at high effort. The existing helper enforces a read-only Codex sandbox and captures the prompt and full response. `--reviewer claude` selects the explicit Claude route with read-only tools. The supervisor executes the independent review and records its findings. The implementation owner does not review its own diff.

For heavy reviews, preserve the existing judge helper gate, its full review lenses, and its authorization and read-only isolation requirements. If those requirements fail, hand off the blocked gate. The enabled `sdlc-review` route applies to light Kanban `review_requested` handoffs claimed by the review-lane dispatcher; ordinary Forge coder reviews use fresh-eyes.

New Codex sessions receive a runtime-generated UUID. The turn records it when `thread.started` arrives; an earlier stop reports an unknown resume ID. Codex takeover requires that observed ID and rejects a different explicit ID. Resumes verify against `thread.started` and reject conflicting later IDs. Workspace admission closes the runner startup gap, and the runner holds a checkout writer lease while the CLI runs. A `done` result with `verified: false` fails status and wait.
