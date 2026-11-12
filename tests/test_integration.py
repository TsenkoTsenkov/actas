"""Integration fixes made after the milestones: help tiers and topics
(SPEC 0.1.3, 10.2), the v2 init starter (0.1.2), `secret_env: {X: null}`
(2.3/4.7), the package-build helper prefix (5.4), Ctrl-C in main(),
`list --check` pooling (4.2), sso-session grouping (D15), and the doctor
additions (--fix, a [default] AWS profile, Gemini .env files). Sandbox only:
fake binaries, temp HOME and config."""

import json
import os
import re
import stat
import sys
import unittest

from actas_testlib import REPO, Sandbox, has_yaml


class Help(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {}}}

    def test_help_lists_the_everyday_tier_and_one_advanced_line(self):  # SPEC test 64
        for args in (("help",), ()):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
            cmds = re.findall(r"^  (\S+)\s", r.stdout, re.M)
            self.assertEqual(cmds, ["init", "run", "exec", "status", "list", "login", "sync", "doctor"])
            adv = [x for x in r.stdout.splitlines() if x.startswith("advanced:")]
            self.assertEqual(len(adv), 1)
            for c in ("shell", "secret", "desktop", "instructions", "migrate", "aws-handoff"):
                self.assertIn(c, adv[0])

    def test_topics(self):
        r = self.cli("help", "manifest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("The four rules", r.stdout)
        r = self.cli("help", "agents")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(self.m.AGENT_RULES.strip(), r.stdout)
        r = self.cli("help", "manifst")
        self.assertEqual(r.returncode, 2)
        self.assertIn("did you mean: manifest", r.stderr)

    def test_full_help_has_no_suppressed_options(self):
        r = self.cli("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("SUPPRESS", r.stdout)
        self.assertIn("aws-handoff", r.stdout)

    def test_ctrl_c_exits_130(self):
        def interrupted(argv=None):
            raise KeyboardInterrupt
        self.m._main = interrupted
        with self.assertRaises(SystemExit) as c:
            self.m.main([])
        self.assertEqual(c.exception.code, 130)


class Init(Sandbox):

    def test_writes_the_v2_starter_without_an_age_key(self):  # SPEC 0.1.2, test 64
        fresh = self.tmp / "fresh"
        r = self.cli("init", env={"ACTAS_ROOT": str(fresh), "SOPS_AGE_KEY_FILE": str(self.tmp / "none")})
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (fresh / "environments.yaml").read_text()
        if self.m.yaml_available():
            self.assertEqual(text, self.m.INIT_MANIFEST)
            self.assertLess(len(text.splitlines()), 20)
        else:                       # F19: no YAML parser here, so a JSON starter
            self.assertEqual(json.loads(text), self.m.INIT_DOC)
        self.assertIn("keychain:", r.stderr + r.stdout)
        self.assertFalse((fresh / ".sops.yaml").exists())
        self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o700)
        r = self.cli("status", "personal", "--json", env={"ACTAS_ROOT": str(fresh)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["profile"], "personal")

    @unittest.skipUnless(has_yaml(), "needs PyYAML")
    def test_json_starter_is_the_yaml_starter(self):
        import yaml
        self.assertEqual(yaml.safe_load(self.m.INIT_MANIFEST), self.m.INIT_DOC)


class SecretEnvNull(Sandbox):
    manifest = {"version": 2,
                "roles": {"npm": {"secret_env": {"NPM_TOKEN": "sops:a/npm"}}},
                "profiles": {"p": {"use": ["npm"], "secret_env": {"NPM_TOKEN": None}}}}

    def test_null_unsets(self):  # SPEC 2.3, 4.7
        cfg = self.m.load(quiet=True)
        self.assertEqual(cfg.problems.items, [])
        res = self.m.assemble(self.m.resolve("p"), want_secrets=False, parent={"NPM_TOKEN": "old"})
        self.assertNotIn("NPM_TOKEN", res["env"])
        self.assertIn("NPM_TOKEN", res["scrubbed"])


class PackageHelper(Sandbox):

    def test_package_build_uses_the_console_script(self):  # SPEC 5.4, test 37
        self.m.BUILD = "package"
        script = self.tmp / "venv" / "bin" / "actas"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\n")
        saved = sys.argv[0]
        sys.argv[0] = str(script)
        try:
            self.assertEqual(self.m.helper_prefix(), self.m.shell_quote(str(script)))
            self.assertTrue(self.m.api_key_helper("sops:a/b").startswith(self.m.shell_quote(str(script))
                                                                        + " secret --ref "))
        finally:
            sys.argv[0] = saved


class Doctor(Sandbox):
    manifest = {"version": 2, "profiles": {
        "with-aws": {"aws": {"profile": "dev"}},
        "no-aws": {},
        "off": {"aws": None},
        "gem": {"match": "%(tmp)s/g", "tool": "gemini"}}}

    def checks(self, *args):
        r = self.cli("doctor", "--json", *args)
        return {c["name"]: c for c in json.loads(r.stdout)["checks"]}

    def test_default_aws_profile_warning(self):  # SPEC 4.8
        self.assertNotIn("aws default", self.checks())
        (self.home / ".aws").mkdir()
        (self.home / ".aws" / "config").write_text("[default]\nregion = eu-west-1\n")
        c = self.checks()["aws default"]
        self.assertEqual(c["status"], "warn")
        self.assertIn("no-aws", c["detail"])
        self.assertIn("gem", c["detail"])
        self.assertNotIn("with-aws", c["detail"])
        self.assertNotIn("off,", c["detail"] + ",")

    def test_gemini_env_files(self):  # SPEC 6.3
        g = self.tmp / "g" / "sub"
        g.mkdir(parents=True)
        self.assertNotIn("gemini env", self.checks("--cwd", str(g)))
        (self.tmp / "g" / ".env").write_text("GEMINI_API_KEY=x\n")
        c = self.checks("--cwd", str(g))["gemini env"]
        self.assertEqual(c["status"], "warn")
        self.assertIn(".env", c["detail"])

    def test_fix_tightens_owned_dirs_only(self):  # SPEC 1, 10.2
        os.chmod(str(self.root), 0o755)
        state = self.tmp / "state"
        (state / "homes" / "claude" / "work").mkdir(parents=True)
        os.chmod(str(state / "homes" / "claude" / "work"), 0o755)
        other = self.tmp / "other"
        other.mkdir(mode=0o755)
        self.assertEqual(self.checks()["config dir"]["status"], "warn")
        c = self.checks("--fix")
        self.assertNotIn("config dir", c)
        for d in (self.root, state / "homes" / "claude" / "work"):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700, d)
        self.assertEqual(stat.S_IMODE(other.stat().st_mode), 0o755)


class AwsGrouping(Sandbox):
    manifest = {"version": 2, "profiles": {
        "a": {"aws": {"profile": "dev"}}, "b": {"aws": {"profile": "dev"}},
        "c": {"aws": {"profile": "other"}}, "d": {}}}

    def test_list_check_probes_each_profile_once(self):  # SPEC 4.2
        r = self.cli("list", "--check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        sts = [c["argv"] for c in self.calls("aws") if c["argv"][:2] == ["sts", "get-caller-identity"]]
        self.assertEqual(sorted(a[a.index("--profile") + 1] for a in sts), ["dev", "other"])
        rows = {p["name"]: p["check"] for p in json.loads(r.stdout)["profiles"]}
        self.assertEqual(rows["a"]["aws"], "ok")
        self.assertIsNone(rows["d"]["aws"])

    def test_portals_group_by_sso_session(self):  # D15
        (self.home / ".aws").mkdir()
        (self.home / ".aws" / "config").write_text(
            "[sso-session corp]\nsso_start_url = https://corp.example/start\n\n"
            "[profile dev]\nsso_session = corp\n[profile ops]\nsso_session = corp\n"
            "[profile other]\nsso_start_url = https://other.example/start\n[default]\nregion = x\n")
        self.assertEqual(self.m.aws_portals(), {"sso-session corp": ["dev", "ops"],
                                                "https://other.example/start": ["other"]})


class MinimalExample(Sandbox):

    def test_minimal_example_is_the_readme_manifest(self):  # SPEC 12, 0.1.1
        text = (REPO / "examples" / "minimal.yaml").read_text()
        readme = (REPO / "README.md").read_text()
        self.assertIn("```yaml\n" + text + "```", readme)
        self.assertLessEqual(len(text.strip().splitlines()), 15)
        if has_yaml():
            self.write_manifest(text)
            m = self.reload()
            self.assertEqual(m.load(quiet=True).problems.items, [])
            self.assertEqual(m.resolve(cwd=str(self.home / "code/client-a/x"))["name"], "client-a")


if __name__ == "__main__":
    unittest.main()
