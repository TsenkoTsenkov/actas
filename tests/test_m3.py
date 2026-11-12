"""M3 (2.0.0-alpha3): the sync rewrite (SPEC 11.1: git-root aware, nested
profiles skipped, ledger, --dry-run, --clean, confirm profiles), the
SessionStart hook and context cache (10.2), `desktop` (11.2-11.3) and
`instructions` with AGENT_RULES and the direnv function (14.1, 11.4)."""

import json
import os
import shlex
import subprocess
import sys
import unittest

from actas_testlib import REPO, Sandbox

SECRETS = {"client-a": {"token": "gw-SECRET-1", "npm": "npm-SECRET-2"}}
SECRET_VALUES = ("gw-SECRET-1", "npm-SECRET-2")


def manifest(client_env=None):
    return {
        "version": 2,
        "profiles": {
            "client-a": {
                "match": ["%(tmp)s/code/client-a", "%(tmp)s/code/client-a-wt"],
                "git": {"name": "Ada Example", "email": "ada@client-a.example"},
                "aws": {"profile": "client-a-dev", "region": "eu-west-1"},
                "claude": {"provider": "gateway", "base_url": "https://llm.client-a.example",
                           "token": "sops:client-a/token",
                           "settings": {"permissions": {"deny": ["Bash(rm *)"]}}},
                "env": client_env if client_env is not None else {"A_VAR": "1", "B_VAR": "2"},
                "secret_env": {"NPM_TOKEN": "sops:client-a/npm"},
            },
            "client-a-prod": {"match": "%(tmp)s/code/client-a/infra/prod", "level": "readonly",
                              "aws": {"profile": "client-a-prod-ro", "region": "eu-west-1"}},
            "personal": {"match": "%(tmp)s/code/personal",
                         "github": {"auth": "gh:ada-personal"}},
            "personal-power": {"use": ["personal"], "match": "%(tmp)s/code/power", "level": "power",
                               "confirm": True},
        },
    }


class Base(Sandbox):
    secrets = SECRETS

    def setUp(self):
        self.manifest = manifest()
        super().setUp()
        self.code = self.tmp / "code"
        for d in ("client-a/infra/prod", "personal", "power"):
            (self.code / d).mkdir(parents=True)
        self.git("init", "-q", str(self.code / "client-a"))

    def git(self, *args):
        r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def sf(self, d):
        return self.code / d / ".claude" / "settings.local.json"

    def read(self, d):
        return json.loads(self.sf(d).read_text())

    def ledger(self):
        f = self.tmp / "state" / "sync-ledger.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def context(self, name):
        return self.tmp / "cache" / "actas" / f"{name}.context.txt"

    def assert_no_secret(self, *texts):
        for t in texts:
            for s in SECRET_VALUES:
                self.assertNotIn(s, t)


class Sync(Base):
    def test_writes_at_repo_root_and_skips_nested_profiles(self):  # SPEC test 56 (D6)
        leftover = self.sf("client-a/infra/prod")
        leftover.parent.mkdir()
        leftover.write_text(json.dumps({"apiKeyHelper": "ccenv secret client-a-prod token",
                                        "env": {"AWS_PROFILE": "old"}}))
        r = self.cli("sync")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.read("client-a")
        self.assertEqual(data["env"]["AWS_PROFILE"], "client-a-dev")
        self.assertEqual(data["env"]["ACTAS"], "client-a")
        # D-E: a file actas wrote there earlier is kept current, never a --clean hint;
        # the update line names the root's profile instead of a "skip" line (R9)
        self.assertIn(f"updating {leftover}", r.stderr)
        self.assertIn("is client-a; use `actas run` here", r.stderr)
        self.assertNotIn(f"skip {leftover.parent.parent}", r.stderr)
        self.assertNotIn("--clean", r.stderr)
        self.assertEqual(json.loads(leftover.read_text())["env"]["AWS_PROFILE"], "client-a-prod-ro")
        self.assertEqual(self.ledger()[str(leftover)]["profile"], "client-a-prod")
        r = self.cli("sync", "client-a-prod", "--clean")       # still a target: kept
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(leftover.exists())
        m = manifest()
        del m["profiles"]["client-a-prod"]                       # no longer a target: stale
        self.write_manifest(m)
        r = self.cli("sync", "--clean", "--dry-run")
        self.assertIn(f"would clean {leftover}", r.stdout)
        self.assertTrue(leftover.exists())
        r = self.cli("sync", "--clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(leftover.exists())
        self.assertFalse(leftover.parent.exists())
        self.assertTrue(self.sf("client-a").exists())          # other profiles untouched

    def test_worktree_maps_to_main_checkout(self):  # SPEC test 56
        repo = self.code / "client-a"
        self.git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "commit", "-q", "--allow-empty", "-m", "init")
        self.git("-C", str(repo), "worktree", "add", "-q", str(self.code / "client-a-wt"))
        r = self.cli("sync", "client-a")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("wrote"), 1, r.stdout)
        self.assertTrue(self.sf("client-a").exists())
        self.assertFalse(self.sf("client-a-wt").exists())

    def test_private_files_ledger_and_clean(self):  # SPEC test 57 (D10, D11)
        f = self.sf("client-a")
        f.parent.mkdir()
        f.write_text(json.dumps({"env": {"KEEP": "1"},
                                 "permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(curl *)"]}}))
        r = self.cli("sync")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        data = self.read("client-a")
        self.assertEqual((data["env"]["A_VAR"], data["env"]["B_VAR"], data["env"]["KEEP"]), ("1", "2", "1"))
        deny = data["permissions"]["deny"]                      # the user's rule first, then actas's
        self.assertEqual(deny[0], "Bash(curl *)")
        self.assertIn("Bash(rm *)", deny)
        self.assertIn("Bash(git credential *)", deny)           # M4: the standard preset
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(hook.endswith("hook session-start --profile client-a --source sync"), hook)
        led = self.ledger()[str(f)]
        self.assertEqual(led["profile"], "client-a")
        self.assertIn("Bash(rm *)", led["permissions"]["deny"])
        self.assertNotIn("Bash(curl *)", led["permissions"]["deny"])   # the user's rule is not owned
        self.assertIn("Bash(git push)", led["permissions"]["ask"])
        self.assertIn("B_VAR", led["env"])
        self.assertTrue(led["helpers"] and all(os.path.isabs(h) for h in led["helpers"]))
        self.assertEqual((self.tmp / "state" / "sync-ledger.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.sf("personal").exists())

        self.write_manifest(manifest(client_env={"A_VAR": "1"}))      # B_VAR removed from the manifest
        self.assertEqual(self.cli("sync").returncode, 0)
        data = self.read("client-a")
        self.assertNotIn("B_VAR", data["env"])                       # D11: the ledger drops it
        self.assertEqual((data["env"]["A_VAR"], data["env"]["KEEP"]), ("1", "1"))
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)      # not duplicated

        r = self.cli("sync", "--clean")                              # D-E: every file is still a target
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read("client-a")["env"]["ACTAS"], "client-a")
        self.assertTrue(self.sf("personal").exists())

        m = manifest(client_env={"A_VAR": "1"})
        for n in ("client-a", "personal", "personal-power"):
            del m["profiles"][n]
        self.write_manifest(m)
        r = self.cli("sync")
        self.assertIn(f"stale {self.sf('client-a')} (client-a)", r.stderr)
        self.assertTrue(self.sf("client-a").exists())
        r = self.cli("sync", "--clean")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read("client-a"), {"env": {"KEEP": "1"},
                                                 "permissions": {"allow": ["Bash(ls)"],
                                                                 "deny": ["Bash(curl *)"]}})
        self.assertFalse(self.sf("personal").exists())               # became {}: deleted
        self.assertEqual(self.ledger(), {})

    def test_dry_run_writes_nothing(self):
        r = self.cli("sync", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"would write {self.sf('client-a')}", r.stdout)
        self.assertIn("+env.AWS_PROFILE", r.stdout)
        self.assertIn("+apiKeyHelper", r.stdout)
        self.assertFalse((self.code / "client-a" / ".claude").exists())
        self.assertFalse((self.tmp / "state").exists())
        self.assertFalse(self.context("client-a").exists())

    def test_confirm_profiles_and_git_exclude(self):  # SPEC test 58
        exclude = self.code / "client-a" / ".git" / "info" / "exclude"
        before = exclude.read_bytes() if exclude.exists() else None
        r = self.cli("sync")
        self.assertIn("personal-power requires confirmation", r.stderr)
        self.assertFalse(self.sf("power").exists())
        self.assertIn('add ".claude/settings.local.json" to .gitignore', r.stderr)
        self.assertEqual(exclude.read_bytes() if exclude.exists() else None, before)
        (self.code / "client-a" / ".gitignore").write_text(".claude/settings.local.json\n")
        r = self.cli("sync", "--include-confirm")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("to .gitignore", r.stderr)
        self.assertEqual(self.read("power")["env"]["ACTAS_LEVEL"], "power")

    def test_no_secret_in_generated_files(self):  # SPEC test 35
        self.assertEqual(self.cli("sync").returncode, 0)
        with_cwd = {"cwd": str(self.code / "client-a")}
        self.assertEqual(self.cli("run", "client-a", "--no-check", **with_cwd).returncode, 0)
        self.assertEqual(json.loads(self.out.read_text())["env"]["NPM_TOKEN"], "npm-SECRET-2")
        texts = [p.read_text() for d in (self.code, self.tmp / "cache", self.tmp / "state")
                 for p in d.rglob("*") if p.is_file() and ".git" not in p.parts]
        self.assertTrue(texts)
        self.assert_no_secret(*texts)
        self.assertNotIn("NPM_TOKEN", self.read("client-a")["env"])

    def test_shared_is_gone(self):
        r = self.cli("sync", "--shared")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in actas 2.0", r.stderr)


class Hook(Base):  # SPEC test 60
    def hook(self, source, **env):
        return self.cli("hook", "session-start", "--profile", "client-a", "--source", source, env=env)

    def test_context_and_dedup(self):
        r = self.hook("sync")
        self.assertEqual((r.returncode, r.stdout),
                         (0, "actas: profile client-a (run actas sync to refresh this note)\n"))
        self.assertEqual(self.cli("sync", "client-a").returncode, 0)
        text = self.context("client-a").read_text()
        self.assertEqual(self.context("client-a").stat().st_mode & 0o777, 0o600)
        self.assertTrue(text.startswith("actas: profile client-a (standard) · aws client-a-dev eu-west-1 · "
                                        "claude gateway\nactas: credentials follow this session"), text)
        self.assertIn("infra/prod → client-a-prod", text)               # nested profile note
        self.assertEqual(self.hook("sync").stdout, text)
        self.assertEqual(self.hook("sync", ACTAS_SESSION="4242").stdout, "")   # run's hook speaks
        self.assertEqual(self.hook("run", ACTAS_SESSION="4242", ACTAS="client-a").stdout, text)
        self.assertEqual(self.hook("run", ACTAS="personal").stdout, "")
        r = self.cli("hook", "session-start", "--profile", "../x", "--source", "run")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertNotIn("nested", self.cli("sync", "personal").stderr)
        self.assertNotIn("subdirectories", self.context("personal").read_text())

    def test_run_installs_its_hook_and_session(self):
        r = self.cli("run", "client-a", "--no-check")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(self.out.read_text())
        (group,) = got["settings"]["hooks"]["SessionStart"]
        words = shlex.split(group["hooks"][0]["command"])
        self.assertTrue(all(os.path.isabs(w) for w in words[:2]))
        self.assertEqual(words[2:], ["hook", "session-start", "--profile", "client-a", "--source", "run"])
        self.assertTrue(got["env"]["ACTAS_SESSION"].isdigit())
        self.assertNotIn("ACTAS_SESSION", got["settings"]["env"])
        self.assertTrue(self.context("client-a").exists())
        # The installed command really prints the note.
        out = subprocess.run(words, capture_output=True, text=True, env=dict(os.environ, ACTAS="client-a"))
        self.assertEqual(out.stdout, self.context("client-a").read_text())


class LedgerChecks(Base):  # SPEC test 38 and the 6.1 run warning
    def test_run_and_doctor_read_the_ledger(self):
        self.assertEqual(self.cli("sync", "client-a").returncode, 0)
        here = {"cwd": str(self.code / "client-a")}
        r = self.cli("run", "personal", "--no-check", **here)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("this repo is synced to client-a", r.stderr)
        r = self.cli("run", "client-a", "--no-check", **here)
        self.assertNotIn("synced to", r.stderr)
        self.assertNotIn("no longer exists", r.stderr)
        self.assertNotIn("helper path", self.cli("doctor").stdout)
        f = self.tmp / "state" / "sync-ledger.json"
        led = json.loads(f.read_text())
        next(iter(led.values()))["helpers"].append(str(self.tmp / "gone" / "python3"))
        f.write_text(json.dumps(led))
        r = self.cli("run", "client-a", "--no-check", **here)
        self.assertIn("no longer exists; run actas sync", r.stderr)
        r = self.cli("doctor", "--json")
        sync = [c for c in json.loads(r.stdout)["checks"] if c["name"] == "sync"]
        self.assertEqual(sync[0]["status"], "warn")
        self.assertIn("run actas sync", sync[0]["detail"])


class Desktop(Base):  # SPEC test 61
    def test_print_3p(self):
        r = self.cli("desktop", "client-a", "--print-3p")
        self.assertEqual(r.returncode, 0, r.stderr)
        block = json.loads(r.stdout)
        self.assertEqual(block["inferenceProvider"], "gateway")
        self.assertEqual(block["inferenceGatewayBaseUrl"], "https://llm.client-a.example")
        self.assertEqual(block["inferenceGatewayAuthScheme"], "bearer")
        helper, args = block["inferenceCredentialHelper"], block["inferenceCredentialHelperArgs"]
        self.assertTrue(os.path.isabs(helper) and os.access(helper, os.X_OK))
        self.assertEqual(args[1:], ["secret", "--ref", "sops:client-a/token"])
        self.assertEqual(args[0], str(self.bin))
        self.assertIn("inferenceCredentialHelperTtlSec", r.stderr)
        self.assert_no_secret(r.stdout, r.stderr)
        # The pair really yields the token (as the app would run it).
        out = subprocess.run([helper] + args, capture_output=True, text=True)
        self.assertEqual(out.stdout, "gw-SECRET-1")
        r = self.cli("desktop", "personal", "--print-3p")
        self.assertEqual(r.returncode, 1)
        self.assertIn("gateway and api-key", r.stderr)

    def test_vscode_and_summary(self):
        r = self.cli("desktop", "client-a", "--vscode")
        self.assertEqual(r.returncode, 0, r.stderr)
        pairs = json.loads(r.stdout)["claudeCode.environmentVariables"]
        env = {p["name"]: p["value"] for p in pairs}
        self.assertEqual((env["AWS_PROFILE"], env["ACTAS"]), ("client-a-dev", "client-a"))
        self.assertNotIn("NPM_TOKEN", env)
        self.assertEqual(env["ACTAS_GIT_BASE"], "0")
        self.assert_no_secret(r.stdout, r.stderr)
        r = self.cli("desktop", "client-a")
        self.assertIn("--print-3p", r.stdout)
        self.assertIn("Remote Control is off", r.stdout)
        r = self.cli("desktop", "personal-power")
        self.assertIn("needs confirmation", r.stdout)
        self.assertIn("signed in to", r.stdout)


class Instructions(Base):  # SPEC test 63 (instructions side)
    def test_agent_rules(self):
        r = self.cli("instructions")
        self.assertEqual(r.stdout, self.m.AGENT_RULES + "\n")
        self.assertTrue(r.stdout.startswith("## Credentials (actas)\n"))
        self.assertEqual([ln[:2] for ln in r.stdout.splitlines()[2:]],
                         ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8."])
        for bad in ("this will prompt", "After approval", "actas request"):
            self.assertNotIn(bad, r.stdout)
        for agent, hint in (("claude", "@AGENTS.md"), ("gemini", "context.fileName"), ("codex", "AGENTS.md")):
            r = self.cli("instructions", "--agent", agent)
            self.assertEqual(r.stdout, self.m.AGENT_RULES + "\n")       # stdout stays pasteable
            self.assertIn(hint, r.stderr)

    def test_direnv_function(self):  # SPEC 11.4
        r = self.cli("instructions", "--direnv")
        self.assertEqual(r.stdout, (REPO / "contrib" / "direnvrc").read_text())
        rc = self.tmp / "direnvrc"
        rc.write_text(r.stdout)
        wrapper = self.fakebin / "actas"
        wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(self.bin))} \"$@\"\n")
        wrapper.chmod(0o755)
        script = ('watch_file() { :; }; log_error() { echo "LOGERR $*" >&2; }; . "$1"; cd "$2" || exit 9; '
                  'use_actas; rc=$?; echo "rc=$rc ACTAS=${ACTAS-} AWS=${AWS_PROFILE-} NPM=${NPM_TOKEN-unset}"')
        out = subprocess.run(["bash", "-c", script, "_", str(rc), str(self.code / "client-a")],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout, "rc=0 ACTAS=client-a AWS=client-a-dev NPM=unset\n", out.stderr)
        out = subprocess.run(["bash", "-c", script, "_", str(rc), str(self.tmp)],
                             capture_output=True, text=True)
        self.assertTrue(out.stdout.startswith("rc=1 ACTAS= "), out.stdout)
        self.assertIn("LOGERR actas failed", out.stderr)


if __name__ == "__main__":
    unittest.main()
