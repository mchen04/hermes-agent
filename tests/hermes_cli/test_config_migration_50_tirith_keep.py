"""LOCAL-PATCH tirith-keep: config v50 keeps the bundled tirith scanner's settings.

Upstream's 49 → 50 step (911da42e9e) dropped ``security.tirith_*`` when it removed the scanner.
This install keeps the scanner, so a profile that chose fail-closed scanning must stay fail-closed
across the migration, and a profile that never set the keys keeps the v49 defaults."""

import hermes_yaml as yaml


def _migrate(tmp_path, monkeypatch, config: dict) -> dict:
    from hermes_cli.config_migrations import run_migrations

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    results = {"env_added": [], "config_added": [], "warnings": []}
    run_migrations(config.get("_config_version", 0), results, quiet=True)
    return yaml.safe_load(path.read_text(encoding="utf-8")), results


def test_v50_keeps_an_explicit_fail_closed_policy(tmp_path, monkeypatch):
    security = {"redact_secrets": True, "tirith_enabled": True, "tirith_fail_open": False,
                "tirith_path": "tirith", "tirith_timeout": 5}
    raw, results = _migrate(tmp_path, monkeypatch, {"_config_version": 49, "security": dict(security)})
    assert raw["security"] == security
    assert not any("tirith" in str(item) for item in results["config_added"])


def test_v50_leaves_an_unset_policy_on_the_scanner_defaults(tmp_path, monkeypatch):
    from hermes_cli.config import DEFAULT_CONFIG

    raw, _ = _migrate(tmp_path, monkeypatch, {"_config_version": 49, "security": {"redact_secrets": True}})
    assert raw["security"] == {"redact_secrets": True}
    # Same defaults the v49 code shipped: enabled, fail-open unless a profile opts into fail-closed.
    assert {k: v for k, v in DEFAULT_CONFIG["security"].items() if k.startswith("tirith")} == {
        "tirith_enabled": True, "tirith_path": "tirith", "tirith_timeout": 5, "tirith_fail_open": True}
    assert DEFAULT_CONFIG["_config_version"] == 50


def test_tirith_keys_are_not_treated_as_retired_legacy_keys():
    from hermes_cli.config_migrations import LEGACY_KEY_STEPS, MIGRATIONS

    assert 50 in dict(MIGRATIONS)
    assert 50 not in LEGACY_KEY_STEPS
