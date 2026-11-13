"""CLI experience: short usage errors with one hint, `actas <profile> -- cmd`,
`--no-color` in any position, per-command descriptions and examples, the
`list` markers and `--names`, and `actas completion bash|zsh|fish`."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import unittest

from actas_testlib import Sandbox

AGENT = {"CLAUDECODE": "1"}


class UsageErrors(Sandbox):
    manifest = {"version": 2, "profiles": {"personal": {}, "work": {}}}

    def err(self, *args, **kw):
        r = self.cli(*args, **kw)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stderr

    def assert_short(self, err, cmd):
        self.assertIn(f"usage: actas {cmd}", err)
        self.assertNotIn("commands:", err)                     # no full help dump
        self.assertLess(len(err.splitlines()), 6, err)

    def test_unknown_option_suggests_the_close_one(self):
        err = self.err("status", "--jsn")
        self.assert_short(err, "status")
        self.assertIn("did you mean: --json?", err)
        self.assertNotIn("claude", err)                        # the run hint is for run only
        self.assertNotIn("after --", err)
        self.assertIn("did you mean: --dry-run?", self.err("sync", "--dryrun"))
        self.assertIn("did you mean: --strict?", self.err("doctor", "--strct"))

    def test_no_close_option_points_to_the_command_help(self):
        for args, cmd in ((("list", "--bogus"), "list"), (("list", "personal"), "list"),
                          (("status", "--verbose"), "status")):
            with self.subTest(args=args):
                err = self.err(*args)
                self.assert_short(err, cmd)
                self.assertIn(f"more: actas help {cmd}", err)
                self.assertNotIn("after --", err)

    def test_run_and_exec_say_where_arguments_go(self):
        err = self.err("run", "personal", "--resume")
        self.assert_short(err, "run")
        self.assertIn("arguments for the tool go after --  (actas run <profile> -- --resume)", err)
        self.assertNotIn("<env>", err)
        self.assertIn("after --", self.err("run", "personal", "--model", "opus"))   # not "--tool"
        self.assertIn("did you mean: --login?", self.err("run", "personal", "--logn"))
        err = self.err("exec", "personal", "aws", "s3", "ls")
        self.assert_short(err, "exec")
        self.assertIn("put the command after --  (actas exec <profile> -- aws s3 ls)", err)

    def test_errors_raised_inside_a_command_are_short_too(self):
        err = self.err("run", "-t", "foo")
        self.assert_short(err, "run")
        self.assertIn("invalid choice", err)
        err = self.err("completion")
        self.assert_short(err, "completion")
        err = self.err("doctor", "--", "x")                    # D28
        self.assert_short(err, "doctor")
        self.assertIn("only to run and exec", err)

    def test_unknown_command_keeps_the_full_help(self):  # D1
        err = self.err("lst")
        self.assertIn("commands:", err)
        self.assertIn("did you mean: list?", err)

    def test_no_color_in_any_position(self):
        for args in (("--no-color", "status", "personal"), ("status", "personal", "--no-color"),
                     ("list", "--no-color"), ("exec", "personal", "--no-color", "--", "true")):
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
        for args in (["status", "personal", "--no-color"], ["--no-color", "status", "personal"],
                     ["status", "personal"]):
            self.m._MODE["no_color"] = False
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.m._main(args)
            self.assertEqual(self.m._MODE["no_color"], "--no-color" in args, args)


class ProfileShorthand(Sandbox):
    manifest = {"version": 2, "profiles": {
        "personal": {"env": {"WHO": "personal"}},
        "list": {"env": {"WHO": "list"}},                     # named like a command
        "prod": {"confirm": True}}}

    def test_profile_then_double_dash_is_exec(self):
        r = self.cli("personal", "--", "sh", "-c", "echo $ACTAS $WHO")
        self.assertEqual((r.returncode, r.stdout), (0, "personal personal\n"), r.stderr)
        r = self.cli("--cwd", str(self.tmp), "personal", "--reason", "x", "--", "sh", "-c", "exit 7")
        self.assertEqual(r.returncode, 7, r.stderr)                   # the command's own exit code
        actions = [json.loads(x)["action"] for x in (self.m.STATE / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(actions, ["exec", "exec"])                     # audited as exec

    def test_the_gate_still_decides(self):
        r = self.cli("prod", "--", "sh", "-c", "echo ran")             # confirm, and no terminal
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue(r.stderr.startswith("actas-stop:"), r.stderr)
        self.assertNotIn("ran", r.stdout)

    def test_agents_must_spell_out_exec(self):
        r = self.cli("personal", "--", "sh", "-c", "echo ran", env=AGENT)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("actas-stop", r.stderr)
        self.assertIn("did you mean: actas exec personal -- sh -c 'echo ran'", r.stderr)
        self.assertIn("agent sessions spell out exec", r.stderr)

    def test_bare_profile_and_typos_get_a_pointer(self):
        r = self.cli("personal")
        self.assertEqual(r.returncode, 2)
        self.assertIn("'personal' is a profile, not a command", r.stderr)
        self.assertIn("actas exec personal -- <command>  (or: actas run personal)", r.stderr)
        self.assertNotIn("commands:", r.stderr)
        r = self.cli("persnal", "--", "true")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command 'persnal'", r.stderr)
        self.assertIn("did you mean: actas exec personal -- ...?", r.stderr)

    def test_commands_win_over_profile_names(self):
        r = self.cli("list", "--", "true")
        self.assertEqual(r.returncode, 2)
        self.assertIn("only to run and exec", r.stderr)
        r = self.cli("exec", "list", "--", "sh", "-c", "echo $WHO")
        self.assertEqual(r.stdout, "list\n", r.stderr)

    def test_broken_or_missing_manifest(self):
        doc = json.loads(json.dumps(self.manifest))
        doc["profiles"]["personal"]["level"] = "admin"
        doc["profiles"]["personal"]["bogus_key"] = 1
        self.write_manifest(doc)
        r = self.cli("zzzq")                     # the probe for profile names stays quiet
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("bogus_key", r.stderr)
        r = self.cli("personal", "--", "true")   # exec's own load still refuses the manifest
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("profiles.personal.level", r.stderr)
        (self.root / "environments.yaml").unlink()
        r = self.cli("personal", "--", "true")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command 'personal'", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_v1_manifest(self):
        self.write_manifest({"version": 1, "environments": {"old": {"env": {"WHO": "old"}}}})
        r = self.cli("old", "--", "sh", "-c", "echo $WHO")
        self.assertEqual((r.returncode, r.stdout), (0, "old\n"), r.stderr)


class ListMarkers(Sandbox):
    manifest = {"version": 2, "profiles": {
        "personal": {"match": "%(tmp)s/code/personal"},
        "work": {"match": "%(tmp)s/code/work"}},
        "roles": {"base": {}}}

    def lines(self, *args, env=None):
        r = self.cli("list", *args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def row(self, lines, name):
        return next(x for x in lines if x.split()[0] == name)

    def test_this_directory_and_this_session(self):
        here = str(self.tmp / "code/personal/x")
        plain = self.lines()
        self.assertFalse([x for x in plain if "this directory" in x or "this session" in x])
        marked = self.lines("--cwd", here)
        self.assertTrue(self.row(marked, "personal").startswith("personal *  "))
        self.assertEqual(len(self.row(marked, "personal")), len(self.row(plain, "personal")))  # no longer
        self.assertIn("* this directory", marked)
        self.assertEqual(marked[-1], "roles: base")
        both = self.lines("--cwd", here, env={"ACTAS": "work"})
        self.assertTrue(self.row(both, "work").startswith("work +  "))
        self.assertIn("* this directory, + this session", both)
        same = self.lines("--cwd", here, env={"ACTAS": "personal"})
        self.assertTrue(self.row(same, "personal").startswith("personal *+ "))

    def test_json_is_unchanged(self):
        r = self.cli("list", "--json", "--cwd", str(self.tmp / "code/personal"), env={"ACTAS": "work"})
        d = json.loads(r.stdout)
        self.assertEqual(set(d), {"ok", "warnings", "profiles", "roles"})
        self.assertEqual(set(d["profiles"][0]), {"name", "description", "match", "level", "confirm", "tool",
                                                 "provider", "aws", "github", "remote_control"})

    def test_names(self):
        r = self.cli("list", "--names")
        self.assertEqual((r.returncode, r.stdout), (0, "personal\nwork\n"), r.stderr)
        r = self.cli("list", "--names", env=AGENT)                      # read-only: fine for agents
        self.assertEqual((r.returncode, r.stdout), (0, "personal\nwork\n"), r.stderr)


class CommandHelp(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {}}}

    def test_every_command_has_a_description_and_examples(self):
        _, sub = self.m.build_parser()
        for name, sp in sub.choices.items():
            if name in self.m.HIDDEN:
                continue
            with self.subTest(command=name):
                self.assertTrue(sp.description)
                text = sp.format_help()
                self.assertIn("\nexamples:\n  actas ", text.replace('  eval "$(actas', "  actas"))
                for act in sp._actions:
                    if act.metavar == "profile":
                        self.assertTrue(act.help, name)

    def test_help_command(self):
        r = self.cli("help", "run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: actas run [profile] [-t TOOL] [--login] [--no-check] [-- tool args...]", r.stdout)
        self.assertIn("actas run work -- --resume    pass --resume to claude", r.stdout)
        self.assertIn("completion", [x for x in self.cli("help").stdout.splitlines()
                                     if x.startswith("advanced:")][0])


class Completion(Sandbox):
    manifest = {"version": 2, "profiles": {"personal": {}, "work": {}}}

    def script(self, shell):
        r = self.cli("completion", shell)
        self.assertEqual(r.returncode, 0, r.stderr)
        f = self.tmp / f"actas.{shell}"
        f.write_text(r.stdout)
        return r.stdout, f

    def test_scripts_cover_every_public_command(self):
        _, sub = self.m.build_parser()
        public = [n for n in sub.choices if n not in self.m.HIDDEN]
        for shell in ("bash", "zsh", "fish"):
            with self.subTest(shell=shell):
                text, _ = self.script(shell)
                for n in public:
                    self.assertIn(n, text)
                self.assertIn("list --names", text)
                self.assertNotIn("git-credential", text)

    def check_syntax(self, shell):
        exe = shutil.which(shell)
        if not exe:
            self.skipTest(f"no {shell}")
        _, f = self.script(shell)
        r = subprocess.run([exe, "-n", str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return exe, f

    def test_bash_syntax(self):
        self.check_syntax("bash")

    def test_zsh_syntax_and_registration(self):
        exe, f = self.check_syntax("zsh")
        code = (f"autoload -U compinit; compinit -u -d {self.tmp}/zcompdump; source {f}; "
                "print -r -- ${_comps[actas]}")
        r = subprocess.run([exe, "-f", "-c", code], capture_output=True, text=True,
                           env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.stdout.strip(), "_actas", r.stderr)

    def test_fish_syntax(self):
        self.check_syntax("fish")

    def test_bash_completes(self):
        exe, f = self.check_syntax("bash")
        wrapper = self.tmp / "wrap" / "actas"
        wrapper.parent.mkdir()
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{self.bin}" "$@"\n')
        wrapper.chmod(0o755)

        def complete(*words):
            words = [str(wrapper)] + list(words)
            arr = " ".join("'" + w.replace("'", "'\\''") + "'" for w in words)
            code = (f"source {f}; COMP_WORDS=({arr}); COMP_CWORD={len(words) - 1}; _actas; "
                    'printf "%s\\n" "${COMPREPLY[@]}"')
            r = subprocess.run([exe, "-c", code], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return [x for x in r.stdout.splitlines() if x]

        self.assertEqual(complete("sta"), ["status"])
        self.assertEqual(complete("--cwd", "/tmp", "ex"), ["exec"])
        self.assertEqual(complete("exec", ""), ["personal", "work"])
        self.assertEqual(complete("status", "w"), ["work"])
        self.assertEqual(complete("exec", "personal", ""), [])          # one profile only
        self.assertEqual(complete("run", "-t", ""), ["claude", "codex", "gemini"])
        self.assertEqual(complete("exec", "--reason", ""), [])          # free text
        self.assertEqual(complete("status", "--j"), ["--json"])
        self.assertEqual(complete("help", "man"), ["manifest"])
        self.assertEqual(complete("completion", ""), ["bash", "zsh", "fish"])
        self.assertIn("echo", complete("exec", "personal", "--", "ech"))
        self.assertEqual(complete("exec", "personal", "--", "echo", "zzzq"), [])


if __name__ == "__main__":
    unittest.main()
