"""Oneshot stdin transports literal input and retains the actual run receipt."""

import io
import json
import sys

import pytest

from hermes_cli import oneshot
from hermes_cli._parser import build_top_level_parser


@pytest.mark.parametrize("stdin_mode", [True, False])
def test_prompt_transport_preserves_literal_input_and_actual_receipt(
    tmp_path, monkeypatch, capsys, stdin_mode
):
    original = 'Original packet: $(literal) `literal` "quoted"\n' * (
        5000 if stdin_mode else 1
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(original if stdin_mode else "must not read")
    )
    receipt = tmp_path / "usage.json"
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args([
        "-z",
        "-" if stdin_mode else original,
        "--model",
        "requested-model",
        "--provider",
        "requested-provider",
        "--usage-file",
        str(receipt),
    ])
    calls = []

    def agent(prompt, **kwargs):
        calls.append((prompt, kwargs["model"], kwargs["provider"]))
        return '{"artifact":"complete"}', dict(
            model="observed-model",
            provider="observed-provider",
            completed=True,
            failed=False,
        )

    monkeypatch.setattr(oneshot, "_run_agent", agent)
    assert (
        oneshot.run_oneshot(
            args.oneshot,
            model=args.model,
            provider=args.provider,
            usage_file=args.usage_file,
        )
        == 0
    )
    assert calls == [(original, "requested-model", "requested-provider")]
    assert json.loads(capsys.readouterr().out)["artifact"] == "complete"
    usage = json.loads(receipt.read_text())
    assert (usage["model"], usage["provider"], usage["completed"], usage["failed"]) == (
        "observed-model",
        "observed-provider",
        True,
        False,
    )


def test_empty_stdin_never_calls_provider_and_retains_failed_receipt(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(" \n"))

    def unexpected_call(*args, **kwargs):
        raise AssertionError("provider called")

    monkeypatch.setattr(oneshot, "_run_agent", unexpected_call)
    receipt = tmp_path / "usage.json"
    assert oneshot.run_oneshot("-", usage_file=str(receipt)) == 2
    assert json.loads(receipt.read_text())["failed"] is True
