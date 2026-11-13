"""init, doctor, list --check and login --all (package p4).

- init fills the starter's git identity from the global git config, or leaves
  it commented out: never a placeholder that ends up in commits.
- doctor warns about placeholder (example-domain) git identities, gh: accounts
  that are not logged in, aws profiles missing from the AWS config files, and
  missing tools for the secret refs and agent tools the manifest uses. It never
  reads a secret to do so.
- list --check reads a gateway token shared by several profiles once.
- login --all exits 1 and names the portals whose login failed.

Fake binaries only (Sandbox): nothing reaches a real gh, aws, op or keychain.
"""

import json
import os
import sys
import unittest
from unittest import mock

from actas_testlib import Sandbox, has_yaml, make_fakebin


class Base(Sandbox):
    def only(self, *names):
        """Only these fakes on PATH (plus git for init), no system dirs."""
        os.environ["PATH"] = str(make_fakebin(self.tmp / "only", only=names + ("ps",)))

    def doctor(self, *args, **kw):
        r = self.cli("doctor", "--json", *args, **kw)
        self.assertNotIn("Traceback", r.stderr)
        d = json.loads(r.stdout)
        return r.returncode, d["checks"]

    def named(self, checks, name):
        return [c for c in checks if c["name"] == name]


# ------------------------------------------------------------------ init

class Init(Base):
    def gitconfig(self, name, email):
        (self.home / ".gitconfig").write_text(f"[user]\n\tname = {name}\n\temail = {email}\n")

    def test_without_a_global_identity_the_git_line_stays_a_comment(self):
        r = self.cli("init", env={"SOPS_AGE_KEY_FILE": str(self.tmp / "none")})
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.root / "environments.yaml").read_text()
        if self.m.yaml_available():
            self.assertIn("# git:", text)
            self.assertNotIn("\n    git:", text)
        else:
            self.assertNotIn("git", json.loads(text)["profiles"]["personal"])
        # Commits in the matched directory do not get a placeholder identity.
        (self.home / "code" / "personal").mkdir(parents=True)
        r = self.cli("exec", "--cwd", str(self.home / "code" / "personal"), "personal", "--",
                     "git", "config", "user.email")
        self.assertNotIn("example.com", r.stdout)
        _, checks = self.doctor()
        self.assertEqual(self.named(checks, "identity"), [])

    def test_the_global_git_identity_fills_the_starter(self):
        self.gitconfig("Ada Lovelace", "ada@lovelace.dev")
        r = self.cli("init", env={"SOPS_AGE_KEY_FILE": str(self.tmp / "none")})
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.root / "environments.yaml").read_text()
        self.assertNotIn("Your Name", text)
        self.assertIn("ada@lovelace.dev", text)
        prof = self.reload().resolve("personal")
        self.assertEqual(prof["git"], {"name": "Ada Lovelace", "email": "ada@lovelace.dev"})
        r = self.cli("exec", "personal", "--", "git", "config", "user.email")
        self.assertEqual(r.stdout.strip(), "ada@lovelace.dev", r.stderr)
        _, checks = self.doctor()
        self.assertEqual(self.named(checks, "identity"), [])

    @unittest.skipUnless(has_yaml(), "needs PyYAML")
    def test_quotes_in_the_identity_survive_the_yaml(self):
        import yaml
        ident = ('Adá "the Countess" O\'Neil: #1', "ada+x@lovelace.dev")
        text, doc = self.m.init_starter(ident)
        self.assertEqual(yaml.safe_load(text), doc)
        self.assertEqual(doc["profiles"]["personal"]["git"], dict(zip(("name", "email"), ident)))
        self.assertEqual(self.m.init_starter(None), (self.m.INIT_MANIFEST, self.m.INIT_DOC))

    def test_an_incomplete_global_identity_is_not_used(self):
        (self.home / ".gitconfig").write_text("[user]\n\tname = Ada\n")
        self.assertIsNone(self.m.git_global_identity())
        self.gitconfig("Ada", "ada@lovelace.dev")
        self.assertEqual(self.m.git_global_identity(), ("Ada", "ada@lovelace.dev"))


# ---------------------------------------------------------- doctor: identity

class Identity(Base):
    manifest = {"version": 2, "profiles": {
        "personal": {"match": "%(home)s/code/personal",
                     "git": {"name": "Your Name", "email": "you@example.com"}},
        "client-a": {"match": "%(home)s/code/client-a",
                     "git": {"name": "Ada Example", "email": "ada@client-a.example"}},
        "real": {"match": "%(home)s/code/real", "git": {"name": "Ada", "email": "ada@lovelace.dev"}},
        "near": {"match": "%(home)s/code/near", "git": {"email": "ada@myexample.com"}}}}

    def test_placeholder_identities_warn(self):
        code, checks = self.doctor()
        self.assertEqual(code, 0)                                   # a warning, not an error
        text = "\n".join(c["detail"] for c in self.named(checks, "identity"))
        self.assertEqual({c["status"] for c in self.named(checks, "identity")}, {"warn"})
        self.assertIn("Your Name <you@example.com> in personal", text)
        self.assertIn("ada@client-a.example> in client-a", text)
        self.assertNotIn("real", text)
        self.assertNotIn("near", text)                              # only reserved domains
        code, _ = self.doctor("--strict")
        self.assertEqual(code, 1)

    def test_placeholder_rule(self):
        p = self.m.placeholder_git
        for g in ({"name": "Your Name"}, {"email": "a@example.org"}, {"email": "a@x.example.net"},
                  {"email": "A@Corp.EXAMPLE"}, {"email": "a@nowhere.invalid"}):
            self.assertTrue(p(g), g)
        for g in ({}, {"name": "Ada"}, {"email": "a@example.company"}, {"email": "a@examples.com"}):
            self.assertFalse(p(g), g)

    def test_a_shared_placeholder_is_reported_once(self):
        self.write_manifest({"version": 2,
                             "defaults": {"git": {"name": "Ada Example", "email": "ada@example.com"}},
                             "profiles": {"a": {"match": "/a"}, "b": {"match": "/b"}}})
        _, checks = self.doctor()
        (c,) = self.named(checks, "identity")
        self.assertIn("in a, b", c["detail"])


# --------------------------------------------------- doctor: gh and aws refs

class Refs(Base):
    manifest = {"version": 2, "profiles": {
        "personal": {"match": "%(home)s/code/personal", "github": {"auth": "gh:ada-personal"},
                     "aws": {"profile": "personal", "region": "eu-west-1"}},
        "client-a": {"match": "%(home)s/code/client-a", "github": {"auth": "gh:ada-client-a"},
                     "aws": {"profile": "client-a-dev", "region": "eu-west-1"},
                     "secret_env": {"NPM_TOKEN": "gh:ada-npm@ghe.example.org"}},
        "static": {"match": "%(home)s/code/static", "aws": {"profile": "static-keys"}},
        "custom": {"match": "%(home)s/code/custom",
                   "aws": {"profile": "elsewhere", "config_file": "%(tmp)s/aws-config"}},
        "off": {"match": "%(home)s/code/off", "aws": None}}}

    def setUp(self):
        super().setUp()
        aws = self.home / ".aws"
        aws.mkdir()
        (aws / "config").write_text("[profile something-else]\nregion = eu-west-1\n"
                                    "[profile personall]\nregion = eu-west-1\n"
                                    "[sso-session client-a-dev]\nsso_start_url = https://x\n")
        (aws / "credentials").write_text("[static-keys]\naws_access_key_id = AKIAFAKE\n"
                                         "aws_secret_access_key = not-a-real-SECRET\n")
        (self.tmp / "aws-config").write_text("[profile elsewhere]\nregion = eu-west-1\n")

    def refs(self, checks):
        return "\n".join(c["detail"] for c in self.named(checks, "refs"))

    def test_unknown_gh_accounts_and_aws_profiles_warn(self):
        code, checks = self.doctor(env={"FAKE_GH_USERS": "someone-else"})
        self.assertEqual(code, 0)
        text = self.refs(checks)
        self.assertIn("gh:ada-personal (profiles.personal.github.auth) is not an account logged in to gh "
                      "on github.com; run: actas login personal --module github", text)
        self.assertIn("gh:ada-client-a", text)
        self.assertIn("gh:ada-npm@ghe.example.org (profiles.client-a.secret_env.NPM_TOKEN) is not an account "
                      "logged in to gh on ghe.example.org; run: gh auth login --hostname ghe.example.org", text)
        self.assertIn("aws profile personal (personal) is not in ~/.aws/config or ~/.aws/credentials "
                      "(did you mean personall?)", text)
        self.assertIn("aws profile client-a-dev (client-a)", text)     # an sso-session is not a profile
        self.assertNotIn("static-keys", text)                           # in the credentials file
        self.assertNotIn("elsewhere", text)                             # in its own config_file
        self.assertNotIn("not-a-real-SECRET", json.dumps(checks))
        self.assertEqual(self.named(checks, "gh accts")[0]["status"], "warn")
        self.assertEqual(self.cli("doctor", "--strict").returncode, 1)
        # Local lookups only: no token was read.
        self.assertNotIn(["auth", "token"], [c["argv"][:2] for c in self.calls("gh")])

    def test_logged_in_accounts_and_existing_profiles_are_quiet(self):
        with (self.home / ".aws" / "config").open("a") as f:
            f.write("[profile personal]\n[profile client-a-dev]\n")
        # gh logins are case-insensitive.
        code, checks = self.doctor(env={"FAKE_GH_USERS": "Ada-Personal,ada-client-a"})
        text = self.refs(checks)
        self.assertNotIn("gh:ada-personal", text)
        self.assertNotIn("gh:ada-client-a ", text)
        self.assertNotIn("aws profile", text)
        self.assertIn("gh:ada-npm@ghe.example.org", text)              # another host
        (c,) = self.named(checks, "gh accts")
        self.assertEqual(c["detail"], "Ada-Personal, ada-client-a")

    def test_inside_a_session_doctor_reads_the_users_own_gh_accounts(self):
        # A fail-closed session home and a token in the env must not hide the accounts.
        off = self.tmp / "state" / "homes" / "github" / "_off"
        off.mkdir(parents=True)
        code, checks = self.doctor(env={"FAKE_GH_USERS": "ada-personal,ada-client-a",
                                        "GH_CONFIG_DIR": str(off), "GH_TOKEN": "gho_other"})
        (c,) = self.named(checks, "gh accts")
        self.assertEqual((c["status"], c["detail"]), ("warn", "ada-personal, ada-client-a"))  # warn: ghe
        self.assertNotIn("gh:ada-personal", self.refs(checks))

    def test_without_gh_the_gh_tool_line_warns(self):
        self.only("claude", "aws")
        _, checks = self.doctor()
        (c,) = self.named(checks, "gh")
        self.assertEqual(c["status"], "warn")
        self.assertIn("needed for gh: accounts, e.g. profiles.", c["detail"])
        self.assertEqual(self.named(checks, "gh accts"), [])


# ----------------------------------------------- doctor: secret ref tools

class SecretTools(Base):
    manifest = {"version": 2, "profiles": {
        "work": {"match": "%(home)s/code/work", "tool": "codex",
                 "secret_env": {"X": "sops:a/x", "NPM_TOKEN": "op://Work/npm/token",
                                "PASS": {"cmd": ["no-such-pass-tool", "show", "x"]},
                                "OK": {"cmd": ["/bin/echo", "hi"]}}}}}

    def test_missing_tools_for_used_refs_warn_and_strict_fails(self):
        self.only("gh", "claude")
        code, checks = self.doctor("--strict", env={"SOPS_AGE_KEY_FILE": ""})
        self.assertEqual(code, 1)
        by = {c["name"]: c for c in checks}
        for name in ("secrets", "sops", "age key", "op", "codex"):
            self.assertEqual(by[name]["status"], "warn", by[name])
        self.assertIn("profiles.work.secret_env.X", by["secrets"]["detail"])
        self.assertIn("op:// refs, e.g. profiles.work.secret_env.NPM_TOKEN", by["op"]["detail"])
        self.assertIn("tool: codex, e.g. profiles.work.tool", by["codex"]["detail"])
        self.assertNotIn("gemini", by)                                  # not used: not listed
        self.assertEqual(by["age"]["status"], "optional-missing")       # only for creating keys
        refs = "\n".join(c["detail"] for c in self.named(checks, "refs"))
        self.assertIn("no-such-pass-tool not found (needed for the cmd: ref in profiles.work.secret_env.PASS)",
                      refs)
        self.assertNotIn("/bin/echo", refs)

    def test_installed_tools_are_ok_and_nothing_is_read(self):
        (self.root / "secrets.enc.yaml").write_text("{}")
        key = self.tmp / "age.txt"
        key.write_text("# public key: age1fake\n")
        code, checks = self.doctor(env={"SOPS_AGE_KEY_FILE": str(key)})
        by = {c["name"]: c for c in checks}
        for name in ("secrets", "sops", "age key", "op", "codex"):
            self.assertEqual(by[name]["status"], "ok", by[name])
        # Existence checks only: no secret was read or decrypted.
        self.assertEqual(self.calls("op") + self.calls("sops") + self.calls("security"), [])

    def test_age_key_is_optional_when_sops_uses_another_key_type(self):
        self.only("gh", "claude", "sops")
        (self.root / ".sops.yaml").write_text("creation_rules:\n  - kms: arn:aws:kms:x\n")
        _, checks = self.doctor(env={"SOPS_AGE_KEY_FILE": ""})
        (c,) = self.named(checks, "age key")
        self.assertEqual(c["status"], "optional-missing")

    def test_without_refs_the_tools_stay_optional(self):
        self.write_manifest({"version": 2, "profiles": {"p": {"match": "/p"}}})
        self.only("gh", "claude")
        _, checks = self.doctor(env={"SOPS_AGE_KEY_FILE": ""})
        by = {c["name"]: c for c in checks}
        for name in ("secrets", "sops", "age key", "aws"):
            self.assertEqual(by[name]["status"], "optional-missing", by[name])
        self.assertNotIn("op", by)
        self.assertNotIn("codex", by)
        self.assertEqual(self.named(checks, "refs"), [])

    def test_keychain_refs(self):
        self.write_manifest({"version": 2, "profiles": {
            "k": {"match": "/k", "secret_env": {"T": "keychain:actas/k"}}}})
        cfg = self.m.load(quiet=True)
        profs = self.m.all_profiles(cfg)[0]
        refs = self.m.profile_refs(profs)
        self.assertEqual(refs["keychain"], {"keychain:actas/k": [("k", "secret_env.T")]})
        with mock.patch.object(self.m.sys, "platform", "linux"):
            (c,) = self.m.ref_checks(profs, refs, [])
        self.assertIn("need macOS", c[2])
        with mock.patch.object(self.m.sys, "platform", "darwin"), \
                mock.patch.object(self.m, "find_bin", lambda n: None if n == "security" else "/x"):
            (c,) = self.m.ref_checks(profs, refs, [])
        self.assertIn("security not found", c[2])
        with mock.patch.object(self.m.sys, "platform", "darwin"), \
                mock.patch.object(self.m, "find_bin", lambda n: "/usr/bin/" + n):
            self.assertEqual(self.m.ref_checks(profs, refs, []), [])

    def test_env_literals_are_not_refs(self):
        self.write_manifest({"version": 2, "profiles": {
            "p": {"match": "/p", "env": {"LABEL": "gh:not-an-account"}}}})
        _, checks = self.doctor(env={"FAKE_GH_USERS": "ada"})
        self.assertEqual(self.named(checks, "refs"), [])


class V1Refs(Base):
    """A v1 manifest gets the same checks (its refs are translated)."""
    manifest = {"version": 1, "environments": {"personal": {
        "match": ["%(home)s/code/personal"], "aws": {"profile": "personal"},
        "github": {"account": "ada-personal", "name": "Ada", "email": "ada@example.com"}}}}

    def test_v1_manifest(self):
        code, checks = self.doctor(env={"FAKE_GH_USERS": "someone-else"})
        self.assertEqual(code, 0)
        refs = "\n".join(c["detail"] for c in self.named(checks, "refs"))
        self.assertIn("gh:ada-personal", refs)
        self.assertIn("aws profile personal", refs)
        self.assertEqual(len(self.named(checks, "identity")), 1)


# --------------------------------------------------------- list --check

class ListCheck(Base):
    manifest = {"version": 2,
                "roles": {"gw": {"claude": {"provider": "gateway", "base_url": "https://llm.example.org",
                                            "token": "op://vault/gw/token"}}},
                "profiles": {n: {"match": f"/{n}", "use": ["gw"]} for n in ("a", "b", "c", "d")}}

    def test_a_shared_token_is_read_once(self):
        (self.tmp / "fake_store.json").write_text(json.dumps({"op": {"op://vault/gw/token": "tok"}}))
        r = self.cli("list", "--check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)["profiles"]
        self.assertEqual([x["check"]["secret"] for x in rows], ["ok"] * 4)
        self.assertEqual(len(self.calls("op")), 1)
        self.assertNotIn("tok\n", r.stdout)

    def test_a_missing_shared_token_is_missing_everywhere(self):
        r = self.cli("list", "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("secret=missing"), 4)
        self.assertEqual(len(self.calls("op")), 1)


# --------------------------------------------------------- login --all

FAKE_AWS = r'''#!%(py)s
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"bin": "aws", "argv": args, "stdin": None}) + "\n")
prof = args[args.index("--profile") + 1] if "--profile" in args else ""
marker = os.path.join(os.environ["FAKE_MARKERS"], prof)
if args[:2] == ["sso", "login"]:
    if prof in os.environ.get("FAKE_SSO_FAIL", "").split(","):
        sys.exit(255)
    if prof not in os.environ.get("FAKE_SSO_NOOP", "").split(","):
        open(marker, "w").close()
    sys.exit(0)
if args[:2] == ["sts", "get-caller-identity"]:
    if os.path.exists(marker):
        print(json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:x"}))
        sys.exit(0)
    sys.stderr.write("Error when retrieving token from sso: Token has expired and refresh failed\n")
    sys.exit(255)
sys.exit(0)
'''


class LoginAll(Base):
    manifest = {"version": 2, "profiles": {
        "one": {"match": "/one", "aws": {"profile": "one"}},
        "two": {"match": "/two", "aws": {"profile": "two"}}}}

    def setUp(self):
        super().setUp()
        (self.home / ".aws").mkdir()
        (self.home / ".aws" / "config").write_text(
            "[profile one]\nsso_start_url = https://one.awsapps.com/start\n"
            "[profile two]\nsso_start_url = https://two.awsapps.com/start\n")
        aws = self.fakebin / "aws"
        aws.write_text(FAKE_AWS % {"py": sys.executable})
        aws.chmod(0o755)
        (self.tmp / "markers").mkdir()
        os.environ["FAKE_MARKERS"] = str(self.tmp / "markers")

    def sso_logins(self):
        return [c["argv"] for c in self.calls("aws") if c["argv"][:2] == ["sso", "login"]]

    def test_all_logins_succeed(self):
        r = self.cli("login", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.sso_logins()), 2)
        self.assertNotIn("failed", r.stdout)
        # Already logged in: no second browser round-trip.
        r = self.cli("login", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("ok "), 2)
        self.assertEqual(len(self.sso_logins()), 2)

    def test_a_failed_login_exits_1_and_the_rest_still_run(self):
        r = self.cli("login", "--all", env={"FAKE_SSO_FAIL": "one"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed  https://one.awsapps.com/start", r.stdout)
        self.assertNotIn("failed  https://two", r.stdout)
        self.assertIn("1 of 2 SSO logins did not take: https://one.awsapps.com/start", r.stderr)
        self.assertEqual(len(self.sso_logins()), 2)                   # two was still logged in
        self.assertNotIn("Traceback", r.stderr)

    def test_a_login_that_does_not_take_fails(self):
        # sso login exits 0, but the credentials are still expired afterwards.
        r = self.cli("login", "--all", env={"FAKE_SSO_NOOP": "two"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed  https://two.awsapps.com/start", r.stdout)
        self.assertIn("did not take: https://two.awsapps.com/start", r.stderr)


if __name__ == "__main__":
    unittest.main()
