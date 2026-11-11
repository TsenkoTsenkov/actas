"""M0 (v1.1) bug fixes: D1 D2 D4 D5 D8 D10 D12 D13 D16 D17 D22 D29 D31,
sops --value-stdin. Defect numbers refer to the compat-contract notes."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest

from ccenv_testlib import SYSTEM_PATH, Sandbox, make_fakebin

V1 = {
    "version": 1,
    "defaults": {"provider": "subscription"},
    "environments": {
        "personal": {
            "match": ["%(tmp)s/w/personal"],
            "aws": {"profile": "personal-sso", "region": "eu-west-1"},
            "github": {"account": "ada-personal", "name": "Ada Example", "email": "ada@example.com"},
        },
        "client-a": {
            "match": ["%(tmp)s/w/client-a"],
            "provider": "gateway",
            "gateway": {"base_url": "https://llm.client-a.example", "secret_from": "corp"},
            "secret_env": {"NPM_TOKEN": "npm", "EXTRA": "extra"},
        },
        "partner": {
            "match": ["%(tmp)s/w/partner"],
            "github": {"name": "Ada Example", "email": "ada@example.com"},
            "secret_env": {"GH_TOKEN": "gh_token", "GITHUB_TOKEN": "gh_token"},
            "claude_args": ["--permission-mode", "plan"],
        },
    },
}
SECRETS = {"corp": {"token": "gw-secret"},
           "client-a": {"npm": "npm-secret", "extra": "extra-secret"},
           "partner": {"gh_token": "ghp_partner_pat"}}


class Base(Sandbox):
    manifest = V1
    secrets = SECRETS

    def setUp(self):
        super().setUp()
        for d in ("w/personal", "w/client-a", "w/partner"):
            (self.tmp / d).mkdir(parents=True)

    def git_pairs(self, env):
        n = int(env["GIT_CONFIG_COUNT"])
        return [(env.get(f"GIT_CONFIG_KEY_{i}"), env.get(f"GIT_CONFIG_VALUE_{i}")) for i in range(n)]


class DidYouMean(Base):  # D1
    def test_hint_is_computed_not_parsed(self):
        for bad, good in (("lst", "list"), ("statu", "status"), ("doctr", "doctor")):
            r = self.cli(bad)
            self.assertEqual(r.returncode, 2)
            self.assertIn(f"did you mean: {good}?", r.stderr)
            self.assertIn("commands:", r.stderr)

    def test_help_topic_and_no_close_match(self):
        r = self.cli("help", "migrat")
        self.assertEqual(r.returncode, 2)
        self.assertIn("did you mean: migrate?", r.stderr)
        r = self.cli("zzzzqq")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("did you mean", r.stderr)

    def test_hidden_commands_are_not_suggested_or_listed(self):
        r = self.cli("--help")
        self.assertNotIn("git-credential", r.stdout)


class GitHelper(Base):  # D2, D29
    def test_secret_sourced_gh_token_gets_the_helper(self):
        env = self.m.assemble(self.m.resolve("partner"))["env"]
        self.assertEqual(env["GH_TOKEN"], "ghp_partner_pat")
        self.assertEqual(env["GITHUB_TOKEN"], "ghp_partner_pat")
        pairs = self.git_pairs(env)
        self.assertEqual(pairs[0], ("user.name", "Ada Example"))
        self.assertEqual(pairs[2], ("credential.https://github.com.helper", ""))
        key, helper = pairs[3]
        self.assertEqual(key, "credential.https://github.com.helper")
        self.assertIn("git-credential --ref sops:partner/gh_token --username x-access-token", helper)
        self.assertNotIn("ghp_partner_pat", helper)

    @unittest.skipUnless(shutil.which("git", path=SYSTEM_PATH), "needs git")
    def test_git_credential_fill_returns_the_pat(self):
        m = self.m
        res = m.assemble(m.resolve("partner"))
        env = m.child_env(res, os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        r = subprocess.run([shutil.which("git", path=SYSTEM_PATH), "credential", "fill"],
                           input="protocol=https\nhost=github.com\n\n", capture_output=True,
                           text=True, env=env, cwd=str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("username=x-access-token", r.stdout)
        self.assertIn("password=ghp_partner_pat", r.stdout)

    def test_account_helper_uses_gh_ref(self):
        pairs = self.git_pairs(self.m.assemble(self.m.resolve("personal"))["env"])
        self.assertIn("--ref gh:ada-personal", pairs[-1][1])

    def test_git_config_appends_to_parent(self):
        os.environ.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "a.b", "GIT_CONFIG_VALUE_0": "1",
                           "GIT_CONFIG_KEY_1": "c.d", "GIT_CONFIG_VALUE_1": "2"})
        env = self.m.assemble(self.m.resolve("partner"))["env"]
        self.assertEqual(env["GIT_CONFIG_COUNT"], "6")
        self.assertEqual(env["CCENV_GIT_BASE"], "2")
        self.assertEqual(env["GIT_CONFIG_KEY_2"], "user.name")
        self.assertNotIn("GIT_CONFIG_KEY_0", env)        # parent pairs kept, not rewritten

    def test_nested_ccenv_truncates_to_base(self):
        os.environ.update({"GIT_CONFIG_COUNT": "5", "CCENV_GIT_BASE": "1"})
        env = self.m.assemble(self.m.resolve("personal"))["env"]
        self.assertEqual(env["CCENV_GIT_BASE"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "user.name")
        self.assertEqual(env["GIT_CONFIG_COUNT"], str(1 + 4))
        child = self.m.child_env(self.m.assemble(self.m.resolve("client-a")),
                                 dict(os.environ, GIT_CONFIG_KEY_3="stale"))
        self.assertEqual(child["GIT_CONFIG_COUNT"], "1")   # no pairs: back to the base
        self.assertNotIn("GIT_CONFIG_KEY_3", child)


class GitCredential(Base):
    def test_get_format_and_noops(self):
        r = self.cli("git-credential", "--ref", "sops:partner/gh_token", "--username", "bot", "get",
                     stdin="protocol=https\nhost=github.com\n\n")
        self.assertEqual(r.stdout, "username=bot\npassword=ghp_partner_pat\n")
        for op in ("store", "erase"):
            r = self.cli("git-credential", "--ref", "sops:partner/gh_token", op, stdin="x=y\n\n")
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_refuses_control_characters_and_missing(self):
        (self.root / "secrets.enc.yaml").write_text(json.dumps({"p": {"t": "a\nb"}}))
        r = self.cli("git-credential", "--ref", "sops:p/t", "get", stdin="")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self.cli("git-credential", "--ref", "sops:p/t", "--username", "x\ny", "get", stdin="")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self.cli("git-credential", "--ref", "sops:p/missing", "get", stdin="")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class HelperPaths(Base):  # D8
    def test_api_key_helper_is_absolute(self):
        s = self.m.settings_for(self.m.resolve("client-a"))
        words = shlex.split(s["apiKeyHelper"])
        self.assertTrue(os.path.isabs(words[0]) and os.path.isabs(words[1]), words)
        self.assertEqual(words[1], str(self.bin))
        self.assertEqual(words[2:], ["secret", "--ref", "sops:corp/token"])
        r = subprocess.run(s["apiKeyHelper"], shell=True, capture_output=True, text=True,
                           env=dict(os.environ, PATH=""))
        self.assertEqual(r.stdout, "")   # no sops on an empty PATH (fallback disabled) ...
        r = subprocess.run(s["apiKeyHelper"], shell=True, capture_output=True, text=True,
                           env=dict(os.environ, PATH="", CCENV_PATH_EXTRA=str(self.fakebin)))
        self.assertEqual(r.stdout, "gw-secret")  # ... but CCENV_PATH_EXTRA finds it

    def test_aws_auth_refresh_uses_absolute_aws(self):
        s = self.m.settings_for(self.m.resolve("personal"))
        self.assertEqual(s["awsAuthRefresh"], f"{self.fakebin}/aws sso login --profile personal-sso")

    def test_venv_python_is_replaced_by_the_base_interpreter(self):
        m, real = self.m, (sys.prefix, sys.base_prefix, getattr(sys, "_base_executable", None))
        venv = self.tmp / "proj" / ".venv"
        base = self.tmp / "base" / "python3"
        base.parent.mkdir(parents=True)
        base.write_text("")
        base.chmod(0o755)
        try:
            sys.prefix, sys.base_prefix = str(venv), str(self.tmp / "base")
            sys._base_executable = str(venv / "bin" / "python3")     # inside the venv
            os.environ["PATH"] = f"{venv}/bin:{base.parent}"
            self.assertEqual(m.helper_python(), str(base))
            sys._base_executable = str(base)
            self.assertEqual(m.helper_python(), str(base))
        finally:
            sys.prefix, sys.base_prefix = real[0], real[1]
            if real[2] is None:
                del sys._base_executable
            else:
                sys._base_executable = real[2]


class Sync(Base):  # D4 D5 D10 D12
    manifest = {"version": 1, "environments": {
        "specific": {"match": ["%(tmp)s/w/a"]},
        "broad": {"match": ["%(tmp)s/w/*"], "aws": {"profile": "broad"}},
    }}

    def setUp(self):
        super().setUp()
        for d in ("w/a", "w/b"):
            (self.tmp / d).mkdir(parents=True)

    def read(self, d):
        return json.loads((self.tmp / d / ".claude" / "settings.local.json").read_text())

    def test_longest_match_and_private_atomic_writes(self):
        r = self.cli("sync")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("AWS_PROFILE", self.read("w/a")["env"])     # v1 wrote "broad" here
        self.assertEqual(self.read("w/b")["env"]["AWS_PROFILE"], "broad")
        self.assertIn("resolves to specific", r.stderr)
        f = self.tmp / "w/b/.claude/settings.local.json"
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(f.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sorted(p.name for p in f.parent.iterdir()), ["settings.local.json"])

    def test_existing_file_tightened_and_bad_files_skipped(self):
        good = self.tmp / "w/b/.claude/settings.local.json"
        good.parent.mkdir()
        good.write_text(json.dumps({"env": None, "permissions": {"allow": ["Bash(ls)"]}}))
        good.chmod(0o644)
        bad = self.tmp / "w/a/.claude/settings.local.json"
        bad.parent.mkdir()
        bad.write_text("{not json")
        r = self.cli("sync")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("skip", r.stderr)
        self.assertEqual(bad.read_text(), "{not json")
        data = self.read("w/b")
        self.assertEqual(data["env"]["AWS_PROFILE"], "broad")
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(good.stat().st_mode & 0o777, 0o600)

    def test_unknown_profile_is_a_message(self):
        r = self.cli("sync", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("unknown profile 'nope'", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class MissingBinaries(Base):  # D13
    def only(self, *names):
        # Only these fakes: no system dirs, which may hold a real gh or aws
        # (GitHub's Ubuntu runners ship /usr/bin/gh). Fakes need no PATH.
        # (ps needs no PATH: the sandbox binary's PS_BINS names the fake.)
        os.environ["PATH"] = str(make_fakebin(self.tmp / "only", only=names + ("ps",)))

    def test_run_without_aws_or_gh(self):
        self.only("claude", "sops")
        r = self.cli("run", "personal")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("gh not found", r.stderr)
        out = json.loads(self.out.read_text())
        self.assertNotIn("awsAuthRefresh", out["settings"])
        self.assertEqual(out["env"]["AWS_PROFILE"], "personal-sso")

    def test_run_without_claude(self):
        self.only("aws", "gh")
        r = self.cli("run", "personal", "--no-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("claude not found on PATH", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_doctor_and_list_check_without_tools(self):
        self.only()
        r = self.cli("doctor")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("optional-missing", r.stdout)
        r = self.cli("list", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class Validation(Base):  # D16 D17 D18
    def test_string_claude_args_are_split(self):
        v1 = json.loads(json.dumps(V1))
        v1["environments"]["partner"]["claude_args"] = "--permission-mode plan"
        self.write_manifest(v1)
        r = self.cli("run", "partner", "--no-check", "--", "--resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude_args", r.stderr)
        argv = json.loads(self.out.read_text())["argv"]
        self.assertEqual(argv[2:], ["--permission-mode", "plan", "--resume"])

    def test_env_key_injection_is_rejected(self):
        self.write_manifest({"version": 1, "environments": {"x": {"env": {"X;echo INJECTED;Y": "1"}}}})
        r = self.cli("shell", "x")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("invalid variable name", r.stderr)

    def test_env_null_unsets(self):
        self.write_manifest({"version": 1, "environments": {"x": {"env": {"DROP": None, "KEEP": "1"}}}})
        r = self.cli("shell", "x", env={"DROP": "parent"})
        self.assertIn("export KEEP=1", r.stdout)
        self.assertNotIn("DROP=", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("unset DROP"), r.stdout)


class Secrets(Base):  # D22 D31 --value-stdin
    def test_one_decrypt_for_all_refs(self):
        res, err = self.quiet(self.m.assemble, self.m.resolve("client-a"))
        env = res["env"]
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "gw-secret")
        self.assertEqual(env["NPM_TOKEN"], "npm-secret")
        self.assertEqual(env["EXTRA"], "extra-secret")
        self.assertEqual(len(self.calls("sops")), 1)
        self.assertIn("--output-type", self.calls("sops")[0]["argv"])

    def test_missing_secret_warns_and_continues(self):
        (self.root / "secrets.enc.yaml").write_text(json.dumps({"client-a": {"npm": "x"}}))
        res, err = self.quiet(self.m.assemble, self.m.resolve("client-a"))
        env = res["env"]
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertIn("secret corp.token is not set", err)
        self.assertIn("ccenv secret corp token --set", err)

    def test_set_uses_value_stdin(self):
        r = self.cli("secret", "partner", "new_key", "--set", stdin="s3cret-value\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        sets = [c for c in self.calls("sops") if c["argv"][:1] == ["set"] and "--help" not in c["argv"]]
        self.assertEqual(len(sets), 1)
        self.assertIn("--value-stdin", sets[0]["argv"])
        self.assertNotIn("s3cret-value", json.dumps(sets[0]["argv"]))
        self.assertEqual(json.loads(sets[0]["stdin"]), "s3cret-value")
        r = self.cli("secret", "partner", "new_key")
        self.assertEqual(r.stdout, "s3cret-value")

    def test_old_sops_falls_back_with_warning(self):
        r = self.cli("secret", "partner", "k2", "--set", stdin="v2", env={"FAKE_SOPS_OLD": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("visible in `ps`", r.stderr)

    def test_ref_form_and_exactly_one_newline(self):
        (self.root / "secrets.enc.yaml").write_text(json.dumps({"p": {"pem": "line\n\n", "t": "v"}}))
        r = self.cli("secret", "--ref", "sops:p/pem")
        self.assertEqual(r.stdout, "line\n")
        r = self.cli("secret", "--ref", "gh:ada_client@github.example.com")
        self.assertEqual(r.stdout, "gho_ada_client")
        self.assertIn(["auth", "token", "--user", "ada_client", "--hostname", "github.example.com"],
                      [c["argv"] for c in self.calls("gh")])
        r = self.cli("secret", "--ref", "sops:p/missing")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        r = self.cli("secret", "--ref", "nope")
        self.assertEqual((r.returncode, r.stdout), (1, ""))

    def test_v1_secret_does_not_need_a_valid_manifest(self):
        (self.root / "environments.yaml").write_text("{ this is not valid")
        r = self.cli("secret", "partner", "gh_token")
        self.assertEqual((r.returncode, r.stdout), (0, "ghp_partner_pat"))


if __name__ == "__main__":
    unittest.main()
