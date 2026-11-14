"""Packaging and install: one reading of manifest scalars whichever YAML
parser runs, `--version` naming the install, the skills and direnv function
carried by the program (`instructions --skill`, `setup`), `sync --clean
--all` for uninstalling, and install.sh next to a uv/pipx install."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from actas_testlib import BIN, REPO, SYSTEM_PATH, Sandbox, has_yaml, make_fakebin
from test_m3 import Base as SyncBase
from test_m3 import manifest as sync_manifest
from test_packaging import FAKE_CURL, load_hook

VERSION = None


def version():
    global VERSION
    if VERSION is None:
        import re
        VERSION = re.search(r'__version__ = "([^"]+)"', BIN.read_text()).group(1)
    return VERSION


def mikefarah_yq(path):
    """The real mikefarah/yq on the developer's PATH, or None."""
    yq = shutil.which("yq", path=path)
    if not yq:
        return None
    r = subprocess.run([yq, "--version"], capture_output=True, text=True)
    return yq if "mikefarah" in r.stdout + r.stderr else None


MANIFEST_V2 = """version: 2
profiles:
  a:
    env:
      PORT: 0755
      ZIP: 012345
      ACCOUNT: 012345678901
      HEX: 0x1F
      BIG: 1_000
      TIME: 1:30
      DAY: 2024-01-01
      VER: 1.10
      EXP: 1e3
      FLAG: on
      COUNTRY: NO
      COUNT: 42
      NEG: -3
      HALF: 1.5
      EMPTY: null
"""
EXPECTED_ENV = {"PORT": "0755", "ZIP": "012345", "ACCOUNT": "012345678901", "HEX": "0x1F",
                "BIG": "1_000", "TIME": "1:30", "DAY": "2024-01-01", "VER": "1.10", "EXP": "1e3",
                "FLAG": "on", "COUNTRY": "NO", "COUNT": "42", "NEG": "-3", "HALF": "1.5", "EMPTY": None}


class ScalarRules(Sandbox):
    def test_scalar_value(self):
        sv = self.m.scalar_value
        self.assertEqual([sv(x) for x in ("42", "-3", "0", "1.5", "-0.25")], [42, -3, 0, 1.5, -0.25])
        for raw in ("0755", "012345678901", "0x1F", "1_000", "1.10", "1e3", "+5", "-0", "00", ".5"):
            self.assertEqual(sv(raw), raw, raw)

    def test_unwrap_yq(self):
        w = self.m.YQ_SCALAR
        got = self.m.unwrap_yq({"version": {w: "2"}, "l": [{w: "0755"}, "x", True],
                                "m": {"a": {w: "2024-01-01"}, "b": {w: "1.5"}, "keep": {w: 1, "o": 2}}})
        self.assertEqual(got, {"version": 2, "l": ["0755", "x", True],
                               "m": {"a": "2024-01-01", "b": 1.5, "keep": {w: 1, "o": 2}}})

    def test_fake_yq_gets_the_typed_expression(self):
        """Without PyYAML, parse_text asks yq for the source text of numbers and dates."""
        yq = self.fakebin / "yq"
        yq.write_text("#!%s\nimport json, sys\nexpr = sys.argv[-1]\n"
                      "if 'tag ==' in expr:\n"
                      "    print(json.dumps({'version': {%r: '2'}, 'env': {'A': {%r: '0755'}}}))\n"
                      "else:\n"
                      "    print(json.dumps({'version': 2, 'env': {'A': 493}}))\n"
                      % (sys.executable, self.m.YQ_SCALAR, self.m.YQ_SCALAR))
        yq.chmod(0o755)
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.assertEqual(self.m.parse_text("version: 2\nenv: {A: 0755}\n", "x"),
                             {"version": 2, "env": {"A": "0755"}})


@unittest.skipUnless(has_yaml(), "needs PyYAML")
class PyYamlScalars(Sandbox):
    def test_env_values_stay_as_written(self):
        self.write_manifest(MANIFEST_V2)
        cfg, err = self.quiet(self.m.load)
        self.assertEqual([p for _, p, _ in cfg.problems.items], ["profiles.a.env.HALF"], err)   # D18: floats warn
        self.assertEqual(cfg.profiles["a"]["env"], EXPECTED_ENV)

    def test_true_false_are_still_errors(self):
        self.write_manifest("version: 2\nprofiles:\n  a:\n    env: {FLAG: true, OTHER: False}\n")
        errs = [m for _, _, m in self.m.load(strict=False, quiet=True).problems.errors()]
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(all("quote the value" in m for m in errs), errs)

    def test_booleans_numbers_and_settings_keep_their_types(self):
        doc = self.m.parse_text("version: 2\na: [true, TRUE, False, 8080, 0.5, ~, yes, off]\n", "x")
        self.assertEqual(doc, {"version": 2, "a": [True, True, False, 8080, 0.5, None, "yes", "off"]})
        self.assertIs(type(doc["a"][3]), int)

    def test_v1_file_is_read_as_before(self):
        """v1 keeps YAML 1.1 (what the v1 binary read): yes is a boolean, 0755 octal."""
        self.write_manifest("version: 1\nenvironments:\n  a:\n    env: {FLAG: yes, PORT: 0755}\n")
        cfg = self.m.load(strict=False, quiet=True)
        self.assertEqual(cfg.version, 1)
        self.assertEqual(cfg.profiles["a"]["env"], {"FLAG": "True", "PORT": "493"})
        self.write_manifest("environments:\n  a:\n    env: {FLAG: yes}\n")        # no version: v1 too
        self.assertEqual(self.m.load(strict=False, quiet=True).profiles["a"]["env"], {"FLAG": "True"})


class SameWithEveryParser(Sandbox):
    """PyYAML and mikefarah/yq give the same document."""

    def setUp(self):
        dev_path = os.environ.get("PATH", "")
        super().setUp()
        yq = mikefarah_yq(self._saved_env.get("PATH", dev_path))
        if not (yq and has_yaml()):
            self.skipTest("needs PyYAML and mikefarah/yq")
        (self.fakebin / "yq").symlink_to(yq)

    def test_parity(self):
        texts = [MANIFEST_V2, "version: 2\nx: [true, 8080, 0.5, ~, '0755', 2024-01-01T10:00:00Z]\n"]
        for text in texts:
            with self.subTest(text=text[:30]):
                with_pyyaml = self.m.parse_text(text, "x")
                with mock.patch.dict(sys.modules, {"yaml": None}):
                    with_yq = self.m.parse_text(text, "x")
                self.assertEqual(with_pyyaml, with_yq)
        # v1 goes through plain yq, as the v1 binary did. yq itself changed how
        # it reads 0755 (493 before v4.40ish, 755 after), so compare with it.
        v1 = "version: 1\nenvironments:\n  a:\n    env: {PORT: 0755, N: 7}\n"
        plain = json.loads(subprocess.run([str(self.fakebin / "yq"), "-o", "json", "."], input=v1,
                                          capture_output=True, text=True, check=True).stdout)
        self.assertIn(plain["environments"]["a"]["env"]["PORT"], (493, 755))
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.assertEqual(self.m.parse_text(v1, "x")["environments"]["a"]["env"],
                             plain["environments"]["a"]["env"])


class Version(Sandbox):
    def test_copied_single_file_prints_the_version_alone(self):
        r = self.cli("--version")
        self.assertEqual((r.returncode, r.stdout), (0, f"actas {version()}\n"), r.stderr)

    @unittest.skipUnless((REPO / ".git").exists() and shutil.which("git"), "needs a git checkout")
    def test_checkout_names_its_commit(self):
        r = subprocess.run([sys.executable, str(BIN), "--version"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^actas %s \(git [0-9a-f]{4,}(\+changes)?\)\n$" % version().replace(".", r"\."))

    def test_package_names_itself_and_its_commit(self):
        site = self.tmp / "site"
        info = site / f"actas-{version()}.dist-info"
        info.mkdir(parents=True)
        (site / "actas_cli.py").write_text(load_hook().package_source(BIN.read_text(encoding="utf-8")))
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: actas\nVersion: {version()}\n")
        code = "import sys, actas_cli; sys.argv = ['actas', '--version']; actas_cli.main()"
        env = dict(os.environ, PYTHONPATH=str(site))
        run = lambda: subprocess.run([sys.executable, "-c", code], cwd=str(self.tmp),  # noqa: E731
                                     capture_output=True, text=True, env=env)
        r = run()
        self.assertEqual(r.stdout, f"actas {version()} (package)\n", r.stderr)
        (info / "direct_url.json").write_text(json.dumps(
            {"url": "https://github.com/example-org/actas",
             "vcs_info": {"vcs": "git", "commit_id": "0123456789abcdef0123456789abcdef01234567"}}))
        r = run()
        self.assertEqual(r.stdout, f"actas {version()} (package, git 0123456)\n", r.stderr)


class BundledFiles(Sandbox):
    def test_repo_copies_match_the_program(self):
        for name, text in self.m.SKILLS.items():
            self.assertEqual((REPO / "skills" / name / "SKILL.md").read_text(), text,
                             f"skills/{name}/SKILL.md differs from SKILLS[{name!r}] in bin/actas; regenerate "
                             f"it: bin/actas instructions --skill {name} > skills/{name}/SKILL.md")
        self.assertEqual((REPO / "contrib" / "direnvrc").read_text(), self.m.DIRENVRC)
        self.assertEqual(sorted(p.name for p in (REPO / "skills").iterdir() if p.is_dir()),
                         sorted(self.m.SKILLS))

    def test_instructions_skill_prints_it(self):
        for args, name in ((["--skill"], "actas"), (["--skill", "aws-handoff"], "aws-handoff")):
            r = self.cli("instructions", *args)
            self.assertEqual((r.returncode, r.stdout), (0, self.m.SKILLS[name]), r.stderr)
        r = self.cli("instructions", "--skill", env={"CLAUDECODE": "1"})       # printing is agent-safe
        self.assertEqual((r.returncode, r.stdout), (0, self.m.SKILLS["actas"]), r.stderr)
        self.assertEqual(self.cli("instructions", "--skill", "--direnv").returncode, 2)
        self.assertEqual(self.cli("instructions", "--skill", "nope").returncode, 2)


class Setup(Sandbox):
    def skill(self, name="actas", base=None):
        return (base or self.home / ".claude" / "skills") / name / "SKILL.md"

    def test_skill_install_update_and_refusals(self):
        dest = self.skill()
        r = self.cli("setup", "skill", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would write ~/.claude/skills/actas/SKILL.md", r.stdout)
        self.assertFalse(dest.exists())
        r = self.cli("setup", "skill")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(dest.read_text(), self.m.SKILLS["actas"])
        self.assertIn("up to date", self.cli("setup", "skill").stdout)
        dest.write_text("---\nname: actas\ndescription: an older copy\n---\nold\n")      # ours: replaced
        r = self.cli("setup", "skill")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(dest.read_text(), self.m.SKILLS["actas"])
        dest.write_text("my own notes\n")                                             # not ours
        r = self.cli("setup", "skill")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--force", r.stderr)
        self.assertEqual(dest.read_text(), "my own notes\n")
        self.assertEqual(self.cli("setup", "skill", "--force").returncode, 0)
        self.assertEqual(dest.read_text(), self.m.SKILLS["actas"])

    def test_never_writes_through_a_symlink(self):
        checkout = self.tmp / "checkout" / "skills" / "actas"
        checkout.mkdir(parents=True)
        (checkout / "SKILL.md").write_text("checkout copy\n")
        base = self.home / ".claude" / "skills"
        base.mkdir(parents=True)
        (base / "actas").symlink_to(checkout)
        r = self.cli("setup", "skill", "--force")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is a symlink", r.stderr)
        self.assertEqual((checkout / "SKILL.md").read_text(), "checkout copy\n")
        (checkout / "SKILL.md").write_text(self.m.SKILLS["actas"])                  # current: fine
        r = self.cli("setup", "skill")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("up to date", r.stdout)

    def test_aws_handoff_goes_into_the_project_and_dir_option(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        r = self.cli("setup", "skill", "aws-handoff", cwd=str(proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.skill("aws-handoff", proj / ".claude" / "skills").read_text(),
                         self.m.SKILLS["aws-handoff"])
        self.assertIn("commit it", r.stderr)
        other = self.tmp / "cfgdir" / "skills"
        self.assertEqual(self.cli("setup", "skill", "--dir", str(other)).returncode, 0)
        self.assertEqual(self.skill(base=other).read_text(), self.m.SKILLS["actas"])
        self.assertEqual(self.cli("setup", "skill", "nope").returncode, 2)
        self.assertEqual(self.cli("setup", "skill", "--file", "x").returncode, 2)

    def test_direnv(self):
        xdg = self.tmp / "xdgcfg"
        rc = xdg / "direnv" / "direnvrc"
        rc.parent.mkdir(parents=True)
        rc.write_text(self.m.DIRENVRC)                                  # an earlier pasted copy
        env = {"XDG_CONFIG_HOME": str(xdg)}
        r = self.cli("setup", "direnv", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((xdg / "direnv" / "lib" / "actas.sh").read_text(), self.m.DIRENVRC)
        self.assertIn("also defines use_actas", r.stderr)
        self.assertIn("up to date", self.cli("setup", "direnv", env=env).stdout)
        f = self.tmp / "rc.sh"
        self.assertEqual(self.cli("setup", "direnv", "--file", str(f)).returncode, 0)
        self.assertEqual(f.read_text(), self.m.DIRENVRC)
        self.assertEqual(self.cli("setup", "direnv", "--dir", "x").returncode, 2)

    def test_human_only(self):
        for args in (["setup", "skill"], ["setup", "direnv"]):
            r = self.cli(*args, env={"CLAUDECODE": "1"})
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("reason=human_only", r.stderr.splitlines()[0])
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".config" / "direnv").exists())

    def test_doctor_names_a_stale_copy(self):
        dest = self.skill()
        dest.parent.mkdir(parents=True)
        dest.write_text("---\nname: actas\n---\nfrom an older actas\n")
        self.write_manifest({"version": 2, "profiles": {"a": {}}})
        r = self.cli("doctor", "--json")
        checks = [c for c in json.loads(r.stdout)["checks"] if c["name"] == "skill"]
        self.assertEqual(len(checks), 1, r.stdout)
        self.assertEqual(checks[0]["status"], "info")
        self.assertIn("actas setup skill --dir ~/.claude/skills", checks[0]["detail"])
        dest.write_text(self.m.SKILLS["actas"])
        r = self.cli("doctor", "--json")
        self.assertEqual([c for c in json.loads(r.stdout)["checks"] if c["name"] == "skill"], [])


class CleanAll(SyncBase):
    def test_removes_what_sync_wrote_everywhere(self):
        f = self.sf("client-a")
        f.parent.mkdir()
        f.write_text(json.dumps({"env": {"KEEP": "1"}}))
        r = self.cli("sync")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.sf("personal").exists())
        self.assertEqual(len(self.ledger()), 2)
        r = self.cli("sync", "--clean", "--all", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"would clean {f}", r.stdout)
        self.assertIn(f"would clean {self.sf('personal')}", r.stdout)
        self.assertIn("apiKeyHelper", f.read_text())
        (self.root / "environments.yaml").write_text("version: 2\nprofiles: [broken\n")   # needs no manifest
        r = self.cli("sync", "--clean", "--all")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read("client-a"), {"env": {"KEEP": "1"}})          # the user's keys stay
        self.assertFalse(self.sf("personal").exists())
        self.assertEqual(self.ledger(), {})
        self.assertIn("README: Uninstall", r.stderr)
        audit = (self.tmp / "state" / "audit.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(audit[-1]).get("reason"), "clean-all")

    def test_usage_and_human_only(self):
        for args in (["--all"], ["--clean", "--all", "client-a"], ["--clean", "--all", "--include-confirm"]):
            r = self.cli("sync", *args)
            self.assertEqual(r.returncode, 2, (args, r.stderr))
        self.assertEqual(self.cli("sync").returncode, 0)
        r = self.cli("sync", "--clean", "--all", env={"CLAUDECODE": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertTrue(self.sf("client-a").exists())

    def test_force_guard_still_applies(self):
        self.write_manifest(sync_manifest())
        self.assertEqual(self.cli("sync", "client-a").returncode, 0)
        claude = self.home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}}))
        r = self.cli("sync", "--clean", "--all")
        self.assertTrue(self.sf("client-a").exists())            # it hides the global routing: kept
        self.assertIn("--force", r.stderr)
        self.assertIn("1 synced file(s) kept", r.stderr)
        self.assertEqual(self.cli("sync", "--clean", "--all", "--force").returncode, 0)
        self.assertFalse(self.sf("client-a").exists())
        self.assertEqual(self.ledger(), {})


class InstallBesideUv(unittest.TestCase):
    """install.sh never replaces a uv/pipx install's link, and says nothing
    about a ccenv link that already is an alias of actas."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        fakebin = make_fakebin(self.tmp / "fakebin")
        release = self.tmp / "release"
        release.mkdir()
        self.payload = BIN.read_bytes()
        (release / "actas").write_bytes(self.payload)
        (release / "SHA256SUMS").write_text(f"{hashlib.sha256(self.payload).hexdigest()}  actas\n")
        (fakebin / "curl").write_text(FAKE_CURL)
        (fakebin / "curl").chmod(0o755)
        (fakebin / "python3").symlink_to(sys.executable)
        self.env = {"HOME": str(self.home), "PATH": f"{fakebin}:{SYSTEM_PATH}", "TMPDIR": str(self.tmp),
                    "LANG": "C", "FAKE_RELEASE": str(release), "FAKE_CURL_LOG": str(self.tmp / "curl.log"),
                    "ACTAS_ROOT": str(self.tmp / "cfg"), "ACTAS_STATE": str(self.tmp / "state")}
        self.dir = self.home / ".local" / "bin"
        self.dir.mkdir(parents=True)

    def install(self, *args):
        return subprocess.run(["sh", str(REPO / "install.sh")] + list(args), cwd=str(self.tmp),
                              capture_output=True, text=True, env=self.env)

    def test_uv_and_pipx_links_are_refused_even_with_force(self):
        for tool in (self.home / ".local/share/uv/tools/actas/bin", self.tmp / "custom/tools/actas/bin",
                     self.home / ".local/pipx/venvs/actas/bin"):
            with self.subTest(tool=str(tool)):
                tool.mkdir(parents=True, exist_ok=True)
                (tool / "actas").write_text("uv's actas")
                dest = self.dir / "actas"
                if dest.is_symlink():
                    dest.unlink()
                dest.symlink_to(tool / "actas")
                for extra in ([], ["--force"]):
                    r = self.install(*extra)
                    self.assertEqual(r.returncode, 1)
                    self.assertIn("managed by uv or pipx", r.stderr)
                    self.assertIn("uv tool upgrade actas", r.stderr)
                    self.assertTrue(dest.is_symlink())
                    self.assertEqual((tool / "actas").read_text(), "uv's actas")

    def test_other_symlink_message_does_not_assume_a_checkout(self):
        other = self.tmp / "elsewhere" / "actas"
        other.parent.mkdir()
        other.write_text("x")
        (self.dir / "actas").symlink_to(other)
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("If it points into a git checkout", r.stderr)
        self.assertIn("--force", r.stderr)

    def test_ccenv_notes(self):
        (self.dir / "ccenv").symlink_to("actas")                      # already an alias
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ccenv", r.stdout)
        (self.dir / "ccenv").unlink()
        (self.dir / "ccenv").symlink_to(self.home / ".local/share/uv/tools/actas/bin/ccenv")
        r = self.install()
        self.assertIn("alias from a uv/pipx install", r.stdout)
        self.assertNotIn("pre-rename", r.stdout)
        (self.dir / "ccenv").unlink()
        (self.dir / "ccenv").write_text("#!/bin/sh\n")                # a pre-rename copy
        r = self.install()
        self.assertIn("old (pre-rename) install", r.stdout)


class Ci(unittest.TestCase):
    def test_one_leg_installs_pyyaml(self):
        """The README and examples/ tests skip without a YAML parser."""
        self.assertIn("pip install pyyaml", (REPO / ".github/workflows/test.yml").read_text())
        self.assertIn("pip install pyyaml", (REPO / ".github/workflows/release.yml").read_text())


if __name__ == "__main__":
    unittest.main()
