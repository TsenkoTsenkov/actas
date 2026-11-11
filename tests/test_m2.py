"""M2 (2.0.0-alpha2): the Plan engine, scrub scope, null isolation and
inherit (SPEC 4.1-4.8, 5.1-5.3), the modules, Claude providers and homes
(6.1), codex and gemini at environment level (6.2, 6.3), secret refs and
adapters (9), exec, shell formats and `secret set REF`."""

import json
import os
import shutil
import sys
import time
import unittest

from ccenv_testlib import SYSTEM_PATH, Sandbox

GW = {"provider": "gateway", "base_url": "https://llm.client-a.example", "token": "sops:gw/token"}
M = {
    "version": 2,
    "roles": {"gw": {"claude": GW}},
    "profiles": {
        "plain": {"match": "%(tmp)s/w/plain", "git": {"name": "Ada Example", "email": "ada@example.com"},
                  "aws": {"profile": "dev", "region": "eu-west-1"}},
        "inh": {"aws": "inherit", "inherit": ["KEEP_ME", "CLAUDE_CODE_USE_BEDROCK"],
                "unset": ["CLAUDE_CODE_USE_BEDROCK", "DROP_ME"], "env": {"DROP_ME": "x"}},
        "off": {"aws": None, "gcp": None, "azure": None, "github": None, "git": None},
        "pat": {"github": {"auth": "sops:pat/gh"}, "git": {"name": "Ada Example"}},
        "acct": {"github": {"auth": "gh:ada-ok"}},
        "gone": {"github": {"auth": "gh:ada-missing"}},
        "gwp": {"use": ["gw"], "secret_env": {"NPM_TOKEN": "sops:gw/npm"}},
        "bed": {"aws": {"profile": "b", "region": "us-east-1"},
                "claude": {"provider": "bedrock", "models": {"opus": "o-1", "sonnet": "s-1"},
                           "bearer_token": "sops:gw/bedrock"}},
        "vtx": {"gcp": {"project": "p-1", "home": "client-b"},
                "claude": {"provider": "vertex", "region": "us-east5"}},
        "az": {"azure": {"subscription": "sub-1", "tenant": "ten-1"}},
        "home": {"claude": {"home": "work", "settings": {"permissions": {"defaultMode": "plan"}}}},
        "cx": {"tool": "codex", "codex": {"home": "partner", "login": "api", "api_key": "sops:cx/key",
                                          "args": ["--search"]}},
        "gem-ro": {"level": "readonly", "tool": "gemini", "gcp": {"project": "gp-1"},
                   "gemini": {"home": "g", "auth": "vertex", "location": "us-central1"}},
        "gem-pw": {"level": "power", "tool": "gemini",       # no confirm: run without a prompt
                   "gemini": {"api_key": "sops:gw/gemini"}},
        "prec": {"aws": {"profile": "from-module"}, "env": {"AWS_PROFILE": "from-env"}},
        "refs": {"secret_env": {"K1": "keychain:svc/acct", "O1": "op://vault/item/field",
                                "C1": 'cmd:["printf", "%s", "a;b $(x)"]'}},
    },
}
SECRETS = {"gw": {"token": "gw-SECRET", "npm": "npm-SECRET", "bedrock": "bedrock-SECRET",
                  "gemini": "gemini-SECRET"},
           "pat": {"gh": "ghp_pat_SECRET"}, "cx": {"key": "sk-codex-SECRET"}}
ALL_SECRETS = ("gw-SECRET", "npm-SECRET", "bedrock-SECRET", "gemini-SECRET", "ghp_pat_SECRET",
               "sk-codex-SECRET", "gho_ada-ok")


class Base(Sandbox):
    manifest = M
    secrets = SECRETS

    def launch(self, *args, env=None, rc=0):
        if self.out.exists():
            self.out.unlink()
        r = self.cli("run", "--no-check", *args, env=env)
        self.assertEqual(r.returncode, rc, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return (json.loads(self.out.read_text()) if rc == 0 else None), r.stderr

    def exec_env(self, *args, env=None):
        r = self.cli("exec", *args, "--", "env", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)

    def homes(self, *parts):
        return str(self.tmp.joinpath("state", "homes", *parts))


class ScrubScope(Base):
    def test_v2_scrubs_referenced_modules_only(self):  # SPEC test 19
        self.write_manifest({"version": 2, "profiles": {"p": {"aws": {"profile": "dev"}}}})
        parent = {"AWS_PROFILE": "leak", "AWS_ACCESS_KEY_ID": "AKIA-leak", "CLAUDE_CODE_USE_BEDROCK": "1",
                  "OPENAI_API_KEY": "sk-keep", "GOOGLE_APPLICATION_CREDENTIALS": "/keep.json",
                  "CLAUDE_CONFIG_DIR": "/keep-home", "ANTHROPIC_DEFAULT_OPUS_MODEL": "leak"}
        out, err = self.launch("p", env=parent)
        env = out["env"]
        self.assertEqual(env["AWS_PROFILE"], "dev")
        for k in ("AWS_ACCESS_KEY_ID", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_DEFAULT_OPUS_MODEL"):
            self.assertNotIn(k, env)
        for k in ("OPENAI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_CONFIG_DIR"):
            self.assertEqual(env[k], parent[k])
        self.assertIn("cleared from your shell: ANTHROPIC_DEFAULT_OPUS_MODEL, AWS_ACCESS_KEY_ID, "
                      "CLAUDE_CODE_USE_BEDROCK", err)
        self.assertNotIn("AWS_PROFILE", err.split("cleared from your shell:")[1].splitlines()[0])

    def test_inherit_and_unset(self):  # SPEC test 20
        env = self.exec_env("inh", env={"AWS_PROFILE": "shell", "AWS_SESSION_TOKEN": "t", "KEEP_ME": "1",
                                        "CLAUDE_CODE_USE_BEDROCK": "1", "DROP_ME": "p",
                                        "ANTHROPIC_API_KEY": "sk-x"})
        self.assertEqual((env["AWS_PROFILE"], env["AWS_SESSION_TOKEN"]), ("shell", "t"))  # aws: inherit
        self.assertEqual(env["KEEP_ME"], "1")                                            # inherit: [X]
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)          # unset beats inherit
        self.assertNotIn("DROP_ME", env)                          # ... and the env Plan
        self.assertNotIn("ANTHROPIC_API_KEY", env)                # claude is referenced: owned

    def test_null_isolates(self):  # SPEC test 21
        env = self.exec_env("off", env={"AWS_PROFILE": "leak", "GH_TOKEN": "leak", "AZURE_CONFIG_DIR": "/x"})
        cfg, creds = env["AWS_CONFIG_FILE"], env["AWS_SHARED_CREDENTIALS_FILE"]
        self.assertEqual(cfg, self.homes("aws", "_off", "config"))
        for f in (cfg, creds):
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)
            self.assertEqual(os.path.getsize(f), 0)
        self.assertEqual(env["CLOUDSDK_CONFIG"], self.homes("gcp", "_off"))
        self.assertEqual(env["AZURE_CONFIG_DIR"], self.homes("azure", "_off"))
        self.assertEqual(env["GH_CONFIG_DIR"], self.homes("github", "_off"))
        self.assertEqual(os.stat(self.homes("github", "_off")).st_mode & 0o777, 0o700)
        self.assertNotIn("AWS_PROFILE", env)
        self.assertNotIn("GH_TOKEN", env)
        pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
                 for i in range(int(env["GIT_CONFIG_COUNT"]))]
        self.assertEqual(pairs, [("credential.https://github.com.helper", "")])

    def test_status_why_lists_the_scrub_scope(self):
        d = json.loads(self.cli("status", "plain", "--why", "--json", env={"GH_TOKEN": "x"}).stdout)
        self.assertIn("AWS_ACCESS_KEY_ID", d["env"]["scrub"])
        self.assertIn("OPENAI_API_KEY", d["env"]["scrub"])     # codex is referenced (profile cx)
        self.assertNotIn("AWS_PROFILE", d["env"]["scrub"])     # set by the profile
        self.assertEqual(d["env"]["clears"], ["GH_TOKEN"])
        self.assertFalse(os.path.exists(self.tmp / "state" / "homes"))   # status creates nothing


class GitHub(Base):  # SPEC test 22
    def test_token_form_fails_closed(self):
        env = self.exec_env("pat")
        self.assertEqual(env["GH_TOKEN"], "ghp_pat_SECRET")
        self.assertEqual(env["GITHUB_TOKEN"], "ghp_pat_SECRET")
        self.assertEqual(env["GH_CONFIG_DIR"], self.homes("github", "_off"))
        r = self.cli("exec", "gone", "--", "gh", "auth", "status",
                     env={"FAKE_GH_MISSING": "ada-missing", "GH_TOKEN": "leaked"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("not logged into any GitHub hosts", r.stderr)

    @unittest.skipUnless(shutil.which("git", path=SYSTEM_PATH), "needs git")
    def test_gh_account_helper_works_inside_the_session(self):
        # The fail-closed GH_CONFIG_DIR must not hide the account from the helper.
        r = self.cli("exec", "acct", "--", "git", "credential", "fill",
                     stdin="protocol=https\nhost=github.com\n\n", cwd=str(self.tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("password=gho_ada-ok", r.stdout)

    def test_v1_files_do_not_fail_closed(self):
        self.write_manifest({"version": 1, "environments": {"e": {"github": {"account": "ada"}}}})
        self.assertNotIn("GH_CONFIG_DIR", self.exec_env("e"))


class Precedence(Base):
    def test_env_beats_module_and_doctor_warns(self):  # SPEC test 24
        self.assertEqual(self.exec_env("prec")["AWS_PROFILE"], "from-env")
        d = json.loads(self.cli("doctor", "--json").stdout)
        detail = [x["message"] for c in d["checks"] if c["name"] == "manifest" for x in c["detail"]]
        self.assertTrue(any("AWS_PROFILE is set by both aws and env" in x for x in detail), detail)

    def test_agent_variables(self):  # SPEC test 26
        out, _ = self.launch("plain")
        self.assertEqual((out["env"]["AWS_PAGER"], out["env"]["GIT_TERMINAL_PROMPT"]), ("", "0"))
        self.assertEqual(out["env"]["CCENV_AGENT"], "claude")
        human = self.exec_env("plain")
        for k in ("AWS_PAGER", "GIT_TERMINAL_PROMPT", "CCENV_AGENT"):
            self.assertNotIn(k, human)
        # M4: an agent exec is gated (test_m4); here only the variables matter.
        agent = self.m.assemble(self.m.resolve("plain"), want_secrets=False, ctx={"agent": True})["env"]
        self.assertEqual(agent["GIT_TERMINAL_PROMPT"], "0")


class Providers(Base):
    def test_subscription_blanks(self):  # SPEC test 28
        out, _ = self.launch("plain", env={"ANTHROPIC_BASE_URL": "https://leak"})
        s = out["settings"]
        for k in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                  "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS", "ANTHROPIC_BASE_URL"):
            self.assertEqual(s["env"][k], "")
        self.assertEqual(s["apiKeyHelper"], "")
        self.assertNotIn("ANTHROPIC_BASE_URL", out["env"])
        self.assertEqual(s["env"]["AWS_DEFAULT_REGION"], "eu-west-1")
        self.assertEqual((s["env"]["CCENV"], s["env"]["CCENV_LEVEL"]), ("plain", "standard"))

    def test_gateway_v2_keeps_the_token_out_of_env(self):  # SPEC test 29
        out, _ = self.launch("gwp")
        self.assertEqual(out["env"]["ANTHROPIC_BASE_URL"], GW["base_url"])
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", out["env"])
        self.assertEqual(out["env"]["NPM_TOKEN"], "npm-SECRET")
        words = out["settings"]["apiKeyHelper"].split()
        self.assertTrue(os.path.isabs(words[0]) and os.path.isabs(words[1]))
        self.assertEqual(words[2:], ["secret", "--ref", "sops:gw/token"])
        self.assertNotIn("NPM_TOKEN", out["settings"]["env"])

    def test_bedrock(self):  # SPEC test 30
        out, err = self.launch("bed")
        env, s = out["env"], out["settings"]
        self.assertEqual((env["CLAUDE_CODE_USE_BEDROCK"], env["AWS_REGION"]), ("1", "us-east-1"))
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "o-1")
        self.assertEqual(env["AWS_BEARER_TOKEN_BEDROCK"], "bedrock-SECRET")
        self.assertEqual((s["env"]["CLAUDE_CODE_USE_BEDROCK"], s["env"]["CLAUDE_CODE_USE_VERTEX"]), ("1", ""))
        self.assertIn("awsAuthRefresh", s)
        self.assertNotIn("AWS_BEARER_TOKEN_BEDROCK", s["env"])
        self.write_manifest({"version": 2, "profiles": {"b": {"aws": {"profile": "x"},
                                                              "claude": {"provider": "bedrock"}}}})
        r = self.cli("exec", "b", "--", "true")
        self.assertEqual(r.returncode, 1)
        self.assertIn("has no aws.region", r.stderr)

    def test_vertex(self):  # SPEC test 31
        env = self.exec_env("vtx")
        self.assertEqual((env["CLAUDE_CODE_USE_VERTEX"], env["ANTHROPIC_VERTEX_PROJECT_ID"],
                          env["CLOUD_ML_REGION"]), ("1", "p-1", "us-east5"))
        self.assertEqual((env["CLOUDSDK_CORE_PROJECT"], env["GOOGLE_CLOUD_PROJECT"]), ("p-1", "p-1"))
        self.assertEqual(env["CLOUDSDK_CONFIG"], self.homes("gcp", "client-b"))

    def test_azure_without_home_is_noted(self):
        env = self.exec_env("az")
        self.assertEqual((env["ARM_SUBSCRIPTION_ID"], env["ARM_TENANT_ID"]), ("sub-1", "ten-1"))
        self.assertNotIn("AZURE_CONFIG_DIR", env)
        self.assertIn("only Terraform", self.cli("status", "az", "--why").stdout)

    def test_claude_home(self):  # SPEC test 32
        out, err = self.launch("home", env={"CLAUDE_CONFIG_DIR": "/parent-home"})
        self.assertEqual(out["env"]["CLAUDE_CONFIG_DIR"], self.homes("claude", "work"))
        self.assertNotIn("CLAUDE_CONFIG_DIR", out["settings"]["env"])
        self.assertIn("has no login yet: run /login", err)
        perms = out["settings"]["permissions"]                  # M4: plus the level preset
        self.assertEqual(perms["defaultMode"], "plan")
        self.assertIn("Bash(git credential *)", perms["deny"])
        os.makedirs(self.homes("claude", "work", "x"))
        self.assertNotIn("first launch", self.launch("home")[1])

    def test_settings_files_are_content_named_and_pruned(self):  # SPEC test 33
        cache = self.tmp / "cache" / "ccenv"
        cache.mkdir(parents=True)
        old = cache / "gone.0123456789ab.settings.json"
        old.write_text("{}")
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        a, _ = self.launch("plain")
        b, _ = self.launch("gwp")
        files = sorted(cache.glob("*.settings.json"))
        self.assertEqual(len(files), 2)
        self.assertFalse(old.exists())
        for f in files:
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertRegex(f.name, r"^(plain|gwp)\.[0-9a-f]{12}\.settings\.json$")
        self.assertEqual(a["argv"][1], str(cache / [f.name for f in files if f.name.startswith("plain")][0]))


class OtherTools(Base):  # SPEC test 34
    def test_codex_home_and_no_secret(self):
        out, _ = self.launch("cx", "--", "resume")
        self.assertEqual(out["env"]["CODEX_HOME"], self.homes("codex", "partner"))
        self.assertEqual(os.stat(self.homes("codex", "partner")).st_mode & 0o777, 0o700)
        self.assertEqual(out["argv"], ["--search", "resume"])
        self.assertEqual(out["env"]["CCENV_AGENT"], "codex")
        self.assertNotIn("sk-codex-SECRET", json.dumps(out))

    def test_codex_login_pipes_the_key(self):
        r = self.cli("login", "cx")
        self.assertEqual(r.returncode, 0, r.stderr)
        call = self.calls("codex")[-1]
        self.assertEqual(call["argv"], ["login", "--with-api-key"])
        self.assertEqual(call["stdin"], "sk-codex-SECRET\n")

    def test_gemini_approval_mode(self):
        out, _ = self.launch("gem-ro")
        self.assertEqual(out["argv"][:2], ["--approval-mode", "default"])
        env = out["env"]
        self.assertEqual((env["GEMINI_CLI_HOME"], env["GOOGLE_GENAI_USE_VERTEXAI"]),
                         (self.homes("gemini", "g"), "true"))
        self.assertEqual((env["GOOGLE_CLOUD_PROJECT"], env["GOOGLE_CLOUD_LOCATION"]), ("gp-1", "us-central1"))
        out, _ = self.launch("gem-pw")
        self.assertEqual(out["argv"][:2], ["--approval-mode", "auto_edit"])
        self.assertEqual(out["env"]["GEMINI_API_KEY"], "gemini-SECRET")
        out, _ = self.launch("plain", "-t", "gemini")
        self.assertEqual(out["argv"][:2], ["--approval-mode", "default"])

    def test_no_secret_in_any_generated_file(self):  # SPEC test 35
        for p in ("plain", "gwp", "bed", "home", "cx", "gem-pw"):
            self.launch(p)
        for d in (self.tmp / "cache", self.tmp / "state"):
            for root, _, files in os.walk(d):
                for f in files:
                    text = open(os.path.join(root, f), errors="replace").read()
                    for s in ALL_SECRETS:
                        self.assertNotIn(s, text, f)


class Shell(Base):
    def sh(self, *args, env=None):
        r = self.cli("shell", *args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_sh_format(self):  # SPEC test 9
        out = self.sh("pat", env={"AWS_PROFILE": "leak", "GH_TOKEN": "leak"})
        lines = out.splitlines()
        self.assertEqual(lines[-1], "unset AWS_PROFILE")
        self.assertEqual(lines[-2], "export CCENV=pat")
        self.assertIn("export GH_TOKEN=ghp_pat_SECRET", lines)
        self.assertEqual(self.sh("pat", "--format", "direnv", env={"AWS_PROFILE": "leak"}),
                         self.sh("pat", env={"AWS_PROFILE": "leak"}))

    def test_no_secrets_json_and_env(self):
        out = self.sh("pat", "--no-secrets", env={"GH_TOKEN": "leak"})
        self.assertNotIn("GH_TOKEN=", out)
        self.assertIn("git-credential --ref sops:pat/gh", out)       # helpers stay
        self.assertIn("unset GH_TOKEN", out)
        d = json.loads(self.sh("pat", "--format", "json"))
        self.assertTrue(d["ok"])
        self.assertNotIn("GH_TOKEN", d["set"])
        self.assertNotIn("ghp_pat_SECRET", json.dumps(d))
        env = self.sh("plain", "--format", "env", env={"GIT_CONFIG_COUNT": "3", "CCENV_GIT_BASE": "1"})
        kv = dict(line.split("=", 1) for line in env.splitlines())
        self.assertEqual((kv["GIT_CONFIG_KEY_0"], kv["GIT_CONFIG_COUNT"]), ("user.name", "2"))
        self.assertEqual(kv["CCENV_GIT_BASE"], "0")                  # F0: a nested ccenv drops them
        self.assertEqual(self.calls("sops"), [])

    def test_fish_quoting_round_trips(self):  # SPEC test 27
        tricky = ["a\\", "a\\'b", "$(x)", "it's", "a b\tc"]
        self.write_manifest({"version": 2, "profiles": {
            "f": {"env": {f"V{i}": v for i, v in enumerate(tricky)}}}})
        out = self.sh("f", "--format", "fish", env={"AWS_PROFILE": "x"})
        got = {}
        for line in out.splitlines():
            if line.startswith("set -gx "):
                name, rest = line[len("set -gx "):].split(" ", 1)
                got[name] = fish_unquote(rest)
        self.assertEqual([got[f"V{i}"] for i in range(len(tricky))], tricky)


def fish_unquote(s):
    """fish single quotes: only \\\\ and \\' are escapes."""
    assert s[0] == "'" and s[-1] == "'", s
    out, i, body = [], 0, s[1:-1]
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body) and body[i + 1] in "\\'":
            out.append(body[i + 1])
            i += 2
        else:
            assert body[i] != "'", s
            out.append(body[i])
            i += 1
    return "".join(out)


class Refs(Base):
    def test_keychain_op_and_cmd(self):  # SPEC test 41
        (self.tmp / "fake_store.json").write_text(json.dumps(
            {"keychain": {"svc/acct": "kc-value"}, "op": {"op://vault/item/field": "op-value"}}))
        r = self.cli("secret", "--ref", 'cmd:["printf", "%s", "a;b $(x)"]')
        self.assertEqual((r.returncode, r.stdout), (0, "a;b $(x)"))       # argv, no shell
        r = self.cli("secret", "--ref", "op://vault/item/field")
        self.assertEqual(r.stdout, "op-value")
        self.assertEqual(self.calls("op")[-1]["argv"], ["read", "op://vault/item/field"])
        r = self.cli("secret", "--ref", "keychain:svc/acct")
        if sys.platform == "darwin":
            self.assertEqual(r.stdout, "kc-value")
            self.assertEqual(self.calls("security")[-1]["argv"],
                             ["find-generic-password", "-s", "svc", "-a", "acct", "-w"])
            env = self.exec_env("refs")
            self.assertEqual((env["K1"], env["O1"], env["C1"]), ("kc-value", "op-value", "a;b $(x)"))
        else:
            self.assertEqual((r.returncode, r.stdout), (1, ""))
            self.assertIn("secret-tool", r.stderr)

    def test_bad_refs(self):  # SPEC test 42
        for ref in ("keychain:-x/y", "op://a/$HOME", "cmd:[]", "sops:a"):
            r = self.cli("secret", "--ref", ref)
            self.assertEqual((r.returncode, r.stdout), (1, ""), ref)

    def test_secret_set_ref(self):  # SPEC test 40, 9.3
        r = self.cli("secret", "set", "sops:new/key", stdin="v-1\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        sets = [c for c in self.calls("sops") if c["argv"][:1] == ["set"] and "--help" not in c["argv"]]
        self.assertNotIn("v-1", json.dumps(sets[-1]["argv"]))
        self.assertEqual(json.loads(sets[-1]["stdin"]), "v-1")
        self.assertEqual(self.cli("secret", "--ref", "sops:new/key").stdout, "v-1")
        self.assertEqual(self.cli("secret", "set", "sops:new/key", "value").returncode, 2)
        r = self.cli("secret", "set", "op://vault/item/field", stdin="x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("read-only", r.stderr)
        if sys.platform == "darwin":
            r = self.cli("secret", "set", "keychain:svc/acct", stdin="kc-2\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            call = self.calls("security")[-1]
            self.assertEqual(call["argv"][-1], "-w")                     # -w last, no value
            self.assertNotIn("kc-2", json.dumps(call["argv"]))
            self.assertEqual(self.cli("secret", "--ref", "keychain:svc/acct").stdout, "kc-2")

    def test_missing_secret_unsets_the_parent_value(self):
        (self.root / "secrets.enc.yaml").write_text(json.dumps({"gw": {"token": "x"}}))
        env = self.exec_env("gwp", env={"NPM_TOKEN": "parent-value"})
        self.assertNotIn("NPM_TOKEN", env)


class Cli(Base):
    def test_exec(self):
        r = self.cli("exec", "plain", "--", "sh", "-c", "echo $AWS_PROFILE; exit 7")
        self.assertEqual((r.returncode, r.stdout), (7, "dev\n"))         # the child's exit code
        r = self.cli("exec", "plain", "--", "no-such-binary-xyz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no-such-binary-xyz not found", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.cli("exec", "plain").returncode, 2)
        (self.tmp / "w" / "plain").mkdir(parents=True)
        r = self.cli("--cwd", str(self.tmp / "w/plain"), "exec", "--", "sh", "-c", "echo $CCENV")
        self.assertEqual(r.stdout, "plain\n")

    def test_sync_git_pairs_start_at_zero(self):  # SPEC test 23 (sync side)
        (self.tmp / "w" / "plain").mkdir(parents=True)
        r = self.cli("sync", "plain", env={"GIT_CONFIG_COUNT": "3", "CCENV_GIT_BASE": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        env = json.loads((self.tmp / "w/plain/.claude/settings.local.json").read_text())["env"]
        self.assertEqual((env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"]), ("2", "user.name"))
        self.assertEqual(env["CCENV_GIT_BASE"], "0")                 # F0: a nested ccenv drops them
        self.assertEqual(env["AWS_PAGER"], "")                       # sync is an agent session

    def test_double_dash_only_for_run_and_exec(self):  # D28
        r = self.cli("status", "plain", "--", "x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("only to run and exec", r.stderr)

    def test_login_modules(self):
        r = self.cli("login", "az", "--module", "azure")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("az")[-1]["argv"], ["login", "--tenant", "ten-1"])
        self.assertIn("not running `az account set`", r.stderr)
        r = self.cli("login", "vtx", "--module", "gcp", "--adc")
        self.assertEqual([c["argv"] for c in self.calls("gcloud")],
                         [["auth", "login"], ["auth", "application-default", "login"]])
        r = self.cli("login", "pat", "--module", "github")
        self.assertIn("ccenv secret set sops:pat/gh", r.stderr)
        self.assertEqual(self.cli("login", "pat").returncode, 1)          # nothing to log in to


if __name__ == "__main__":
    unittest.main()
