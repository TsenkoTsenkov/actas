"""M1 commands: status [--why] [--json], list --json, doctor manifest checks,
migrate (print and --write)."""

import hashlib
import json
import os
import unittest

from ccenv_testlib import Sandbox, has_yaml
from test_manifest import V2

V1 = {
    "version": 1,
    "defaults": {"provider": "subscription"},
    "environments": {
        "personal": {"match": ["%(tmp)s/code/personal"],
                     "aws": {"profile": "personal-sso", "region": "eu-west-1"},
                     "github": {"account": "ada-personal", "name": "Ada Example", "email": "ada@example.com"}},
        "client-a": {"match": ["%(tmp)s/code/client-a"], "provider": "gateway",
                     "gateway": {"base_url": "https://gw.client-a.example", "secret_from": "corp"},
                     "aws": {"profile": "client-a-dev"},
                     "github": {"account": "ada-client-a", "email": "ada@client-a.example"},
                     "env": {"TF_IN_AUTOMATION": "1", "OFF": None}},
        "client-b": {"match": ["%(tmp)s/code/client-b"], "provider": "gateway",
                     "gateway": {"base_url": "https://gw.client-a.example", "secret_from": "corp"}},
        "corp": {"match": ["%(tmp)s/code/corp"], "provider": "gateway",
                 "gateway": {"base_url": "https://gw.client-a.example", "secret": "token"},
                 "github": {"name": "Ada Example", "email": "ada@corp.example"},
                 "secret_env": {"GH_TOKEN": "gh_token", "GITHUB_TOKEN": "gh_token", "NPM_TOKEN": "npm"},
                 "claude_args": ["--permission-mode", "plan"]},
    },
}


class Status(Sandbox):
    manifest = V2
    secrets = {"personal": {"gh_ro_pat": "ghp_SECRET_VALUE"}}

    def status(self, *args, **kw):
        r = self.cli("status", "--json", *args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_json_shape(self):
        d = self.status("--cwd", str(self.tmp / "code/client-a/infra/prod/x"))
        for k in ("ok", "warnings", "session", "cwd", "mismatch", "profile", "level", "confirm",
                  "tool", "agents_may_use", "identities"):
            self.assertIn(k, d)
        self.assertEqual(d["profile"], "client-a-prod")
        self.assertEqual(d["cwd"]["matched_by"], "pattern")
        self.assertEqual(d["cwd"]["pattern"], str(self.tmp / "code/client-a/infra/prod"))
        self.assertEqual(d["identities"]["github"]["ref"], {"ref": "sops:client-a-prod/gh_ro_pat",
                                                             "present": None})
        self.assertEqual(d["identities"]["aws"], {"profile": "client-a-prod-readonly", "region": "eu-west-1"})
        self.assertFalse(d["mismatch"])
        self.assertEqual(self.calls(), [])           # status never calls gh, sops or aws

    def test_session_mismatch(self):
        prod = self.tmp / "code/client-a/infra/prod"         # the real cwd: an explicit --cwd would choose
        prod.mkdir(parents=True, exist_ok=True)
        d = self.status(env={"CCENV": "client-a-test", "PWD": str(prod)}, cwd=prod)
        self.assertEqual(d["session"], {"profile": "client-a-test", "source": "env", "record_pid": None})
        self.assertEqual((d["profile"], d["matched_by"]), ("client-a-test", "session"))
        self.assertEqual(d["cwd"]["profile"], "client-a-prod")
        self.assertTrue(d["mismatch"])
        self.assertEqual(d["agents_may_use"], ["client-a-prod"])

    def test_why_json_and_text(self):
        d = self.status("personal-power", "--why")
        self.assertEqual(d["chain"], ["defaults", "gh-personal-ro", "aws-personal-ro", "no-pulumi",
                                      "personal", "aws-personal-power", "personal-power"])
        self.assertEqual(d["keys"]["aws.profile"],
                         {"value": "personal-poweruser", "from": "aws-personal-power",
                          "was": {"from": "aws-personal-ro", "value": "personal-readonly"}})
        self.assertEqual(d["keys"]["git.name"]["from"], "defaults")
        self.assertIn("GH_TOKEN", d["env"]["secret"])
        self.assertIn("AWS_PROFILE", d["env"]["set"])
        self.assertNotIn("ghp_SECRET_VALUE", json.dumps(d))
        r = self.cli("status", "personal-power", "--why")
        self.assertIn("aws.profile = personal-poweruser (from aws-personal-power, "
                      "was personal-readonly from aws-personal-ro)", r.stdout)

    def test_errors_in_json_mode(self):
        r = self.cli("status", "--json", "nope")
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertEqual((d["ok"], d["code"], d["warnings"]), (False, "error", []))
        self.assertIn("unknown profile", d["message"])


class List(Sandbox):
    manifest = V2

    def test_json(self):
        r = self.cli("list", "--json")
        d = json.loads(r.stdout)
        self.assertEqual((d["ok"], d["warnings"]), (True, []))
        rows = {p["name"]: p for p in d["profiles"]}
        self.assertEqual(set(rows), set(V2["profiles"]))
        self.assertEqual(rows["client-c"]["provider"], "gateway")
        self.assertFalse(rows["client-c"]["remote_control"])
        self.assertTrue(rows["personal"]["remote_control"])
        self.assertEqual(rows["personal-power"]["level"], "power")
        self.assertEqual(rows["client-a-test"]["github"], "ada-client-a")
        self.assertEqual(rows["partner"]["tool"], "codex")
        self.assertIn("no-apply", [x["name"] for x in d["roles"]])

    def test_text(self):
        r = self.cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("level=power confirm", r.stdout)
        self.assertIn("roles: ", r.stdout)


class Doctor(Sandbox):
    manifest = V2

    def doctor(self, *args):
        r = self.cli("doctor", "--json", *args)
        return r.returncode, json.loads(r.stdout)

    def manifest_detail(self, d):
        return [x for c in d["checks"] if c["name"] == "manifest" for x in c["detail"]]

    def test_checks(self):
        doc = json.loads(json.dumps(V2))
        doc["roles"]["aws-personal-power"]["env"] = {"AWS_PROFILE": "other", "API_TOKEN": "x"}
        doc["profiles"]["solo"] = {"level": "power"}
        doc["profiles"]["client-a-test"]["agents_may_use"] = ["client-a-prod", "personal-power"]
        doc["profiles"]["cmdref"] = {"match": ["/x"], "secret_env": {"T": {"cmd": ["pass", "show", "t"]}}}
        self.write_manifest(doc)
        code, d = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn({"name", "status", "detail"}, [set(c) - {"summary"} for c in d["checks"]])
        msgs = [(x["level"], x["message"]) for x in self.manifest_detail(d)]
        text = "\n".join(m for _, m in msgs)
        self.assertIn("level power without confirm", text)                       # solo
        self.assertIn("personal-power is higher-level or needs confirm", text)
        self.assertIn("AWS_PROFILE is set by both aws and env", text)
        self.assertIn("profiles.personal-power.env.API_TOKEN: env values are written", text)
        self.assertIn("runs a command to read a secret", text)
        self.assertIn("no match and nothing uses it", text)                     # solo
        code, d = self.doctor("--strict")
        self.assertEqual(code, 1)
        self.assertFalse(d["ok"])

    def test_readonly_gh_and_confirm_match_infos(self):
        doc = json.loads(json.dumps(V2))
        doc["profiles"]["scratch"]["github"] = {"auth": "gh:ada-personal"}
        doc["profiles"]["personal-power"]["match"] = ["/somewhere"]
        self.write_manifest(doc)
        text = "\n".join(x["message"] for x in self.manifest_detail(self.doctor()[1]))
        self.assertIn("readonly with a gh: account", text)
        self.assertIn("confirm profile with match", text)

    def test_errors_exit_1(self):
        doc = json.loads(json.dumps(V2))
        doc["profiles"]["personal"]["level"] = "standard"        # above its readonly layers: fine
        doc["profiles"]["personal-power"]["level"] = "readonly"  # below its power layer: error
        self.write_manifest(doc)
        code, d = self.doctor()
        self.assertEqual(code, 1)
        errs = [x for x in self.manifest_detail(d) if x["level"] == "error"]
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("compose upward", errs[0]["message"])
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)


@unittest.skipUnless(has_yaml(), "migrate emits YAML; the round trip needs PyYAML")
class Migrate(Sandbox):
    manifest = V1
    secrets = {"corp": {"token": "t", "gh_token": "p", "npm": "n"}}

    def test_print_keeps_v1_sops_paths(self):
        sec = self.root / "secrets.enc.yaml"
        before = (hashlib.sha256(sec.read_bytes()).hexdigest(), sec.stat().st_mtime_ns)
        r = self.cli("migrate")
        self.assertEqual(r.returncode, 0, r.stderr)
        import yaml
        doc = yaml.safe_load(r.stdout)
        self.assertEqual(doc["version"], 2)
        p = doc["profiles"]
        self.assertEqual(p["client-a"]["claude"]["token"], "sops:corp/token")
        self.assertEqual(p["corp"]["claude"]["token"], "sops:corp/token")
        self.assertEqual(p["corp"]["github"]["auth"], "sops:corp/gh_token")
        self.assertEqual(p["corp"]["secret_env"], {"NPM_TOKEN": "sops:corp/npm"})
        self.assertEqual(p["personal"]["github"]["auth"], "gh:ada-personal")
        self.assertEqual(p["client-a"]["env"], {"TF_IN_AUTOMATION": "1", "OFF": None})
        self.assertNotIn("permissions", p["personal"])
        self.assertIn("built-in agent rules apply", r.stderr)
        self.assertIn("share one claude block", r.stderr)
        self.assertEqual((hashlib.sha256(sec.read_bytes()).hexdigest(), sec.stat().st_mtime_ns), before)
        self.assertEqual(self.calls(), [])

    def test_output_resolves_to_the_same_identities(self):
        old = {n: self.m.resolve(n) for n in V1["environments"]}
        out = self.cli("migrate").stdout
        (self.root / "environments.yaml").write_text(out)
        m = self.reload()
        for n, prof in old.items():
            new = m.resolve(n)
            ids = m.identities(new)
            if "aws" not in prof:              # v1 had no aws block: migrate keeps the shell's (R11)
                self.assertEqual(ids.pop("aws"), {"mode": "inherit"}, n)
            self.assertEqual(ids, m.identities(prof), n)
            after = m.assemble(new, want_secrets=False)["env"]
            if (new.get("github") or {}).get("auth"):     # v2 GitHub fails closed (listed by migrate)
                self.assertTrue(after.pop("GH_CONFIG_DIR").endswith("/homes/github/_off"))
            self.assertEqual(after, m.assemble(prof, want_secrets=False)["env"], n)
            self.assertEqual(new["match"], prof["match"])

    def test_write_keeps_a_backup_and_refuses_twice(self):
        original = (self.root / "environments.yaml").read_text()
        r = self.cli("migrate", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "environments.v1.yaml").read_text(), original)
        self.assertIn("version: 2", (self.root / "environments.yaml").read_text())
        self.assertEqual(self.cli("migrate").returncode, 0)          # already v2: nothing to do
        (self.root / "environments.yaml").write_text(original)
        r = self.cli("migrate", "--write")
        self.assertEqual(r.returncode, 1)
        self.assertIn("exists", r.stderr)


class JsonEverywhere(Sandbox):
    manifest = V2

    def test_ok_and_warnings_keys(self):
        for args in (["status", "personal"], ["list"], ["doctor"]):
            d = json.loads(self.cli(*args, "--json").stdout)
            self.assertIn("ok", d, args)
            self.assertIn("warnings", d, args)


if __name__ == "__main__":
    unittest.main()
