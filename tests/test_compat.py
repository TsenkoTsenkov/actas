"""T-COMPAT (SPEC 2.4 acceptance): on a v1 manifest, the v2 binary resolves
every probe directory like the v1 binary (tests/compat/ccenv_v1, saved from
main) and launches claude with the same process env and settings, modulo
exactly the differences in ENV_RULES / SETTINGS_RULES below.

Each rule is one bullet of the SPEC 2.4 list, plus one for SPEC 6.1 (run's
settings carry every non-secret variable the process env gets). A rule only
*allows* a difference; anything not listed fails the test.

Two fixtures: examples/environments.v1.yaml, and LIVE_SHAPE, which has the
same shape as a real-world v1 manifest (6 environments, nested override,
shared gateway token via secret_from, secret_env GH_TOKEN) with placeholder
names only."""

import json
import os
import re
import shlex
import unittest

from ccenv_testlib import REPO, V1_BIN, Sandbox, has_yaml, load_module

MISSING = "<missing>"
V1_HELPER = ('!f(){ test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" '
             '"$GH_TOKEN"; }; f')
ADDED_VARS = {"CCENV_LEVEL", "CCENV_SESSION", "CCENV_AGENT", "CCENV_GIT_BASE"}
AGENT_VARS = {"GH_PROMPT_DISABLED", "GIT_TERMINAL_PROMPT", "AWS_PAGER"}
ANTHROPIC3 = {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"}
USE_FLAGS = {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
             "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS"}

# What the shell running ccenv already exports. Several of these are owned
# by v1 (scrubbed from M2 on); CLAUDE_CONFIG_DIR and OPENAI_API_KEY are not
# and must always survive for v1 files (SPEC 16.2).
PARENT = {"AWS_PROFILE": "leaked-profile", "GH_TOKEN": "leaked-gh", "ANTHROPIC_API_KEY": "sk-parent",
          "CLAUDE_CONFIG_DIR": "/tmp/parent-claude-home", "OPENAI_API_KEY": "sk-openai-parent",
          "AWS_ACCESS_KEY_ID": "AKIA-PARENT-PLACEHOLDER"}

LIVE_SHAPE = {
    "version": 1,
    "defaults": {"provider": "subscription"},
    "environments": {
        "personal": {
            "match": ["~/code/personal/*", "~/projects/*", "~/side-project", "~/notes-tool"],
            "provider": "subscription",
            "aws": {"profile": "personal-admin", "region": "eu-central-1"},
            "github": {"account": "ada-personal", "name": "Ada Example", "email": "ada@example.com"},
        },
        "client-b": {
            "match": ["~/code/client-b/*"],
            "provider": "subscription",
            "github": {"account": "ada-client-b", "name": "Ada Example", "email": "ada@client-b.example"},
            "env": {"CLOUDSDK_CORE_PROJECT": ""},
        },
        "client-a": {
            "match": ["~/work/client-a-cdk", "~/work/client-a-api", "~/work/client-a-internal",
                      "~/work/client-a-cdk-*"],
            "provider": "gateway",
            "gateway": {"base_url": "https://gateway.corp.example", "secret": "token", "secret_from": "corp"},
            "aws": {"profile": "client-a-prod", "region": "eu-central-1"},
            "github": {"account": "ada-client-a", "name": "Ada Example", "email": "ada@corp.example"},
        },
        "client-a-test": {
            "match": ["~/work/client-a-cdk/test"],
            "provider": "gateway",
            "gateway": {"base_url": "https://gateway.corp.example", "secret": "token", "secret_from": "corp"},
            "aws": {"profile": "client-a-test", "region": "eu-central-1"},
            "github": {"account": "ada-client-a", "email": "ada@corp.example"},
        },
        "client-c": {
            "match": ["~/work/hosting", "~/work/managed"],
            "provider": "gateway",
            "gateway": {"base_url": "https://gateway.corp.example", "secret": "token", "secret_from": "corp"},
            "github": {"account": "ada-client-c", "name": "Ada Example", "email": "ada@corp.example"},
        },
        "corp": {
            "match": ["~/work/rate-engine"],
            "provider": "gateway",
            "gateway": {"base_url": "https://gateway.corp.example", "secret": "token"},
            "aws": {"profile": "corp-dev", "region": "eu-central-1"},
            "github": {"name": "Ada Example", "email": "ada@corp.example"},
            "secret_env": {"GH_TOKEN": "gh_token", "GITHUB_TOKEN": "gh_token"},
        },
    },
}
LIVE_SECRETS = {"corp": {"token": "gw-secret-corp", "gh_token": "ghp_pat_corp"}}
LIVE_PROBES = {  # directory under HOME -> what v1 resolves (frozen expectations)
    "code/personal/repo": "personal", "code/personal/repo/deep": "personal", "code/personal": None,
    "projects/x": "personal", "side-project": "personal", "side-project/sub": "personal",
    "notes-tool": "personal", "code/client-b/app": "client-b", "code/client-b": None,
    "work/client-a-cdk": "client-a", "work/client-a-cdk/test": "client-a-test",
    "work/client-a-cdk/test/x": "client-a-test", "work/client-a-cdk-wip": "client-a",
    "work/client-a-cdk-wip/test": "client-a", "work/client-a-api/src": "client-a",
    "work/hosting": "client-c", "work/managed/x": "client-c", "work/rate-engine": "corp",
    "work/rate-engine/sub": "corp", "documents": None, "work": None, "WORK/HOSTING": None,
}

EXAMPLE_SECRETS = {"client-a": {"token": "gw-secret-a"}, "partner-org": {"gh_token": "ghp_pat_partner"}}
EXAMPLE_PROBES = {
    "code/personal": "personal", "code/personal/x/y": "personal", "code/sandbox-a": "personal",
    "code/sandbox-a/deep": "personal", "code/sandbox-": "personal", "code/client-a": "client-a",
    "code/client-a/api": "client-a", "code/client-a/infra": "client-a-infra",
    "code/client-a/infra/x": "client-a-infra", "code/client-a-other": None,
    "code/partner-org/x": "partner-org", "code": None, "Code/Personal": None,
}


class Ctx:
    def __init__(self, v1env, parent):
        self.parent = parent
        self.provider = (v1env.get("provider") or "subscription").lower()
        self.gh_secret = "GH_TOKEN" in (v1env.get("secret_env") or {})
        self.account = (v1env.get("github") or {}).get("account")
        aws = v1env.get("aws") or {}
        # D-A: the modules this environment configures (only those are scrubbed)
        self.modules = {"claude"} | ({"aws"} if aws.get("profile") or aws.get("region") else set()) \
            | ({"github"} if self.account or self.gh_secret else set())
        name = v1env["name"]
        self.gh_ref = (f"sops:{name}/{v1env['secret_env']['GH_TOKEN']}" if self.gh_secret
                       else f"gh:{self.account}" if self.account else None)
        self.agent = True                     # `run` launches claude: an agent session


# --- normalizers --------------------------------------------------------------

def norm_git(env, side, ctx):
    """Fold GIT_CONFIG_* into one pseudo-key holding the pair list; helper
    strings become ("<github-helper>", ref)."""
    env = dict(env)
    n = int(env.pop("GIT_CONFIG_COUNT", 0) or 0)
    pairs = []
    for i in range(n):
        k, v = env.pop(f"GIT_CONFIG_KEY_{i}", None), env.pop(f"GIT_CONFIG_VALUE_{i}", None)
        if k == "credential.https://github.com.helper" and v:
            m = re.match(r"^!(.*) git-credential --ref (\S+) --username x-access-token$", v)
            if side == "v1" and v == V1_HELPER:
                v = ("<github-helper>", f"gh:{ctx.account}")
            elif side == "v2" and m and all(os.path.isabs(w) for w in shlex.split(m.group(1))):
                v = ("<github-helper>", shlex.split(m.group(2))[0])
        pairs.append((k, v))
    for k in [k for k in env if k.startswith("GIT_CONFIG_")]:
        del env[k]
    if pairs:
        env["GIT_CONFIG_*"] = tuple(pairs)
    return env


def norm_settings(s, side):
    flat = {f"env.{k}": v for k, v in (s.get("env") or {}).items()}
    for k, v in s.items():
        if k == "env":
            continue
        if k == "apiKeyHelper" and v:
            words = shlex.split(v)
            i = words.index("secret")
            prefix_ok = side == "v1" or all(os.path.isabs(w) for w in words[:i])
            rest = words[i + 1:]
            ref = rest[1] if rest[:1] == ["--ref"] else f"sops:{rest[0]}/{rest[1]}"
            v = ("<secret-helper>", ref) if prefix_ok else v
        if k == "awsAuthRefresh":
            words = shlex.split(v)
            prefix_ok = side == "v1" or os.path.isabs(words[0])
            v = ("<aws-sso-login>", words[words.index("--profile") + 1]) if prefix_ok else v
        flat[k] = v
    return flat


# --- allowed differences: one rule per SPEC 2.4 bullet ---------------------------

ENV_RULES = [
    ("scrubbed V1_OWNED variables the parent had, of modules the environment configures (D-A)",
     lambda k, a, b, c: b == MISSING and k in V1_OWNED and c.parent.get(k) == a
     and module_of(k) in c.modules),
    ("added: CCENV_LEVEL, CCENV_SESSION, CCENV_AGENT, CCENV_GIT_BASE",
     lambda k, a, b, c: a == MISSING and k in ADDED_VARS),
    ("added in agent sessions: GH_PROMPT_DISABLED, GIT_TERMINAL_PROMPT, AWS_PAGER",
     lambda k, a, b, c: a == MISSING and k in AGENT_VARS and c.agent),
    ("the three ANTHROPIC_* variables are unset instead of \"\" in subscription mode",
     lambda k, a, b, c: k in ANTHROPIC3 and a == "" and b == MISSING and c.provider == "subscription"),
    ("GITHUB_TOKEN is the PAT where secret_env.GH_TOKEN existed",
     lambda k, a, b, c: k == "GITHUB_TOKEN" and c.gh_secret and b == c.v2_gh_token),
    ("the git helper is the --ref form and is attached to secret-sourced GH_TOKEN",
     lambda k, a, b, c: k == "GIT_CONFIG_*" and c.gh_secret and b == (a if a != MISSING else ())
     + (("credential.https://github.com.helper", ""),
        ("credential.https://github.com.helper", ("<github-helper>", c.gh_ref)))),
]
SETTINGS_RULES = [
    # apiKeyHelper/awsAuthRefresh: absolute paths and the --ref form are
    # folded in by norm_settings (a relative v2 prefix stays un-normalized
    # and therefore fails).
    # SPEC 2.4 names the subscription blanks; SPEC 6.1 gives gateway the same
    # CLAUDE_CODE_USE_* blanks (v1 files only have subscription and gateway).
    ("settings gain the blanks for CLAUDE_CODE_USE_*",
     lambda k, a, b, c: a == MISSING and b == "" and k[len("env."):] in USE_FLAGS),
    # SPEC 6.1: "settings env = every set variable from all Plans, plus
    # CCENV/CCENV_LEVEL" - a settings value beats a shell export, so run's
    # settings repeat the (already checked) process env. Secrets never: the
    # values must equal the process env and check_launches greps for them.
    ("settings env gains the process env's non-secret variables (SPEC 6.1)",
     lambda k, a, b, c: a == MISSING and k.startswith("env.") and c.v2_env.get(k[4:], MISSING) == b),
    # SPEC 6.1 settings example: run's settings carry the SessionStart hook
    # (absolute helper prefix, --source run, this profile's name).
    ("settings gain the ccenv SessionStart hook (SPEC 6.1)",
     lambda k, a, b, c: k == "hooks" and a == MISSING and is_run_hook(b, c.v2_env.get("CCENV"))),
]


def is_run_hook(v, name):
    try:
        (group,) = v["SessionStart"]
        (hook,) = group["hooks"]
        words = shlex.split(hook["command"])
    except (KeyError, TypeError, ValueError):
        return False
    return (set(v) == {"SessionStart"} and set(group) == {"hooks"} and hook["type"] == "command"
            and all(os.path.isabs(w) for w in words[:2])
            and words[2:] == ["hook", "session-start", "--profile", name, "--source", "run"])
V1_OWNED = set()   # filled from the v2 module (single source) in setUpModule


def module_of(var):
    return "aws" if var.startswith("AWS_") else "github" if var.startswith(("GH_", "GITHUB_")) else "claude"


def setUpModule():
    V1_OWNED.update(load_module().V1_OWNED)


def unexplained(a, b, rules, ctx):
    out = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, MISSING), b.get(k, MISSING)
        if x != y and not any(rule(k, x, y, ctx) for _, rule in rules):
            out.append((k, x, y))
    return out


class CompatBase(Sandbox):
    probes = {}

    def setUp(self):
        super().setUp()
        self.v1 = load_module(V1_BIN, "ccenv_v1_under_test")

    def resolve_both(self, d):
        path = str(self.home / d)
        try:
            v1 = self.quiet(self.v1.resolve, None, path)[0]["name"]
        except SystemExit:
            v1 = None
        cfg = self.m.load(quiet=True)
        v2, how, _, _ = self.m.locate(cfg, path)
        return v1, v2, how

    def check_resolution(self):
        for d, expected in self.probes.items():
            v1, v2, how = self.resolve_both(d)
            self.assertEqual(v1, expected, f"frozen v1 expectation for {d}")
            if v1 is None and how in ("logical-pwd", "casefold", "fallback"):
                continue          # SPEC 3.3: fallbacks apply only where v1 found nothing
            self.assertEqual(v2, v1, d)

    def launch(self, binary, name):
        if self.out.exists():
            self.out.unlink()
        r = self.cli("run", name, "--no-check", env=PARENT, binary=binary)
        self.assertEqual(r.returncode, 0, f"{binary.name} run {name}: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)
        return json.loads(self.out.read_text())

    def check_launches(self):
        envs = self.v1.manifest()
        self.assertTrue(envs)
        for name, v1env in envs.items():
            ctx = Ctx(v1env, dict(os.environ, **PARENT))
            a, b = self.launch(V1_BIN, name), self.launch(self.bin, name)
            ctx.v2_gh_token = b["env"].get("GH_TOKEN")
            ctx.v2_env = b["env"]
            self.assertEqual(a["argv"][0], "--settings")
            self.assertEqual(a["argv"][2:], b["argv"][2:], name)
            diff = unexplained(norm_git(a["env"], "v1", ctx), norm_git(b["env"], "v2", ctx), ENV_RULES, ctx)
            self.assertEqual(diff, [], f"{name}: process env differs beyond SPEC 2.4")
            diff = unexplained(norm_settings(a["settings"], "v1"), norm_settings(b["settings"], "v2"),
                               SETTINGS_RULES, ctx)
            self.assertEqual(diff, [], f"{name}: settings differ beyond SPEC 2.4")
            self.assertNotIn("permissions", b["settings"])      # v1 files render no agent rules
            for k in ("CLAUDE_CONFIG_DIR", "OPENAI_API_KEY"):
                self.assertEqual(b["env"][k], PARENT[k])
            for secret in ("gw-secret", "ghp_pat"):
                self.assertNotIn(secret, json.dumps(b["settings"]))


class LiveShape(CompatBase):
    manifest = LIVE_SHAPE
    secrets = LIVE_SECRETS
    probes = LIVE_PROBES

    def test_resolution_matches_v1(self):
        self.check_resolution()

    def test_env_and_settings_match_v1(self):
        self.check_launches()

    def test_known_fixes_are_visible(self):
        # D2: corp's PAT now has a git helper; v1 had none.
        b = self.launch(self.bin, "corp")
        keys = [b["env"][f"GIT_CONFIG_KEY_{i}"] for i in range(int(b["env"]["GIT_CONFIG_COUNT"]))]
        self.assertIn("credential.https://github.com.helper", keys)
        self.assertEqual(b["env"]["GITHUB_TOKEN"], "ghp_pat_corp")


@unittest.skipUnless(has_yaml(), "the example manifest is YAML; needs PyYAML")
class Example(CompatBase):
    secrets = EXAMPLE_SECRETS
    probes = EXAMPLE_PROBES

    def setUp(self):
        super().setUp()
        (self.root / "environments.yaml").write_text((REPO / "examples/environments.v1.yaml").read_text())
        self.m = self.reload()

    def test_resolution_matches_v1(self):
        self.check_resolution()

    def test_env_and_settings_match_v1(self):
        self.check_launches()


class RulesAreTight(CompatBase):
    """The harness itself: an unlisted difference must be reported."""
    manifest = LIVE_SHAPE
    secrets = LIVE_SECRETS

    def test_unlisted_difference_fails(self):
        ctx = Ctx(self.v1.manifest()["client-b"], dict(PARENT))
        ctx.v2_gh_token = "gho_ada-client-b"
        a = {"AWS_PROFILE": "x", "ANTHROPIC_BASE_URL": ""}
        self.assertEqual(unexplained(a, {"ANTHROPIC_BASE_URL": ""}, ENV_RULES, ctx),
                         [("AWS_PROFILE", "x", MISSING)])                     # not the parent's value
        a = {"AWS_PROFILE": "leaked-profile"}                                 # the parent's, but client-b
        self.assertEqual(unexplained(a, {}, ENV_RULES, ctx),                  # has no aws block (D-A)
                         [("AWS_PROFILE", "leaked-profile", MISSING)])
        self.assertEqual(unexplained({}, {"NEW_VAR": "1"}, ENV_RULES, ctx), [("NEW_VAR", MISSING, "1")])
        self.assertEqual(unexplained({"GITHUB_TOKEN": "a"}, {"GITHUB_TOKEN": "b"}, ENV_RULES, ctx),
                         [("GITHUB_TOKEN", "a", "b")])                        # no secret_env GH_TOKEN
        rel = {"apiKeyHelper": "ccenv secret corp token"}
        self.assertNotEqual(norm_settings(rel, "v2"), norm_settings(rel, "v1"))  # relative prefix
        ctx.v2_env = {"AWS_REGION": "eu-west-1"}
        self.assertEqual(unexplained({}, {"env.AWS_REGION": "us-east-1", "env.X": "1"}, SETTINGS_RULES, ctx),
                         [("env.AWS_REGION", MISSING, "us-east-1"), ("env.X", MISSING, "1")])
        ctx.v2_env = {"CCENV": "client-b"}
        hook = lambda cmd: {"SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}]}  # noqa: E731
        good = "/usr/bin/python3 /x/ccenv hook session-start --profile client-b --source run"
        self.assertEqual(unexplained({}, {"hooks": hook(good)}, SETTINGS_RULES, ctx), [])
        for bad in (good.replace("client-b", "other"), good.replace("run", "sync"),
                    good.replace("/x/ccenv", "ccenv"), good + " extra"):
            self.assertEqual(len(unexplained({}, {"hooks": hook(bad)}, SETTINGS_RULES, ctx)), 1, bad)


if __name__ == "__main__":
    unittest.main()
