"""Security hardening (p6): credential values an agent's shell expanded into
a refused command are hidden in ask_user, the --json payload and the audit
log; Claude deny rules cover the exact helper command lines actas writes and
actas by any path; Gemini credential stores are Read-denied; the ask_user
warning covers package runners and wrappers. Everything here only tightens."""

import hashlib
import json
import re
import unittest

from test_m4 import AGENT, Base

GH_VALUE = "gho_FAKEsessionVALUE0123456789abcdef"      # fake: never a real credential
PASTED = "ghp_" + "A1b2C3d4" * 4 + "Zz00"           # token-shaped, in no variable
DSN_VALUE = "postgres://u:FAKEpw-0042@db.example/x"


def matches(rule, command):
    """Claude's Bash(...) rule syntax: `*` matches any characters."""
    m = re.fullmatch(r"Bash\((.*)\)", rule)
    if not m:
        return False
    return re.fullmatch(".*".join(map(re.escape, m.group(1).split("*"))), command, re.S) is not None


class Redaction(Base):
    def setUp(self):
        super().setUp()
        self.record("std")

    def refuse(self, *cmd, env=None):
        code, out, err = self.call("exec", "pwf", "--json", "--", *cmd, env={**AGENT, **(env or {})})
        self.assertEqual(code, 3, err)
        return json.loads(out), out, err

    def test_session_variable_value_is_hidden(self):
        cmd = ["curl", "-H", f"Authorization: Bearer {GH_VALUE}", "https://api.github.com/user"]
        d, out, err = self.refuse(*cmd, env={"GH_TOKEN": GH_VALUE})
        self.assertNotIn(GH_VALUE, out + err)
        self.assertEqual(d["command"][2], "Authorization: Bearer <$GH_TOKEN>")
        self.assertIn("curl -H 'Authorization: Bearer <$GH_TOKEN>'", d["ask_user"])
        self.assertIn("actas exec pwf -- curl -H 'Authorization: Bearer <$GH_TOKEN>'", d["ask_user"])
        self.assertIn("Note: credential values are hidden as <$GH_TOKEN>", d["ask_user"])
        self.assertIn("profile pwf's own value", d["ask_user"])
        self.assertIn(d["ask_user"], err)
        line = self.audit_lines()[-1]
        self.assertNotIn(GH_VALUE, json.dumps(line))
        self.assertEqual(line["argv_sha256"], hashlib.sha256(json.dumps(cmd).encode()).hexdigest(),
                         "the audit hash still covers the real argv")

    def test_token_shaped_value_is_hidden(self):
        d, out, err = self.refuse("gh", "api", "-H", f"Authorization: token {PASTED}", "user")
        self.assertNotIn(PASTED, out + err)
        self.assertIn("Authorization: token <token>", d["command"])
        self.assertIn("hidden as <token>", d["ask_user"])
        d, out, err = self.refuse("curl", "-H", "Authorization: Bearer opaque.Value_1234567890")
        self.assertNotIn("opaque.Value_1234567890", out + err)
        self.assertEqual(d["command"][-1], "Authorization: Bearer <token>")

    def test_manifest_secret_env_names_are_hidden(self):
        self.write_manifest({**json.loads(json.dumps(__import__("test_m4").MANIFEST)), "defaults": {
            "secret_env": {"DB_DSN": "sops:std/x"}}})
        self.reload()
        self.patch()
        d, out, err = self.refuse("psql", DSN_VALUE, env={"DB_DSN": DSN_VALUE})
        self.assertNotIn(DSN_VALUE, out + err)
        self.assertEqual(d["command"], ["psql", "<$DB_DSN>"])

    def test_names_that_say_secret_are_hidden_and_plain_values_kept(self):
        d, out, err = self.refuse("deploy", "--key", "s3cr3t-value-77", "--region", "eu-west-1",
                                  env={"MY_SERVICE_PASSWORD": "s3cr3t-value-77", "AWS_REGION": "eu-west-1",
                                       "SOME_TOKEN": "short"})
        self.assertNotIn("s3cr3t-value-77", out + err)
        self.assertEqual(d["command"], ["deploy", "--key", "<$MY_SERVICE_PASSWORD>", "--region", "eu-west-1"])

    def test_nothing_hidden_means_no_note(self):
        d, _, _ = self.refuse("terraform", "plan", env={"GH_TOKEN": GH_VALUE})
        self.assertEqual(d["command"], ["terraform", "plan"])
        self.assertNotIn("Note:", d["ask_user"])

    def test_human_only_refusal_hides_values(self):
        code, out, err = self.call("login", PASTED, env=AGENT)
        self.assertEqual(code, 3, err)
        self.assertNotIn(PASTED, out + err)
        self.assertNotIn(PASTED, (self.m.STATE / "audit.jsonl").read_text())

    def test_audit_cmd0_hidden(self):
        self.call("exec", "pwf", "--", GH_VALUE, env={**AGENT, "GH_TOKEN": GH_VALUE})
        self.assertEqual(self.audit_lines()[-1]["cmd0"], "<$GH_TOKEN>")

    def test_redact_argv_unit(self):
        shown, hidden = self.m.redact_argv(["echo", f"--token={PASTED};", "npm_token", "ghs_deploy", "eyJhbGciOiJIUzI1NiJ9.e30.sig"])
        self.assertEqual(shown[2:4], ["npm_token", "ghs_deploy"])     # key names, not tokens
        self.assertEqual(shown[4], "<token>")
        self.assertEqual(hidden, ["<token>"])
        self.assertEqual(shown[1], "--token=<token>;")


class RunnerWarning(Base):
    WARN = "Warning: this runs a shell or interpreter"

    def ask(self, *cmd):
        return json.loads(self.call("exec", "std", "--json", "--", *cmd, env=AGENT)[1])["ask_user"]

    def test_code_runners_warn(self):
        for cmd in (["npx", "x"], ["uvx", "x"], ["bunx", "x"], ["deno", "run", "x.ts"], ["pipx", "run", "x"],
                    ["go", "run", "."], ["cargo", "run"], ["make", "deploy"], ["docker", "run", "img"],
                    ["kubectl", "exec", "pod", "--", "id"], ["sudo", "ls"], ["/usr/bin/nohup", "x"],
                    ["npm", "exec", "x"], ["uv", "run", "x.py"], ["ssh", "host", "id"], ["awk", "BEGIN{}"]):
            self.assertIn(self.WARN, self.ask(*cmd), cmd)

    def test_plain_commands_do_not_warn(self):
        for cmd in (["go", "build"], ["docker", "ps"], ["kubectl", "get", "pods"], ["terraform", "plan"],
                    ["npm", "ci"], ["git", "status"]):
            self.assertNotIn(self.WARN, self.ask(*cmd), cmd)


class HelperDenyRules(Base):
    MANIFEST = {"version": 2, "profiles": {
        "gw": {"claude": {"provider": "gateway", "base_url": "https://llm.example", "token": "sops:g/gw"},
               "github": {"auth": "sops:g/gh"}},
        "off": {"permissions": {"builtin": False},
                "claude": {"provider": "gateway", "base_url": "https://llm.example", "token": "sops:g/gw"}},
        "pw": {"level": "power"}}}

    def setUp(self):
        super().setUp()
        self.write_manifest(self.MANIFEST)
        self.reload()
        self.patch()

    def helpers(self, name="gw"):
        prof = self.m.resolve(name)
        s = self.m.settings_for(prof)
        env = self.m.assemble(prof, want_secrets=False)["env"]
        git = [env[f"GIT_CONFIG_VALUE_{i}"].lstrip("!") for i in range(int(env.get("GIT_CONFIG_COUNT", 0)))
               if "git-credential" in env[f"GIT_CONFIG_VALUE_{i}"]]
        return s["permissions"]["deny"], [s["apiKeyHelper"]] + git

    def assertDenied(self, deny, command):
        self.assertTrue(any(matches(r, command) for r in deny), f"no deny rule matches {command!r}")

    def test_written_helper_lines_are_denied(self):
        deny, helpers = self.helpers()
        self.assertEqual(len(helpers), 2, helpers)
        for h in helpers:
            self.assertDenied(deny, h)

    def test_quoted_helper_prefix_is_denied(self):
        self.m.helper_prefix = lambda quiet=False: "'/opt/my py/bin/python3' '/x y/bin/actas'"
        deny, helpers = self.helpers()
        for h in helpers:
            self.assertDenied(deny, h)
        self.assertIn("Bash('/opt/my py/bin/python3' '/x y/bin/actas' secret*)", deny)

    def test_any_path_to_actas_or_ccenv_is_denied(self):
        deny = self.m.agent_policy(self.m.resolve("pw"))["deny"]
        for c in ("/usr/local/bin/actas secret --ref sops:a/b", "/venv/bin/actas git-credential --ref x",
                  "python3 /src/actas/bin/actas shell gw", "/old/bin/ccenv secret a b",
                  "/opt/bin/ccenv git-credential --ref x", "/x/actas aws-handoff gw", "actas secret a b"):
            self.assertDenied(deny, c)
        for c in ("/usr/local/bin/actas status", "actas list", "/x/actas exec gw -- ls"):
            self.assertFalse(any(matches(r, c) for r in deny), c)

    def test_builtin_false_renders_no_helper_rules(self):
        self.assertEqual(self.m.agent_policy(self.m.resolve("off"))["deny"], [])

    def test_policy_rendering_does_not_warn(self):
        self.m.helper_python = lambda: str(self.tmp / ".venv" / "bin" / "python3")
        _, err = self.quiet(self.m.agent_policy, self.m.resolve("pw"))
        self.assertEqual(err, "")


class GeminiCredRules(Base):
    def test_gemini_stores_denied_when_referenced(self):
        home = self.tmp / "ghome"
        self.write_manifest({"version": 2, "profiles": {
            "g": {"tool": "gemini", "gemini": {"home": str(home)}},
            "c": {}}})
        self.reload()
        rules = self.m.resolve("c")["_cred_rules"]
        for f in ("oauth_creds.json", "mcp-oauth-tokens.json", ".env"):
            self.assertIn(f"Read(~/.gemini/{f})", rules)
            self.assertIn(f"Read(/{home}/.gemini/{f})", rules)
            self.assertTrue(any(r.endswith(f"/homes/gemini/*/.gemini/{f})") for r in rules), f)
        self.assertNotIn("Read(~/.gemini/settings.json)", rules)

    def test_no_gemini_rules_without_gemini(self):
        rules = self.m.resolve("std")["_cred_rules"]
        self.assertFalse([r for r in rules if ".gemini" in r])


if __name__ == "__main__":
    unittest.main()
