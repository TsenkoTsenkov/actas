"""Tests for actas. Standard library only: python3 -m unittest discover tests

Every test points ACTAS_ROOT and XDG_CACHE_HOME at a temporary directory,
so nothing here reads or writes your real configuration.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from actas_testlib import SYSTEM_PATH, Sandbox

# The secrets round-trip needs the real sops and age (with a throwaway key in
# a temp dir). It is opt-in so no default test run ever invokes real sops:
# ACTAS_TEST_REAL_SOPS=1 python3 -m unittest discover -s tests (CI sets it).
REAL_SOPS = shutil.which("sops") if os.environ.get("ACTAS_TEST_REAL_SOPS") else None
REAL_AGE_KEYGEN = shutil.which("age-keygen") if REAL_SOPS else None

MANIFEST = """{
  "version": 1,
  "defaults": {"provider": "subscription"},
  "environments": {
    "work": {
      "match": ["%(root)s/work"],
      "aws": {"profile": "work-sso", "region": "eu-west-1"},
      "github": {"name": "Ada Example", "email": "ada@example.com"}
    },
    "work-infra": {
      "match": ["%(root)s/work/infra"],
      "provider": "gateway",
      "gateway": {"base_url": "https://llm-gateway.example.com", "secret": "token"}
    },
    "oss": {
      "match": ["%(root)s/oss/*"],
      "env": {"FOO": "bar"}
    }
  }
}
"""


class Base(Sandbox):
    """Isolated per test: HOME, ACTAS_ROOT, ACTAS_STATE, XDG dirs and a PATH
    of fake binaries (see actas_testlib)."""

    def setUp(self):
        super().setUp()
        self.write_manifest(MANIFEST % {"root": self.tmp})
        for d in ("work/infra", "work/api", "oss/lib"):
            (self.tmp / d).mkdir(parents=True)
        self.m = self.reload()


class Resolve(Base):
    def test_longest_match_wins(self):
        self.assertEqual(self.m.resolve(cwd=self.tmp / "work/api")["name"], "work")
        self.assertEqual(self.m.resolve(cwd=self.tmp / "work/infra")["name"], "work-infra")
        self.assertEqual(self.m.resolve(cwd=self.tmp / "work/infra/sub")["name"], "work-infra")

    def test_glob_match(self):
        self.assertEqual(self.m.resolve(cwd=self.tmp / "oss/lib")["name"], "oss")

    def test_explicit_name_and_unknown(self):
        self.assertEqual(self.m.resolve("oss")["name"], "oss")
        with self.assertRaises(SystemExit):
            self.m.resolve("nope")

    def test_no_match(self):
        with self.assertRaises(SystemExit):
            self.m.resolve(cwd="/")

    def test_defaults_merge(self):
        self.assertEqual(self.m.identities(self.m.resolve("oss"))["claude"]["provider"], "subscription")


class Settings(Base):
    def test_subscription_cancels_gateway_vars(self):
        # 2.0: removed from the process env (v1 set ""); settings still blank them.
        parent = dict(os.environ, ANTHROPIC_BASE_URL="https://gw", ANTHROPIC_AUTH_TOKEN="t")
        res = self.m.assemble(self.m.resolve("work"), parent=parent)
        env = self.m.child_env(res, parent)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertEqual(env["AWS_PROFILE"], "work-sso")
        self.assertEqual(env["ACTAS"], "work")
        self.assertEqual(self.m.settings_for(self.m.resolve("work"))["env"]["ANTHROPIC_BASE_URL"], "")

    def test_git_identity_is_per_process(self):
        env = self.m.assemble(self.m.resolve("work"))["env"]
        pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
                 for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(pairs["user.email"], "ada@example.com")

    def test_gateway_token_never_in_settings(self):
        s = self.m.settings_for(self.m.resolve("work-infra"))
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], "https://llm-gateway.example.com")
        self.assertIn(" secret --ref sops:work-infra/token", s["apiKeyHelper"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", s["env"])

    def test_shell_values_are_quoted(self):
        self.assertEqual(self.m.shell_quote("a'b; rm -rf /"), "'a'\"'\"'b; rm -rf /'")

    def test_settings_file_is_private(self):
        f = self.tmp / "cache" / "actas" / "x.json"
        self.m.write_private(f, "{}")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)


class Names(Base):
    def test_rejects_path_injection(self):
        for bad in ('a"]["b', "a b", "", "-x", "../x"):
            with self.assertRaises(SystemExit, msg=bad):
                self.m.secret_path(bad, "token")
            with self.assertRaises(SystemExit, msg=bad):
                self.m.secret_path("work", bad)

    def test_accepts_normal_names(self):
        self.assertEqual(self.m.secret_path("work-infra", "gh_token"), '["work-infra"]["gh_token"]')


class Sync(Base):
    def test_sync_writes_and_drops_stale_aws(self):
        proj = self.tmp / "oss/lib/.claude/settings.local.json"
        proj.parent.mkdir(parents=True)
        proj.write_text(json.dumps({"env": {"AWS_PROFILE": "old", "KEEP": "1"},
                                    "awsAuthRefresh": "aws sso login --profile old",
                                    "permissions": {"allow": ["Bash(ls)"]}}))
        r = self.cli("sync", "oss")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(proj.read_text())
        self.assertNotIn("AWS_PROFILE", data["env"])
        self.assertNotIn("awsAuthRefresh", data)
        self.assertEqual(data["env"]["KEEP"], "1")
        self.assertEqual(data["env"]["FOO"], "bar")
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})


class Cli(Base):
    def test_unknown_command_prints_full_help(self):
        r = self.cli("lst")
        self.assertEqual(r.returncode, 2)
        self.assertIn("commands:", r.stderr)
        self.assertIn("did you mean: list?", r.stderr)

    def test_stray_claude_flag_hints_double_dash(self):
        r = self.cli("run", "work", "--resume")
        self.assertEqual(r.returncode, 2)
        self.assertIn("after --", r.stderr)

    def test_no_args_shows_help(self):
        r = self.cli()
        self.assertEqual(r.returncode, 0)
        self.assertIn("commands:", r.stdout)

    def test_cwd_after_command(self):
        r = self.cli("shell", "--cwd", str(self.tmp / "oss/lib"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("export ACTAS=oss", r.stdout)

    def test_list(self):
        r = self.cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("work-infra", r.stdout)
        self.assertIn("no (gateway)", r.stdout)

    def test_init_never_overwrites(self):
        before = (self.root / "environments.yaml").read_text()
        r = self.cli("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "environments.yaml").read_text(), before)

    def test_init_fresh(self):
        fresh = self.tmp / "fresh"
        env = dict(os.environ, ACTAS_ROOT=str(fresh), SOPS_AGE_KEY_FILE=str(self.tmp / "none"))
        r = subprocess.run([sys.executable, str(self.bin), "init"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((fresh / "environments.yaml").exists())


@unittest.skipUnless(REAL_SOPS and REAL_AGE_KEYGEN, "set ACTAS_TEST_REAL_SOPS=1 (needs sops and age)")
class Secrets(Base):
    def setUp(self):
        super().setUp()
        os.environ["PATH"] = os.pathsep.join(
            [os.path.dirname(REAL_SOPS), os.path.dirname(REAL_AGE_KEYGEN), SYSTEM_PATH])

    def test_roundtrip_via_stdin(self):
        key = self.tmp / "age.txt"
        subprocess.run([REAL_AGE_KEYGEN, "-o", str(key)], check=True, capture_output=True)
        os.environ["SOPS_AGE_KEY_FILE"] = str(key)
        try:
            (self.root / "environments.yaml").rename(self.tmp / "m.bak")
            self.assertEqual(self.cli("init").returncode, 0)
            (self.tmp / "m.bak").replace(self.root / "environments.yaml")
            r = self.cli("secret", "work-infra", "token", "--set", stdin="s3cret\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("s3cret", (self.root / "secrets.enc.yaml").read_text())
            r = self.cli("secret", "work-infra", "token")
            self.assertEqual(r.stdout, "s3cret")
        finally:
            os.environ.pop("SOPS_AGE_KEY_FILE", None)


if __name__ == "__main__":
    unittest.main()
