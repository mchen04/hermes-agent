"""Historical error prose cannot override a current human decision."""
from hermes_cli import goals


def test_historical_rate_limit_does_not_restart_approval_block(monkeypatch):
    monkeypatch.setattr(goals, "judge_goal", lambda *args, **kwargs:
                        ("blocked", "User approval is still required before sending.", False, None, False))
    blocked = []
    turns = []
    result = goals.run_kanban_goal_loop(
        task_id="task", goal_text="Send only with approval.",
        run_turn=turns.append, task_status_fn=lambda: "running", block_fn=blocked.append,
        first_response="Earlier I hit a rate limit, then the request succeeded.",
    )
    assert result["outcome"] == "blocked_unachievable"
    assert len(blocked) == 1 and "approval" in blocked[0]
    assert not turns
