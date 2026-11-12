"""M4 (2.0.0-beta1): guardrails. Agent policy presets rendered as Claude rules
(SPEC 7), agent detection (8.2), the process table and session records plus
the elevation gate (8.3), `actas-stop:` refusals, exit codes 3/4, ask_user
and the --json payload (8.4, 10.4), the audit log (8.5), human-only commands,
aws-handoff (10.2, D26) and status --probe.

Gate tests run in-process with an injected process table (PS_READER) and
typed answer (CONFIRM_FN), and a captured exec. A few end-to-end tests go
through the CLI and the real `ps` (via the fake that masks agent names)."""

import contextlib
import io
import json
import os
import shlex
import unittest

from actas_testlib import Sandbox, make_fakebin

T0 = "Wed Nov 11 09:00:00 2026"
T1 = "Wed Nov 11 10:00:00 2026"
ME, PP = os.getpid(), os.getppid()
REC_PID = 7001                     # the "agent" process two levels up
SECRETS = {"std": {"npm": "npm-SECRET-1", "x": "x-SECRET-2"}, "pw": {"gh": "gh-SECRET-3"}}
SECRET_VALUES = ("npm-SECRET-1", "x-SECRET-2", "gh-SECRET-3", "VALUE-SECRET-4")

MANIFEST = {
    "version": 2,
    "profiles": {
        "ro": {"match": "%(tmp)s/code/ro", "level": "readonly", "aws": {"profile": "ro-aws"}},
        "ro2": {"level": "readonly", "colour": "x"},             # unknown key: a held warning
        "std": {"match": "%(tmp)s/code/std", "agents_may_use": ["ro2", "std2", "pwf", "conf"],
                "aws": {"profile": "std-aws", "region": "eu-west-1"}, "github": {"auth": "gh:ada-std"},
                "secret_env": {"NPM_TOKEN": "sops:std/npm"}},
        "std2": {"secret_env": {"X_TOKEN": "sops:std/x"}},
        "pwf": {"level": "power", "aws": {"profile": "pw-aws"}, "github": {"auth": "sops:pw/gh"}},
        "conf": {"level": "power", "confirm": True, "aws": {"profile": "conf-aws"}},
    },
}


class Base(Sandbox):
    secrets = SECRETS

    def setUp(self):
        self.manifest = MANIFEST
        super().setUp()
        for d in ("ro", "std"):
            (self.tmp / "code" / d).mkdir(parents=True)
        self.execs = []
        self.prompts = []
        self.patch()

    def patch(self):
        self.m.exec_cmd = lambda cmd, env, hint="": self.execs.append((cmd, env))

    def ps(self, agent_comm="zsh", stale=False, extra=()):
        rows = [(ME, PP, T0, "python3"), (PP, REC_PID, T0, "zsh"),
                (REC_PID, 7000, T0 if stale else T1, agent_comm), (7000, 1, T0, "login"),
                (1, 0, T0, "/sbin/launchd")] + list(extra)
        return "\n".join(f"{p:>6} {pp:>6} {t}     {c}" for p, pp, t, c in rows) + "\n"

    def record(self, profile, pid=REC_PID, start=T1, root=None):
        d = self.m.SESSIONS
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "start": start, "profile": profile, "level": "x",
            "root": root or str(self.m.ROOT), "state": str(self.m.STATE)}))

    def call(self, *argv, env=None, ps=None, confirm=None, overrides=False, cwd=None):
        """main(argv) in-process -> (exit code, stdout, stderr). env: extra
        variables (None removes one). Agent tests drop ACTAS_ROOT/STATE unless
        overrides is True (the module already resolved its paths). cwd: the
        process's working directory for the call."""
        saved, here = dict(os.environ), os.getcwd()
        if cwd:
            os.chdir(cwd)
            os.environ["PWD"] = str(cwd)        # macOS: /var is a link to /private/var
        if not overrides:
            os.environ.pop("ACTAS_ROOT", None)
            os.environ.pop("ACTAS_STATE", None)
        for k, v in (env or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.m.PS_READER = lambda: ps if ps is not None else self.ps()

        def answer(prompt):
            self.prompts.append(prompt)
            return confirm
        self.m.CONFIRM_FN = answer
        self.m.WARNINGS[:] = []
        out, err, code = io.StringIO(), io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.m.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        finally:
            os.environ.clear()
            os.environ.update(saved)
            os.chdir(here)
        return code, out.getvalue(), err.getvalue()

    def audit_lines(self):
        f = self.m.STATE / "audit.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


AGENT = {"CLAUDECODE": "1"}


class GateTruthTable(Base):
    """SPEC 8.3 gate: agent/human x record/no record x ACTAS x levels x
    agents_may_use x confirm x overrides (SPEC tests 44-51)."""

    # (who, record profile, $ACTAS, target, expected: "allow:<profile>" or a refusal reason)
    AGENT_CASES = [
        ("rec std, default target is the session (cwd is ro)", "std", None, None, "allow:std"),
        ("rec std, own profile", "std", None, "std", "allow:std"),
        ("rec std, listed lower", "std", None, "ro2", "allow:ro2"),
        ("rec std, listed lateral", "std", None, "std2", "allow:std2"),
        ("rec std, not listed (lower)", "std", None, "ro", "not_allowed"),
        ("rec std, listed but higher", "std", None, "pwf", "elevation"),
        ("rec std, listed confirm", "std", None, "conf", "confirm"),
        ("rec conf, its own confirm profile", "conf", None, "conf", "confirm"),
        ("rec std, ACTAS spoofs conf", "std", "conf", "conf", "confirm"),
        ("rec ro, ACTAS spoofs pwf", "ro", "pwf", "pwf", "not_allowed"),
        ("rec ro, ACTAS spoofs std", "ro", "std", None, "allow:ro"),
        ("rec pwf, anchored power", "pwf", None, "pwf", "allow:pwf"),
        ("no rec, no ACTAS, readonly of the cwd", None, None, "ro", "allow:ro"),
        ("no rec, no ACTAS, another readonly", None, None, "ro2", "no_session"),
        ("no rec, no ACTAS, standard", None, None, "std", "no_session"),
        ("no rec, no ACTAS, default = cwd ro", None, None, None, "allow:ro"),
        ("no rec, ACTAS std, self", None, "std", "std", "allow:std"),
        ("no rec, ACTAS std, listed lower", None, "std", "ro2", "allow:ro2"),
        ("no rec, ACTAS std, higher", None, "std", "pwf", "elevation"),
        ("no rec, ACTAS pwf, unanchored power", None, "pwf", "pwf", "unanchored"),
        ("no rec, ACTAS names no profile", None, "nope", "std", "no_session"),
    ]

    def check(self, label, code, out, err, expect, target):
        if expect.startswith("allow:"):
            self.assertEqual(code, 0, f"{label}: {err}")
            self.assertEqual(len(self.execs), 1, label)
            self.assertEqual(self.execs[-1][1]["ACTAS"], expect[6:], label)
        else:
            self.assertEqual(code, 3, f"{label}: {err}")
            self.assertEqual(self.execs, [], label)
            self.assertEqual(err.splitlines()[0],
                             f"actas-stop: code=needs_user reason={expect} profile={target}", label)

    def test_agent_branch(self):
        for label, rec, actas, target, expect in self.AGENT_CASES:
            with self.subTest(label):
                self.execs.clear()
                for f in self.m.SESSIONS.glob("*.json") if self.m.SESSIONS.exists() else []:
                    f.unlink()
                if rec:
                    self.record(rec)
                argv = ["exec"] + ([target] if target else [])
                code, out, err = self.call(*argv, "--", "true", env=dict(AGENT, ACTAS=actas),
                                           cwd=self.tmp / "code" / "ro")
                self.check(label, code, out, err, expect, target or ("std" if rec == "std" else "ro"))
                self.assertEqual(self.prompts, [], "the agent branch never prompts")

    def test_agent_detected_by_ancestor_alone(self):
        self.record("std")
        code, _, err = self.call("exec", "pwf", "--", "true", ps=self.ps(agent_comm="claude"))
        self.assertEqual(code, 3, err)
        self.assertIn("reason=elevation", err.splitlines()[0])
        for comm in ("/Applications/Claude.app/Contents/MacOS/Claude", "/opt/x/bin/codex", "gemini"):
            code, _, err = self.call("exec", "std", "--", "true", ps=self.ps(agent_comm=comm))
            self.assertEqual(code, 0, f"{comm}: {err}")     # anchored record: allowed, but agent context
            self.assertEqual(self.execs[-1][1].get("GIT_TERMINAL_PROMPT"), "0", comm)

    def test_env_markers_alone(self):
        for marker in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION", "CODEX_SANDBOX_NETWORK_DISABLED",
                       "GEMINI_CLI", "ACTAS_AGENT"):
            code, _, err = self.call("exec", "std", "--", "true", env={marker: "1"}, ps="")
            self.assertEqual(code, 3, marker)
            self.assertIn("reason=no_session", err.splitlines()[0], marker)

    def test_overrides(self):  # SPEC test 47
        self.record("std", root="/elsewhere")
        code, _, err = self.call("exec", "std", "--", "true", env=AGENT)
        self.assertIn("reason=override profile=std", err.splitlines()[0])
        self.record("std")                                          # same root: fine
        code, _, err = self.call("exec", "std", "--", "true", env=AGENT, overrides=True)
        self.assertEqual(code, 0, err)
        self.m.SESSIONS.joinpath(f"{REC_PID}.json").unlink()        # no record + ACTAS_ROOT set
        code, _, err = self.call("exec", "ro", "--", "true", env=AGENT, overrides=True)
        self.assertIn("reason=override profile=ro", err.splitlines()[0])

    def test_stale_or_foreign_records_are_ignored(self):  # SPEC test 50
        self.record("std")
        code, _, err = self.call("exec", "std", "--", "true", env=AGENT, ps=self.ps(stale=True))
        self.assertIn("reason=no_session", err.splitlines()[0])
        self.m.SESSIONS.joinpath(f"{REC_PID}.json").unlink()
        self.record("std", pid=4242)                                # not an ancestor
        code, _, err = self.call("exec", "std", "--", "true", env=AGENT,
                                 ps=self.ps(extra=[(4242, 1, T1, "claude")]))
        self.assertIn("reason=no_session", err.splitlines()[0])

    def test_cd_into_another_profile_changes_nothing(self):  # SPEC test 47
        self.record("std")
        code, _, err = self.call("exec", "--", "true", env=dict(AGENT, ACTAS=None), cwd=self.tmp / "code" / "ro")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.execs[-1][1]["ACTAS"], "std")

    def test_human_branch(self):  # SPEC tests 51, 54
        cases = [  # (ACTAS, target, typed, expected code, prompted)
            (None, "std", None, 0, False),
            (None, "pwf", None, 0, False),            # power without confirm, no session: no prompt
            ("std", "ro2", None, 0, False),           # lower: no prompt
            ("std", "std", None, 0, False),           # direnv ACTAS + no tty + no markers: human
            ("ro", "std", "std", 0, True),            # elevation over $ACTAS, confirmed
            (None, "conf", "conf", 0, True),
            (None, "conf", "wrong", 1, True),
            (None, "conf", None, 3, True),             # no terminal
        ]
        for actas, target, typed, rc, prompted in cases:
            with self.subTest(actas=actas, target=target, typed=typed):
                self.prompts.clear()
                self.execs.clear()
                code, _, err = self.call("exec", target, "--", "terraform", "apply", "-auto-approve",
                                         env={"ACTAS": actas}, confirm=typed, ps=self.ps(), overrides=True)
                self.assertEqual(code, rc, err)
                self.assertEqual(bool(self.prompts), prompted)
                self.assertEqual(len(self.execs), 1 if rc == 0 else 0)
                if prompted:
                    self.assertIn("to run: terraform apply -auto-approve.", self.prompts[0])
                    self.assertIn(f"-> {target} (", self.prompts[0])
                if rc == 3:
                    self.assertEqual(err.splitlines()[0],
                                     f"actas-stop: code=needs_user reason=no_tty profile={target}")
                if rc == 1:
                    self.assertIn("not confirmed", err)
        decisions = [x["decision"] for x in self.audit_lines() if x["action"] == "exec"]
        for d in ("allowed", "confirmed-tty", "refused-confirm", "refused-needs-user"):
            self.assertIn(d, decisions)

    def test_human_run_of_a_confirm_profile(self):
        code, _, err = self.call("run", "conf", "--no-check", confirm="conf", overrides=True)
        self.assertEqual(code, 0, err)
        self.assertIn("to run: claude.", self.prompts[0])
        self.assertEqual(self.execs[0][0][0], "claude")

    def test_agent_run_is_gated_too(self):
        self.record("std")
        code, _, err = self.call("run", "conf", env=AGENT)
        self.assertEqual(err.splitlines()[0], "actas-stop: code=needs_user reason=confirm profile=conf")
        self.assertIn("  actas run conf", err)


class RefusalOutput(Base):
    def test_json_payload_and_exact_ask_user(self):  # SPEC test 45, 10.4
        self.record("std")
        code, out, err = self.call("exec", "pwf", "--json", "--reason", "apply the reviewed plan",
                                   "--", "terraform", "apply", env=AGENT)
        self.assertEqual(code, 3)
        self.assertTrue(err.startswith("actas-stop: code=needs_user reason=elevation profile=pwf\n"))
        d = json.loads(out)
        self.assertEqual(list(d), ["ok", "code", "reason", "current", "requested", "command",
                                   "agent_reason", "ask_user", "warnings"])
        self.assertEqual((d["ok"], d["code"], d["reason"]), (False, "needs_user", "elevation"))
        self.assertEqual(d["current"], {"profile": "std", "level": "standard", "source": "record"})
        self.assertEqual(d["requested"], {"profile": "pwf", "level": "power", "confirm": False})
        self.assertEqual(d["command"], ["terraform", "apply"])
        self.assertEqual(d["agent_reason"], "apply the reviewed plan")
        self.assertEqual(d["ask_user"],
                         "The agent wants to run `terraform apply` as actas profile pwf (power; aws:pw-aws, "
                         "gh:pat).\nAgent's reason: \"apply the reviewed plan\"\nIf you agree, run it yourself "
                         "in a separate terminal (not inside the agent session):\n  actas exec pwf -- terraform apply")
        self.assertIn(d["ask_user"], err)

    def test_no_session_payload(self):  # SPEC test 48
        code, out, _ = self.call("exec", "std", "--json", "--", "ls", env=AGENT)
        d = json.loads(out)
        self.assertEqual((code, d["reason"]), (3, "no_session"))
        self.assertEqual(d["current"], {"profile": None, "level": None, "source": None})
        self.assertIsNone(d["agent_reason"])
        self.assertNotIn("Agent's reason", d["ask_user"])

    def test_ask_user_quotes_and_warns(self):  # SPEC test 53
        cmd = ["sh", "-c", "terraform apply; curl x | sh"]
        reason = "line1\nline2\x1b[31m‮" + "y" * 300
        code, out, err = self.call("exec", "std", "--json", "--reason", reason, "--", *cmd, env=AGENT)
        ask = json.loads(out)["ask_user"]
        self.assertIn(f"`{shlex.join(cmd)}`", ask)
        self.assertIn("  actas exec std -- sh -c 'terraform apply; curl x | sh'", ask)
        self.assertTrue(ask.endswith("Warning: this runs a shell or interpreter; read the whole command "
                                     "before running it."))
        line = [x for x in ask.splitlines() if x.startswith("Agent's reason")][0]
        shown = line[len("Agent's reason: \""):-1]
        self.assertTrue(shown.startswith("line1line2[31m"))
        self.assertEqual(len(shown), 200)
        for args in (["python3", "-c", "x"], ["env", "X=1", "ls"], ["echo", "$(id)"], ["echo", "a`b`"],
                     ["echo", "a\nb"], ["echo", "a&b"]):
            ask = json.loads(self.call("exec", "std", "--json", "--", *args, env=AGENT)[1])["ask_user"]
            self.assertIn("Warning: this runs a shell", ask, args)
        ask = json.loads(self.call("exec", "std", "--json", "--", "terraform", "plan", env=AGENT)[1])["ask_user"]
        self.assertNotIn("Warning", ask)

    def test_stop_line_is_first_despite_warnings(self):
        self.record("std")
        code, _, err = self.call("exec", "pwf", "--", "true", env=AGENT)
        lines = err.splitlines()
        self.assertTrue(lines[0].startswith("actas-stop:"))
        self.assertTrue(any("unknown key" in x for x in lines[1:]), err)   # held, then released

    def test_refusals_never_leak_secrets(self):
        self.record("std")
        outputs = [self.call("exec", t, "--json", "--", "printenv", env=AGENT) for t in ("pwf", "conf", "ro")]
        outputs.append(self.call("secret", "std", "npm", "VALUE-SECRET-4", "--set", env=AGENT))
        outputs.append(self.call("shell", "std", env=AGENT))
        self.assertEqual(self.calls("sops"), [], "a refusal resolves no secret")
        blob = "".join(o + e for _, o, e in outputs) + (self.m.STATE / "audit.jsonl").read_text()
        for s in SECRET_VALUES:
            self.assertNotIn(s, blob)
        self.assertIn("run: actas secret std npm --set", blob)       # the value is never echoed


class HumanOnly(Base):
    REFUSED = [["login", "std"], ["sync"], ["sync", "--dry-run"], ["init"], ["secret", "set", "sops:std/npm"],
               ["secret", "std", "npm", "--set"], ["shell", "std"], ["shell", "--format", "fish"],
               ["shell", "--format", "direnv"], ["aws-handoff", "std", "--stdout"], ["migrate", "--write"]]

    def test_refused_in_agent_context(self):  # SPEC test 52
        self.record("std")
        for argv in self.REFUSED:
            with self.subTest(argv=argv):
                code, out, err = self.call(*argv, env=AGENT)
                self.assertEqual(code, 3, err)
                first = err.splitlines()[0]
                self.assertTrue(first.startswith("actas-stop: code=needs_user reason=human_only profile="), first)
                self.assertIn("In your own terminal (not inside the agent session), run: actas " + argv[0], err)
        self.assertEqual(self.calls(), [], "nothing ran")
        self.assertFalse((self.m.STATE / "sync-ledger.json").exists())
        self.assertFalse((self.root / ".sops.yaml").exists())

    def test_allowed_in_agent_context(self):
        self.record("std")
        for argv in (["shell", "std", "--no-secrets"], ["shell", "--format", "json"], ["status", "--json"],
                     ["list"], ["doctor"], ["instructions"], ["migrate"], ["secret", "--ref", "sops:std/npm"]):
            with self.subTest(argv=argv):
                code, out, err = self.call(*argv, "--cwd", str(self.tmp / "code" / "std"), env=AGENT)
                self.assertNotIn("actas-stop", err)
                self.assertNotEqual(code, 3, err)
        reads = [x for x in self.audit_lines() if x["action"] == "secret-read"]
        self.assertEqual(reads[0]["refs"], ["sops:std/npm"])
        self.assertEqual(reads[0]["agent"]["record_pid"], REC_PID)

    def test_humans_are_not_refused(self):
        code, out, err = self.call("shell", "std", overrides=True)
        self.assertEqual(code, 0, err)
        self.assertIn("export NPM_TOKEN=npm-SECRET-1", out)


class Records(Base):
    def test_run_writes_its_record_and_prunes(self):
        dead = self.m.SESSIONS / "999999.json"
        dead.parent.mkdir(parents=True)
        dead.write_text("{}")
        code, _, err = self.call("run", "std", "--no-check", ps=self.ps(), overrides=True)
        self.assertEqual(code, 0, err)
        env = self.execs[0][1]
        self.assertEqual(env["ACTAS_SESSION"], str(ME))
        f = self.m.SESSIONS / f"{ME}.json"
        rec = json.loads(f.read_text())
        self.assertEqual({k: rec[k] for k in ("pid", "start", "profile", "level", "root", "state")},
                         {"pid": ME, "start": T0, "profile": "std", "level": "standard",
                          "root": str(self.m.ROOT), "state": str(self.m.STATE)})
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.m.SESSIONS.stat().st_mode & 0o777, 0o700)
        self.assertFalse(dead.exists())
        run = [x for x in self.audit_lines() if x["action"] == "run"][0]
        self.assertEqual((run["profile"], run["decision"], run["refs"], run["cmd0"]),
                         ("std", "allowed", ["gh:ada-std", "sops:std/npm"], "claude"))
        self.assertEqual((self.m.STATE / "audit.jsonl").stat().st_mode & 0o777, 0o600)  # SPEC test 55
        text = (self.m.STATE / "audit.jsonl").read_text()
        for s in SECRET_VALUES + ("gho_ada-std",):
            self.assertNotIn(s, text)

    def test_no_process_table_still_runs(self):
        code, _, err = self.call("run", "std", "--no-check", ps="", overrides=True)
        self.assertEqual(code, 0, err)
        self.assertIn("no session record", err)
        self.assertFalse((self.m.SESSIONS / f"{ME}.json").exists())

    def test_status_reports_session_agent_and_surface(self):
        self.record("std")
        code, out, _ = self.call("status", "--json", env=dict(AGENT, TERM="xterm"),
                                 ps=self.ps(agent_comm="claude"), cwd=self.tmp / "code" / "ro")
        d = json.loads(out)
        self.assertEqual(d["session"], {"profile": "std", "source": "record", "record_pid": REC_PID})
        self.assertEqual((d["profile"], d["matched_by"], d["mismatch"]), ("std", "session", True))
        self.assertEqual(d["agent"], {"detected": True, "signals": ["CLAUDECODE", "ancestor:claude"]})
        self.assertEqual(d["surface"], "terminal")
        d = json.loads(self.call("status", "--json", ps=self.ps(
            agent_comm="/Applications/Claude.app/Contents/MacOS/Claude"))[1])
        self.assertEqual((d["surface"], d["agent"]["signals"]), ("desktop", ["ancestor:Claude.app"]))
        d = json.loads(self.call("status", "std", "--json", env={"TERM": None}, ps="")[1])
        self.assertEqual((d["agent"], d["surface"], d["session"]["source"]),
                         ({"detected": False, "signals": []}, "unknown", None))


class EndToEnd(Base):
    """Through the CLI and the real process table (masked by the fake ps)."""

    def setUp(self):
        super().setUp()
        # Agent tests cannot carry ACTAS_ROOT/ACTAS_STATE (that is an override),
        # so use the default layout under the temp HOME and XDG_STATE_HOME.
        cfg = self.home / ".config" / "actas"
        cfg.mkdir(parents=True)
        for f in ("environments.yaml", "secrets.enc.yaml"):
            (cfg / f).write_text((self.root / f).read_text())
        self.e2e = {"ACTAS_ROOT": None, "ACTAS_STATE": None, "CLAUDECODE": "1"}
        os.environ.pop("ACTAS_ROOT")
        os.environ.pop("ACTAS_STATE")
        self.reload()
        self.start = self.m.ps_table()[ME][1]                   # this process: an ancestor of the CLI
        self.sessions = self.tmp / "xstate" / "actas" / "sessions"

    def cli_env(self, *args, env=None, cwd=None):
        e = {k: v for k, v in os.environ.items()}
        e.update({k: v for k, v in (env or {}).items() if v is not None})
        for k, v in (env or {}).items():
            if v is None:
                e.pop(k, None)
        os.environ.clear()
        os.environ.update(e)
        try:
            return self.cli(*args, cwd=cwd)
        finally:
            os.environ.clear()
            os.environ.update(self.env)
            os.environ.pop("ACTAS_ROOT", None)
            os.environ.pop("ACTAS_STATE", None)

    def write_rec(self, profile):
        self.sessions.mkdir(parents=True, exist_ok=True)
        (self.sessions / f"{ME}.json").write_text(json.dumps({
            "pid": ME, "start": self.start, "profile": profile, "level": "standard",
            "root": str(self.home / ".config" / "actas"), "state": str(self.tmp / "xstate" / "actas")}))

    def test_record_of_the_test_process_governs(self):  # SPEC test 44
        self.write_rec("std")
        r = self.cli_env("exec", "--", "printenv", "ACTAS", env=self.e2e, cwd=self.tmp / "code" / "ro")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "std"), r.stderr)
        r = self.cli_env("exec", "pwf", "--", "true", env=self.e2e)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stderr.splitlines()[0], "actas-stop: code=needs_user reason=elevation profile=pwf")

    def test_ancestor_name_alone_is_agent_context(self):
        env = {"ACTAS_ROOT": None, "ACTAS_STATE": None, "FAKE_PS_AS": f"{ME}:claude"}
        d = json.loads(self.cli_env("status", "std", "--json", env=env).stdout)
        self.assertEqual(d["agent"], {"detected": True, "signals": ["ancestor:claude"]})
        r = self.cli_env("sync", env=env)
        self.assertEqual(r.returncode, 3)
        self.assertTrue(r.stderr.startswith("actas-stop: code=needs_user reason=human_only profile=-\n"))
        d = json.loads(self.cli_env("status", "std", "--json", env={"FAKE_PS_FAIL": "1"}).stdout)
        self.assertFalse(d["agent"]["detected"])

    def test_run_records_the_agent_pid(self):
        r = self.cli_env("run", "std", "--no-check", env={"CLAUDECODE": None})
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = json.loads(self.out.read_text())["env"]["ACTAS_SESSION"]
        rec = json.loads((self.sessions / f"{pid}.json").read_text())
        self.assertEqual((rec["pid"], rec["profile"]), (int(pid), "std"))
        self.assertTrue(rec["start"])


class Presets(Base):
    def settings(self, name):
        return self.quiet(lambda: self.m.settings_for(self.m.resolve(name)))[0]

    def test_levels_render_their_preset(self):  # SPEC 7.1
        std = self.settings("std")["permissions"]
        self.assertIn("Bash(git push)", std["ask"])
        self.assertIn("Bash(terraform apply*)", std["ask"])
        self.assertIn("Bash(actas exec *)", std["ask"])
        self.assertIn("Bash(git credential *)", std["deny"])
        self.assertIn("Bash(aws * delete-*)", std["ask"])
        ro = self.settings("ro")["permissions"]
        self.assertIn("Bash(git push)", ro["deny"])
        self.assertIn("Bash(kubectl apply *)", ro["deny"])
        self.assertIn("Bash(actas run*)", ro["ask"])
        pw = self.settings("pwf")["permissions"]
        self.assertNotIn("Bash(git push)", pw["deny"] + pw["ask"])
        self.assertIn("Bash(terraform destroy*)", pw["ask"])
        self.assertIn("Bash(sops *)", pw["deny"])

    def test_deny_paths_follow_overrides_and_referenced_modules(self):  # SPEC test 59
        deny = self.settings("std")["permissions"]["deny"]
        self.assertIn(f"Edit(/{self.root}/**)", deny)                 # ACTAS_ROOT, as //abs
        self.assertIn(f"Edit(/{self.tmp}/state/**)", deny)            # ACTAS_STATE
        self.assertIn("Read(~/.config/sops/age/keys.txt)", deny)      # HOME-relative form
        self.assertIn("Read(~/.aws/sso/cache)", deny)
        self.assertIn("Read(~/.aws/sso/cache/**)", deny)                # the directory's files too
        self.assertIn("Read(~/.config/gh/hosts.yml)", deny)
        self.assertIn(f"Read(/{self.tmp}/state/homes/claude/*/.credentials.json)", deny)
        self.assertFalse([r for r in deny if "gcloud" in r or ".azure" in r or "codex" in r])
        self.assertFalse([r for r in deny if "homes/claude/*/**" in r or r.endswith("homes/claude/**)")])
        m = json.loads(json.dumps(MANIFEST))
        m["profiles"]["g"] = {"gcp": {"project": "p", "home": "~/gc"}, "tool": "codex"}
        self.write_manifest(m)
        self.reload()
        deny = self.settings("std")["permissions"]["deny"]
        for r in ("Read(~/.config/gcloud)", f"Read(/{self.tmp}/state/homes/gcp/*/**)", "Read(~/gc/**)",
                  "Read(~/.codex/auth.json)"):
            self.assertIn(r, deny)

    def test_builtin_false_and_composition(self):  # SPEC 7.2
        m = json.loads(json.dumps(MANIFEST))
        m["roles"] = {"no-pulumi": {"permissions": {"deny": ["Bash(pulumi up *)"]}},
                      "no-apply": {"permissions": {"deny": ["Bash(terraform apply *)"], "ask": ["Bash(make *)"]}},
                      "bare": {"permissions": {"builtin": False}}}
        m["profiles"]["mine"] = {"use": ["no-pulumi", "no-apply", "bare"],
                                 "claude": {"settings": {"permissions": {"deny": ["Bash(rm *)"]}}}}
        m["profiles"]["stacked"] = {"use": ["no-pulumi", "no-apply"]}
        self.write_manifest(m)
        self.reload()
        p = self.settings("mine")["permissions"]
        self.assertEqual(p, {"deny": ["Bash(pulumi up *)", "Bash(terraform apply *)", "Bash(rm *)"],
                             "ask": ["Bash(make *)"]})
        s = self.settings("stacked")["permissions"]
        self.assertIn("Bash(pulumi up *)", s["deny"])
        self.assertIn("Bash(git credential *)", s["deny"])
        d = json.loads(self.cli("status", "mine", "--why", "--json").stdout)
        self.assertEqual(d["agent_policy"], {"builtin": False, "deny": p["deny"], "ask": p["ask"],
                                             "gemini": "default"})
        text = self.cli("doctor").stdout
        self.assertIn("looser than the standard preset", text)

    def test_v1_files_render_no_rules(self):
        self.write_manifest({"environments": {"w": {"match": "/x", "aws": {"profile": "p"}}}})
        self.reload()
        self.assertNotIn("permissions", self.settings("w"))

    def test_synced_settings_carry_the_rules(self):
        r = self.cli("sync", "std")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads((self.tmp / "code" / "std" / ".claude" / "settings.local.json").read_text())
        self.assertIn("Bash(git push)", data["permissions"]["ask"])
        self.assertIn(f"Edit(/{self.root}/**)", data["permissions"]["deny"])
        sync = [x for x in self.audit_lines() if x["action"] == "sync"]
        self.assertEqual(len(sync), 1)

    def test_rule_paths(self):
        self.assertEqual(self.m.rule_path(self.home / "x"), "~/x")
        self.assertEqual(self.m.rule_path("/opt/y"), "//opt/y")


class Probes(Base):
    def setUp(self):
        super().setUp()
        m = json.loads(json.dumps(MANIFEST))
        m["profiles"]["all"] = {"aws": {"profile": "a1"}, "gcp": {"project": "p"},
                                "azure": {"subscription": "sub-1"}, "github": {"auth": "gh:ada"}}
        self.write_manifest(m)

    def test_probe_ok(self):
        r = self.cli("status", "all", "--probe", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        ids = json.loads(r.stdout)["identities"]
        self.assertEqual(ids["aws"]["probe"]["status"], "ok")
        self.assertEqual(ids["aws"]["probe"]["account"], "123456789012")
        self.assertEqual(ids["gcp"]["probe"], {"status": "ok"})
        self.assertEqual(ids["azure"]["probe"]["id"], "sub-1")
        self.assertEqual(ids["github"]["probe"], {"status": "ok", "login": "ada"})
        aws = [c for c in self.calls("aws") if c["argv"][:2] == ["sts", "get-caller-identity"]][0]
        self.assertEqual(aws["argv"], ["sts", "get-caller-identity", "--profile", "a1", "--output", "json",
                                       "--cli-connect-timeout", "3", "--cli-read-timeout", "5"])
        self.assertNotIn("gho_ada", r.stdout + r.stderr)

    def test_no_probe_no_calls(self):
        self.assertEqual(self.cli("status", "all", "--json").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_expired_exits_4(self):
        r = self.cli("status", "all", "--probe", "--json",
                     env={"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "Error: Token has expired and refresh failed",
                          "FAKE_AZ_SUB": "other"})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(r.stderr.splitlines()[0], "actas-stop: code=expired reason=login profile=all")
        self.assertIn("run: actas login all", r.stderr)
        self.assertIn("az is on subscription other", r.stderr)
        self.assertEqual(json.loads(r.stdout)["identities"]["aws"]["probe"], {"status": "expired"})


class AwsHandoff(Base):
    def test_expired_needs_login(self):  # D26
        env = {"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "Token has expired"}
        r = self.cli("aws-handoff", "std", "--stdout", env=env)
        self.assertEqual(r.returncode, 4)
        self.assertEqual(r.stderr.splitlines()[0], "actas-stop: code=expired reason=login profile=std")
        argvs = [c["argv"][:2] for c in self.calls("aws")]
        self.assertNotIn(["sso", "login"], argvs)
        self.assertNotIn(["configure", "export-credentials"], argvs)
        r = self.cli("aws-handoff", "std", "--stdout", "--login", env=env)
        self.assertIn(["sso", "login"], [c["argv"][:2] for c in self.calls("aws")])

    def test_stdout_and_clipboards(self):
        r = self.cli("aws-handoff", "std", "--stdout")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("aws_access_key_id = ASIAFAKE", r.stdout)
        self.assertEqual([x["action"] for x in self.audit_lines()], ["aws-handoff"])
        self.assertNotIn("aws-SECRET", (self.m.STATE / "audit.jsonl").read_text())
        bare = make_fakebin(self.tmp / "bare", only=("aws", "ps"))
        r = self.cli("aws-handoff", "std", env={"PATH": str(bare)})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no clipboard tool", r.stderr)
        self.assertEqual(r.stdout, "")
        clip = make_fakebin(self.tmp / "clip", only=("aws", "ps", "wl-copy"))
        r = self.cli("aws-handoff", "std", env={"PATH": str(clip), "WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertIn("ASIAFAKE", self.calls("wl-copy")[0]["stdin"])
        self.assertIn("copied to clipboard (wl-copy)", r.stderr)


class Login(Base):
    def test_still_expired_after_login_exits_4(self):
        r = self.cli("login", "std", env={"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "Token has expired"})
        self.assertEqual(r.returncode, 4, r.stderr)
        # login is interactive (human-only): its progress lines come first
        self.assertIn("\nactas-stop: code=expired reason=login profile=std\n", r.stderr)
        self.assertIn(["sso", "login"], [c["argv"][:2] for c in self.calls("aws")])
        self.assertEqual([x["action"] for x in self.audit_lines()][:1], ["login"])


if __name__ == "__main__":
    unittest.main()
