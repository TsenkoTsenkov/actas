"""Regression tests for an audit of bin/actas: inputs that used to end in a
traceback (or unsafe output) now end in one clear message."""

import os
import unittest

from actas_testlib import Sandbox


def manifest(**profiles):
    return {"version": 2, "profiles": profiles}


class GithubHost(Sandbox):
    manifest = manifest(ghe={"match": "~/code/ghe",
                             "github": {"auth": "gh:ada", "host": "https://ghe.example.com"}})

    def test_url_as_host_is_a_manifest_error(self):
        r = self.cli("status", "ghe", "--json")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("github.host", r.stderr)
        self.assertIn("not a host name", r.stderr)

    def test_list_reports_it_instead_of_a_bad_ref(self):
        r = self.cli("list")
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("ada@https", r.stdout)
        self.assertIn("not a host name", r.stderr)


class ListCheckIncompleteProfile(Sandbox):
    # A gateway without a token: doctor rejects it; list --check used to
    # crash with KeyError in emit_claude.
    manifest = manifest(gw={"match": "~/code/gw", "aws": {"profile": "client-a"},
                            "claude": {"provider": "gateway", "base_url": "https://llm.example.com"}},
                        ok={"match": "~/code/ok", "git": {"name": "Ada Example", "email": "ada@example.com"}})

    def test_no_traceback_and_the_profile_is_named(self):
        for args in (("list",), ("list", "--check")):
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("profile gw:", r.stderr)
                self.assertIn("ok", r.stdout)


class ShellOutputNames(Sandbox):
    manifest = manifest(p={"match": "~/code/p", "aws": {"profile": "client-a", "region": "eu-west-1"}})

    def test_odd_inherited_names_never_reach_the_shell(self):
        evil = "AWS_CONTAINER_CREDENTIALS_X;echo PWNED"
        for fmt in ("sh", "fish"):
            with self.subTest(fmt=fmt):
                r = self.cli("shell", "p", "--no-secrets", "--format", fmt, env={evil: "1"})
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("PWNED", r.stdout)
                self.assertIn("not a valid variable name", r.stderr)


class NonUtf8Files(Sandbox):
    manifest = manifest(p={"match": "~/code/p", "aws": {"profile": "client-a"}})

    def test_manifest(self):
        path = self.root / "environments.yaml"
        path.write_bytes(path.read_bytes() + b"\n# caf\xe9\n")
        r = self.cli("list")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("not valid UTF-8", r.stderr)

    def test_aws_config(self):
        cfg = self.home / ".aws" / "config"
        cfg.parent.mkdir()
        cfg.write_bytes(b"# caf\xe9\n[profile client-a]\nregion = eu-west-1\n")
        r = self.cli("doctor")
        self.assertNotIn("Traceback", r.stderr)


class CredentialNamePatterns(Sandbox):
    def test_redaction_keeps_non_secret_auth_names(self):
        """The sync ledger's broad pattern no longer shadows redact_argv's."""
        os.environ["SSH_AUTH_SOCK"] = "/tmp/agent.sock"
        os.environ["CLIENT_TOKEN"] = "s3cr3t-value"
        shown, _ = self.m.redact_argv(["ssh", "/tmp/agent.sock", "s3cr3t-value"])
        self.assertIn("/tmp/agent.sock", shown)
        self.assertNotIn("s3cr3t-value", shown)
        self.assertTrue(self.m.LEDGER_CRED_RE.search("SSH_AUTH_SOCK"))


if __name__ == "__main__":
    unittest.main()
