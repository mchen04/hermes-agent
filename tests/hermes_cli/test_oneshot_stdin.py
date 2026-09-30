"""`hermes -z -` reads the prompt from stdin and hands it to the gateway turn intact."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
DRIVER = """
import json
from pathlib import Path
import sys
from hermes_cli import gateway_chat, gateway_chat_startup

capture = Path(sys.argv.pop(1))
async def run_gateway_chat(args, emitter=None):
    capture.write_text(json.dumps(dict(prompt=args.oneshot, model=args.model, provider=args.provider,
                                       reasoning=args.reasoning, usage_file=args.usage_file)), encoding="utf-8")
    print("OK")
    return 0
gateway_chat.run_gateway_chat = run_gateway_chat
gateway_chat_startup.ensure_launch_provider = lambda args: True
from hermes_cli.main import main
main()
"""


def _invoke(tmp_path, *, fast, prompt, stdin):
    home = tmp_path / "home"
    home.mkdir()
    capture, usage = tmp_path / "captured.json", tmp_path / "usage.json"
    env = dict(os.environ, HOME=str(home), HERMES_HOME=str(home / ".hermes"),
               HERMES_DISABLE_FAST_CHAT_LAUNCH="0" if fast else "1", PYTHONUTF8="1")
    completed = subprocess.run(
        [sys.executable, "-c", DRIVER, str(capture), "--safe-mode", "-m", "gpt-5.6-luna",
         "--provider", "openai-codex", "--reasoning", "low", "-t", "web",
         "--usage-file", str(usage), "-z", prompt],
        input=stdin, text=True, encoding="utf-8", capture_output=True, env=env, cwd=REPO, timeout=90, check=False,
    )
    return completed, capture, usage


@pytest.mark.parametrize("fast", [False, True], ids=["full-parser", "fast-parser"])
@pytest.mark.parametrize("piped", [False, True], ids=["literal", "stdin"])
def test_prompt_transport_preserves_text_and_route(tmp_path, fast, piped):
    literal = '  Compare "A" and B; preserve $(printf ignored), `id`, and \\ paths.\n東京 🌙\n'
    # Larger than common per-argument limits: source packets and revision history travel on stdin.
    packet = json.dumps({
        "question": literal,
        "sources": "Dated evidence and URL: https://example.test/source\n" * 32768,
        "artifact": "Previous complete draft\n" * 1024,
        "review_history": [{"objections": ["Keep the original horizon"], "response": literal}],
    }, ensure_ascii=False)
    expected = packet if piped else literal
    result, capture, usage = _invoke(tmp_path, fast=fast, prompt="-" if piped else literal, stdin=packet)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "OK\n"
    actual = json.loads(capture.read_text(encoding="utf-8"))
    assert actual["prompt"] == expected
    assert (actual["model"], actual["provider"], actual["reasoning"]) == ("gpt-5.6-luna", "openai-codex", "low")
    assert actual["usage_file"] == str(usage)


@pytest.mark.parametrize("fast", [False, True], ids=["full-parser", "fast-parser"])
@pytest.mark.parametrize("stdin", ["", " \n\t "], ids=["empty", "whitespace"])
def test_empty_stdin_fails_before_the_turn_with_failure_receipt(tmp_path, fast, stdin):
    result, capture, usage = _invoke(tmp_path, fast=fast, prompt="-", stdin=stdin)
    assert result.returncode == 2, result.stderr
    assert result.stdout == "" and "stdin" in result.stderr and "empty" in result.stderr
    assert not capture.exists()
    receipt = json.loads(usage.read_text(encoding="utf-8"))
    assert receipt["failed"] is True and receipt["completed"] is False
    assert receipt["api_calls"] == 0
