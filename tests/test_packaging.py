"""Packaging: the wheel build hook (hatch_build.py) and install.sh.

install.sh runs against a fake `curl` that serves files from a temp
"release" directory, with HOME, ACTAS_ROOT, ACTAS_STATE and the XDG dirs in
temp dirs and the fake gh/aws/sops binaries first on PATH. Nothing touches
the network. A real `uv build` is opt-in: ACTAS_TEST_BUILD=1 (CI's package
job builds the wheel itself).
"""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from actas_testlib import BIN, REPO, SYSTEM_PATH, make_fakebin

INSTALL_SH = REPO / "install.sh"

FAKE_CURL = r'''#!/bin/sh
# fake curl: serves $FAKE_RELEASE/<last URL segment>; logs URLs to $FAKE_CURL_LOG
out=; url=
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift 2 ;;
    --proto|--proto-redir|--retry) shift 2 ;;
    -*) shift ;;
    *) url=$1; shift ;;
  esac
done
echo "$url" >> "$FAKE_CURL_LOG"
src="$FAKE_RELEASE/${url##*/}"
[ -f "$src" ] || exit 22
cat "$src" > "$out"
'''


def load_hook():
    """hatch_build.py with a stub hatchling, so the test needs no build deps."""
    names = ["hatchling", "hatchling.builders", "hatchling.builders.hooks",
             "hatchling.builders.hooks.plugin", "hatchling.builders.hooks.plugin.interface"]
    saved = {n: sys.modules.get(n) for n in names}
    for n in names:
        sys.modules[n] = types.ModuleType(n)
    sys.modules[names[-1]].BuildHookInterface = object
    try:
        # exec, not import: no __pycache__ is written into the repo
        mod = types.ModuleType("actas_hatch_build")
        path = REPO / "hatch_build.py"
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
        return mod
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = make_fakebin(self.tmp / "fakebin")
        self.env = {
            "HOME": str(self.home), "PATH": f"{self.bin}:{SYSTEM_PATH}",
            "ACTAS_ROOT": str(self.tmp / "cfg"), "ACTAS_STATE": str(self.tmp / "state"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"), "XDG_STATE_HOME": str(self.tmp / "xstate"),
            "TMPDIR": str(self.tmp), "LANG": "C", "NO_COLOR": "1",
        }


class BuildHook(Isolated):
    def test_package_source_changes_only_the_build_line(self):
        hook = load_hook()
        src = BIN.read_text(encoding="utf-8")
        out = hook.package_source(src)
        diff = [(a, b) for a, b in zip(src.splitlines(), out.splitlines()) if a != b]
        self.assertEqual(len(src.splitlines()), len(out.splitlines()))
        self.assertEqual(len(diff), 1)
        self.assertTrue(diff[0][0].startswith('BUILD = "script"'))
        self.assertTrue(diff[0][1].startswith('BUILD = "package"'))
        with self.assertRaises(RuntimeError):
            hook.package_source(src.replace('BUILD = "script"', 'BUILD = "other"'))

    def test_version_is_single_sourced(self):
        # hatchling's default version pattern must find __version__ in bin/actas.
        m = re.search(r'(?im)^(__version__|VERSION) *= *([\'"])v?(?P<version>.+?)\2',
                      BIN.read_text(encoding="utf-8"))
        self.assertIsNotNone(m)
        self.assertIn('actas = "actas_cli:main"', (REPO / "pyproject.toml").read_text())

    def test_generated_module_runs_as_console_script(self):
        pkg = self.tmp / "site"
        pkg.mkdir()
        (pkg / "actas_cli.py").write_text(load_hook().package_source(BIN.read_text(encoding="utf-8")))
        code = ("import sys, actas_cli; print(actas_cli.BUILD); "
                "sys.argv = ['actas', '--version']; actas_cli.main()")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(self.tmp), capture_output=True,
                           text=True, env=dict(self.env, PYTHONPATH=str(pkg)))
        version = re.search(r'__version__ = "([^"]+)"', BIN.read_text()).group(1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["package", "actas", version, "(package)"])

    @unittest.skipUnless(os.environ.get("ACTAS_TEST_BUILD") and shutil.which("uv"),
                         "set ACTAS_TEST_BUILD=1 (needs uv and network for hatchling)")
    def test_uv_build(self):
        r = subprocess.run(["uv", "build", "--wheel", "-o", str(self.tmp / "dist"), str(REPO)],
                           capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(list((self.tmp / "dist").glob("actas-*.whl"))), 1)


class InstallScript(Isolated):
    def setUp(self):
        super().setUp()
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.payload = BIN.read_bytes()
        (self.release / "actas").write_bytes(self.payload)
        self.write_sums(hashlib.sha256(self.payload).hexdigest())
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        (self.bin / "python3").symlink_to(sys.executable)
        self.curl_log = self.tmp / "curl.log"
        self.env.update(FAKE_RELEASE=str(self.release), FAKE_CURL_LOG=str(self.curl_log))
        self.dest = self.home / ".local" / "bin" / "actas"

    def write_sums(self, digest):
        (self.release / "SHA256SUMS").write_text(f"{digest}  actas\n{'0' * 64}  install.sh\n")

    def install(self, *args, shell="sh"):
        return subprocess.run([shell, str(INSTALL_SH)] + list(args), cwd=str(self.tmp),
                              capture_output=True, text=True, env=self.env)

    def urls(self):
        return self.curl_log.read_text().split()

    def leftovers(self):
        return [p.name for p in self.dest.parent.iterdir() if p.name.startswith(".actas.")]

    def test_installs_latest_with_checksum(self):
        shells = ["sh"] + (["dash"] if shutil.which("dash") else [])
        for sh in shells:
            with self.subTest(shell=sh):
                r = self.install(shell=sh)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.dest.read_bytes(), self.payload)
                self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o755)
                self.assertIn("sha256 ok", r.stdout)
                self.assertIn("gh attestation verify", r.stdout)
                self.assertIn("actas 2.", r.stdout)       # ran the installed file
                self.assertEqual(self.leftovers(), [])
        self.assertIn("https://github.com/TsenkoTsenkov/actas/releases/latest/download/actas",
                      self.urls())

    def test_given_version_and_upgrade_in_place(self):
        self.dest.parent.mkdir(parents=True)
        self.dest.write_text("old")
        r = self.install("--version", "2.0.0", "--repo", "example-org/actas")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.dest.read_bytes(), self.payload)
        self.assertEqual(self.urls()[:2], [
            "https://github.com/example-org/actas/releases/download/v2.0.0/actas",
            "https://github.com/example-org/actas/releases/download/v2.0.0/SHA256SUMS"])

    # F43: `latest` never resolves to a prerelease; say how to install one.
    def test_missing_latest_release_hints_at_prereleases(self):
        (self.release / "actas").unlink()
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("for a prerelease pass --version TAG", r.stderr)
        r = self.install("--version", "v2.0.0")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("prerelease", r.stderr)

    def test_checksum_mismatch_installs_nothing(self):
        self.write_sums("a" * 64)
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum mismatch", r.stderr)
        self.assertFalse(self.dest.exists())

    def test_missing_checksum_entry_installs_nothing(self):
        (self.release / "SHA256SUMS").write_text(f"{'0' * 64}  other\n")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.dest.exists())

    def test_symlink_refused_without_force_then_link_replaced(self):
        checkout = self.tmp / "checkout-actas"
        checkout.write_text("checkout copy")
        self.dest.parent.mkdir(parents=True)
        self.dest.symlink_to(checkout)
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("symlink", r.stderr)
        self.assertTrue(self.dest.is_symlink())
        self.assertEqual(checkout.read_text(), "checkout copy")
        r = self.install("--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.dest.is_symlink())
        self.assertEqual(self.dest.read_bytes(), self.payload)
        self.assertEqual(checkout.read_text(), "checkout copy")   # never written through
        self.assertEqual(self.leftovers(), [])

    def test_bad_arguments(self):
        for args in (["--version", "v1;rm -rf x"], ["--version", "main"],
                     ["--repo", "no-slash"], ["--version"], ["--nope"]):
            with self.subTest(args=args):
                r = self.install(*args)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("error", r.stderr)
        self.assertFalse(self.dest.exists())

    def test_custom_dir_not_on_path(self):
        d = self.tmp / "tools"
        r = self.install("--dir", str(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((d / "actas").read_bytes(), self.payload)
        self.assertIn("not on your PATH", r.stdout)

    def test_posix_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(INSTALL_SH)]).returncode, 0)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-s", "sh", str(INSTALL_SH)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
