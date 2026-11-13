"""Robustness: file-system errors end in one `actas:` line (no traceback
unless ACTAS_DEBUG=1), sync keeps its ledger when a later repo fails,
per-invocation state is reset, `secret ENV KEY` goes through read_ref; and
regression tests for emission and safety branches nothing else pinned: git
signing / ssh_key / per-URL credentials, github.home on GHE, aws-handoff's
long-lived-key path, the first `secret set`, and `login --all`."""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest

from actas_testlib import FAKE_COMMON, SYSTEM_PATH, Sandbox, make_fakebin

ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0     # chmod does not stop root


def git_init(d):
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)


class ReadOnly:
    """chmod a directory read-only for the block; always restored."""

    def __init__(self, d):
        self.d = d

    def __enter__(self):
        self.d.mkdir(parents=True, exist_ok=True)
        self.d.chmod(0o500)

    def __exit__(self, *exc):
        self.d.chmod(0o700)


@unittest.skipIf(ROOT_USER, "permission tests need a non-root user")
class SyncPartialFailure(Sandbox):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.tmp / "a", self.tmp / "b"
        for d in (self.a, self.b):
            git_init(d)
        self.both = {"version": 2, "profiles": {"pa": {"match": str(self.a), "env": {"FOO": "1"}},
                                                "pb": {"match": str(self.b), "env": {"FOO": "2"}}}}
        self.write_manifest(self.both)
        self.ledger = self.tmp / "state" / "sync-ledger.json"
        self.fa = self.a / ".claude" / "settings.local.json"

    def entries(self):
        return json.loads(self.ledger.read_text())

    def test_written_files_stay_ledgered_when_a_later_repo_fails(self):
        with ReadOnly(self.b / ".claude"):
            r = self.cli("sync")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(f"skip {self.b / '.claude' / 'settings.local.json'}: cannot write it "
                      "(Permission denied)", r.stderr)
        self.assertTrue(self.fa.exists())
        entry = self.entries()[str(self.fa)]
        self.assertEqual(entry["profile"], "pa")
        self.assertTrue(entry["permissions"], "the rules actas added must be ledgered")
        self.assertEqual(list(self.entries()), [str(self.fa)])
        self.assertEqual([x["action"] for x in map(json.loads, (self.tmp / "state" / "audit.jsonl")
                                                   .read_text().splitlines())], ["sync"])
        # Recovery: pb syncs once writable; dropping pa and --clean deletes its file outright.
        self.assertEqual(self.cli("sync").returncode, 0)
        self.write_manifest({"version": 2, "profiles": {"pb": self.both["profiles"]["pb"]}})
        r = self.cli("sync", "--clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.fa.exists(), "a file written before the failure must be cleanable")
        self.assertNotIn(str(self.fa), self.entries())

    def test_clean_that_cannot_remove_keeps_the_entry(self):
        self.assertEqual(self.cli("sync").returncode, 0)
        self.write_manifest({"version": 2, "profiles": {"pb": self.both["profiles"]["pb"]}})
        with ReadOnly(self.a / ".claude"):
            r = self.cli("sync", "--clean")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(f"skip {self.fa}: cannot clean it", r.stderr)
        self.assertIn(str(self.fa), self.entries())          # a later --clean retries
        r = self.cli("sync", "--clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.fa.exists())

    def test_unwritable_cache_warns_and_still_ledgers(self):
        cache = self.tmp / "cache"
        with ReadOnly(cache):
            r = self.cli("sync")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("could not write pa's session note", r.stderr)
        self.assertEqual(set(self.entries()), {str(self.fa), str(self.b / ".claude" / "settings.local.json")})


@unittest.skipIf(ROOT_USER, "permission tests need a non-root user")
class NoTracebacks(Sandbox):
    manifest = {"version": 2, "profiles": {"pa": {"env": {"FOO": "1"}}}}

    def test_run_with_an_unwritable_cache(self):
        cache = self.tmp / "cache"
        with ReadOnly(cache):
            r = self.cli("run", "pa")
            dbg = self.cli("run", "pa", env={"ACTAS_DEBUG": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.strip().splitlines(),
                         [f"actas: {cache / 'actas'}: Permission denied (ACTAS_DEBUG=1 shows the traceback)"])
        self.assertEqual(dbg.returncode, 1)
        self.assertIn("Traceback", dbg.stderr)
        self.assertIn("PermissionError", dbg.stderr)


class MainHandlers(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {}}}

    def main(self, exc, *argv):
        def boom(argv=None):
            raise exc
        self.m._main = boom
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as c:
                self.m.main(list(argv))
        return c.exception.code, out.getvalue(), err.getvalue()

    def test_oserror_is_one_line(self):
        f = self.m.HOME / "x" / "f.json"
        code, out, err = self.main(PermissionError(13, "Permission denied", str(f)))
        self.assertEqual(code, 1)
        self.assertEqual(err, "actas: ~/x/f.json: Permission denied (ACTAS_DEBUG=1 shows the traceback)\n")
        code, _, err = self.main(OSError(28, "No space left on device"))
        self.assertEqual((code, err), (1, "actas: No space left on device (ACTAS_DEBUG=1 shows the traceback)\n"))

    def test_oserror_reraised_with_debug(self):
        os.environ["ACTAS_DEBUG"] = "1"
        self.m._main = lambda argv=None: (_ for _ in ()).throw(PermissionError(13, "Permission denied", "/x"))
        with self.assertRaises(PermissionError):
            self.m.main([])

    def test_json_mode_reports_the_error(self):
        self.m._MODE["json"] = True
        code, out, _ = self.main(PermissionError(13, "Permission denied", "/x"))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "error")

    def test_broken_pipe_is_quiet(self):
        code, _, err = self.main(BrokenPipeError(32, "Broken pipe"))
        self.assertEqual((code, err), (1, ""))

    def test_state_is_reset_per_invocation(self):
        m = self.m
        m.WARNINGS.append("old warning")
        m.HELD.append("old held line")
        m._SOPS.update(data={"p": {"k": "stale"}}, err=None)
        m._CACHE["who"] = {"stale": True}
        m._MODE.update(json=True, no_color=True, hold=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                m.main(["--version"])
        self.assertEqual((m.WARNINGS, m.HELD, m._SOPS), ([], [], {}))
        self.assertNotIn("who", m._CACHE)
        self.assertFalse(m._MODE["json"] or m._MODE["no_color"])
        self.assertNotIn("old held line", err.getvalue())


class SecretEnvKey(Sandbox):
    secrets = {"p": {"k": "v"}}

    def test_v1_form_reads_through_read_ref(self):
        r = self.cli("secret", "p", "k")
        self.assertEqual((r.returncode, r.stdout), (0, "v"))
        self.assertIn("--extract", self.calls("sops")[-1]["argv"])      # one key, not the whole file
        r = self.cli("secret", "p", "nope")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("could not read secret p.nope: component not found "
                      "(run: actas secret p nope --set)", r.stderr)
        r = self.cli("secret", "p", "bad/key")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid secret name 'bad/key'", r.stderr)
        (self.root / "secrets.enc.yaml").unlink()
        r = self.cli("secret", "p", "k")
        self.assertIn("no secrets file at", r.stderr)


GIT = {"version": 2, "profiles": {
    "g": {"git": {"name": "Ada", "email": "ada@example.com", "ssh_key": "~/.ssh/id_client",
                  "signing": {"format": "ssh", "key": "/keys/id_client.pub", "program": "/opt/ssh-keygen"},
                  "sign": True,
                  "credentials": {"https://git.example.com": {"token": "keychain:svc/acct"},
                                  "https://other.example.com": {"token": "sops:g/tok", "username": "bot"}}},
          "github": {"home": "h", "host": "ghe.example.com"}},
    "gpg": {"git": {"signing": {"format": "openpgp", "key": "ABC123", "program": "/opt/gpg"}, "sign": False}},
    "gh": {"github": {"home": "h"}},
}}


class GitEmission(Sandbox):
    manifest = GIT

    def shell(self, name, env=None):
        r = self.cli("shell", name, "--format", "json", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(r.stdout)["set"]
        return s, [(s[f"GIT_CONFIG_KEY_{i}"], s[f"GIT_CONFIG_VALUE_{i}"])
                   for i in range(int(s.get("GIT_CONFIG_COUNT", "0")))]

    def test_identity_ssh_signing_and_credentials(self):  # SPEC 4.6 order
        s, pairs = self.shell("g")
        helper = f"!{self.m.helper_prefix()} git-credential --ref"
        ghe = "credential.https://ghe.example.com.helper"
        self.assertEqual(pairs, [
            ("user.name", "Ada"), ("user.email", "ada@example.com"),
            ("core.sshCommand", f"ssh -i {self.home / '.ssh' / 'id_client'} -o IdentitiesOnly=yes"),
            ("gpg.format", "ssh"), ("user.signingkey", "/keys/id_client.pub"),
            ("gpg.ssh.program", "/opt/ssh-keygen"),
            ("commit.gpgsign", "true"), ("tag.gpgsign", "true"),
            (ghe, ""), (ghe, f"!{self.fakebin / 'gh'} auth git-credential"),
            ("credential.https://git.example.com.helper", ""),
            ("credential.https://git.example.com.helper",
             f"{helper} keychain:svc/acct --username x-access-token"),
            ("credential.https://other.example.com.helper", ""),
            ("credential.https://other.example.com.helper", f"{helper} sops:g/tok --username bot"),
        ])
        # every helper comes right after an empty reset of the same key
        for i, (k, v) in enumerate(pairs):
            if k.endswith(".helper") and v:
                self.assertEqual(pairs[i - 1], (k, ""))
        self.assertEqual(s["GH_CONFIG_DIR"], str(self.tmp / "state" / "homes" / "github" / "h"))
        self.assertEqual(s["GH_HOST"], "ghe.example.com")
        self.assertNotIn("GH_TOKEN", s)

    def test_gpg_signing_and_sign_false(self):
        _, pairs = self.shell("gpg")
        self.assertEqual(pairs, [("gpg.format", "openpgp"), ("user.signingkey", "ABC123"),
                                 ("gpg.program", "/opt/gpg"),
                                 ("commit.gpgsign", "false"), ("tag.gpgsign", "false")])

    def test_github_home_on_github_com(self):
        s, pairs = self.shell("gh")
        key = "credential.https://github.com.helper"
        self.assertEqual(pairs, [(key, ""), (key, f"!{self.fakebin / 'gh'} auth git-credential")])
        self.assertNotIn("GH_HOST", s)
        # Without gh, actas adds no helper rather than one that cannot run.
        bare = make_fakebin(self.tmp / "bare", only=("ps",))
        s, pairs = self.shell("gh", env={"PATH": f"{bare}:{SYSTEM_PATH}"})
        if not self.m.shutil.which("gh", path=SYSTEM_PATH):
            self.assertEqual(pairs, [])
        self.assertEqual(s["GH_CONFIG_DIR"], str(self.tmp / "state" / "homes" / "github" / "h"))


LONG_AWS = r'''
args = sys.argv[1:]
log()
if args[:2] == ["sts", "get-caller-identity"]:
    print(json.dumps({"Account": "123456789012"}))
elif args[:2] == ["configure", "export-credentials"]:
    print(json.dumps({"Version": 1, "AccessKeyId": "AKIA-LONG", "SecretAccessKey": "LONG-SECRET"}))
elif args[:2] == ["sts", "get-session-token"]:
    mode = os.environ.get("FAKE_GST", "ok")
    if mode == "fail":
        sys.stderr.write("denied\n")
        sys.exit(255)
    if mode == "garbage":
        print("{}")
    else:
        print(json.dumps({"Credentials": {"AccessKeyId": "ASIA-SESS", "SecretAccessKey": "SESS-SECRET",
                                          "SessionToken": "SESS-TOKEN",
                                          "Expiration": "2026-01-01T00:00:00Z"}}))
'''


class AwsHandoffLongLived(Sandbox):
    manifest = {"version": 2, "profiles": {"iam": {"aws": {"profile": "iam-keys", "region": "eu-west-1"}}}}

    def setUp(self):
        super().setUp()
        aws = self.fakebin / "aws"
        aws.write_text(f"#!{sys.executable}\n" + FAKE_COMMON + LONG_AWS)

    def test_trades_long_lived_keys_for_a_session_token(self):
        r = self.cli("aws-handoff", "iam", "--stdout")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aws_access_key_id = ASIA-SESS", r.stdout)
        self.assertIn("aws_session_token = SESS-TOKEN", r.stdout)
        for leak in ("AKIA-LONG", "LONG-SECRET"):
            self.assertNotIn(leak, r.stdout + r.stderr)
        self.assertIn("long-lived keys - trading them for a 3600s session token", r.stderr)
        self.assertIn(["sts", "get-session-token", "--duration-seconds", "3600", "--profile", "iam-keys"],
                      [c["argv"] for c in self.calls("aws")])

    def test_refuses_when_no_session_token(self):
        for mode, msg in (("fail", "refusing to hand them over: denied"),
                          ("garbage", "get-session-token printed no usable credentials")):
            r = self.cli("aws-handoff", "iam", "--stdout", env={"FAKE_GST": mode})
            self.assertEqual((r.returncode, r.stdout), (1, ""), mode)
            self.assertIn(msg, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            for leak in ("AKIA-LONG", "LONG-SECRET"):
                self.assertNotIn(leak, r.stdout + r.stderr)

    def test_export_without_keys_dies_cleanly(self):
        (self.fakebin / "aws").write_text(
            f"#!{sys.executable}\n" + FAKE_COMMON + "log()\nif sys.argv[1:3] == ['configure', "
            "'export-credentials']:\n    print('[]')\n")
        r = self.cli("aws-handoff", "iam", "--stdout")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("export-credentials printed no usable credentials", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class FirstSecret(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {}}}

    def secrets_file(self):
        return self.root / "secrets.enc.yaml"

    def test_no_sops_config_creates_nothing(self):
        r = self.cli("secret", "set", "sops:p/k", stdin="v\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"no {self.root / '.sops.yaml'} saying who can decrypt secrets (run: actas init)",
                      r.stderr)
        self.assertFalse(self.secrets_file().exists())
        self.assertEqual([c for c in self.calls("sops") if "set" in c["argv"][:1]], [])

    def test_creates_the_file_owner_only(self):
        (self.root / ".sops.yaml").write_text("creation_rules: [{age: age1x}]\n")
        r = self.cli("secret", "set", "sops:p/k", stdin="v-SECRET\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.stat(self.secrets_file()).st_mode & 0o777, 0o600)
        enc = [c["argv"] for c in self.calls("sops") if "-e" in c["argv"]]
        self.assertEqual(enc, [["--config", str(self.root / ".sops.yaml"), "-e", "-i",
                                str(self.secrets_file())]])
        self.assertEqual(self.cli("secret", "--ref", "sops:p/k").stdout, "v-SECRET")

    def test_failed_encryption_leaves_no_plaintext_file(self):
        (self.root / ".sops.yaml").write_text("creation_rules: [{age: age1x}]\n")
        bad = self.tmp / "badsops"
        bad.mkdir()
        (bad / "sops").write_text(f"#!{sys.executable}\n" + FAKE_COMMON
                                  + "log()\nsys.stderr.write('no key\\n')\nsys.exit(1)\n")
        (bad / "sops").chmod(0o755)
        r = self.cli("secret", "set", "sops:p/k", stdin="v\n",
                     env={"PATH": f"{bad}:{self.fakebin}:{SYSTEM_PATH}"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not initialise secrets file: no key", r.stderr)
        self.assertFalse(self.secrets_file().exists())


class LoginAll(Sandbox):
    manifest = {"version": 2, "profiles": {"a": {"aws": {"profile": "used-1"}},
                                           "b": {"aws": {"profile": "used-2"}}}}

    def aws_config(self, text):
        (self.home / ".aws").mkdir(exist_ok=True)
        (self.home / ".aws" / "config").write_text(text)

    def test_logs_in_only_to_portals_a_profile_uses(self):
        self.aws_config("[profile used-1]\nsso_session = corp\n[profile used-2]\nsso_session = corp\n"
                        "[profile other]\nsso_start_url = https://other.awsapps.com/start\n"
                        "[sso-session corp]\nsso_start_url = https://corp.awsapps.com/start\n")
        r = self.cli("login", "--all", env={"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "Token has expired"})
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("1 SSO portals for profiles: used-1, used-2", r.stderr)
        logins = [c["argv"] for c in self.calls("aws") if c["argv"][:2] == ["sso", "login"]]
        self.assertEqual(len(logins), 1, logins)
        self.assertIn("used-1", logins[0])
        self.assertIn("login   sso-session corp  (2 profiles)", r.stdout)

    def test_no_sso_profile_in_use(self):
        self.aws_config("[profile other]\nsso_start_url = https://other.awsapps.com/start\n")
        r = self.cli("login", "--all")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no profile uses an SSO profile from ~/.aws/config", r.stderr)
        self.assertEqual([c for c in self.calls("aws") if c["argv"][:2] == ["sso", "login"]], [])


if __name__ == "__main__":
    unittest.main()
