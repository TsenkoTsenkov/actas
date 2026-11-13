"""Fixes made while integrating the improvement branches: a map on a combined
rule list, a secret in the agent's --reason, YAML 1.1 boolean words in a
version 2 manifest, and the macOS process reader with a huge environment."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from actas_testlib import Sandbox

CS = {"claude": {"settings": {"permissions": {"deny": ["Bash(rm -rf *)"]}}}}


class CombinedListsTakeLists(Sandbox):
    manifest = {"version": 2, "roles": {"guard": CS},
                "profiles": {"q": {"use": ["guard"], "claude": {"settings": {"permissions": {"deny": {}}}}},
                             "r": {"use": ["guard"], "permissions": {"ask": ["Bash(git push *)"]}},
                             "s": {"claude": {"settings": {"permissions": {"ask": 5}}}}}}

    def test_a_map_cannot_replace_a_role_deny_list(self):
        cfg = self.m.load(quiet=True)
        with self.assertRaises(self.m.ManifestError) as e:
            self.m.merge_profile("q", cfg)
        self.assertIn("claude.settings.permissions.deny in q: expected a list of strings", str(e.exception))

    def test_a_scalar_is_an_error_not_a_traceback(self):
        with self.assertRaises(self.m.ManifestError):
            self.m.merge_profile("s", self.m.load(quiet=True))

    def test_lists_still_combine(self):
        prof = self.m.merge_profile("r", self.m.load(quiet=True))
        self.assertIn("Bash(rm -rf *)", self.m.agent_policy(prof)["deny"])


class ReasonIsRedacted(Sandbox):
    manifest = {"version": 2, "profiles": {"a": {}}}

    def test_reason_hides_token_and_env_value(self):
        os.environ["NPM_TOKEN"] = "npm-value-0123456789"
        tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        shown = self.m.clean_reason(f"push with {tok} and npm-value-0123456789")
        self.assertNotIn(tok, shown)
        self.assertNotIn("npm-value-0123456789", shown)
        self.assertIn("<token>", shown)
        self.assertIn("<$NPM_TOKEN>", shown)
        T = self.m.merge_profile("a", self.m.load(quiet=True))
        ask = self.m.ask_user(T, ["true"], ["actas", "exec", "a", "--", "true"], f"because {tok}")
        self.assertNotIn(tok, ask)


class Yaml11Words(Sandbox):
    def doctor(self, text):
        self.write_manifest(text)
        r = self.cli("doctor")
        return r.returncode, r.stdout + r.stderr

    def setUp(self):
        super().setUp()
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("needs PyYAML")

    def test_confirm_yes_has_a_clear_error(self):
        code, out = self.doctor("version: 2\nprofiles:\n  a:\n    confirm: yes\n")
        self.assertNotEqual(code, 0, out)
        self.assertIn("reads yes/no/on/off as text", out)

    def test_claude_settings_word_warns(self):
        code, out = self.doctor("version: 2\nprofiles:\n  a:\n    claude:\n      settings:\n"
                                "        includeCoAuthoredBy: no\n")
        self.assertIn("passed to Claude as text, not a boolean", out)


@unittest.skipUnless(sys.platform == "darwin", "macOS process reader")
class ChainWithHugeEnvironment(unittest.TestCase):
    def test_read_chain_matches_ps(self):
        here = Path(__file__).resolve().parent
        code = f"""
import sys
sys.path.insert(0, {str(here)!r})
from actas_testlib import load_module
from pathlib import Path
m = load_module(Path({str(here.parent / 'bin' / 'actas')!r}))
chain = m.read_chain()
full = m.read_ps()
if chain is None:
    print('SKIP'); sys.exit(0)
rows = {{}}
for l in full.splitlines():
    if l.split() and l.split()[0].isdigit():
        rows[l.split()[0]] = l.split(None, 7)[-1]
bad = [l for l in chain.splitlines() if l.split() and rows.get(l.split()[0]) not in (None, l.split(None, 7)[-1])]
print('BAD' if bad else 'OK', bad)
"""
        env = dict(os.environ, BIG="x" * 300000)
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        if r.stdout.startswith("SKIP"):
            self.skipTest("no native reader here")
        self.assertTrue(r.stdout.startswith("OK"), r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
