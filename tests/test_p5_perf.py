"""Performance work (p5): the macOS ancestor reader that replaces `ps -A`
on every gated and helper command, concurrent probes in `status --probe` and
`actas run`, libyaml parsing with readable errors, and session-record
pruning that no longer needs the whole process table.

The reader tests compare it with the real system ps on this machine. They
only compare rows; they never assert on (or print) what the rows say."""

import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from unittest import mock

from actas_testlib import BIN, REPO, Sandbox, has_yaml, load_module

DARWIN = sys.platform == "darwin"


def table(m, text):
    """ps_table's parse of a reader's text, without its cache."""
    m._CACHE.clear()
    saved = m.PS_READER
    m.PS_READER = lambda: text
    try:
        return dict(m.ps_table())
    finally:
        m.PS_READER = saved
        m._CACHE.clear()


class LstartFormat(unittest.TestCase):
    def setUp(self):
        self.m = load_module(BIN, "actas_p5_lstart")
        self.saved_tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()

    def tearDown(self):
        if self.saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.saved_tz
        time.tzset()

    def test_day_of_month_is_not_zero_padded(self):
        # 2026-11-02 16:52:57 UTC: ps prints "Mon Nov  2 16:52:57 2026", and
        # ps_table collapses the double space.
        self.assertEqual(self.m.ps_lstart(1793638377), "Mon Nov 2 16:52:57 2026")
        self.assertEqual(self.m.ps_lstart(1794441600), "Thu Nov 12 00:00:00 2026")

    def test_names_do_not_follow_the_locale(self):
        import locale
        saved = locale.setlocale(locale.LC_TIME)
        try:
            for loc in ("de_DE.UTF-8", "fr_FR.UTF-8"):
                try:
                    locale.setlocale(locale.LC_TIME, loc)
                except locale.Error:
                    continue
                self.assertEqual(self.m.ps_lstart(1793638377), "Mon Nov 2 16:52:57 2026")
        finally:
            locale.setlocale(locale.LC_TIME, saved)


@unittest.skipUnless(DARWIN, "the sysctl reader is macOS only")
class ChainReader(unittest.TestCase):
    def setUp(self):
        self.m = load_module(BIN, "actas_p5_chain")    # the shipped binary: PS_NATIVE on

    def test_rows_equal_the_system_ps_rows(self):
        chain = table(self.m, self.m.read_chain())
        full = table(self.m, self.m.read_ps())
        self.assertIn(os.getpid(), chain)                # write_record needs its own row
        self.assertIn(os.getppid(), chain)
        diff = [p for p, row in chain.items() if full.get(p) != row]
        # A process can only end between the two reads, never change its row.
        self.assertEqual([p for p in diff if p in full], [])
        self.assertEqual(self.m.ancestors(chain), [p for p in self.m.ancestors(full) if p in chain])

    def test_the_chain_reaches_launchd(self):
        chain = table(self.m, self.m.read_chain())
        self.assertIn(1, chain)                          # another user's process: from `ps -p`
        self.assertEqual(chain[1], table(self.m, self.m.read_ps())[1])

    def test_detection_is_unchanged(self):
        self.m._CACHE.clear()
        self.m.PS_READER = self.m.read_chain
        native = self.m.detect_agent()
        self.m._CACHE.clear()
        self.m.PS_READER = self.m.read_ps
        self.assertEqual(native, self.m.detect_agent())
        self.m._CACHE.clear()

    def test_an_unprintable_argv0_is_left_to_ps(self):
        # The child's own argv[0] holds a tab: ps escapes it (a\011b), so the
        # reader must not print it raw (which would also break the line format).
        code = textwrap.dedent(f"""
            import importlib.machinery, importlib.util, os, sys
            loader = importlib.machinery.SourceFileLoader("a", {str(BIN)!r})
            spec = importlib.util.spec_from_loader("a", loader)
            m = importlib.util.module_from_spec(spec); loader.exec_module(m)
            def rows(text):
                m._CACHE.clear(); m.PS_READER = lambda: text; return dict(m.ps_table())
            me = os.getpid()
            print(rows(m.read_chain())[me] == rows(m.read_ps())[me], rows(m.read_chain())[me][1:2] != ("",))
        """)
        r = subprocess.run(["a\tb", "-c", code], executable=sys.executable, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), ["True", "True"], r.stderr)

    @unittest.skipIf(os.getuid() == 0, "root reads every process's arguments")
    def test_no_system_ps_for_unreadable_rows_means_ps_A(self):
        self.m.PS_BINS = ("/nonexistent/ps",)
        self.assertIsNone(self.m.read_chain())

    def test_an_unexpected_struct_layout_means_ps_A(self):
        self.m.KINFO_SIZE = 600            # the kernel's struct no longer fits: ENOMEM
        self.assertIsNone(self.m.read_chain())
        self.m.KINFO_SIZE, self.m.KINFO_PID = 648, 44
        self.assertIsNone(self.m.read_chain())    # the pid field does not hold the pid


class ReaderChoice(unittest.TestCase):
    def setUp(self):
        self.m = load_module(BIN, "actas_p5_choice")
        self.m.read_ps = lambda: "PS-A"

    def test_off_macos_it_is_ps_A(self):
        with mock.patch.object(self.m.sys, "platform", "linux"):
            self.assertIsNone(self.m.read_chain())
            self.assertEqual(self.m.read_procs(), "PS-A")

    def test_a_failed_chain_falls_back_to_ps_A(self):
        self.m.read_chain = lambda: None
        self.assertEqual(self.m.read_procs(), "PS-A")
        self.m.read_chain = lambda: "CHAIN"
        self.assertEqual(self.m.read_procs(), "CHAIN")
        self.m.PS_NATIVE = False
        self.assertEqual(self.m.read_procs(), "PS-A")

    def test_the_reader_is_wired_in_and_the_sandbox_turns_it_off(self):
        self.assertIs(self.m.PS_READER, self.m.read_procs)
        src = BIN.read_text()
        self.assertEqual(src.count("\nPS_NATIVE = True\n"), 1)     # a seam line actas_testlib rewrites
        self.assertIn("PS_READER = read_procs", src)


class SandboxSeam(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {}}}

    def test_sandbox_copy_uses_the_fake_ps(self):
        self.assertFalse(self.m.PS_NATIVE)
        self.assertIn("\nPS_NATIVE = False\n", self.bin.read_text())


class RecordPruning(Sandbox):
    """With only the ancestor chain in the table, a live session elsewhere
    (another terminal's `actas run`) must keep its record."""
    manifest = {"version": 2, "profiles": {"p": {"level": "readonly"}}}

    def test_live_records_outside_the_chain_are_kept_and_dead_ones_pruned(self):
        me = os.getpid()
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.m.PS_READER = lambda: f"{me} 1 Wed Nov 11 09:00:00 2026 python3\n"
            d = self.m.SESSIONS
            d.mkdir(parents=True)
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait()
            for name in (f"{other.pid}.json", f"{dead.pid}.json", "0.json", "junk.json"):
                (d / name).write_text("{}")
            self.assertTrue(self.quiet(self.m.write_record, {"name": "p", "level": "readonly"})[0])
            left = sorted(f.name for f in d.glob("*.json"))
            self.assertIn(f"{other.pid}.json", left)       # alive, though not in the table
            self.assertIn(f"{me}.json", left)
            for gone in (f"{dead.pid}.json", "0.json", "junk.json"):
                self.assertNotIn(gone, left)
        finally:
            other.kill()
            other.wait()

    def test_pid_alive(self):
        self.assertTrue(self.m.pid_alive(os.getpid()))
        self.assertTrue(self.m.pid_alive(1))              # another user's: EPERM, alive
        self.assertFalse(self.m.pid_alive(0))
        self.assertFalse(self.m.pid_alive(2 ** 40))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(self.m.pid_alive(p.pid))


def slow(fakebin, names, delay, times):
    """Make the fakes take `delay` s each and log (name, start, end) to
    times: overlapping intervals prove the calls ran side by side, however
    slow a loaded machine makes everything else."""
    for n in names:
        f = fakebin / n
        first, rest = f.read_text().split("\n", 1)
        f.write_text(first + f"""
import time as _t, sys as _s, os as _o
_t0 = _t.time(); _t.sleep({delay})
open({str(times)!r}, "a").write("%s %r %r\\n" % (_o.path.basename(_s.argv[0]), _t0, _t.time()))
""" + rest)


def intervals(times):
    rows = [x.split() for x in times.read_text().splitlines()]
    return {r[0]: (float(r[1]), float(r[2])) for r in rows}


class ProbeConcurrency(Sandbox):
    manifest = {"version": 2, "profiles": {
        "m": {"match": ["~/code/m"], "aws": {"profile": "p"}, "gcp": {"home": "m", "project": "x"},
              "azure": {"home": "m", "subscription": "sub-2"}, "github": {"auth": "gh:ada"}}}}

    def test_status_probe_runs_the_probes_side_by_side(self):
        times = self.tmp / "times"
        slow(self.fakebin, ("aws", "gcloud", "az"), 0.5, times)
        r = self.cli("status", "--probe", "m", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        iv = intervals(times)
        self.assertEqual(sorted(iv), ["aws", "az", "gcloud"])
        self.assertLess(max(a for a, _ in iv.values()), min(b for _, b in iv.values()), iv)  # all overlap
        ids = json.loads(r.stdout)["identities"]
        self.assertEqual(sorted(m for m in ids if ids[m].get("probe")), ["aws", "azure", "gcp", "github"])
        self.assertEqual(ids["aws"]["probe"]["status"], "ok")
        self.assertEqual(ids["gcp"]["probe"], {"status": "ok"})
        self.assertEqual(ids["azure"]["probe"]["id"], "sub-1")
        self.assertEqual(ids["github"]["probe"], {"status": "ok", "login": "ada"})
        self.assertIn("az is on subscription sub-1, not sub-2", r.stderr)    # applied after the join

    def test_expired_probes_are_still_reported(self):
        r = self.cli("status", "--probe", "m", env={"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "ExpiredToken",
                                                     "FAKE_RC": "1"})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("actas login m --module gcp --module azure", r.stderr)

    def test_in_parallel_raises_in_the_caller(self):
        def boom():
            raise SystemExit(7)
        with self.assertRaises(SystemExit):
            self.m.in_parallel({"a": lambda: 1, "b": boom})
        self.assertEqual(self.m.in_parallel({"a": lambda: 1, "b": lambda: 2}), {"a": 1, "b": 2})


class RunProbeOverlap(Sandbox):
    manifest = {"version": 2, "profiles": {"r": {"aws": {"profile": "p"},
                                                 "secret_env": {"TOK": "sops:r/tok"}}}}
    secrets = {"r": {"tok": "v"}}

    def test_the_aws_probe_overlaps_secret_resolution(self):
        times = self.tmp / "times"
        slow(self.fakebin, ("aws", "sops"), 0.5, times)
        r = self.cli("run", "r", "-t", "codex", env={"FAKE_AWS_RC": "255", "FAKE_AWS_ERR": "ExpiredToken"})
        self.assertEqual(r.returncode, 0, r.stderr)
        iv = intervals(times)
        self.assertLess(max(iv["aws"][0], iv["sops"][0]), min(iv["aws"][1], iv["sops"][1]), iv)
        self.assertIn("AWS credentials for p have expired; starting anyway", r.stderr)
        self.assertEqual(json.loads(self.out.read_text())["env"]["TOK"], "v")

    def test_no_check_and_ok_probes_stay_quiet(self):
        r = self.cli("run", "r", "-t", "codex", "--no-check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c for c in self.calls("aws") if c["argv"][:1] == ["sts"]], [])
        r = self.cli("run", "r", "-t", "codex")
        self.assertNotIn("have expired", r.stderr)
        self.assertEqual(len([c for c in self.calls("aws") if c["argv"][:1] == ["sts"]]), 1)


@unittest.skipUnless(has_yaml(), "needs PyYAML")
class YamlLoader(unittest.TestCase):
    def setUp(self):
        self.m = load_module(BIN, "actas_p5_yaml")

    def test_libyaml_and_pure_python_agree_on_the_examples(self):
        import yaml
        for f in sorted((REPO / "examples").glob("*.yaml")):
            text = f.read_text()
            self.assertEqual(self.m.parse_text(text, f.name), yaml.safe_load(text) or {}, f.name)

    def test_uses_libyaml_when_present(self):
        import yaml
        if not hasattr(yaml, "CSafeLoader"):
            self.skipTest("PyYAML without libyaml")
        with mock.patch.object(yaml, "safe_load", side_effect=AssertionError("pure-Python loader used")):
            self.assertEqual(self.m.parse_text("a: [1, 2]\n", "x"), {"a": [1, 2]})

    def test_errors_keep_the_source_line_and_caret(self):
        with self.assertRaises(self.m.ManifestError) as cm:
            self.m.parse_text("version: 2\nprofiles:\n  p:\n    match: [x\n", "m.yaml")
        msg = str(cm.exception)
        self.assertIn("m.yaml: invalid YAML", msg)
        self.assertIn("^", msg)

    def test_safe_types_only(self):
        with self.assertRaises(self.m.ManifestError):
            self.m.parse_text("a: !!python/object/apply:os.system ['true']\n", "x")


if __name__ == "__main__":
    unittest.main()
