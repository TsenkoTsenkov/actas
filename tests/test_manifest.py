"""M1: loader and validation (SPEC 2.1-2.3), translate_v1 (2.4), merge with
provenance (3.1) and directory resolution with fallbacks (3.3)."""

import json
import os
import sys
import unittest

from ccenv_testlib import Sandbox, has_yaml

MINIMAL = """\
version: 2
profiles:
  personal:
    match: %(tmp)s/code/personal
    git:    { name: Ada Example, email: ada@example.com }
    github: { auth: "gh:ada-personal" }
    aws:    { profile: personal, region: eu-west-1 }
  client-a:
    match: %(tmp)s/code/client-a
    git:    { name: Ada Example, email: ada@client-a.example }
    github: { auth: "gh:ada-client-a" }
    aws:    { profile: client-a-dev, region: eu-west-1 }
    claude: { provider: gateway, base_url: "https://llm.client-a.example", token: "keychain:ccenv/client-a" }
"""

# SPEC 2.5 (the user's shape, placeholder names), as JSON.
V2 = {
    "version": 2,
    "defaults": {"git": {"name": "Ada Example", "email": "ada@example.com"}},
    "fallback": "scratch",
    "roles": {
        "gh-personal-ro": {"level": "readonly", "github": {"auth": "sops:personal/gh_ro_pat"}},
        "gh-personal": {"github": {"auth": "gh:ada-personal"}},
        "aws-personal-ro": {"level": "readonly", "aws": {"profile": "personal-readonly", "region": "eu-west-1"}},
        "aws-personal-power": {"level": "power", "aws": {"profile": "personal-poweruser"}},
        "corp-gateway": {"claude": {"provider": "gateway", "base_url": "https://llm-gateway.corp.example",
                                    "token": "sops:corp-gateway/token"}},
        "no-pulumi": {"permissions": {"deny": ["Bash(pulumi up *)", "Bash(pulumi destroy *)"]}},
        "no-apply": {"permissions": {"deny": ["Bash(terraform apply *)", "Bash(terraform destroy *)"]}},
        "client-a-base": {"use": ["corp-gateway"], "git": {"email": "ada@client-a.example"},
                          "github": {"auth": "gh:ada-client-a"}},
    },
    "profiles": {
        "personal": {"match": ["%(tmp)s/code/personal", "%(tmp)s/code/sandbox-*"],
                     "use": ["gh-personal-ro", "aws-personal-ro", "no-pulumi"]},
        "personal-power": {"use": ["personal", "aws-personal-power"], "confirm": True},
        "work": {"match": ["%(tmp)s/code/oss-work"], "claude": {"home": "work"}},
        "client-a-test": {"match": ["%(tmp)s/code/client-a", "%(tmp)s/code/client-a-*"],
                          "use": ["client-a-base"],
                          "aws": {"profile": "client-a-test-dev", "region": "eu-west-1"},
                          "agents_may_use": ["client-a-prod"]},
        "client-a-prod": {"match": ["%(tmp)s/code/client-a/infra/prod"], "use": ["client-a-base", "no-apply"],
                          "level": "readonly", "github": {"auth": "sops:client-a-prod/gh_ro_pat"},
                          "aws": {"profile": "client-a-prod-readonly", "region": "eu-west-1"}},
        "client-b": {"match": ["%(tmp)s/code/client-b"], "git": {"email": "ada@client-b.example"},
                     "github": {"auth": "gh:ada-client-b"},
                     "gcp": {"home": "client-b", "project": "client-b-dev",
                             "impersonate": "viewer@client-b-dev.iam.gserviceaccount.com"},
                     "aws": None},
        "client-c": {"match": ["%(tmp)s/code/client-c"], "use": ["corp-gateway"],
                     "azure": {"home": "client-c", "subscription": "00000000-0000-0000-0000-000000000000",
                               "tenant": "11111111-1111-1111-1111-111111111111"},
                     "aws": None},
        "partner": {"match": ["%(tmp)s/code/partner-org"], "github": {"auth": "sops:partner/gh_token"},
                    "tool": "codex", "codex": {"home": "partner"}},
        "scratch": {"level": "readonly"},
    },
}


def v2():
    return json.loads(json.dumps(V2))


class Loader(Sandbox):
    manifest = V2

    def load_errors(self, doc):
        self.write_manifest(doc)
        cfg = self.reload().load(strict=False, quiet=True)
        return [(lvl, p, msg) for lvl, p, msg in cfg.problems.items]

    def test_spec_example_loads_cleanly(self):
        cfg = self.m.load(quiet=True)
        self.assertEqual(cfg.problems.items, [])
        self.assertEqual(cfg.fallback, "scratch")
        self.assertIn("corp-gateway", cfg.roles)

    @unittest.skipUnless(has_yaml(), "needs PyYAML")
    def test_minimal_manifest_resolves(self):
        self.write_manifest(MINIMAL)
        m = self.reload()
        p = m.resolve(cwd=str(self.tmp / "code/client-a/x"))
        self.assertEqual(p["name"], "client-a")
        self.assertEqual(p["claude"]["token"], "keychain:ccenv/client-a")
        self.assertEqual(m.resolve(cwd=str(self.tmp / "code/personal"))["aws"]["profile"], "personal")

    def test_unknown_key_warns_with_suggestion(self):
        doc = v2()
        doc["profiles"]["work"]["secrets_env"] = {"X": "sops:a/b"}
        items = self.load_errors(doc)
        self.assertIn(("warning", "profiles.work.secrets_env", "unknown key (did you mean secret_env?)"), items)
        self.assertEqual(self.m.fmt_problem("profiles.work.secrets_env", items[0][2]),
                         "unknown key profiles.work.secrets_env (did you mean secret_env?)")

    def test_type_errors_name_the_path(self):
        doc = v2()
        doc["profiles"]["work"]["aws"] = {"profile": True}
        doc["profiles"]["work"]["env"] = {"FLAG": True, "N": 3}
        doc["profiles"]["work"]["unset"] = "AWS_PROFILE"
        doc["profiles"]["work"]["secret_env"] = {"TOKEN": "bare_key"}
        errs = {p: m for lvl, p, m in self.load_errors(doc) if lvl == "error"}
        self.assertEqual(errs["profiles.work.aws.profile"], "expected a string, got bool")
        self.assertIn("quote the value", errs["profiles.work.env.FLAG"])
        self.assertEqual(errs["profiles.work.unset"], "expected a list, got string")
        self.assertIn('write a ref such as "sops:work/token"', errs["profiles.work.secret_env.TOKEN"])
        self.assertNotIn("bare_key", errs["profiles.work.secret_env.TOKEN"])
        self.assertNotIn("profiles.work.env.N", errs)

    def test_refs_are_validated(self):
        bad = ["sops:a", "keychain:-x/y", "gh:-bad", "op://vault/$x", "cmd:[]", "cmd:not-json", "vault:x"]
        for ref in bad:
            self.assertIsNotNone(self.m.ref_error(ref), ref)
        for ref in ["sops:a/b", "keychain:ccenv/client a", "op://Vault/item/field",
                    "gh:ada_client", "gh:ada@ghe.example.com", 'cmd:["pass", "show", "x"]']:
            self.assertIsNone(self.m.ref_error(ref), ref)
        doc = v2()
        doc["profiles"]["work"]["secret_env"] = {"T": {"cmd": ["pass", "show", "t"]}}
        self.assertEqual([i for i in self.load_errors(doc) if i[0] == "error"], [])
        self.assertEqual(self.m.load(quiet=True).profiles["work"]["secret_env"]["T"],
                         'cmd:["pass", "show", "t"]')

    def test_structural_errors(self):
        cases = [
            ({"defaults": {"level": "power"}}, "defaults.level", "put it in a role"),
            ({"defaults": {"match": ["x"]}}, "defaults.match", "put it in a role"),
            ({"roles": {"r": {"match": ["x"]}}}, "roles.r.match", "never matched"),
            ({"roles": {"scratch": {}}}, "roles.scratch", "duplicate name"),
            ({"fallback": "nope"}, "fallback", "must name a profile"),
            ({"environments": {}}, "environments", "rename to profiles"),
            ({"permission_sets": {}}, "permission_sets", "define a role"),
            ({"version": 3}, "version", "must be 1 or 2"),
        ]
        for change, path, text in cases:
            doc = v2()
            for k, v in change.items():
                if isinstance(v, dict) and isinstance(doc.get(k), dict):
                    doc[k].update(v)
                else:
                    doc[k] = v
            errs = [(p, msg) for lvl, p, msg in self.load_errors(doc) if lvl == "error"]
            self.assertTrue(any(p == path and text in msg for p, msg in errs), (change, errs))

    def test_legacy_keys_in_v2_name_migrate(self):
        doc = v2()
        doc["profiles"]["work"]["provider"] = "gateway"
        doc["profiles"]["work"]["github"] = {"account": "ada"}
        errs = [msg for lvl, p, msg in self.load_errors(doc) if lvl == "error"]
        self.assertEqual(len(errs), 2)
        self.assertTrue(all("ccenv migrate" in e for e in errs), errs)

    def test_errors_stop_commands_with_the_path(self):
        doc = v2()
        doc["profiles"]["work"]["level"] = "admin"
        self.write_manifest(doc)
        r = self.cli("list")
        self.assertEqual(r.returncode, 1)
        self.assertIn("profiles.work.level: expected one of readonly, standard, power", r.stderr)


class TranslateV1(Sandbox):
    manifest = {
        "version": 1,
        "defaults": {"provider": "subscription", "aws": {"region": "eu-west-1"}},
        "environments": {
            "shallow": {"aws": {"profile": "p1"}},
            "owner": {"provider": "Gateway", "gateway": {"base_url": "https://gw.example"}},
            "borrower": {"provider": "gateway",
                         "gateway": {"base_url": "https://gw.example", "secret": "tok", "secret_from": "owner"}},
            "pat-and-account": {"github": {"account": "ada_client", "name": "Ada"},
                                "secret_env": {"GH_TOKEN": "pat", "GITHUB_TOKEN": "other", "X": "x"}},
            "pat-only": {"github": {"email": "ada@example.com"},
                         "secret_env": {"GH_TOKEN": "pat", "GITHUB_TOKEN": "pat"}},
            "flags": {"env": {"BOOL": True, "NUM": 5}, "secrets_env": {}},
            "empty": None,
        },
    }

    def setUp(self):
        super().setUp()
        self.cfg, self.err = self.quiet(self.m.load)

    def test_shallow_defaults_lose_the_region(self):
        self.assertEqual(self.cfg.profiles["shallow"]["aws"], {"profile": "p1"})
        self.assertEqual(self.cfg.profiles["empty"]["aws"], {"region": "eu-west-1"})

    def test_gateway_token_and_secret_from(self):
        self.assertEqual(self.cfg.profiles["owner"]["claude"],
                         {"provider": "gateway", "base_url": "https://gw.example",
                          "token": "sops:owner/token", "export_token": True})
        self.assertEqual(self.cfg.profiles["borrower"]["claude"]["token"], "sops:owner/tok")

    def test_secret_env_gh_token_becomes_github_auth(self):
        p = self.cfg.profiles["pat-and-account"]
        self.assertEqual(p["github"], {"auth": "sops:pat-and-account/pat"})
        self.assertEqual(p["git"], {"name": "Ada"})
        self.assertEqual(p["secret_env"], {"GITHUB_TOKEN": "sops:pat-and-account/other",
                                           "X": "sops:pat-and-account/x"})
        q = self.cfg.profiles["pat-only"]
        self.assertEqual(q["github"], {"auth": "sops:pat-only/pat"})
        self.assertNotIn("secret_env", q)

    def test_compat_flags_and_lenient_env(self):
        self.assertTrue(self.m.resolve("flags")["_v1"])
        self.assertEqual(self.cfg.profiles["flags"]["permissions"], {"builtin": False})
        self.assertEqual(self.cfg.profiles["flags"]["env"], {"BOOL": "True", "NUM": "5"})
        self.assertIn("environments.flags.env.BOOL: boolean True", self.err)
        self.assertIn("unknown key environments.flags.secrets_env (did you mean secret_env?)", self.err)
        self.assertEqual(self.m.identities(self.m.resolve("shallow"))["claude"]["provider"], "subscription")

    def test_emu_account_accepted(self):
        prof = self.m.resolve("pat-and-account")
        self.assertEqual(self.m.identities(prof)["github"]["auth"], "ref")
        self.write_manifest({"version": 1, "environments": {"e": {"github": {"account": "ada_client"}}}})
        self.assertEqual(self.reload().resolve("e")["github"]["auth"], "gh:ada_client")

    def test_v1_errors(self):
        for env, text in (({"provider": "gatway"}, "did you mean gateway?"),
                          ({"level": "readonly"}, 'add "version: 2"'),
                          ({"gateway": {"secret": "a b"}, "provider": "gateway"}, "invalid name")):
            self.write_manifest({"version": 1, "environments": {"e": env}})
            r = self.cli("list")
            self.assertEqual(r.returncode, 1, env)
            self.assertIn(text, r.stderr)
        self.write_manifest({"roles": {}, "environments": {}})
        self.assertIn("ccenv migrate", self.cli("list").stderr)

    def test_gateway_without_base_url_fails_on_use_only(self):
        self.write_manifest({"version": 1, "environments": {"gw": {"provider": "gateway"}, "ok": {}}})
        self.assertEqual(self.cli("list").returncode, 0)
        r = self.cli("shell", "gw")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no gateway.base_url", r.stderr)


class Merge(Sandbox):
    manifest = V2

    def doc_with(self, roles=None, profiles=None):
        doc = {"version": 2, "roles": roles or {}, "profiles": profiles or {}}
        self.write_manifest(doc)
        return self.reload()

    def err(self, m, name):
        return self.dies(m.resolve, name)

    def test_diamond_cycle_and_depth(self):
        m = self.doc_with({"D": {}, "B": {"use": ["D"]}, "C": {"use": "D"}},
                          {"A": {"use": ["B", "C"]}})
        self.assertEqual(m.resolve("A")["_chain"], ["defaults", "D", "B", "C", "A"])
        m = self.doc_with({"x": {"use": ["y"]}, "y": {"use": ["x"]}}, {"p": {"use": ["x"]}})
        self.assertIn("cycle: p -> x -> y -> x", self.err(m, "p"))
        roles = {f"r{i}": {"use": [f"r{i + 1}"]} for i in range(9)}
        roles["r9"] = {}
        m = self.doc_with(roles, {"p": {"use": ["r0"]}, "q": {"use": ["r3"]}})
        self.assertIn("deeper than 8", self.err(m, "p"))
        self.assertEqual(len(m.resolve("q")["_chain"]), 9)

    def test_permission_sets_union_and_sticky(self):
        p = self.m.resolve("client-a-prod")
        self.assertEqual(p["permissions"]["deny"], ["Bash(terraform apply *)", "Bash(terraform destroy *)"])
        m = self.doc_with({"a": {"permissions": {"deny": ["X", "Y"]}}, "b": {"permissions": {"deny": ["Y", "Z"]}}},
                          {"p": {"use": ["a", "b"]}})
        self.assertEqual(m.resolve("p")["permissions"]["deny"], ["X", "Y", "Z"])
        for later in ({"permissions": {"deny": None}}, {"permissions": None}, {"unset": None}):
            m = self.doc_with({"a": {"permissions": {"deny": ["X"]}, "unset": ["AWS_PROFILE"]}},
                              {"p": dict(later, use=["a"])})
            msg = self.err(m, "p")
            self.assertIn("cannot replace", msg)
            self.assertIn("(added by a)", msg)

    def test_nested_null_in_first_layer_and_provenance(self):
        m = self.doc_with({}, {"p": {"env": {"X": None, "Y": "1"}}})
        p = m.resolve("p")
        self.assertIn(("env", "X"), p["_nulled"])
        self.assertEqual(p["_prov"][("env", "Y")]["from"], "p")
        r = m.assemble(p, parent={"X": "parent"})
        self.assertEqual(r["unset"], ["X"])

    def test_levels_and_confirm(self):
        self.assertEqual(self.m.resolve("personal")["level"], "readonly")
        pp = self.m.resolve("personal-power")
        self.assertEqual((pp["level"], pp["confirm"]), ("power", True))
        self.assertEqual(self.m.resolve("client-a-test")["level"], "standard")
        m = self.doc_with({"pw": {"level": "power"}}, {"p": {"use": ["pw"], "level": "readonly"}})
        self.assertIn("compose upward", self.err(m, "p"))

    def test_local_keys_not_inherited_and_later_layer_wins(self):
        pp = self.m.resolve("personal-power")
        self.assertEqual(pp["match"], [])
        self.assertEqual(pp["aws"], {"profile": "personal-poweruser", "region": "eu-west-1"})
        self.assertEqual(pp["github"]["auth"], "sops:personal/gh_ro_pat")
        prod = self.m.resolve("client-a-prod")
        self.assertEqual(prod["github"], {"auth": "sops:client-a-prod/gh_ro_pat"})
        self.assertEqual(prod["agents_may_use"], [])
        self.assertEqual(prod["git"], {"name": "Ada Example", "email": "ada@client-a.example"})
        self.assertEqual(prod["claude"]["provider"], "gateway")

    def test_provenance_was(self):
        prov = self.m.resolve("personal-power")["_prov"]
        self.assertEqual(prov[("aws", "profile")],
                         {"from": "aws-personal-power", "value": "personal-poweruser",
                          "was": {"from": "aws-personal-ro", "value": "personal-readonly"}})

    def test_null_and_inherit_modules(self):
        b = self.m.resolve("client-b")
        self.assertIn(("aws",), b["_nulled"])
        self.assertEqual(self.m.identities(b)["aws"], {"mode": "off"})
        m = self.doc_with({"a": {"aws": {"profile": "x"}}}, {"p": {"use": ["a"], "aws": "inherit"}})
        self.assertEqual(m.resolve("p")["aws"], "inherit")

    def test_roles_are_not_run(self):
        msg = self.err(self.m, "corp-gateway")
        self.assertIn("is a role", msg)

    def test_selectors(self):
        m = self.doc_with({}, {"b": {"claude": {"provider": "bedrock"}}})
        self.assertIn("aws.region", self.err(m, "b"))
        m = self.doc_with({"a": {"github": {"auth": "gh:x"}}}, {"p": {"use": ["a"], "github": {"home": "h"}}})
        self.assertIn("both auth and home", self.err(m, "p"))


class Resolution(Sandbox):
    manifest = {
        "version": 2,
        "profiles": {
            "parent": {"match": ["%(tmp)s/r/a"]},
            "child": {"match": ["%(tmp)s/r/a/b"]},
            "glob": {"match": ["%(tmp)s/r/g/*"]},
            "tie1": {"match": ["%(tmp)s/r/t/xx"]},
            "tie2": {"match": ["%(tmp)s/r/t/xx"]},
            "Mixed": {"match": ["%(tmp)s/r/CaseDir"]},
        },
    }

    def name(self, d, m=None):
        return (m or self.m).locate((m or self.m).load(), str(self.tmp / d))[:2]

    def test_longest_match_tie_and_glob(self):
        self.assertEqual(self.name("r/a/x"), ("parent", "pattern"))
        self.assertEqual(self.name("r/a/b/c"), ("child", "pattern"))
        self.assertEqual(self.name("r/t/xx"), ("tie1", "pattern"))
        self.assertEqual(self.name("r/g/deep/er"), ("glob", "pattern"))      # '*' crosses '/'
        self.assertEqual(self.name("r/g")[0], None)                           # X/* is not X

    def test_logical_pwd(self):
        real = self.tmp / "real"
        real.mkdir()
        (self.tmp / "r").mkdir()
        os.symlink(str(real), str(self.tmp / "r" / "a"))
        r = self.cli("status", "--json", cwd=str(real), env={"PWD": str(self.tmp / "r/a")})
        out = json.loads(r.stdout)
        self.assertEqual((out["profile"], out["cwd"]["matched_by"]), ("parent", "logical-pwd"))

    @unittest.skipUnless(sys.platform == "darwin", "casefold retry is for case-insensitive macOS")
    def test_casefold(self):
        self.assertEqual(self.name("r/casedir/x"), ("Mixed", "casefold"))

    def test_fallback_and_no_match(self):
        msg = self.dies(self.m.resolve, None, str(self.tmp / "elsewhere"))
        self.assertIn("no profile matches", msg)
        self.assertIn("known: parent, child", msg)
        doc = json.loads(json.dumps(self.manifest))
        doc["fallback"] = "tie2"
        self.write_manifest(doc)
        m = self.reload()
        self.assertEqual(self.name("elsewhere", m), ("tie2", "fallback"))


if __name__ == "__main__":
    unittest.main()
