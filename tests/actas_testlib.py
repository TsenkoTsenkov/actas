"""Shared test support: a sandbox per test and fake backend binaries.

Every Sandbox test gets its own HOME, ACTAS_ROOT, ACTAS_STATE, XDG_CACHE_HOME
and XDG_STATE_HOME under a temp dir, and a PATH of fake binaries plus the
system dirs (for git and env), so no test can reach a real gh, aws or sops
login, or the user's ~/.config/actas. The fakes log every call (argv and
stdin) as JSON lines to $FAKE_LOG.

The sandbox runs a copy of bin/actas (self.bin, also what the in-process
module self.m is loaded from, so helper strings name it) whose two fixed-place
seams, PS_BINS and USER_STATE (the passwd-home state dir holding the fixed
sessions dirs), point at the fake ps and a temp dir, and whose PS_NATIVE (the
macOS sysctl ancestor reader) is off: no test reads the real
process table's agent names or the developer's ~/.local/state/actas/sessions
(or ~/.local/state/ccenv/sessions, which a pre-rename install that never moved
its state dir still uses), and the shipped binary has no override for them.

Fake behaviour:
  gh       `auth token --user U [--hostname H]` prints gho_U unless U is in
           $FAKE_GH_MISSING (comma list); `auth status` lists $FAKE_GH_USERS.
           Like real gh, a GH_CONFIG_DIR without hosts.yml means "not logged
           in" (auth token and auth status fail); GH_TOKEN wins over it.
  sops     reads $ACTAS_ROOT/secrets.enc.yaml as *plain JSON*:
           `-d --extract '["a"]["b"]' F`, `-d --output-type json F`,
           `set [--value-stdin] F PATH [VALUE]`, `set --help` (mentions
           --value-stdin unless $FAKE_SOPS_OLD), `--config C -e -i F`.
  aws      `sts get-caller-identity` exits $FAKE_AWS_RC (default 0).
  claude, codex, gemini
           write {"argv", "env", "settings"} as JSON to $FAKE_OUT.
           codex `login --with-api-key` logs the key it reads on stdin.
  security `find-generic-password -s S -a A -w` and `op read REF` answer from
           $FAKE_STORE (JSON {"keychain": {"S/A": v}, "op": {REF: v}});
           `add-generic-password ... -w` stores a line read from stdin.
  gcloud, az
           log and exit $FAKE_RC (default 0); `az account show` prints
           {"id": $FAKE_AZ_SUB or "sub-1", ...}.
  aws      also: $FAKE_AWS_ERR goes to stderr when $FAKE_AWS_RC != 0;
           `configure export-credentials` prints fake temporary credentials.
  gh       also: `api user` prints the login behind GH_TOKEN (gho_U -> U);
           `auth status --active` lists only the first of $FAKE_GH_USERS;
           `auth login` and `auth switch` log and succeed.
  ps       the real `ps` output with agent process names masked, so tests
           behave the same inside an agent session; $FAKE_PS_AS="pid:name,..."
           renames processes (e.g. the test process to "claude"), and
           $FAKE_PS_FAIL makes it fail. It does not log.
  wl-copy  logs what it receives (a clipboard stand-in).
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin" / "actas"
V1_BIN = REPO / "tests" / "compat" / "ccenv_v1"
SYSTEM_PATH = "/usr/bin:/bin"

FAKE_COMMON = r'''
import json, os, sys
def log(stdin=None):
    p = os.environ.get("FAKE_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps({"bin": os.path.basename(sys.argv[0]), "argv": sys.argv[1:],
                                "stdin": stdin}) + "\n")
'''

FAKES = {
    "gh": r'''
args = sys.argv[1:]
log()
cfg = os.environ.get("GH_CONFIG_DIR")
logged_out = bool(cfg) and not os.path.exists(os.path.join(cfg, "hosts.yml"))
if args[:2] == ["auth", "status"] and os.environ.get("GH_TOKEN"):
    print("  Logged in to github.com account token-user (GH_TOKEN)")
elif args[:2] in (["auth", "status"], ["auth", "token"]) and logged_out:
    sys.stderr.write("You are not logged into any GitHub hosts. To log in, run: gh auth login\n")
    sys.exit(1)
elif args[:2] == ["auth", "token"]:
    user = args[args.index("--user") + 1] if "--user" in args else "active"
    if user in os.environ.get("FAKE_GH_MISSING", "").split(","):
        sys.stderr.write("no oauth token found for github.com account " + user + "\n")
        sys.exit(1)
    print("gho_" + user)
elif args[:2] == ["api", "user"]:
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GH_ENTERPRISE_TOKEN") or ""
    if not tok:
        sys.exit(1)
    print(tok[4:] if tok.startswith("gho_") else "token-user")
elif args[:2] == ["auth", "status"]:
    users = [u for u in os.environ.get("FAKE_GH_USERS", "").split(",") if u]
    for u in users[:1] if "--active" in args else users:
        print("  Logged in to github.com account " + u + " (keyring)")
elif args[:2] in (["auth", "login"], ["auth", "switch"]):
    pass
else:
    sys.exit(1)
''',
    "sops": r'''
args = sys.argv[1:]
stdin = sys.stdin.read() if "--value-stdin" in args else None
log(stdin)
def load(f):
    t = open(f).read()
    try:
        return json.loads(t)
    except ValueError:
        return {"_meta": {"managed_by": "actas"}}
def keys(path):
    return [k.strip('"') for k in path.strip("[]").split("][")]
if args[:1] == ["set"] and "--help" in args:
    print("--value-file  read the value from a file")
    if not os.environ.get("FAKE_SOPS_OLD"):
        print("--value-stdin  read the value from stdin")
elif args[:1] == ["set"]:
    rest = [a for a in args[1:] if a != "--value-stdin"]
    f, path = rest[0], rest[1]
    value = json.loads(stdin if stdin is not None else rest[2])
    data = load(f)
    a, b = keys(path)
    data.setdefault(a, {})[b] = value
    open(f, "w").write(json.dumps(data))
elif "-e" in args and "-i" in args:
    f = args[-1]
    open(f, "w").write(json.dumps(load(f)))
elif "-d" in args and "--extract" in args:
    a, b = keys(args[args.index("--extract") + 1])
    data = load(args[-1])
    if a not in data or b not in data[a]:
        sys.stderr.write("component not found\n")
        sys.exit(1)
    sys.stdout.write(str(data[a][b]))
elif "-d" in args and "--output-type" in args:
    sys.stdout.write(json.dumps(load(args[-1])))
else:
    sys.exit(2)
''',
    "aws": r'''
args = sys.argv[1:]
log()
if args[:2] == ["sts", "get-caller-identity"]:
    rc = int(os.environ.get("FAKE_AWS_RC", "0"))
    if rc == 0:
        print(json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:x"}))
    else:
        sys.stderr.write(os.environ.get("FAKE_AWS_ERR", ""))
    sys.exit(rc)
if args[:2] == ["configure", "export-credentials"]:
    print(json.dumps({"Version": 1, "AccessKeyId": "ASIAFAKE", "SecretAccessKey": "aws-SECRET-key",
                      "SessionToken": "aws-SECRET-session", "Expiration": "2026-01-01T00:00:00Z"}))
sys.exit(0)
''',
    "claude": r'''
log()
argv = sys.argv[1:]
settings = None
if "--settings" in argv:
    with open(argv[argv.index("--settings") + 1]) as f:
        settings = json.load(f)
out = os.environ.get("FAKE_OUT")
if out:
    with open(out, "w") as f:
        json.dump({"argv": argv, "env": dict(os.environ), "settings": settings}, f)
''',
}
FAKES["gemini"] = FAKES["claude"]
FAKES["codex"] = r'''
if "--with-api-key" in sys.argv:
    log(sys.stdin.read())
    sys.exit(0)
''' + FAKES["claude"]
FAKES["security"] = r'''
args = sys.argv[1:]
store_path = os.environ.get("FAKE_STORE", "/nonexistent")
store = json.load(open(store_path)) if os.path.exists(store_path) else {}
val = lambda flag: args[args.index(flag) + 1]
if args[:1] == ["find-generic-password"]:
    log()
    v = store.get("keychain", {}).get(val("-s") + "/" + val("-a"))
    if v is None:
        sys.stderr.write("security: SecKeychainSearchCopyNext: The specified item could not be found.\n")
        sys.exit(44)
    print(v)
elif args[:1] == ["add-generic-password"]:
    v = sys.stdin.readline().rstrip("\n")
    log(v)
    store.setdefault("keychain", {})[val("-s") + "/" + val("-a")] = v
    json.dump(store, open(store_path, "w"))
else:
    log()
    sys.exit(1)
'''
FAKES["op"] = r'''
log()
store_path = os.environ.get("FAKE_STORE", "/nonexistent")
store = json.load(open(store_path)) if os.path.exists(store_path) else {}
v = store.get("op", {}).get(sys.argv[-1]) if sys.argv[1:2] == ["read"] else None
if v is None:
    sys.stderr.write("[ERROR] could not read secret\n")
    sys.exit(1)
print(v)
'''
for _name in ("gcloud", "az"):
    FAKES[_name] = "log()\nsys.exit(int(os.environ.get('FAKE_RC', '0')))\n"
FAKES["az"] = r'''
if sys.argv[1:3] == ["account", "show"] and os.environ.get("FAKE_RC", "0") == "0":
    print(json.dumps({"id": os.environ.get("FAKE_AZ_SUB", "sub-1"), "tenant": "t-1", "user": "ada"}))
''' + FAKES["az"]
FAKES["wl-copy"] = "log(sys.stdin.read())\n"
AGENT_NAMES = ("claude", "codex", "gemini", "agy", "cursor-agent", "copilot", "aider", "opencode",
               "goose", "amp", "kiro-cli")
FAKES["ps"] = r'''
import subprocess
if os.environ.get("FAKE_PS_FAIL"):
    sys.exit(1)
real = "/bin/ps" if os.path.exists("/bin/ps") else "/usr/bin/ps"
out = subprocess.run([real] + sys.argv[1:], capture_output=True, text=True,
                     env=dict(os.environ, LC_ALL="C")).stdout
rename = dict(x.split(":", 1) for x in os.environ.get("FAKE_PS_AS", "").split(",") if ":" in x)
for line in out.splitlines():
    f = line.split(None, 7)
    if len(f) == 8:
        comm = f[7]
        if os.path.basename(comm) in %r or "Claude.app/" in comm:
            comm = "/masked/agent"
        comm = rename.get(f[0], comm)
        line = " ".join(f[:7] + [comm])
    print(line)
''' % (AGENT_NAMES,)


def make_fakebin(d, only=None):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for name, body in FAKES.items():
        if only is not None and name not in only:
            continue
        f = d / name
        f.write_text(f"#!{sys.executable}\n" + FAKE_COMMON + body)
        f.chmod(0o755)
    return d


SEAMS = ('PS_BINS = ("/bin/ps", "/usr/bin/ps")',
         'USER_STATE = Path(passwd_home() or HOME) / ".local" / "state"',
         'PS_NATIVE = True')


def sandbox_bin(dest, ps, sessions):
    """A copy of bin/actas whose PS_BINS / USER_STATE lines name ps and the
    state dir above sessions (<user state>/actas/sessions, or the legacy
    <user state>/ccenv/sessions while only that one exists; both derive
    from the same line), and whose PS_NATIVE is off, so the process table
    always comes from that ps (the macOS sysctl reader would see the real,
    unmasked agent ancestors)."""
    src = BIN.read_text(encoding="utf-8")
    user_state = Path(sessions).parent.parent
    for old, new in zip(SEAMS, (f"PS_BINS = ({str(ps)!r},)", f"USER_STATE = Path({str(user_state)!r})",
                                "PS_NATIVE = False")):
        assert src.count(old) == 1, f"seam line changed in bin/actas: {old}"
        src = src.replace(old, new)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src, encoding="utf-8")
    dest.chmod(0o755)
    return dest


def load_module(path=BIN, name="actas_under_test"):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def has_yaml():
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


class Sandbox(unittest.TestCase):
    """os.environ is replaced by a minimal, isolated environment for the
    duration of each test (and restored afterwards)."""

    manifest = None          # dict (written as JSON) or str (written as is)
    secrets = None           # dict -> plaintext JSON "encrypted" file for fake sops

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.root = self.tmp / "cfg"
        for d in (self.home, self.root):
            d.mkdir()
        self.fakebin = make_fakebin(self.tmp / "fakebin")
        self.user_sessions = self.tmp / "passwd-home" / ".local" / "state" / "actas" / "sessions"
        self.bin = sandbox_bin(self.tmp / "bin" / "actas", self.fakebin / "ps", self.user_sessions)
        self.log = self.tmp / "fake.log"
        self.out = self.tmp / "fake.out"
        self.env = {
            "HOME": str(self.home), "PATH": f"{self.fakebin}:{SYSTEM_PATH}",
            "ACTAS_ROOT": str(self.root), "ACTAS_STATE": str(self.tmp / "state"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"), "XDG_STATE_HOME": str(self.tmp / "xstate"),
            "ACTAS_FALLBACK_PATH": "", "FAKE_LOG": str(self.log), "FAKE_OUT": str(self.out),
            "FAKE_STORE": str(self.tmp / "fake_store.json"),
            "LANG": "C.UTF-8", "NO_COLOR": "1",
        }
        self._saved_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.env)
        if self.manifest is not None:
            self.write_manifest(self.manifest)
        if self.secrets is not None:
            (self.root / "secrets.enc.yaml").write_text(json.dumps(self.secrets))
        self.reload()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ------------------------------------------------------------
    def fmt(self, text):
        return text.replace("%(tmp)s", str(self.tmp)).replace("%(home)s", str(self.home))

    def write_manifest(self, m):
        text = m if isinstance(m, str) else json.dumps(m, indent=2)
        (self.root / "environments.yaml").write_text(self.fmt(text))

    def reload(self):
        self.m = load_module(self.bin)
        self.m.CONFIRM_FN = lambda prompt: None      # never read the real /dev/tty
        return self.m

    def cli(self, *args, stdin=None, env=None, cwd=None, binary=None):
        e = dict(os.environ)     # the sandboxed environment, incl. per-test changes
        e.update(env or {})
        # A new session has no controlling terminal: a confirmation prompt
        # can never block on the developer's /dev/tty.
        return subprocess.run([sys.executable, str(binary or self.bin), *args], input=stdin,
                              capture_output=True, text=True, env=e, cwd=cwd, start_new_session=True)

    def calls(self, name=None):
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]
        return [r for r in rows if name is None or r["bin"] == name]

    def quiet(self, fn, *a, **k):
        """Call fn with stderr captured; returns (result, stderr text)."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            r = fn(*a, **k)
        return r, buf.getvalue()

    def dies(self, fn, *a, **k):
        """Assert fn exits; returns the stderr text."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                fn(*a, **k)
        return buf.getvalue()
