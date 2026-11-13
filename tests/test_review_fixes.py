"""Regression tests for the code-review findings (F-numbers in
IMPLEMENTATION_NOTES "Review fixes"). Each test fails on the code before
its fix."""

import json
import os
import pty
import re
import shlex
import subprocess
import sys
import time
import unittest
from unittest import mock

from actas_testlib import FAKE_COMMON, REPO, Sandbox, make_fakebin
from test_m4 import AGENT, ME, PP, T0, Base, MANIFEST

SECRETS = {"a": {"gh": "ghp_A_SECRET", "vault": "hvs.A_SECRET", "nl": "tok-NL\n"},
           "w": {"openai": "sk-W_SECRET"}}


class G1Security(Sandbox):
    secrets = SECRETS
    manifest = {
        "version": 2,
        "profiles": {
            "a": {"match": "%(tmp)s/code/a", "github": {"auth": "sops:a/gh"},
                  "secret_env": {"VAULT_TOKEN": "sops:a/vault", "NL_TOKEN": "sops:a/nl"},
                  "env": {"A_ONLY": "1"}},
            "personal": {"match": "%(tmp)s/code/p", "git": {"name": "Ada", "email": "ada@example.com"}},
            "off": {"github": None, "aws": None},
            "w": {"codex": {"login": "api", "api_key": "sops:w/openai"}},
            "wh": {"codex": {"login": "api", "api_key": "sops:w/openai", "home": "wh"}},
        },
    }

    def setUp(self):
        super().setUp()
        for d in ("a", "p"):
            (self.tmp / "code" / d).mkdir(parents=True)

    def exec_env(self, *args, env=None):
        r = self.cli("exec", *args, "--", "env", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)

    # F0: pairs written by sync / --format env carry ACTAS_GIT_BASE=0, so a
    # nested exec into another profile drops a's credential helper.
    def test_f0_synced_git_helper_does_not_survive_exec(self):
        r = self.cli("sync", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        synced = json.loads((self.tmp / "code/a/.claude/settings.local.json").read_text())["env"]
        self.assertEqual(synced["ACTAS_GIT_BASE"], "0")
        parent = {k: v for k, v in synced.items() if k.startswith(("GIT_CONFIG", "ACTAS_GIT"))}
        env = self.exec_env("personal", env=parent)
        helpers = [env[k.replace("KEY", "VALUE")] for k in env
                   if k.startswith("GIT_CONFIG_KEY_") and env[k].endswith(".helper")]
        self.assertEqual(helpers, [])
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")                 # personal's name + email
        out = self.cli("shell", "a", "--format", "env").stdout
        self.assertIn("ACTAS_GIT_BASE=0", out.splitlines())

    # F2: a login inside a shared `_off` home is moved aside, never used.
    def test_f2_contaminated_off_home_is_moved_aside(self):
        off = self.tmp / "state/homes/github/_off"
        off.mkdir(parents=True)
        (off / "hosts.yml").write_text("github.com:\n  oauth_token: gho_LEAK\n")
        r = self.cli("doctor", "--json")
        check = [c for c in json.loads(r.stdout)["checks"] if c["name"] == "off homes"]
        self.assertEqual(check[0]["status"], "error")
        r = self.cli("exec", "off", "--", "sh", "-c", 'ls -A "$GH_CONFIG_DIR"')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("must stay empty", r.stderr)
        self.assertFalse((off / "hosts.yml").exists())
        moved = [p for p in off.parent.iterdir() if p.name.startswith("_off.")]
        self.assertEqual(len(moved), 1)
        self.assertTrue((moved[0] / "hosts.yml").exists())          # the user's login is kept, aside
        aws = self.tmp / "state/homes/aws/_off"                     # the empty aws files do not count
        self.assertTrue((aws / "config").exists())
        self.assertEqual(self.m.off_logins(aws), [])
        (aws / "config").write_text("[default]\nsso_start_url = x\n")
        self.assertEqual(self.m.off_logins(aws), ["config"])

    # F3: the real tty_confirm reads a typed answer from a terminal.
    @unittest.skipUnless(hasattr(pty, "fork") and sys.platform != "win32", "needs a pty")
    def test_f3_tty_confirm_works_on_a_real_terminal(self):
        pid, fd = pty.fork()
        if pid == 0:                       # child: /dev/tty is the pty
            try:
                ans = self.m.tty_confirm("type it: ")
                os.write(1, f"\nANSWER={ans!r}\n".encode())
            finally:
                os._exit(0)
        out = b""
        while b"type it: " not in out:
            out += os.read(fd, 1024)
        os.write(fd, b"personal-power\n")
        try:
            while True:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                out += chunk
        except OSError:
            pass
        os.waitpid(pid, 0)
        self.assertIn(b"ANSWER='personal-power'", out)

    # F4: another profile's secret_env variables do not reach this one (its
    # plain env: names are not owned since round 2, R1).
    def test_f4_secret_env_of_one_profile_is_cleared_for_another(self):
        env = self.exec_env("personal", env={"VAULT_TOKEN": "hvs.A_SECRET", "A_ONLY": "1",
                                             "UNRELATED": "keep", "PATH": os.environ["PATH"]})
        self.assertNotIn("VAULT_TOKEN", env)
        self.assertEqual(env["A_ONLY"], "1")
        self.assertEqual(env["UNRELATED"], "keep")
        m = dict(self.manifest)
        m["profiles"] = dict(m["profiles"], personal={"inherit": ["VAULT_TOKEN"]})
        self.write_manifest(m)
        env = self.exec_env("personal", env={"VAULT_TOKEN": "hvs.A_SECRET"})
        self.assertEqual(env["VAULT_TOKEN"], "hvs.A_SECRET")          # inherit: the escape hatch

    # F5: a symlinked settings file is never read, copied or replaced.
    def test_f5_sync_refuses_symlinked_settings(self):
        target = self.home / ".claude" / ".credentials.json"
        target.parent.mkdir()
        target.write_text(json.dumps({"claudeAiOauth": {"accessToken": "OAUTH_SECRET"}}))
        d = self.tmp / "code/a/.claude"
        d.mkdir()
        (d / "settings.local.json").symlink_to(target)
        r = self.cli("sync", "a")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is a symlink", r.stderr)
        self.assertTrue((d / "settings.local.json").is_symlink())
        self.assertEqual(json.loads(target.read_text()), {"claudeAiOauth": {"accessToken": "OAUTH_SECRET"}})
        (d / "settings.local.json").unlink()
        d.rmdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        d.symlink_to(elsewhere)                                       # .claude itself a link
        r = self.cli("sync", "a")
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

    # F1/D-E: sync never suggests --clean for a nested leftover, and --clean
    # keeps a stale file that hides a global apiKeyHelper unless --force.
    def test_f1_clean_keeps_files_that_hide_global_routing(self):
        self.assertEqual(self.cli("sync", "a").returncode, 0)
        f = self.tmp / "code/a/.claude/settings.local.json"
        g = self.home / ".claude" / "settings.json"
        g.parent.mkdir()
        g.write_text(json.dumps({"apiKeyHelper": "corp-key", "env": {"ANTHROPIC_BASE_URL": "https://corp"}}))
        m = dict(self.manifest)
        m["profiles"] = {k: v for k, v in m["profiles"].items() if k != "a"}
        self.write_manifest(m)
        r = self.cli("sync", "--clean")
        self.assertIn(f"keep {f}", r.stderr)
        self.assertIn("apiKeyHelper, env.ANTHROPIC_BASE_URL", r.stderr)
        self.assertTrue(f.exists())
        r = self.cli("sync", "--clean", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(f.exists())
        self.assertEqual(self.cli("sync").returncode, 0)            # the documented rollback:
        self.write_manifest('{"version": 2, "profiles": {}}\n')    # an empty manifest + --clean --force
        r = self.cli("sync", "--clean", "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.tmp / "code/p/.claude/settings.local.json").exists())

    # F6: a pasted secret is never echoed nor turned into a key name.
    def test_f6_ref_errors_never_repeat_the_value(self):
        m = {"version": 2, "profiles": {"x": {"claude": {"provider": "gateway", "base_url": "https://g",
                                                         "token": "sk-ant-api03-REALSECRET_abc"},
                                              "secret_env": {"T": "sops:ghp_NOSLASH_SECRET"}}}}
        self.write_manifest(m)
        for args in (("status", "x", "--json"), ("doctor", "--json"), ("list",)):
            r = self.cli(*args)
            self.assertNotIn("REALSECRET", r.stdout + r.stderr, args)
            self.assertNotIn("NOSLASH", r.stdout + r.stderr, args)
        r = self.cli("status", "x", "--json")
        self.assertIn("sops:x/token", r.stdout + r.stderr)
        self.assertIn("actas secret set", r.stdout + r.stderr)
        self.write_manifest({"environments": {"v": {"secret_env": {"T": "ghp bad SECRETVALUE"}}}})
        r = self.cli("list")
        self.assertNotIn("SECRETVALUE", r.stdout + r.stderr)

    # F7: login: api without codex.home would overwrite the global ~/.codex.
    def test_f7_codex_api_login_needs_a_home(self):
        r = self.cli("login", "w", "--module", "codex")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs codex.home", r.stderr)
        self.assertEqual([c for c in self.calls("codex")], [])
        self.assertEqual(self.calls("sops"), [])                      # the key is never read
        r = self.cli("login", "wh", "--module", "codex")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("codex")[0]["stdin"], "sk-W_SECRET\n")
        d = json.loads(self.cli("doctor", "--json").stdout)
        msgs = [x["message"] for c in d["checks"] if c["name"] == "manifest" and isinstance(c["detail"], list)
                for x in c["detail"]]
        self.assertTrue(any("profiles.w.codex" in x and "login: api without home" in x for x in msgs), msgs)

    # F17: batch reads strip one trailing newline, like one-off reads.
    def test_f17_batch_reads_strip_the_newline(self):
        r = self.cli("exec", "a", "--", sys.executable, "-c", "import os; print(repr(os.environ['NL_TOKEN']))")
        self.assertEqual(r.stdout.strip(), "'tok-NL'", r.stderr)
        r = self.cli("secret", "--ref", "sops:a/nl")
        self.assertEqual(r.stdout, "tok-NL")


class G2Guardrails(Base):
    """In-process gate tests (test_m4.Base: injected process table, captured exec)."""

    def setUp(self):
        super().setUp()
        m = json.loads(json.dumps(MANIFEST))
        m["profiles"]["std2"]["agents_may_use"] = ["ro"]          # std -> std2 -> ro (ro not in std's list)
        self.write_manifest(m)
        self.ro, self.std = self.tmp / "code" / "ro", self.tmp / "code" / "std"

    def stop_reason(self, err):
        first = err.splitlines()[0] if err else ""
        return first.split("reason=")[1].split()[0] if first.startswith("actas-stop:") else None

    # F8: a run session's record cannot be dropped by moving XDG_STATE_HOME or
    # HOME (ACTAS_SESSION says one should exist), nor by hiding `ps`.
    def test_f8_hidden_record_is_an_override(self):
        code, _, err = self.call("exec", "std", "--", "true", env=dict(AGENT, ACTAS="std",
                                                                       ACTAS_SESSION=str(PP)))
        self.assertEqual(self.stop_reason(err), "override", err)
        code, _, err = self.call("exec", "std", "--", "true", env=dict(AGENT, ACTAS="std"), ps="")
        self.assertEqual(self.stop_reason(err), "no_session", err)   # no process table: $ACTAS not trusted
        os.environ["PATH"] = str(self.tmp)                            # no ps on PATH, no fallback dirs
        self.assertTrue(self.m.read_ps())
        # run sets ACTAS_SESSION only when it wrote a record
        code, _, err = self.call("run", "std", "--no-check", ps="", overrides=True)
        self.assertEqual(code, 0, err)
        self.assertNotIn("ACTAS_SESSION", self.execs[-1][1])

    # F9: a nested `actas run` keeps the outer session's reach.
    def test_f9_nested_run_is_not_transitive(self):
        self.record("std")
        code, _, err = self.call("run", "std2", "-t", "codex", "--no-check", env=AGENT)
        self.assertEqual(code, 0, err)
        rec = json.loads((self.m.SESSIONS / f"{ME}.json").read_text())
        self.assertEqual(rec["allowed"], sorted(["std", "ro2", "std2", "pwf", "conf"]))
        rec.update(pid=PP, start=T0)                                   # make it the nearest ancestor record
        (self.m.SESSIONS / f"{PP}.json").write_text(json.dumps(rec))
        code, _, err = self.call("exec", "ro", "--", "true", env=AGENT)
        self.assertEqual(self.stop_reason(err), "not_allowed", err)    # std2 lists ro, std does not
        code, _, err = self.call("exec", "ro2", "--", "true", env=AGENT)
        self.assertEqual(code, 0, err)
        code, _, err = self.call("run", "std2", "--no-check", overrides=True)   # a human's run: no field
        self.assertNotIn("allowed", json.loads((self.m.SESSIONS / f"{ME}.json").read_text()))

    # F26 / D-C: without a session only the readonly profile of the real cwd.
    def test_f26_sessionless_agent_gets_only_the_cwd_readonly_profile(self):
        cases = [(self.ro, "ro", None), (self.ro, "ro2", "no_session"), (self.std, "ro", "no_session"),
                 (self.std, "std", "no_session")]
        for cwd, target, reason in cases:
            with self.subTest(cwd=cwd.name, target=target):
                code, _, err = self.call("exec", target, "--", "true", env=AGENT, cwd=cwd)
                self.assertEqual(self.stop_reason(err), reason, err)
        code, _, err = self.call("--cwd", str(self.ro), "exec", "ro", "--", "true", env=AGENT, cwd=self.std)
        self.assertEqual(self.stop_reason(err), "no_session", err)    # --cwd does not count
        code, _, err = self.call("run", "ro", "-t", "codex", "--no-check", env=AGENT, cwd=self.ro)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads((self.m.SESSIONS / f"{ME}.json").read_text())["allowed"], ["ro"])

    # F25 / D-B: an explicit --cwd chooses the profile, in sessions too.
    def test_f25_explicit_cwd_chooses_the_target(self):
        self.record("std")
        code, out, err = self.call("--cwd", str(self.ro), "status", "--json", env=AGENT)
        d = json.loads(out)
        self.assertEqual((d["profile"], d["matched_by"], d["session"]["profile"]), ("ro", "pattern", "std"))
        code, out, _ = self.call("--cwd", str(self.ro), "status", env=AGENT)
        self.assertIn("the report above is for ro, not the session", out)
        code, _, err = self.call("exec", "--cwd", str(self.ro), "--", "true", env=AGENT)
        self.assertEqual(self.stop_reason(err), "not_allowed", err)    # gated as ro, never run as std
        self.assertEqual(self.execs, [])
        code, _, err = self.call("exec", "--", "true", env=AGENT, cwd=self.ro)
        self.assertEqual((code, self.execs[-1][1]["ACTAS"]), (0, "std"), err)   # no --cwd: the session
        code, out, _ = self.call("--cwd", str(self.ro), "status", "--json", env={"ACTAS": "std"},
                                 overrides=True)
        self.assertEqual(json.loads(out)["profile"], "ro")              # direnv-style $ACTAS too

    # F12 / F32 / D-F: a fallback-only (or unmatched) directory is no mismatch.
    def test_f12_fallback_is_not_a_mismatch(self):
        m = json.loads((self.root / "environments.yaml").read_text())
        m["fallback"] = "ro2"
        self.write_manifest(m)
        code, out, _ = self.call("--cwd", str(self.tmp), "status", "std", "--json", overrides=True)
        d = json.loads(out)
        self.assertEqual((d["cwd"]["matched_by"], d["mismatch"]), ("fallback", False))
        code, out, _ = self.call("--cwd", str(self.tmp), "status", "std", overrides=True)
        self.assertIn("matches no profile (fallback: ro2)", out)
        code, out, _ = self.call("--cwd", str(self.ro), "status", "std", "--json", overrides=True)
        self.assertTrue(json.loads(out)["mismatch"])
        self.assertIn("this directory is matched to another profile", self.m.AGENT_RULES)

    # F11: `actas exec <p> -- aider` marks the agent it starts.
    def test_f11_exec_marks_known_agent_clis(self):
        self.call("exec", "ro", "--", "/usr/local/bin/aider", "--yes", overrides=True)
        self.assertEqual(self.execs[-1][1]["ACTAS_AGENT"], "aider")
        self.call("exec", "ro", "--", "ls", overrides=True)
        self.assertNotIn("ACTAS_AGENT", self.execs[-1][1])

    # F27: ACTAS_ROOT/ACTAS_STATE that name the default places are no override.
    def test_f27_default_paths_are_not_an_override(self):
        default = {"ACTAS_ROOT": str(self.home / ".config" / "actas"),
                   "ACTAS_STATE": str(self.tmp / "xstate" / "actas")}
        code, _, err = self.call("exec", "ro", "--", "true", env=dict(AGENT, **default), cwd=self.ro)
        self.assertEqual(code, 0, err)
        code, _, err = self.call("exec", "ro", "--", "true", env=dict(AGENT, ACTAS_ROOT=str(self.root)),
                                 cwd=self.ro)
        self.assertEqual(self.stop_reason(err), "override", err)


class G2Docs(Sandbox):
    DOCS = ("README.md", "AGENTS.md", "SECURITY.md", "docs/agents.md", "docs/desktop.md", "docs/recipes.md",
            "docs/manifest.md", "skills/actas/SKILL.md")

    def text(self, path):
        return (REPO / path).read_text()

    # F10 / F21 / D-D: never advise a profile-level ACTAS_AGENT.
    def test_f10_no_profile_level_agent_marker(self):
        for path in self.DOCS:
            self.assertNotRegex(self.text(path), r"env:\s*\{\s*ACTAS_AGENT", path)

    # F11: the docs say interpreted agent CLIs are not recognised by name on macOS.
    def test_f11_docs_admit_the_interpreter_limit(self):
        for path in ("docs/agents.md", "docs/recipes.md"):
            self.assertIn("python", self.text(path), path)
            self.assertIn("ACTAS_AGENT=", self.text(path), path)

    # F13: the threat model does not claim secret reads are stopped.
    def test_f13_threat_model_is_honest_about_helpers(self):
        text = self.text("docs/agents.md")
        self.assertNotIn("read secrets through actas", text)
        self.assertIn("audit-logged", text)

    # F8 / F27: the anchor docs name XDG_STATE_HOME and the exported-ACTAS_ROOT caveat.
    def test_f8_f27_docs(self):
        self.assertIn("XDG_STATE_HOME", self.text("AGENTS.md"))
        self.assertIn("reason=override", self.text("README.md"))



def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


class G3Compat(Sandbox):
    """G3: v1 compatibility and sync/output correctness (CLI, sandboxed)."""
    secrets = {"a": {"gh": "ghp_A_SECRET", "gw": "gw-A_SECRET"}}
    manifest = {
        "version": 2,
        "profiles": {
            "a": {"match": ["%(tmp)s/code/a", "%(tmp)s/code/a-wt", "%(tmp)s/code/folder"],
                  "github": {"auth": "sops:a/gh"}, "aws": {"profile": "a-aws"}},
            "a-sub": {"match": "%(tmp)s/code/a/sub", "aws": {"profile": "a-sub-aws"}},
            "other": {"match": "%(tmp)s/code/folder/theirs"},
            "ghe": {"github": {"auth": "gh:ada@acme.ghe.com"}},
            "gw": {"claude": {"provider": "gateway", "base_url": "https://llm.gw.example",
                              "token": "sops:a/gw"}},
            "foundry": {"env": {"CLAUDE_CODE_USE_FOUNDRY": "1"}},
            "m": {"match": "%(tmp)s/code/m", "claude": {"settings": {"model": "opus"}},
                  "env": {"FOO": "actas"}},
        },
    }

    def setUp(self):
        super().setUp()
        self.code = self.tmp / "code"
        for d in ("a/sub", "folder/api", "folder/group/web", "folder/theirs", "folder/plain", "m"):
            (self.code / d).mkdir(parents=True)
        for d in ("a", "folder/api", "folder/group/web", "folder/theirs"):
            git("init", "-q", str(self.code / d))

    def sf(self, d):
        return self.code / d / ".claude" / "settings.local.json"

    def ok(self, *args, **kw):
        r = self.cli(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def doctor(self, name, **kw):
        d = json.loads(self.cli("doctor", "--json", **kw).stdout)
        return [c["detail"] for c in d["checks"] if c["name"] == name]

    # F14: doctor and status --why do not call the profile's own secrets "cleared".
    def test_f14_secret_names_are_not_cleared(self):
        here = {"cwd": str(self.code / "a"), "env": {"GH_TOKEN": "other", "AWS_ACCESS_KEY_ID": "AKIA-PARENT",
                                                     "PWD": str(self.code / "a")}}
        (shell,) = self.doctor("shell", **here)
        self.assertNotIn("GH_TOKEN", shell)
        self.assertIn("AWS_ACCESS_KEY_ID", shell)
        self.assertIn("aws: inherit", shell)
        d = json.loads(self.ok("status", "--why", "--json", **here).stdout)
        self.assertNotIn("GH_TOKEN", d["env"]["clears"])
        self.assertIn("GH_TOKEN", d["env"]["secret"])
        self.write_manifest({"environments": {"v": {"match": "%(tmp)s/code/a", "aws": {"profile": "p"}}}})
        (shell,) = self.doctor("shell", **here)
        self.assertIn("actas migrate", shell)

    # F28 / D-A: a v1 environment without an aws (github) block keeps the shell's values.
    def test_f28_v1_keeps_unconfigured_module_vars(self):
        self.write_manifest({"environments": {
            "bare": {"match": "%(tmp)s/code/a"},
            "withaws": {"match": "%(tmp)s/code/m", "aws": {"profile": "p"}}}})
        parent = {"AWS_PROFILE": "shell-profile", "AWS_ACCESS_KEY_ID": "AKIA-PARENT", "GH_TOKEN": "gho_shell",
                  "ANTHROPIC_BASE_URL": "https://leak"}
        env = self.exec_env("bare", parent)
        self.assertEqual((env["AWS_PROFILE"], env["AWS_ACCESS_KEY_ID"], env["GH_TOKEN"]),
                         ("shell-profile", "AKIA-PARENT", "gho_shell"))
        self.assertNotIn("ANTHROPIC_BASE_URL", env)                    # v1 blanked it too
        env = self.exec_env("withaws", parent)
        self.assertEqual(env["AWS_PROFILE"], "p")
        self.assertNotIn("AWS_ACCESS_KEY_ID", env)
        self.assertEqual(env["GH_TOKEN"], "gho_shell")
        (self.home / ".aws").mkdir()
        (self.home / ".aws" / "credentials").write_text("[default]\naws_access_key_id = AKIA-X\n")
        (detail,) = self.doctor("aws default")                           # static keys count too
        self.assertIn("~/.aws/credentials has a [default] profile, and bare set no aws", detail)
        self.assertIn("unless your shell exports AWS_PROFILE", detail)
        self.assertIn("after actas migrate", detail)

    def exec_env(self, name, parent):
        r = self.ok("exec", name, "--", "env", env=parent)
        return dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)

    # F15 / D-E: a v1 file in a worktree is reported and kept current.
    def test_f15_worktree_leftover_is_rewritten(self):
        repo = self.code / "a"
        git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "--allow-empty", "-m", "init")
        git("-C", str(repo), "worktree", "add", "-q", str(self.code / "a-wt"))
        f = self.sf("a-wt")
        f.parent.mkdir()
        f.write_text(json.dumps({"apiKeyHelper": "ccenv secret a token", "awsAuthRefresh": "aws sso login",
                                 "env": {"AWS_PROFILE": "old", "MINE": "1"}}))
        r = self.ok("sync", "a")
        self.assertIn(f"updating {f}", r.stderr)
        data = json.loads(f.read_text())
        self.assertEqual((data["env"]["AWS_PROFILE"], data["env"]["ACTAS"], data["env"]["MINE"]),
                         ("a-aws", "a", "1"))
        self.assertNotIn("apiKeyHelper", json.dumps({k: v for k, v in data.items() if v}))
        self.assertEqual(json.loads(self.sf("a").read_text())["env"]["ACTAS"], "a")
        self.assertFalse(self.sf("a/sub").exists())

    # F18 / F20: a matched folder that is not a repo: its child repos get the file.
    def test_f18_child_repos_of_a_plain_folder(self):
        r = self.ok("sync", "a")
        for d in ("folder", "folder/api", "folder/group/web"):
            self.assertEqual(json.loads(self.sf(d).read_text())["env"]["ACTAS"], "a", d)
            self.assertIn(f"wrote {self.sf(d)}", r.stdout)
        self.assertFalse(self.sf("folder/theirs").exists())            # resolves to `other`
        self.assertFalse(self.sf("folder/plain").exists())             # not a repo

    # F16: the human's direnv/shell output leaves gh usable (no _off GH_CONFIG_DIR).
    def test_f16_no_secrets_shell_does_not_pin_gh_off(self):
        out = self.ok("shell", "a", "--no-secrets", "--format", "direnv", env={"GH_TOKEN": "x"}).stdout
        self.assertNotIn("GH_CONFIG_DIR", out)
        self.assertIn("unset GH_TOKEN", out)                             # another identity's token goes
        out = self.ok("shell", "a", "--no-secrets", "--format", "direnv", env={"ACTAS_AGENT": "aider"}).stdout
        self.assertIn("GH_CONFIG_DIR", out)                              # agent sessions stay fail-closed
        r = self.ok("exec", "a", "--", "sh", "-c", 'echo "$GH_CONFIG_DIR"')
        self.assertTrue(r.stdout.strip().endswith("/_off"))

    # F29: in a nested profile's dir the repo-root file is information, not "use profile X".
    def test_f29_nested_run_does_not_suggest_the_other_profile(self):
        self.ok("sync", "a")
        r = self.ok("--cwd", str(self.code / "a/sub"), "run", "--no-check")
        self.assertNotIn("use profile", r.stderr)
        self.assertNotIn("run actas sync", r.stderr)
        self.assertIn("synced to a", r.stderr)
        m = json.loads((self.root / "environments.yaml").read_text())
        m["profiles"]["a"]["match"] = ["%(tmp)s/code/a-wt"]
        self.write_manifest(m)
        r = self.ok("--cwd", str(self.code / "a/sub"), "run", "--no-check")
        self.assertIn("run actas sync", r.stderr)                         # stale entry
        self.assertNotIn("use profile", r.stderr)

    # F33: a *.ghe.com token sets GH_HOST, so gh outside a repo targets that host.
    def test_f33_ghe_com_sets_gh_host(self):
        out = self.ok("shell", "ghe", "--no-secrets").stdout
        self.assertIn("export GH_HOST=acme.ghe.com", out)

    # F34: a user's own key that claude.settings takes over comes back on drop.
    def test_f34_taken_over_key_is_restored(self):
        f = self.sf("m")
        f.parent.mkdir()
        f.write_text(json.dumps({"model": "sonnet", "env": {"FOO": "mine"}}))
        self.ok("sync", "m")
        self.ok("sync", "m")                                             # idempotent: prev survives a re-sync
        data = json.loads(f.read_text())
        self.assertEqual((data["model"], data["env"]["FOO"]), ("opus", "actas"))
        m = json.loads((self.root / "environments.yaml").read_text())
        del m["profiles"]["m"]["claude"]
        self.write_manifest(m)
        self.ok("sync", "m")
        self.assertEqual(json.loads(f.read_text())["model"], "sonnet")
        del m["profiles"]["m"]
        self.write_manifest(m)
        self.ok("sync", "--clean")
        data = json.loads(f.read_text())
        self.assertEqual((data["model"], data["env"]), ("sonnet", {"FOO": "mine"}))

    # F35: --format env for a gateway profile says the URL comes without a credential.
    def test_f35_gateway_env_output_warns(self):
        r = self.ok("shell", "gw", "--format", "env")
        self.assertIn("ANTHROPIC_BASE_URL=https://llm.gw.example", r.stdout)
        self.assertIn("apiKeyHelper", r.stderr)
        self.assertIn("--print-3p", r.stderr)
        self.assertNotIn("gw-A_SECRET", r.stdout + r.stderr)
        self.assertNotIn("apiKeyHelper", self.ok("shell", "a", "--format", "env").stderr)

    # F38: a Foundry profile built from the recipe is not "subscription, remote=yes".
    def test_f38_foundry_env_is_the_provider(self):
        d = json.loads(self.ok("list", "--json").stdout)
        row = [p for p in d["profiles"] if p["name"] == "foundry"][0]
        self.assertEqual((row["provider"], row["remote_control"]), ("foundry", False))
        self.assertIn("foundry · standard · foundry", self.ok("run", "foundry", "--no-check").stderr)


class G3Helpers(Sandbox):
    # F31: a conda env is project-local like a venv: helpers use another python3.
    def test_f31_conda_env_python_is_not_pinned(self):
        env_py = self.tmp / "conda" / "envs" / "foo" / "bin" / "python3"
        base = self.tmp / "base" / "bin" / "python3"
        for p in (env_py, base):
            p.parent.mkdir(parents=True)
            p.write_text("#!/bin/sh\n")
            p.chmod(0o755)
        prefix = str(self.tmp / "conda" / "envs" / "foo")
        os.environ.update(CONDA_PREFIX=prefix, PATH=f"{env_py.parent}:{base.parent}")
        with mock.patch.multiple(sys, prefix=prefix, base_prefix=prefix, executable=str(env_py)), \
                mock.patch.object(sys, "_base_executable", str(env_py), create=True):
            self.assertEqual(self.m.helper_python(), str(base))


class G4Helpers(Sandbox):
    # F44: the Xcode / Command Line Tools python behind macOS's /usr/bin/python3
    # is not written into helpers; the stable stub is.
    def test_f44_xcode_python_maps_to_the_stub(self):
        stub = self.tmp / "usr-bin-python3"
        stub.write_text("#!/bin/sh\n")
        for exe in ("/Applications/Xcode.app/Contents/Developer/usr/bin/python3",
                    "/Applications/Xcode-beta.app/Contents/Developer/usr/bin/python3",
                    "/Library/Developer/CommandLineTools/usr/bin/python3"):
            with mock.patch.multiple(sys, prefix="/p", base_prefix="/p", executable=exe), \
                    mock.patch.object(self.m, "SYSTEM_PY", str(stub)):
                self.assertEqual(self.m.helper_python(), str(stub), exe)
        with mock.patch.multiple(sys, prefix="/p", base_prefix="/p", executable="/opt/homebrew/bin/python3"):
            self.assertEqual(self.m.helper_python(), "/opt/homebrew/bin/python3")


class G3Docs(Sandbox):
    def text(self, path):
        return (REPO / path).read_text()

    # F30 / D-A: the upgrade notes name what synced files now carry, and v1 AWS behaviour.
    def test_f30_f28_upgrade_notes(self):
        for path in ("docs/migration.md", "CHANGELOG.md"):
            t = self.text(path)
            self.assertIn("credential helper", t, path)
            self.assertIn("AWS_PROFILE", t, path)
        self.assertNotIn("does not fail closed", self.text("docs/migration.md").replace("`gh` does not fail closed", ""))

    # F35: the saved-environment example is not a gateway profile.
    def test_f35_desktop_example(self):
        self.assertNotIn("actas shell client-a --format env", self.text("docs/desktop.md"))


# A `yq` like Debian/Ubuntu's kislyuk/yq: rejects `-o`, prints JSON for `yq .`.
# It "parses" only flow-style YAML (JSON once the keys are quoted), enough here.
KISLYUK_YQ = r"""#!%s
import json, re, sys
if "-o" in sys.argv:
    sys.stderr.write("yq: error: argument files: can't open '.'\n")
    sys.exit(2)
text = sys.stdin.read().strip()
text = text if text.startswith("{") else "{" + text + "}"
print(json.dumps(json.loads(re.sub(r"([A-Za-z_]+):", r'"\1":', text))))
""" % sys.executable


class G4Parser(Sandbox):
    """No PyYAML (a yaml.py that raises ImportError) and no yq: a bare
    macOS /usr/bin/python3."""

    def setUp(self):
        super().setUp()
        noyaml = self.tmp / "noyaml"
        noyaml.mkdir()
        (noyaml / "yaml.py").write_text("raise ImportError('no PyYAML here')\n")
        os.environ["PYTHONPATH"] = str(noyaml)
        # Shadow any system yq (GitHub's Ubuntu runners ship mikefarah/yq).
        yq = self.fakebin / "yq"
        yq.write_text("#!/bin/sh\nexit 1\n")
        yq.chmod(0o755)

    # F19/F41: init writes a starter this machine can read; doctor reports instead of dying.
    def test_f19_f41_bare_python_init_and_doctor(self):
        r = self.cli("init", env={"SOPS_AGE_KEY_FILE": str(self.tmp / "none")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("starter is JSON", r.stderr)
        self.assertEqual(json.loads((self.root / "environments.yaml").read_text())["version"], 2)
        r = self.cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("personal", r.stdout)
        (self.root / "environments.yaml").write_text("version: 2\nprofiles: {a: {}}\n")
        r = self.cli("doctor", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        checks = {c["name"]: c for c in json.loads(r.stdout)["checks"]}
        self.assertEqual(checks["manifest"]["status"], "error")
        self.assertIn("pip install --user pyyaml", json.dumps(checks["manifest"]))
        self.assertIn("gh", checks)                                   # the tool checks still ran
        r = self.cli("list")
        self.assertIn("no YAML parser here", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    # F42: kislyuk/yq (apt's `yq`) parses the manifest; migrate's yaml_available agrees.
    def test_f42_kislyuk_yq_parses(self):
        yq = self.fakebin / "yq"
        yq.write_text(KISLYUK_YQ)
        yq.chmod(0o755)
        (self.root / "environments.yaml").write_text('{version: 2, profiles: {a: {env: {X: "y"}}}}\n')
        r = self.cli("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([p["name"] for p in json.loads(r.stdout)["profiles"]], ["a"])
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.assertTrue(self.m.yaml_available())
            self.m._CACHE.clear()
            yq.write_text("#!/bin/sh\nexit 2\n")                    # a yq that parses nothing
            self.assertFalse(self.m.yaml_available())


class G4Handoff(Base):
    # F24: on X11 with wl-clipboard installed, xclip is used, and its
    # background server (which keeps stdout open) does not hang actas.
    def test_f24_xclip_on_x11_does_not_hang(self):
        clip = make_fakebin(self.tmp / "clip", only=("aws", "ps", "wl-copy"))
        (clip / "xclip").write_text(f"#!/bin/sh\n/bin/cat > {self.tmp}/clipboard\n(/bin/sleep 15) &\nexit 0\n")
        (clip / "xclip").chmod(0o755)
        t = time.monotonic()
        r = self.cli("aws-handoff", "std", env={"PATH": str(clip), "DISPLAY": ":0"})
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("copied to clipboard (xclip)", r.stderr)
        self.assertIn("ASIAFAKE", (self.tmp / "clipboard").read_text())
        self.assertEqual(self.calls("wl-copy"), [])


class G4Aws(Sandbox):
    """F40: one AWS probe (probe_aws) and one SSO login argv everywhere."""
    manifest = {"version": 2, "profiles": {
        "a": {"aws": {"profile": "dev", "config_file": "%(tmp)s/aws-a"}},
        "b": {"aws": {"profile": "dev"}},
        "s": {"aws": {"profile": "dev", "sso_session": "corp"}},
        "t": {"aws": {"profile": "typo"}}}}

    def setUp(self):
        super().setUp()
        (self.fakebin / "aws").write_text(f"#!{sys.executable}\n" + FAKE_COMMON + r"""
args = sys.argv[1:]
log()
if args[:2] == ["sts", "get-caller-identity"]:
    if args[args.index("--profile") + 1] == "typo":
        sys.stderr.write("The config profile (typo) could not be found\n")
        sys.exit(255)
    if not os.environ.get("AWS_CONFIG_FILE", "").endswith("aws-a"):
        sys.stderr.write("Error when retrieving token from sso: Token has expired and refresh failed\n")
        sys.exit(255)
    print(json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:x"}))
""")

    def test_f40_one_probe_and_one_login(self):
        r = self.cli("list", "--check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {p["name"]: p["check"]["aws"] for p in json.loads(r.stdout)["profiles"]}
        self.assertEqual(rows, {"a": "ok", "b": "expired", "s": "expired", "t": "missing"})
        sts = [c for c in self.calls("aws") if c["argv"][:2] == ["sts", "get-caller-identity"]]
        self.assertEqual(len(sts), 3)                 # (dev, aws-a), (dev, default), (typo, default)
        self.assertNotIn("have expired", self.cli("run", "t").stderr)   # a typo is not "expired"
        self.assertIn("have expired", self.cli("run", "b").stderr)
        self.cli("run", "s", "--login")
        self.assertIn(["sso", "login", "--sso-session", "corp"], [c["argv"] for c in self.calls("aws")])


class G4Login(Sandbox):
    manifest = {"version": 2, "profiles": {"c": {"github": {"auth": "gh:ada-client"}}}}

    # F39: `actas login --module github` puts gh's previous active account back.
    def test_f39_gh_login_switches_back(self):
        r = self.cli("login", "c", "--module", "github", env={"FAKE_GH_USERS": "ada-personal,ada-client"})
        self.assertEqual(r.returncode, 0, r.stderr)
        argvs = [c["argv"] for c in self.calls("gh")]
        self.assertEqual(argvs[-2][:2], ["auth", "login"])
        self.assertEqual(argvs[-1], ["auth", "switch", "--hostname", "github.com", "--user", "ada-personal"])
        self.assertIn("back to ada-personal", r.stderr)
        r = self.cli("login", "c", "--module", "github", env={"FAKE_GH_USERS": "ada-client"})
        self.assertNotIn(["auth", "switch"], [c["argv"][:2] for c in self.calls("gh")][-1:])
        self.assertNotIn("active account", r.stderr)


class G4Status(Sandbox):
    manifest = {"version": 2, "profiles": {"h": {"match": "~/code/h"}}}

    # Human status abbreviates the matching pattern with ~, like the cwd.
    def test_status_pattern_uses_tilde(self):
        (self.home / "code" / "h").mkdir(parents=True)
        r = self.cli("status", "--cwd", str(self.home / "code" / "h"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("~/code/h -> h  (pattern ~/code/h", r.stdout)
        self.assertNotIn(str(self.home), r.stdout)

    # F36: help shows how to call exec and secret; no-match says what to add.
    def test_f36_usage_lines(self):
        r = self.cli("help", "exec")
        self.assertIn("usage: actas exec [profile] [--reason TEXT] [--json] -- command [args...]", r.stdout)
        self.assertIn("actas secret set REF", self.cli("help", "secret").stdout)
        r = self.cli("help", "exce")
        self.assertEqual(r.returncode, 2)
        self.assertIn("did you mean: exec?", r.stderr)
        self.assertNotIn("commands:", r.stderr)                    # no full help dump
        r = self.cli("exec", "h", "aws", "s3", "ls")
        self.assertIn("actas exec <profile> -- aws s3 ls", r.stderr)
        r = self.cli("exec", "--cwd", str(self.tmp), "--", "true")
        self.assertIn(f'add `match: ["{self.tmp}"]` to a profile (it covers subdirectories too), '
                      "or set `fallback: <profile>`", r.stderr)


class G4Docs(unittest.TestCase):
    def text(self, path):
        return (REPO / path).read_text()

    # F23: setup-uv has no floating major tag after v7; pin an exact commit.
    def test_f23_setup_uv_is_pinned(self):
        import re
        refs = re.findall(r"uses: astral-sh/setup-uv@(\S+)", self.text(".github/workflows/test.yml"))
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[0-9a-f]{40}$")

    # F22 / F37 / F39: the README states the limits.
    def test_f22_f37_f39_readme_limits(self):
        t = self.text("README.md")
        self.assertIn("HTTPS `git push`", t)
        self.assertIn("ssh_key", t[t.index("**A client with its own gateway"):])
        sub = t[t.index("**Two claude.ai subscriptions.**"):t.index("**A client with its own gateway")]
        self.assertIn("one claude.ai account per app", sub)
        self.assertIn("Nothing global changes when you run a profile", t)
        self.assertNotIn("only through `actas run`", self.text("docs/desktop.md"))


class Simplify(Sandbox):
    """The behaviour-preserving simplification of bin/actas: test-only APIs
    are gone, and the merged code paths behave as the separate ones did."""
    manifest = {"version": 2, "profiles": {"good": {"match": "%(tmp)s/g"},
                                           "bad": {"use": ["no-such-role"]}}}
    secrets = {"p": {"k": "v\n"}}

    def test_test_only_apis_are_gone(self):
        for name in ("manifest", "build_env", "secret_get", "sops_bin", "stable_self", "helper_self",
                     "v1_merge", "prov_rows", "yaml_scalar", "agent_signals", "hook_command",
                     "check_claude_settings", "sops_supports_value_stdin", "check_name"):
            self.assertFalse(hasattr(self.m, name), name)
        self.write_manifest({"version": 1, "environments": {"e": {}}})
        cfg = self.reload().load(quiet=True)
        for attr in ("source", "compat", "path"):
            self.assertFalse(hasattr(cfg, attr), attr)
        self.assertTrue(self.m.resolve("e")["_v1"])

    def test_all_profiles_reports_errors_to_list_and_doctor(self):
        profs, errors = self.m.all_profiles(self.m.load(quiet=True))
        self.assertEqual((list(profs), list(errors)), (["good"], ["bad"]))
        r = self.cli("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("profile bad: unknown role or profile 'no-such-role'", r.stderr)
        (self.home / ".aws").mkdir()
        (self.home / ".aws" / "config").write_text("[default]\nregion = eu-west-1\n")
        r = self.cli("doctor", "--json")
        checks = {c["name"]: c for c in json.loads(r.stdout)["checks"]}
        self.assertIn("profiles.bad", json.dumps(checks["manifest"]))
        self.assertIn("good set no aws", checks["aws default"]["detail"])     # merged once, reused
        self.assertNotIn("bad", checks["aws default"]["detail"])

    def test_empty_secret_refused_in_every_set_form(self):
        for args, stdin in ((("secret", "p", "x", "--set"), "\n"), (("secret", "set", "sops:p/x"), "")):
            r = self.cli(*args, stdin=stdin)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn("refusing to store an empty secret", r.stderr)
        # an empty positional value (argparse before 3.12 rejects "" on the command line)
        self.assertIn("refusing to store an empty secret", self.dies(self.m.secret_set, "p", "x", ""))
        self.assertEqual([c for c in self.calls("sops") if c["argv"][:1] == ["set"]], [])

    def test_secret_reads(self):
        self.assertEqual(self.cli("secret", "p", "k").stdout, "v")
        self.assertEqual(self.cli("secret", "--ref", "sops:p/k").stdout, "v")
        r = self.cli("secret", "p", "nope")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("could not read secret p.nope", r.stderr)
        self.assertIn("(run: actas secret p nope --set)", r.stderr)   # same message, plus the fix-it hint
        r = self.cli("secret", "bad name", "k")
        self.assertIn("invalid environment name 'bad name'", r.stderr)
        r = self.cli("secret", "--ref", "sops:p/k", "p")
        self.assertEqual(r.returncode, 2)
        self.assertIn("use either", r.stderr)

    def test_v1_claude_args_forms(self):
        def args(v):
            cfg = self.m.build_config({"version": 1, "environments": {"e": {"claude_args": v}}})
            return cfg.profiles.get("e", {}).get("claude", {}).get("args"), cfg.problems.items
        self.assertEqual(args("--resume -c")[0], ["--resume", "-c"])
        self.assertEqual(args("   ")[0], [])
        self.assertEqual(args(["a"]), (["a"], []))
        self.assertIn("expected a list of strings", str(args(5)[1]))
        self.assertIn("cannot split it", str(args('"open')[1]))

    def test_migrate_scalars(self):
        self.assertEqual(self.m.emit_yaml({"a": [True, None, 1.5, "\u00e9"], "b": False, "c": 7}),
                         ['a: [true, null, 1.5, "\u00e9"]', "b: false", "c: 7"])


class Round2(Sandbox):
    """Round 2 of the review fixes (R1-R13), one test per item."""
    secrets = {"a": {"tok": "A_SECRET_TOKEN"}}
    manifest = {"version": 2, "profiles": {
        "a": {"match": "%(tmp)s/code/a", "env": {"SSH_AUTH_SOCK": "/x/agent.sock", "KUBECONFIG": "/k/a"},
              "secret_env": {"A_TOKEN": "sops:a/tok"}, "aws": {"profile": "a-aws"}},
        "nest": {"match": "%(tmp)s/code/a/sub"},
        "b": {"match": "%(tmp)s/code/b", "github": {"auth": "gh:ada@acme.example"}},
        "gw": {"match": "%(tmp)s/code/gw", "claude": {"provider": "gateway", "base_url": "https://llm.gw.example",
                                                      "token": "sops:a/tok"}},
        "k": {"match": "%(tmp)s/code/k", "env": {"DEPLOY_KEY_PATH": "/keys/k.pem", "PLAIN": "actas"}},
    }}

    def setUp(self):
        super().setUp()
        self.code = self.tmp / "code"
        for d in ("a", "b", "gw", "k"):
            (self.code / d).mkdir(parents=True)

    def sf(self, d):
        return self.code / d / ".claude" / "settings.local.json"

    def ok(self, *args, **kw):
        r = self.cli(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def ledger(self):
        return json.loads((self.tmp / "state" / "sync-ledger.json").read_text())

    def drop_profile(self, name):
        m = json.loads((self.root / "environments.yaml").read_text())
        del m["profiles"][name]
        self.write_manifest(m)

    # R1: plain env: names are not scrubbed for other profiles; session basics never are.
    def test_r1_env_names_are_not_owned(self):
        parent = {"SSH_AUTH_SOCK": "/real/agent", "KUBECONFIG": "/k/mine", "A_TOKEN": "a-leak"}
        out = self.ok("shell", "b", "--no-secrets", "--format", "direnv", env=parent).stdout
        self.assertNotIn("SSH_AUTH_SOCK", out)
        self.assertNotIn("KUBECONFIG", out)
        self.assertIn("unset A_TOKEN", out)                              # secret_env names still are
        names = ("SSH_AUTH_SOCK", "DISPLAY", "EDITOR", "LC_TOKEN", "XDG_TOKEN", "TERM_TOKEN", "COLORTERM",
                 "X_TOKEN")
        cfg = self.m.build_config({"version": 2, "profiles": {"x": {"secret_env": {
            n: "sops:x/k" for n in names}, "env": {"KUBECONFIG": "/k"}}}})
        self.assertEqual(set(self.m.owned_scope(cfg)) & set(names + ("KUBECONFIG",)), {"X_TOKEN"})

    # R2: a profile that fails to merge keeps its synced file under --clean.
    def test_r2_clean_keeps_a_broken_profiles_file(self):
        self.ok("sync", "gw")
        m = json.loads((self.root / "environments.yaml").read_text())
        del m["profiles"]["gw"]["claude"]["base_url"]
        self.write_manifest(m)
        for args in (("sync",), ("sync", "--clean")):
            r = self.cli(*args)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("skip gw:", r.stderr)
            self.assertNotIn("no profile syncs it", r.stderr)
            self.assertNotIn("clean ", r.stdout)
        self.assertTrue(self.sf("gw").exists())
        self.assertIn(str(self.sf("gw")), self.ledger())

    # R3a: actas's own output is never recorded as the user's value.
    def test_r3_own_output_is_not_prev(self):
        own = self.m.put_fragment({"env": {"ACTAS": "k", "ACTAS_LEVEL": "standard", "X": "old"}, "model": "a"},
                                  {"env": {"ACTAS": "k", "X": "new"}, "model": "b"})
        self.assertNotIn("prev", own)                                    # an unledgered actas-written file
        own = self.m.put_fragment({"env": {"ACTAS_LEVEL": "readonly", "X": "mine", "Y": "same"}},
                                  {"env": {"ACTAS_LEVEL": "standard", "X": "actas", "Y": "same"}})
        self.assertEqual(own["prev"], {"env.X": "mine", "env.Y": "same"})   # an equal value is still yours
        self.ok("sync", "k")
        (self.tmp / "state" / "sync-ledger.json").unlink()               # the ledger is lost
        self.ok("sync", "k")
        self.assertNotIn("prev", self.ledger()[str(self.sf("k"))])
        self.drop_profile("k")
        self.ok("sync", "--clean")
        left = json.loads(self.sf("k").read_text())                      # (unledgered permission rules stay)
        self.assertEqual((left.get("env"), left.get("apiKeyHelper")), (None, None))   # no env.ACTAS restored

    # R3b: a credential-looking env value actas replaced is never copied into the ledger.
    def test_r3_credential_values_stay_out_of_the_ledger(self):
        f = self.sf("k")
        f.parent.mkdir()
        f.write_text(json.dumps({"env": {"DEPLOY_KEY_PATH": "USER_SECRET_VALUE", "PLAIN": "mine"}}))
        self.ok("sync", "k")
        self.ok("sync", "k")                                             # the note survives a re-sync
        led = (self.tmp / "state" / "sync-ledger.json").read_text()
        self.assertNotIn("USER_SECRET_VALUE", led)
        entry = json.loads(led)[str(f)]
        self.assertEqual((entry["replaced"], entry["prev"]), (["env.DEPLOY_KEY_PATH"], {"env.PLAIN": "mine"}))
        self.drop_profile("k")
        r = self.ok("sync", "--clean")
        self.assertEqual(json.loads(f.read_text()), {"env": {"PLAIN": "mine"}})
        self.assertIn("env.DEPLOY_KEY_PATH was overwritten", r.stderr)
        sec = " ".join((REPO / "SECURITY.md").read_text().split())
        self.assertNotIn("the sync ledger hold names and refs only", sec)
        self.assertIn("never the value", sec)

    # R4: a v1 subscription file (empty apiKeyHelper and base URL) is v1-written.
    def test_r4_v1_subscription_file_is_adopted(self):
        f = self.sf("gw")
        f.parent.mkdir()
        f.write_text(json.dumps({"apiKeyHelper": "", "env": {"ANTHROPIC_BASE_URL": ""}, "model": "mine"}))
        self.ok("sync", "gw")
        self.assertNotIn("prev", self.ledger()[str(f)])
        self.drop_profile("gw")
        self.ok("sync", "--clean")
        self.assertEqual(json.loads(f.read_text()), {"model": "mine"})

    def in_run_a(self):
        """(inrun(cmd), actas): cmd runs as the agent inside `actas run a`,
        with the default config and state places (no ACTAS_ROOT/STATE)."""
        cfg = self.home / ".config" / "actas"
        cfg.mkdir(parents=True)
        for n in ("environments.yaml", "secrets.enc.yaml"):
            (cfg / n).write_text((self.root / n).read_text())
        os.environ.pop("ACTAS_ROOT")
        os.environ.pop("ACTAS_STATE")
        claude = self.fakebin / "claude"
        claude.write_text('#!/bin/sh\nCLAUDECODE=1 sh -c "$VERIFY_CMD" > "$VERIFY_OUT" 2>&1\n'
                          'echo "rc=$?" >> "$VERIFY_OUT"\n')
        out = self.tmp / "verify.out"

        def inrun(cmd):
            r = self.cli("run", "a", "--no-check", env={"VERIFY_CMD": cmd, "VERIFY_OUT": str(out)},
                         cwd=self.code / "a")
            self.assertEqual(r.returncode, 0, r.stderr)
            return out.read_text()
        return inrun, f"{shlex.quote(sys.executable)} {shlex.quote(str(self.bin))}"

    # R5: inside `actas run a`, dropping ACTAS_SESSION and moving XDG_STATE_HOME
    # does not turn the session into a self-asserted one.
    def test_r5_record_is_found_at_the_fixed_place(self):
        inrun, actas = self.in_run_a()
        self.assertIn("reason=not_allowed", inrun(f"{actas} exec b -- true"))
        self.assertTrue(list(self.user_sessions.glob("*.json")))
        text = inrun(f"env -u ACTAS_SESSION XDG_STATE_HOME={self.tmp}/elsewhere ACTAS=b {actas} exec b -- true")
        self.assertIn("reason=override", text)
        self.assertIn("rc=3", text)

    # R6: the system ps wins over a ps first in PATH.
    def test_r6_system_ps_first(self):
        for name, word in (("pathps", "PATH-PS"), ("sysps", "SYSTEM-PS")):
            (self.tmp / name).mkdir()
            (self.tmp / name / "ps").write_text(f"#!/bin/sh\necho {word}\n")
            (self.tmp / name / "ps").chmod(0o755)
        os.environ["PATH"] = f"{self.tmp / 'pathps'}:{os.environ['PATH']}"
        self.m.PS_BINS = (str(self.tmp / "missing"), str(self.tmp / "sysps" / "ps"))
        self.assertIn("SYSTEM-PS", self.m.read_ps())
        self.m.PS_BINS = (str(self.tmp / "missing"),)
        self.assertIn("PATH-PS", self.m.read_ps())                       # only when no system ps exists
        self.assertIn('PS_BINS = ("/bin/ps", "/usr/bin/ps")', (REPO / "bin" / "actas").read_text())

    # R7: sync moves a contaminated _off home aside, status warns, a failed move dies cleanly.
    def test_r7_off_home_in_sync_and_status(self):
        off = self.tmp / "state" / "homes" / "github" / "_off"
        off.mkdir(parents=True)
        (off / "hosts.yml").write_text("github.com: {user: ada}\n")
        r = self.ok("status", "b")
        self.assertIn("must stay empty", r.stderr)
        self.ok("sync", "--dry-run")
        self.assertTrue((off / "hosts.yml").exists())                    # status and dry runs never move it
        r = self.ok("sync")
        self.assertIn("moved it to", r.stderr)
        self.assertFalse((off / "hosts.yml").exists())
        aside = [p.name for p in off.parent.glob("_off.*")]
        self.assertEqual(len(aside), 1)
        self.assertRegex(aside[0], r"^_off\.\d{8}-\d{6}\.\d+$")
        off.mkdir(exist_ok=True)
        (off / "hosts.yml").write_text("x")
        with mock.patch.object(self.m.os, "rename", side_effect=OSError(13, "Permission denied")):
            err = self.dies(self.m.home_dir, "github", "_off", {"materialize": True})
        self.assertIn("moving it aside failed (Permission denied); remove it by hand", err)

    # R8: a gh:user@host account that is already active is not "switched back".
    def test_r8_gh_switch_back_ignores_the_host(self):
        r = self.ok("login", "b", "--module", "github", env={"FAKE_GH_USERS": "ada"})
        self.assertNotIn(["auth", "switch"], [c["argv"][:2] for c in self.calls("gh")])
        self.assertNotIn("active account", r.stderr)

    # R9: a nested directory that actas updates gets no contradicting "skip" line.
    def test_r9_no_skip_line_before_an_update(self):
        sub = self.code / "a" / "sub"
        sub.mkdir()
        git("init", "-q", str(self.code / "a"))
        r = self.ok("sync", "nest")
        self.assertIn("Claude reads settings.local.json at the repo root", r.stderr)
        leftover = sub / ".claude" / "settings.local.json"
        leftover.parent.mkdir()
        leftover.write_text(json.dumps({"awsAuthRefresh": "aws sso login --profile old",
                                        "env": {"AWS_PROFILE": "old"}}))
        r = self.ok("sync", "nest")
        self.assertIn(f"updating {leftover}", r.stderr)
        self.assertNotIn(f"skip {sub}", r.stderr)

    # R10: a token pasted into a v1 key-name field is rejected without echoing it.
    def test_r10_pasted_tokens_in_v1_key_names(self):
        toks = {"ghp_" + "A1b2" * 9: "secret_env", "sk-ant-api03-" + "x" * 20: "gateway",
                "AKIA" + "ABCDEFGHIJKLMNOP": "secret_env", "n" * 65: "secret_env"}
        for tok, where in toks.items():
            body = ({"secret_env": {"T": tok}} if where == "secret_env" else
                    {"provider": "gateway", "gateway": {"base_url": "https://gw.example", "secret": tok}})
            self.write_manifest({"environments": {"client-a": body}})
            for cmd in (["list"], ["migrate"], ["doctor", "--json"]):
                with self.subTest(tok=tok[:6], cmd=cmd[0]):
                    r = self.cli(*cmd)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("looks like a pasted token, not a key name", r.stdout + r.stderr)
                    self.assertNotIn(tok, r.stdout + r.stderr)
        self.write_manifest({"environments": {"client-a": {"secret_env": {"NPM_TOKEN": "npm-token"}}}})
        self.ok("list")

    # R11: migrate writes `aws: inherit` where v1 had no aws block.
    def test_r11_migrate_keeps_v1_aws_behaviour(self):
        self.write_manifest({"environments": {"client-a": {"aws": {"profile": "a-aws"}},
                                              "client-b": {"env": {"X": "1"}}}})
        r = self.ok("migrate")
        self.assertEqual(len(re.findall(r'"?aws"?: "?inherit"?', r.stdout)), 1)
        self.assertIn("client-b had no aws block", r.stderr)
        self.assertIn("`aws: null`", r.stderr)
        (self.root / "environments.yaml").write_text(r.stdout)
        prof = self.reload().resolve("client-b")
        self.assertEqual(self.m.assemble(prof, parent={"AWS_PROFILE": "mine"})["unset"], [])

    # R12: doctor lists each gh account once.
    def test_r12_doctor_dedupes_gh_accounts(self):
        r = self.cli("doctor", "--json", env={"FAKE_GH_USERS": "ada,bob,ada"})
        checks = {c["name"]: c for c in json.loads(r.stdout)["checks"]}
        self.assertEqual(checks["gh accts"]["detail"], "ada, bob")

    # R13: the docs call self-asserted $ACTAS and D-C guardrails, not a boundary.
    def test_r13_docs_do_not_oversell_the_gate(self):
        agents = " ".join((REPO / "docs" / "agents.md").read_text().split())
        five = agents.split("## In five minutes")[1].split("```")[0]
        self.assertIn("guardrails against mistakes, not a boundary", five)
        self.assertIn("guardrails against mistakes, not a boundary", " ".join((REPO / "SECURITY.md").read_text().split()))

    # Round 2 follow-up (R10b, R3c, R5b, docs).

    # R10b: ordinary v1 key names load; only token-shaped values are refused.
    def test_r10b_v1_key_names_with_token_prefixes_load(self):
        for key in ("npm_token", "npm-token", "ghs_deploy", "hf_token", "sk-gateway-token-2",
                    "github_pat_for_client_acme_repo", "npm_token_2024_deploy_build"):
            for body in ({"secret_env": {"NPM_TOKEN": key}},
                         {"provider": "gateway", "gateway": {"base_url": "https://gw.example", "secret": key}}):
                with self.subTest(key=key):
                    self.write_manifest({"environments": {"client-a": body}})
                    self.ok("list")
        long_name = "client-a-" + "x" * 60                              # secret_from defaults to it
        self.write_manifest({"environments": {long_name: {"provider": "gateway",
                                                          "gateway": {"base_url": "https://gw.example"}}}})
        self.ok("list")
        for tok in ("sk-" + "aB3" * 16, "hf_" + "aB3" * 12, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
                    "xoxb-1234567890-1234567890-AbCdEfGhIjKl", "npm_" + "aB3" * 12):
            self.write_manifest({"environments": {"client-a": {"secret_env": {"T": tok}}}})
            for cmd in ("list", "migrate"):
                with self.subTest(tok=tok[:6], cmd=cmd):
                    r = self.cli(cmd)
                    self.assertEqual(r.returncode, 1, r.stderr)
                    self.assertIn("looks like a pasted token", r.stderr)
                    self.assertNotIn(tok, r.stdout + r.stderr)

    # R3c: your own value that equals what the profile writes comes back on --clean.
    def test_r3c_equal_user_value_is_restored(self):
        f = self.sf("k")
        f.parent.mkdir()
        f.write_text(json.dumps({"env": {"PLAIN": "actas"}, "theme": "dark"}))
        self.ok("sync", "k")
        self.assertEqual(self.ledger()[str(f)]["prev"], {"env.PLAIN": "actas"})
        self.drop_profile("k")
        self.ok("sync", "--clean")
        self.assertEqual(json.loads(f.read_text()), {"env": {"PLAIN": "actas"}, "theme": "dark"})

    # R5b: a record forged in a moved ACTAS_STATE does not beat the fixed copy.
    def test_r5b_forged_record_in_a_moved_state_dir(self):
        inrun, actas = self.in_run_a()
        evil = self.tmp / "evil"
        py = ("import json,sys;from pathlib import Path;us,evil=map(Path,sys.argv[1:]);"
              "r=json.loads(next(us.glob('*.json')).read_text());r.update(profile='b',state=str(evil));"
              "(evil/'sessions').mkdir(parents=True);(evil/'sessions'/(str(r['pid'])+'.json')).write_text(json.dumps(r))")
        text = inrun(f"{shlex.quote(sys.executable)} -c {shlex.quote(py)} {shlex.quote(str(self.user_sessions))} "
                     f"{evil} && ACTAS_STATE={evil} {actas} exec b -- true")
        self.assertTrue(list((evil / "sessions").glob("*.json")))      # the forge was written
        self.assertIn("reason=override", text)
        self.assertIn("rc=3", text)

    # Docs: the scrub list names TERM and TERM_* (not TERM*); item 3 is hedged.
    def test_round2_followup_docs(self):
        manifest = " ".join((REPO / "docs" / "manifest.md").read_text().split())
        self.assertNotIn("`TERM*`", manifest)
        self.assertIn("`TERM`, `TERM_*`", manifest)
        agents = " ".join((REPO / "docs" / "agents.md").read_text().split())
        self.assertNotIn("Nothing else is reachable from a session started with `actas run`, not", agents)



class StatusDisplay(Sandbox):
    """status names a non-claude tool and shows gcp/azure as key=value, not JSON."""
    manifest = {"version": 2, "profiles": {
        "b": {"tool": "codex", "gcp": {"project": "client-b-dev"}}}}

    def test_tool_row_and_readable_module(self):
        self.assertEqual(self.m.describe("gcp", {"project": "client-b-dev", "account": None}),
                         "project=client-b-dev")
        r = self.cli("status", "b")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"(?m)^tool +codex$")
        self.assertRegex(r.stdout, r"(?m)^gcp +project=client-b-dev$")


if __name__ == "__main__":
    unittest.main()
