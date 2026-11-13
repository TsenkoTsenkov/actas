"""Legacy compatibility after the rename ccenv -> actas.

An install that never moved anything keeps working: CCENV* variables are read
when the ACTAS* ones are unset, ~/.config/ccenv and ~/.local/state/ccenv are
used while the new directories do not exist (so are an old `ccenv run`'s
session records), stale CCENV* markers do not reach a child, files synced by
ccenv are taken over by `actas sync`, doctor points at what the rename left
behind (no actas on PATH, an old ccenv skill), and `ccenv` stays a deprecated
alias of the same program. The legacy names never open a way around the
agent gate."""

import json
import os
import shutil

from actas_testlib import REPO, Sandbox
from test_m4 import AGENT, PP, REC_PID, T1, Base

OLD_PREFIX = "/usr/bin/python3 /old/checkout/bin/ccenv"
OLD_HOOK = f"{OLD_PREFIX} hook session-start --profile a --source sync"
SYNC_MANIFEST = {
    "version": 2,
    "profiles": {
        "a": {"match": "%(tmp)s/code/a", "aws": {"profile": "a-aws"},
              "claude": {"provider": "gateway", "base_url": "https://llm.example", "token": "sops:a/token"}},
    },
}


class LegacyCompat(Sandbox):
    """Directories, variables, synced files and the `ccenv` alias (CLI and in-process)."""

    manifest = SYNC_MANIFEST
    secrets = {"a": {"token": "tok-A"}}

    def setUp(self):
        super().setUp()
        self.code = self.tmp / "code" / "a"
        self.code.mkdir(parents=True)
        self.sf = self.code / ".claude" / "settings.local.json"

    def use_defaults(self):
        """No ACTAS_ROOT/ACTAS_STATE: the default places under HOME and XDG_STATE_HOME."""
        os.environ.pop("ACTAS_ROOT", None)
        os.environ.pop("ACTAS_STATE", None)

    def legacy_config(self):
        old = self.home / ".config" / "ccenv"
        old.mkdir(parents=True)
        for f in ("environments.yaml", "secrets.enc.yaml"):
            shutil.copy(self.root / f, old / f)
        return old

    def write_old_file(self, extra_env=None):
        self.sf.parent.mkdir()
        self.sf.write_text(json.dumps({
            "apiKeyHelper": f"{OLD_PREFIX} secret --ref 'sops:a/token'",
            "env": dict({"CCENV": "a", "CCENV_LEVEL": "standard", "AWS_PROFILE": "a-aws", "MINE": "keep"},
                        **(extra_env or {})),
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": OLD_HOOK}]},
                                       {"hooks": [{"type": "command", "command": "echo mine"}]}]}}))

    def without_rules(self, data):
        """The file as text without the permission rules (which name the alias on purpose)."""
        return json.dumps({k: v for k, v in data.items() if k != "permissions"})

    def hook_commands(self, data):
        return [h["command"] for g in data["hooks"]["SessionStart"] for h in g["hooks"]]

    # -- variables -------------------------------------------------------------
    def test_ccenv_root_and_state_are_read_when_actas_ones_are_unset(self):
        self.use_defaults()
        os.environ.update({"CCENV_ROOT": str(self.root), "CCENV_STATE": str(self.tmp / "old-state")})
        m = self.reload()
        self.assertEqual((m.ROOT, m.STATE), (self.root, self.tmp / "old-state"))
        r = self.cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a", r.stdout)
        os.environ["ACTAS_ROOT"] = str(self.tmp / "new-root")              # the new name wins
        self.assertEqual(self.reload().ROOT, self.tmp / "new-root")

    def test_path_and_git_base_fallbacks(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        os.environ.pop("ACTAS_FALLBACK_PATH")
        os.environ.update({"CCENV_FALLBACK_PATH": "", "CCENV_PATH_EXTRA": str(self.fakebin)})
        self.assertEqual(self.m.find_bin("sops"), str(self.fakebin / "sops"))
        os.environ["CCENV_PATH_EXTRA"] = ""
        os.environ["CCENV_FALLBACK_PATH"] = str(self.fakebin)
        self.assertEqual(self.m.find_bin("sops"), str(self.fakebin / "sops"))
        os.environ["ACTAS_FALLBACK_PATH"] = ""                              # "" (set) wins and disables
        self.assertIsNone(self.m.find_bin("sops"))
        out = self.m.git_config_env([("k", "v")], {"GIT_CONFIG_COUNT": "5", "CCENV_GIT_BASE": "1"})
        self.assertEqual((out["GIT_CONFIG_COUNT"], out["ACTAS_GIT_BASE"]), ("2", "1"))
        self.assertNotIn("CCENV_GIT_BASE", out)                             # only the new name is written

    def test_stale_ccenv_markers_do_not_reach_a_child(self):
        old = {"CCENV": "other", "CCENV_LEVEL": "power", "CCENV_GIT_BASE": "0"}
        r = self.cli("exec", "a", "--", "env", env=old)
        env = dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)
        self.assertEqual((env.get("ACTAS"), env.get("ACTAS_LEVEL")), ("a", "standard"), r.stderr)
        self.assertFalse(set(old) & set(env), r.stdout)
        r = self.cli("shell", "a", "--no-secrets", env=old)
        unset = [ln for ln in r.stdout.splitlines() if ln.startswith("unset ")]
        self.assertTrue(unset and set(old) <= set(unset[0].split()[1:]), r.stdout)
        prof, parent = self.m.resolve("a"), dict(old, CCENV_AGENT="codex")
        res = self.m.assemble(prof, want_secrets=False, parent=parent)
        self.assertNotIn("CCENV_AGENT", res["remove"])             # the user's own marker, no ACTAS_AGENT set
        self.assertFalse(set(old) & set(res["scrubbed"]))          # not "cleared from your shell"
        res = self.m.assemble(prof, want_secrets=False, parent=parent, ctx={"tool": "claude"})
        self.assertIn("CCENV_AGENT", res["remove"])                # ACTAS_AGENT=claude: the old one goes

    def test_session_markers_and_the_hook(self):
        self.assertEqual(self.cli("sync", "a").returncode, 0)
        hook = ("hook", "session-start", "--profile", "a", "--source")
        self.assertTrue(self.cli(*hook, "sync").stdout)
        self.assertEqual(self.cli(*hook, "sync", env={"CCENV_SESSION": "42"}).stdout, "")  # run's hook speaks
        self.assertEqual(self.cli(*hook, "run", env={"CCENV": "other"}).stdout, "")
        self.assertTrue(self.cli(*hook, "run", env={"CCENV": "a"}).stdout)
        r = self.cli("exec", "a", "--", "env", env={"CCENV_SESSION": "42", "CCENV": "a"})
        self.assertIn("ACTAS=a", r.stdout.splitlines(), r.stderr)

    def test_rules_scrub_and_markers_cover_both_prefixes(self):
        m = self.m
        self.assertIn("Bash(ccenv secret*)", m.INTENT_RULES["secrets"])     # the alias runs the same program
        self.assertIn("Bash(ccenv exec *)", m.INTENT_RULES["switch"])
        self.assertIn("Bash(actas secret*)", m.INTENT_RULES["secrets"])
        self.assertIn("CCENV", m.NEVER_OWNED_PREFIX)
        self.assertIn("CCENV_SESSION", m.PROCESS_ONLY)
        self.assertIn("CCENV_AGENT", m.AGENT_MARKERS)
        self.assertIn('use_ccenv() { use_actas "$@"; }', m.DIRENVRC)        # `use ccenv` in .envrc files

    # -- directories -----------------------------------------------------------
    def test_legacy_config_and_state_dirs_and_the_doctor_hint(self):
        self.use_defaults()
        old_cfg = self.legacy_config()
        old_state = self.tmp / "xstate" / "ccenv"
        old_state.mkdir(parents=True)
        m = self.reload()
        self.assertEqual((m.ROOT, m.STATE), (old_cfg, old_state))
        r = self.cli("status", "a", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)["profile"]), (0, "a"), r.stderr)
        self.assertFalse((self.home / ".config" / "actas").exists())       # nothing creates the new dirs
        self.assertFalse((self.tmp / "xstate" / "actas").exists())
        checks = json.loads(self.cli("doctor", "--json").stdout)["checks"]
        hints = {c["name"]: c["detail"] for c in checks if c["status"] == "info" and "rename" in str(c["detail"])}
        self.assertIn("mv ~/.config/ccenv ~/.config/actas", hints["config dir"])
        self.assertIn(f"mv {old_state} {self.tmp / 'xstate' / 'actas'}", hints["state dir"])
        (self.home / ".config" / "actas").mkdir()                          # moved (here: a new empty one)
        self.assertEqual(self.reload().ROOT, self.home / ".config" / "actas")
        checks = json.loads(self.cli("doctor", "--json").stdout)["checks"]
        self.assertTrue(any("no longer read" in str(c["detail"]) for c in checks if c["name"] == "config dir"))

    def test_edit_rules_guard_the_new_dirs_while_the_old_ones_are_used(self):
        self.use_defaults()
        self.legacy_config()
        (self.tmp / "xstate" / "ccenv").mkdir(parents=True)
        m = self.reload()
        rules = m.cred_rules(m.load())
        for d in (self.home / ".config", self.tmp / "xstate"):
            for name in ("ccenv", "actas"):
                self.assertIn(f"Edit({m.rule_path(d / name)}/**)", rules)
        (self.home / ".config" / "actas").mkdir()                          # moved: the old one is not read
        shutil.copy(self.root / "environments.yaml", self.home / ".config" / "actas")
        m = self.reload()
        rules = m.cred_rules(m.load())
        self.assertIn(f"Edit({m.rule_path(self.home / '.config' / 'actas')}/**)", rules)
        self.assertNotIn(f"Edit({m.rule_path(self.home / '.config' / 'ccenv')}/**)", rules)

    # -- doctor ----------------------------------------------------------------
    def doctor_checks(self, name, **kw):
        return [c for c in json.loads(self.cli("doctor", "--json", **kw).stdout)["checks"] if c["name"] == name]

    def test_doctor_says_when_actas_is_not_on_path(self):
        (c,) = self.doctor_checks("actas cmd")                              # the sandbox PATH has no actas
        self.assertEqual(c["status"], "warn")
        self.assertIn(f"ln -s {self.bin.resolve()} ~/.local/bin/actas", c["detail"])
        on_path = self.tmp / "onpath"
        on_path.mkdir()
        (on_path / "actas").symlink_to(self.bin)
        self.assertEqual(self.doctor_checks("actas cmd", env={"PATH": f"{on_path}:{os.environ['PATH']}"}), [])

    def test_doctor_finds_an_old_ccenv_skill(self):
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        self.assertEqual(self.doctor_checks("skill"), [])
        (skills / "ccenv").symlink_to(self.tmp / "checkout" / "skills" / "ccenv")    # renamed away by a pull
        (c,) = self.doctor_checks("skill")
        self.assertEqual(c["status"], "warn")
        self.assertIn("no longer exists", c["detail"])
        self.assertIn("rm ~/.claude/skills/ccenv && actas setup skill --dir ~/.claude/skills",
                      c["detail"])                                        # a link: plain rm; no checkout here
        (skills / "ccenv").unlink()
        (skills / "ccenv").mkdir()                                          # an old copy
        (skills / "ccenv" / "SKILL.md").write_text("If ccenv prints a line starting with `ccenv-stop:` ...\n")
        (skills / "actas").mkdir()
        (c,) = self.doctor_checks("skill")
        self.assertIn("looks for ccenv-stop:", c["detail"])
        self.assertIn("remove it (~/.claude/skills/actas is installed): rm -r ~/.claude/skills/ccenv", c["detail"])

    # -- sync ------------------------------------------------------------------
    def test_sync_takes_over_an_unledgered_ccenv_file(self):
        self.write_old_file()
        r = self.cli("sync", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.sf.read_text())
        self.assertEqual([k for k in data["env"] if k.startswith("CCENV")], [])
        self.assertEqual((data["env"]["ACTAS"], data["env"]["ACTAS_LEVEL"], data["env"]["MINE"]),
                         ("a", "standard", "keep"))
        self.assertIn(f"{self.bin} secret --ref", data["apiKeyHelper"])
        cmds = self.hook_commands(data)
        (tool_hook,) = [c for c in cmds if "session-start" in c]           # the old one is replaced
        self.assertTrue(tool_hook.endswith(f"{self.bin} hook session-start --profile a --source sync"), cmds)
        self.assertIn("echo mine", cmds)                                    # the user's own hook stays
        self.assertNotIn("ccenv", self.without_rules(data))

    def test_sync_reads_the_ledger_in_the_legacy_state_dir(self):
        self.use_defaults()
        self.legacy_config()
        state = self.tmp / "xstate" / "ccenv"
        state.mkdir(parents=True)
        self.write_old_file({"OLD_OWNED": "1"})
        (state / "sync-ledger.json").write_text(json.dumps({str(self.sf): {
            "profile": "a", "version": 2, "env": ["CCENV", "CCENV_LEVEL", "AWS_PROFILE", "OLD_OWNED"],
            "top": ["apiKeyHelper"], "permissions": {}, "hooks": [OLD_HOOK], "helpers": ["/old/checkout/bin/ccenv"]}}))
        r = self.cli("sync", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.sf.read_text())
        self.assertNotIn("OLD_OWNED", data["env"])                          # owned per the old ledger
        self.assertEqual((data["env"]["ACTAS"], data["env"]["MINE"]), ("a", "keep"))
        self.assertNotIn("ccenv", self.without_rules(data))
        ledger = json.loads((state / "sync-ledger.json").read_text())       # still the legacy state dir
        self.assertIn("ACTAS", ledger[str(self.sf)]["env"])
        self.assertFalse((self.tmp / "xstate" / "actas").exists())

    # -- the deprecated `ccenv` alias ----------------------------------------------
    def test_ccenv_alias_runs_the_same_program(self):
        self.assertEqual(os.readlink(str(REPO / "bin" / "ccenv")), "actas")   # the checkout's alias
        self.assertIn('ccenv = "actas_cli:main"', (REPO / "pyproject.toml").read_text())
        link = self.bin.parent / "ccenv"
        link.symlink_to("actas")
        r = self.cli("--version", binary=link)
        self.assertEqual(r.stdout.strip(), f"actas {self.m.__version__}")
        r = self.cli("secret", "a", "token", binary=link)                   # v1's helper form
        self.assertEqual((r.returncode, r.stdout), (0, "tok-A"), r.stderr)
        r = self.cli("secret", "a", "token")                                # and the new name
        self.assertEqual((r.returncode, r.stdout), (0, "tok-A"), r.stderr)
        self.assertEqual(self.cli("sync", "a", binary=link).returncode, 0)
        helper = json.loads(self.sf.read_text())["apiKeyHelper"]
        self.assertIn(f"{self.bin} secret --ref", helper)                   # synced through the alias: new name
        self.assertNotIn("ccenv", helper)
        self.assertEqual(self.m.current_name(str(link)), str(self.bin))
        lone = self.tmp / "elsewhere" / "ccenv"                             # no actas next to it: kept
        self.assertEqual(self.m.current_name(str(lone)), str(lone))

    def test_alias_note_only_for_a_person_at_a_terminal(self):
        note = self.m.alias_note
        self.assertIn("deprecated alias", note("run", "/x/bin/ccenv", True))
        self.assertIsNone(note("run", "/x/bin/actas", True))
        self.assertIsNone(note("run", "/x/bin/ccenv", False))
        self.assertIsNone(note("secret", "/x/bin/ccenv", True))             # helper strings stay quiet
        self.assertIsNone(note("hook", "/x/bin/ccenv", True))


class LegacyGate(Base):
    """The agent gate with the pre-rename names (in-process, injected process table)."""

    def setUp(self):
        super().setUp()
        self.ro, self.std = self.tmp / "code" / "ro", self.tmp / "code" / "std"

    def reason(self, err):
        first = err.splitlines()[0] if err else ""
        return first.split("reason=")[1].split()[0] if first.startswith("actas-stop:") else None

    def test_ccenv_from_an_old_synced_file_is_the_session(self):
        code, _, err = self.call("exec", "std2", "--", "true", env=dict(AGENT, CCENV="std"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.execs[-1][1]["ACTAS"], "std2")
        code, _, err = self.call("exec", "std2", "--", "true", env=AGENT)
        self.assertEqual(self.reason(err), "no_session", err)
        code, _, err = self.call("exec", "std2", "--", "true", env=dict(AGENT, ACTAS="ro", CCENV="std"))
        self.assertEqual(self.reason(err), "not_allowed", err)              # ACTAS wins over CCENV

    def test_record_of_an_old_ccenv_run_while_the_state_dir_is_unmoved(self):
        user_state = self.tmp / "passwd-home" / ".local" / "state"
        d = user_state / "ccenv" / "sessions"
        d.mkdir(parents=True)
        self.reload()
        self.patch()
        self.assertEqual(self.m.USER_SESSIONS, d)                          # only the old dir exists
        (d / f"{REC_PID}.json").write_text(json.dumps({
            "pid": REC_PID, "start": T1, "profile": "std", "level": "standard",
            "root": str(self.m.ROOT), "state": str(self.m.STATE)}))
        code, _, err = self.call("exec", "--", "true", env=AGENT, cwd=self.std)
        self.assertEqual((code, self.execs[-1][1]["ACTAS"]), (0, "std"), err)
        code, _, err = self.call("exec", "pwf", "--", "true", env=AGENT)
        self.assertEqual(self.reason(err), "elevation", err)                 # the record governs
        # Once the new state dir exists, the old run's record is not searched;
        # its CCENV_SESSION marker without a record is refused (fails closed).
        (user_state / "actas").mkdir()
        self.reload()
        self.patch()
        code, _, err = self.call("exec", "std", "--", "true",
                                 env=dict(AGENT, CCENV="std", CCENV_SESSION=str(REC_PID)), cwd=self.std)
        self.assertEqual(self.reason(err), "override", err)

    def test_legacy_names_are_no_way_around_the_gate(self):
        code, _, err = self.call("exec", "ro", "--", "true", env=AGENT, cwd=self.ro)
        self.assertEqual(code, 0, err)                                      # the baseline is allowed
        default = str(self.home / ".config" / "actas")
        for extra in ({"CCENV_ROOT": str(self.root)}, {"CCENV_STATE": str(self.tmp / "elsewhere")},
                      {"ACTAS_ROOT": default, "CCENV_ROOT": str(self.root)}):
            with self.subTest(extra=extra):
                code, _, err = self.call("exec", "ro", "--", "true", env=dict(AGENT, **extra), cwd=self.ro)
                self.assertEqual(self.reason(err), "override", err)
        code, _, err = self.call("exec", "std", "--", "true", env=dict(AGENT, CCENV="std", CCENV_SESSION=str(PP)))
        self.assertEqual(self.reason(err), "override", err)                 # a run marker without a record
        code, _, err = self.call("exec", "std", "--", "true",
                                 env=dict(AGENT, CCENV="std", ACTAS_SESSION="", CCENV_SESSION=str(PP)))
        self.assertEqual(self.reason(err), "override", err)                 # an empty new name hides nothing
