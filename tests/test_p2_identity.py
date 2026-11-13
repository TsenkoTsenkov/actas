"""Identity environment correctness: GitHub Enterprise hosts for gh: refs,
the shell's git / cloud identity overrides, and reading gh: refs from inside
another profile's session."""

import json
import os
import subprocess
import sys
import unittest

from actas_testlib import Sandbox

GHE = "ghe.corp.example"

# A gh that logs the GH_CONFIG_DIR it was run with and, like real gh with
# plain-text storage, finds an account only in the config dir holding it.
GH_LOGGING_CFG = """#!%s
import json, os, sys
cfg = os.environ.get("GH_CONFIG_DIR", "")
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"bin": "gh", "argv": sys.argv[1:], "cfg": cfg}) + "\\n")
if cfg and not os.path.exists(os.path.join(cfg, "hosts.yml")):
    sys.stderr.write("no oauth token found\\n")
    sys.exit(1)
print("gho_" + sys.argv[sys.argv.index("--user") + 1])
""" % sys.executable

GIT_PARENT = {"GIT_AUTHOR_NAME": "Client A", "GIT_AUTHOR_EMAIL": "a@client-a.example",
              "GIT_COMMITTER_NAME": "Client A", "GIT_COMMITTER_EMAIL": "a@client-a.example",
              "GIT_SSH_COMMAND": "ssh -i ~/.ssh/client_a"}
GIT_VARS = sorted(GIT_PARENT)


class GheHost(Sandbox):
    """github.host with a gh: ref without @host: the ref is the account on that host."""
    manifest = {"version": 2, "profiles": {
        "ghe": {"github": {"auth": "gh:ada", "host": GHE}},
        "dotcom": {"github": {"auth": "gh:ada", "host": "github.com"}},
        "cloud": {"github": {"auth": "gh:ada", "host": "acme.ghe.com"}},
        "clash": {"github": {"auth": "gh:ada@other.example", "host": GHE}}}}

    def prof(self, name):
        return self.m.merge_profile(name, self.m.load(quiet=True))

    def test_ref_gets_the_configured_host(self):
        self.assertEqual(self.prof("ghe")["github"]["auth"], f"gh:ada@{GHE}")
        self.assertEqual(self.prof("cloud")["github"]["auth"], "gh:ada@acme.ghe.com")
        self.assertEqual(self.prof("dotcom")["github"]["auth"], "gh:ada")

    def test_token_is_read_for_the_host_and_the_helper_names_it(self):
        res, _ = self.quiet(self.m.assemble, self.prof("ghe"))
        argv = [c["argv"] for c in self.calls("gh") if c["argv"][:2] == ["auth", "token"]]
        self.assertEqual(len(argv), 1)
        self.assertEqual(argv[0][argv[0].index("--hostname") + 1], GHE)
        env = res["env"]
        self.assertEqual(env["GH_HOST"], GHE)
        self.assertEqual(env["GH_ENTERPRISE_TOKEN"], "gho_ada")
        self.assertNotIn("GH_TOKEN", env)
        pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
                 for i in range(int(env["GIT_CONFIG_COUNT"])) if env[f"GIT_CONFIG_VALUE_{i}"]}
        helper = pairs[f"credential.https://{GHE}.helper"]
        self.assertIn(f"gh:ada@{GHE}", helper)
        self.assertNotIn("credential.https://github.com.helper", pairs)

    def test_status_and_disagreement_unchanged(self):
        ids = self.m.identities(self.prof("ghe"))
        self.assertEqual(ids["github"], {"auth": "gh", "account": "ada", "host": GHE})
        with self.assertRaises(self.m.ManifestError):
            self.prof("clash")


class GitIdentityScrubV2(Sandbox):
    manifest = {"version": 2, "profiles": {
        "b": {"git": {"name": "Client B", "email": "b@client-b.example", "ssh_key": "~/.ssh/client_b"}},
        "keep": {"git": {"name": "Client B", "email": "b@client-b.example", "ssh_key": "~/.ssh/client_b"},
                 "inherit": ["GIT_SSH_COMMAND"]},
        "own": {"git": "inherit"},
        "plain": {"env": {"X": "1"}}}}

    def run_profile(self, name, parent=GIT_PARENT):
        os.environ.update(parent)
        res = self.m.assemble(self.m.merge_profile(name, self.m.load(quiet=True)), want_secrets=False)
        return res, self.m.child_env(res)

    def git_ident(self, env, which):
        return subprocess.run(["git", "var", f"GIT_{which}_IDENT"], capture_output=True, text=True,
                              env=env, cwd=str(self.tmp)).stdout.rsplit(">", 1)[0] + ">"

    def test_shell_identity_does_not_override_the_profile(self):
        res, env = self.run_profile("b")
        for k in GIT_VARS:
            self.assertIn(k, res["remove"])
            self.assertNotIn(k, env)
        self.assertEqual(sorted(res["scrubbed"]), GIT_VARS)
        self.assertEqual(self.git_ident(env, "AUTHOR"), "Client B <b@client-b.example>")
        self.assertEqual(self.git_ident(env, "COMMITTER"), "Client B <b@client-b.example>")

    def test_a_real_commit_is_authored_by_the_profile(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        _, env = self.run_profile("b")
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"], env=env, check=True)
        log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%an <%ae> %cn <%ce>"],
                             capture_output=True, text=True, env=env).stdout.strip()
        self.assertEqual(log, "Client B <b@client-b.example> Client B <b@client-b.example>")

    def test_inherit_keeps_a_variable(self):
        res, env = self.run_profile("keep")
        self.assertEqual(env["GIT_SSH_COMMAND"], GIT_PARENT["GIT_SSH_COMMAND"])
        self.assertNotIn("GIT_AUTHOR_EMAIL", env)
        res, env = self.run_profile("own")
        for k in GIT_VARS:
            self.assertEqual(env[k], GIT_PARENT[k])

    def test_profile_without_git_block_drops_another_clients_identity(self):
        # v2: the variables of every module the manifest mentions are cleared.
        _, env = self.run_profile("plain")
        for k in GIT_VARS:
            self.assertNotIn(k, env)

    def test_git_ssh_is_left_alone(self):
        # git ranks core.sshCommand above GIT_SSH, so it needs no scrubbing.
        _, env = self.run_profile("b", dict(GIT_PARENT, GIT_SSH="/usr/bin/ssh"))
        self.assertEqual(env["GIT_SSH"], "/usr/bin/ssh")


class GitIdentityScrubV1(Sandbox):
    """D-A: a v1 file only clears what it manages, i.e. what the environment sets."""
    manifest = {"version": 1, "defaults": {"provider": "subscription"}, "environments": {
        "full": {"github": {"account": "ada", "name": "Ada Example", "email": "ada@example.com"}},
        "email": {"github": {"account": "ada", "email": "ada@corp.example"}},
        "none": {"aws": {"profile": "p"}}}}

    def env_for(self, name, parent=None):
        os.environ.update(parent or dict(GIT_PARENT, AWS_ROLE_ARN="arn:aws:iam::1:role/a",
                                         AWS_WEB_IDENTITY_TOKEN_FILE="/tmp/tok"))
        res = self.m.assemble(self.m.merge_profile(name, self.m.load(quiet=True)), want_secrets=False)
        return res, self.m.child_env(res)

    def test_identity_the_environment_sets_is_cleared(self):
        _, env = self.env_for("full")
        for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.assertNotIn(k, env)
        self.assertEqual(env["GIT_SSH_COMMAND"], GIT_PARENT["GIT_SSH_COMMAND"])   # v1 had no ssh_key

    def test_only_the_keys_it_sets(self):
        _, env = self.env_for("email")
        self.assertNotIn("GIT_AUTHOR_EMAIL", env)
        self.assertNotIn("GIT_COMMITTER_EMAIL", env)
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Client A")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Client A")

    def test_no_git_identity_keeps_the_shell_and_v1_aws_scope_is_unchanged(self):
        _, env = self.env_for("none")
        for k in GIT_VARS:
            self.assertEqual(env[k], GIT_PARENT[k])
        self.assertEqual(env["AWS_ROLE_ARN"], "arn:aws:iam::1:role/a")
        self.assertEqual(env["AWS_WEB_IDENTITY_TOKEN_FILE"], "/tmp/tok")


CLOUD_PARENT = {
    "CLOUDSDK_CORE_ACCOUNT": "a@client-a.example", "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE": "/tmp/a",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": "/tmp/a.json",
    "AZURE_CLIENT_ID": "id-a", "AZURE_CLIENT_SECRET": "azure-SECRET-a", "AZURE_TENANT_ID": "t-a",
    "AZURE_SUBSCRIPTION_ID": "s-a", "AZURE_FEDERATED_TOKEN_FILE": "/tmp/f", "ARM_ACCESS_KEY": "k-a",
    "AWS_ROLE_ARN": "arn:aws:iam::1:role/a", "AWS_ROLE_SESSION_NAME": "a",
    "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/tok", "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://x",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/tmp/c"}


class CloudIdentityScrub(Sandbox):
    manifest = {"version": 2, "fallback": "b", "profiles": {
        "b": {"gcp": {"configuration": "b"}, "azure": {"subscription": "s-b"}, "aws": {"profile": "b"}},
        "keep": {"aws": "inherit", "inherit": ["AZURE_CLIENT_ID"]}}}

    def env_for(self, name):
        os.environ.update(CLOUD_PARENT)
        res = self.m.assemble(self.m.merge_profile(name, self.m.load(quiet=True)), want_secrets=False)
        return res, self.m.child_env(res)

    def test_cloud_identity_overrides_are_cleared(self):
        res, env = self.env_for("b")
        for k in CLOUD_PARENT:
            self.assertNotIn(k, env, k)
        self.assertEqual(sorted(res["scrubbed"]), sorted(CLOUD_PARENT))

    def test_inherit_keeps_them(self):
        _, env = self.env_for("keep")
        self.assertEqual(env["AZURE_CLIENT_ID"], "id-a")
        for k in ("AWS_ROLE_ARN", "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE"):
            self.assertEqual(env[k], CLOUD_PARENT[k])
        self.assertNotIn("AZURE_CLIENT_SECRET", env)

    def test_doctor_offers_aws_inherit_for_a_pattern_variable(self):
        env = {"AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://x"}
        out = json.loads(self.cli("doctor", "--json", env=env).stdout)
        (c,) = [c for c in out["checks"] if c["name"] == "shell"]
        self.assertIn("AWS_CONTAINER_CREDENTIALS_FULL_URI", c["detail"])
        self.assertIn("`aws: inherit`", c["detail"])


class GhRefOutsideManagedHomes(Sandbox):
    """read_gh: gh: refs name accounts in the user's own gh config, never in a
    managed home a parent session left in GH_CONFIG_DIR."""
    manifest = {"version": 2, "profiles": {"b": {"github": {"home": "work"}},
                                           "a": {"github": {"auth": "gh:ada"}}}}

    def setUp(self):
        super().setUp()
        gh = self.fakebin / "gh"
        gh.write_text(GH_LOGGING_CFG)
        gh.chmod(0o755)

    def token_reads(self):
        return [c["cfg"] for c in self.calls("gh") if c["argv"][:2] == ["auth", "token"]]

    def test_exec_a_from_a_b_session_reads_the_users_gh(self):
        cfg = self.m.load(quiet=True)
        env_b = self.m.child_env(self.m.assemble(self.m.merge_profile("b", cfg), want_secrets=False,
                                                 ctx={"materialize": True}))
        self.assertIn("/homes/github/work", env_b["GH_CONFIG_DIR"])
        os.environ.clear()
        os.environ.update(env_b)                           # inside `actas run b`
        res, err = self.quiet(self.m.assemble, self.m.merge_profile("a", cfg))
        self.assertEqual(self.token_reads(), [""])
        self.assertEqual(res["env"]["GH_TOKEN"], "gho_ada", err)

    def test_off_home_and_symlinked_state_are_dropped(self):
        state = self.tmp / "state"
        os.environ["GH_CONFIG_DIR"] = str(state / "homes" / "github" / "_off") + "/"
        self.assertEqual(self.m.read_gh("ada", "github.com")[0], "gho_ada")
        link = self.tmp / "state-link"
        (state / "homes" / "github" / "work").mkdir(parents=True, exist_ok=True)
        link.symlink_to(state)
        os.environ["GH_CONFIG_DIR"] = str(link / "homes" / "github" / "work")
        self.assertEqual(self.m.read_gh("ada", "github.com")[0], "gho_ada")
        self.assertEqual(self.token_reads(), ["", ""])

    def test_users_own_gh_config_dir_is_kept(self):
        own = self.tmp / "my-gh"
        own.mkdir()
        (own / "hosts.yml").write_text("github.com: {}\n")
        os.environ["GH_CONFIG_DIR"] = str(own)
        self.assertEqual(self.m.read_gh("ada", "github.com")[0], "gho_ada")
        self.assertEqual(self.token_reads(), [str(own)])


if __name__ == "__main__":
    unittest.main()
