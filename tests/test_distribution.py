"""Distribution channels: the Homebrew formula, the release workflow's PyPI
and Homebrew jobs, the package metadata and the README's install lines."""

import importlib.util
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / ".github" / "scripts" / "brew_formula.py"
RELEASE = (REPO / ".github" / "workflows" / "release.yml").read_text()
README = (REPO / "README.md").read_text()


def load_formula_module():
    spec = importlib.util.spec_from_file_location("brew_formula", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HomebrewFormula(unittest.TestCase):
    def setUp(self):
        self.mod = load_formula_module()

    def test_committed_formula_is_generated(self):
        """Formula/actas.rb is the generator's output (HEAD-only before the
        first stable release, or a stable release the workflow wrote)."""
        committed = (REPO / "Formula" / "actas.rb").read_text()
        m = re.search(r'version "([^"]+)"\n  sha256 "([0-9a-f]{64})"', committed)
        want = self.mod.formula(*m.groups()) if m else self.mod.formula()
        self.assertEqual(committed, want, "regenerate with .github/scripts/brew_formula.py")

    def test_stable_formula_points_at_the_release_asset(self):
        out = self.mod.formula("2.0.0", "a" * 64)
        self.assertIn('url "https://github.com/TsenkoTsenkov/actas/releases/download/v2.0.0/actas"', out)
        self.assertIn('sha256 "' + "a" * 64 + '"', out)
        self.assertIn('head "https://github.com/TsenkoTsenkov/actas.git"', out)

    def test_head_formula_has_no_url(self):
        out = self.mod.formula()
        self.assertNotIn("\n  url ", out)
        self.assertNotIn("sha256", out)

    def test_rejects_prereleases_and_bad_checksums(self):
        with self.assertRaises(SystemExit):
            self.mod.formula("2.0.0b1", "a" * 64)
        with self.assertRaises(SystemExit):
            self.mod.formula("2.0.0", "A" * 64)
        with self.assertRaises(SystemExit):
            self.mod.formula("2.0.0", "a" * 63)


class ReleaseWorkflow(unittest.TestCase):
    def test_pypi_uses_trusted_publishing(self):
        pypi = RELEASE.split("\n  pypi:\n", 1)[1].split("\n  homebrew:\n", 1)[0]
        self.assertIn("pypa/gh-action-pypi-publish", pypi)
        self.assertIn("id-token: write", pypi)
        self.assertIn("name: pypi", pypi)
        self.assertNotIn("password:", pypi)         # no API token

    def test_homebrew_job_runs_only_for_stable_tags(self):
        brew = RELEASE.split("\n  homebrew:\n", 1)[1]
        self.assertIn("needs.release.outputs.stable == 'true'", brew)
        self.assertIn("brew_formula.py", brew)

    def test_release_assets_include_install_sh_and_checksums(self):
        create = RELEASE.split("gh release create", 1)[1].split("\n\n", 1)[0]
        for asset in ("dist/actas", "dist/install.sh", "dist/SHA256SUMS", "dist/*.whl", "dist/*.tar.gz"):
            self.assertIn(asset, create)


class Metadata(unittest.TestCase):
    def test_funding(self):
        self.assertEqual((REPO / ".github" / "FUNDING.yml").read_text().strip(),
                         "buy_me_a_coffee: tsenkotsenkov")
        self.assertIn("https://buymeacoffee.com/tsenkotsenkov", README)
        self.assertIn('Funding = "https://buymeacoffee.com/tsenkotsenkov"',
                      (REPO / "pyproject.toml").read_text())

    def test_readme_install_lines(self):
        install = README.split("## Install\n", 1)[1].split("\n## ", 1)[0]
        for line in ("uv tool install actas", "pipx install actas", "brew install actas",
                     "releases/latest/download/install.sh \\| sh", "wsl --install",
                     "uv tool install git+https://github.com/TsenkoTsenkov/actas"):
            self.assertIn(line, install)


if __name__ == "__main__":
    unittest.main()
