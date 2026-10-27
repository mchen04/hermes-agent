import pytest

from pm.plugin_declarations import read_python_declaration


@pytest.mark.parametrize("manifest,expected", [
    ("python_dependencies: ['demo>=1,<2']\n", True),
    ("python_dependencies: []\n", False),
    ("python_runtime: external\npython_dependencies: ['demo>=1,<2']\n", False),
])
def test_membership_field_uses_the_current_package_admission_rule(tmp_path, manifest, expected):
    (tmp_path / "plugin.yaml").write_text("name: sample\n" + manifest)
    declaration = read_python_declaration(tmp_path)
    assert declaration.is_member is expected
    assert declaration.is_member == declaration.installs_packages


def test_an_empty_pyproject_does_not_become_a_member(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\nversion = "1.0"\ndependencies = []\n')
    declaration = read_python_declaration(tmp_path)
    assert declaration.is_member is False
