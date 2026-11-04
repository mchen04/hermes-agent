"""Secret-shaped names (``*_PASSWORD``, ``*_API_KEY``, ``*SECRET*`` ...) never reach children by default."""

import os

import pytest

from tests.tools._child_env_fixtures import child_env, observe_child, observe_terminal
from tools.environments import local
from tools.environments.local_env_policy import _is_secret_named_env

BLOCKED = ["MROUTER_PASSWORD", "FOO_API_KEY", "MY_RANDOM_SECRET", "IMESSAGE_TOKEN", "DEPLOY_PRIVATE_KEY",
           "SECRETIVE_THING", "mrouter_password", "foo_api_key"]
PLAIN = ["MY_SETTING", "MY_APP_KEY", "LANG"]
ALLOWED = ["CLAUDE_CODE_OAUTH_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "HERMES_REDACT_SECRETS"]


@pytest.mark.parametrize("name", BLOCKED)
def test_secret_shaped_names_match(name):
    assert _is_secret_named_env(name)


@pytest.mark.parametrize("name", PLAIN + ALLOWED + ["PATH", "HOME", "HERMES_HOME", "GH_TOKEN_FILE"])
def test_plain_and_allowed_names_do_not_match(name):
    assert not _is_secret_named_env(name)


@pytest.mark.parametrize("builder", ["foreground", "background", "nonterminal"])
def test_children_never_see_secret_shaped_names(child_env, monkeypatch, builder):
    for name in [*BLOCKED, *PLAIN, *ALLOWED]:
        monkeypatch.setenv(name, "fake-" + name)
    factories = {
        "foreground": lambda: local._make_run_env({}),
        "background": lambda: local._sanitize_subprocess_env(dict(os.environ)),
        "nonterminal": local.hermes_subprocess_env,
    }
    observed = observe_child(factories[builder](), [*BLOCKED, *PLAIN, *ALLOWED, "PATH", "HOME", "HERMES_HOME"])
    assert observed.pop("PATH")
    assert observed == {**dict.fromkeys(BLOCKED), **{k: "fake-" + k for k in [*PLAIN, *ALLOWED]},
                        "HOME": str(child_env), "HERMES_HOME": str(child_env / "hermes")}


def test_terminal_child_observes_secret_shape_policy(child_env, monkeypatch):
    for name in ["MROUTER_PASSWORD", "FOO_API_KEY", "MY_RANDOM_SECRET", "MY_SETTING", "CLAUDE_CODE_OAUTH_TOKEN"]:
        monkeypatch.setenv(name, "fake-" + name)
    env = local.LocalEnvironment(cwd=str(child_env), timeout=30)
    try:
        observed = observe_terminal(env, ["MROUTER_PASSWORD", "FOO_API_KEY", "MY_RANDOM_SECRET", "MY_SETTING",
                                          "CLAUDE_CODE_OAUTH_TOKEN", "PATH", "HOME", "HERMES_HOME", "LANG"])
    finally:
        env.cleanup()
    assert observed["MROUTER_PASSWORD"] is None
    assert observed["FOO_API_KEY"] is None
    assert observed["MY_RANDOM_SECRET"] is None
    assert observed["MY_SETTING"] == "fake-MY_SETTING"
    assert observed["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-CLAUDE_CODE_OAUTH_TOKEN"
    assert observed["HERMES_HOME"] == str(child_env / "hermes")
    assert observed["LANG"] == "C.UTF-8"
    assert observed["PATH"] and observed["HOME"]


def test_passthrough_registration_still_admits_operator_secret(child_env, monkeypatch):
    from tools.env_passthrough import register_env_passthrough
    monkeypatch.setenv("FOO_API_KEY", "fake-FOO_API_KEY")
    monkeypatch.setenv("MROUTER_PASSWORD", "fake-MROUTER_PASSWORD")
    register_env_passthrough(["FOO_API_KEY"])
    observed = observe_child(local._make_run_env({}), ["FOO_API_KEY", "MROUTER_PASSWORD"])
    assert observed == {"FOO_API_KEY": "fake-FOO_API_KEY", "MROUTER_PASSWORD": None}


def test_inheriting_child_keeps_secret_shaped_names(child_env, monkeypatch):
    monkeypatch.setenv("FOO_API_KEY", "fake-FOO_API_KEY")
    observed = observe_child(local.hermes_subprocess_env(inherit_credentials=True), ["FOO_API_KEY"])
    assert observed == {"FOO_API_KEY": "fake-FOO_API_KEY"}
