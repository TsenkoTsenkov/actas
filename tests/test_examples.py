"""The shipped examples and the manifests in docs/ load, validate and resolve.

examples/environments.yaml (v2) and examples/environments.v1.yaml (v1) are
read from the repository, never copied, so the files themselves are tested.
YAML blocks in docs/manifest.md and docs/recipes.md whose top-level keys are
all manifest keys are checked the same way, so the docs cannot drift into
invalid examples. Fake binaries only (Sandbox): nothing reaches a real gh,
aws or sops, and no secret is read (want_secrets=False).
"""

import json
import re
import unittest

from ccenv_testlib import REPO, Sandbox, has_yaml

TOP_KEYS = {"version", "defaults", "profiles", "roles", "fallback"}


def yaml_blocks(path):
    return re.findall(r"^```yaml\n(.*?)^```", path.read_text(), re.S | re.M)


@unittest.skipUnless(has_yaml(), "the examples are YAML; needs PyYAML")
class Examples(Sandbox):

    def use(self, text):
        """Write a manifest, load it strictly, and resolve every profile."""
        (self.root / "environments.yaml").write_text(text)
        m = self.reload()
        cfg = m.load(quiet=True)
        problems = [(lvl, p, msg) for lvl, p, msg in cfg.problems.items if lvl != "info"]
        self.assertEqual(problems, [], "the manifest should load without errors or warnings")
        self.assertTrue(cfg.profiles)
        profs = {}
        for name in cfg.profiles:
            prof = m.merge_profile(name, cfg)            # also checks provider requirements
            res = m.assemble(prof, want_secrets=False)
            self.assertEqual(res["env"]["CCENV"], name)
            m.settings_for(prof, res)
            profs[name] = prof
        nodes = dict(cfg.roles)
        nodes.update(cfg.profiles)
        for name in cfg.roles:
            m.linearize(name, nodes)
        return m, cfg, profs

    def test_v2_example(self):
        m, cfg, profs = self.use((REPO / "examples/environments.yaml").read_text())
        self.assertEqual(cfg.version, 2)
        self.assertEqual(cfg.fallback, "scratch")
        self.assertEqual(profs["personal"]["level"], "readonly")
        pp = profs["personal-power"]
        self.assertEqual((pp["level"], pp["confirm"]), ("power", True))
        self.assertEqual(pp["aws"], {"profile": "personal-poweruser", "region": "eu-west-1"})
        self.assertEqual(pp["github"]["auth"], "sops:personal/gh_ro_pat")
        prod = profs["client-a-prod"]
        self.assertEqual(prod["level"], "readonly")
        self.assertIn("Bash(terraform apply *)", prod["permissions"]["deny"])
        self.assertEqual(prod["claude"]["provider"], "gateway")
        self.assertEqual(profs["client-a-test"]["agents_may_use"], ["client-a-prod"])
        self.assertIn(("aws",), profs["client-b"]["_nulled"])
        self.assertEqual(profs["partner"]["tool"], "codex")
        # Directory resolution, including the longest match and the fallback.
        home = str(self.home)
        for cwd, want in (("code/client-a/api", "client-a-test"),
                          ("code/client-a/infra/prod/vpc", "client-a-prod"),
                          ("code/client-a-tools", "client-a-test"),
                          ("code/sandbox-1", "personal"),
                          ("code/client-b", "client-b"),
                          ("elsewhere", "scratch")):
            self.assertEqual(m.locate(cfg, f"{home}/{cwd}")[0], want, cwd)
        # doctor: no manifest errors or warnings (infos are fine).
        r = self.cli("doctor", "--json")
        manifest = [c for c in json.loads(r.stdout)["checks"] if c["name"] == "manifest"][0]
        self.assertEqual(manifest["status"], "ok", manifest)

    def test_v1_example(self):
        m, cfg, profs = self.use((REPO / "examples/environments.v1.yaml").read_text())
        self.assertEqual(cfg.version, 1)
        self.assertEqual(profs["client-a"]["claude"]["token"], "sops:client-a/token")
        self.assertEqual(profs["client-a-infra"]["claude"]["token"], "sops:client-a/token")
        self.assertEqual(profs["partner-org"]["github"]["auth"], "sops:partner-org/gh_token")
        # migrate prints a v2 document that loads and keeps every profile.
        r = self.cli("migrate")
        self.assertEqual(r.returncode, 0, r.stderr)
        m2, cfg2, profs2 = self.use(r.stdout)
        self.assertEqual(cfg2.version, 2)
        self.assertEqual(sorted(profs2), sorted(profs))

    def test_cli_lists_both_examples(self):
        for name in ("environments.yaml", "environments.v1.yaml"):
            (self.root / "environments.yaml").write_text((REPO / "examples" / name).read_text())
            r = self.cli("list", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["profiles"])

    def test_manifests_in_docs(self):
        checked = 0
        for doc in ("docs/manifest.md", "docs/recipes.md"):
            for block in yaml_blocks(REPO / doc):
                import yaml
                data = yaml.safe_load(block)
                if not isinstance(data, dict) or not set(data) <= TOP_KEYS:
                    continue                             # a fragment, not a manifest
                if "version" not in data:
                    block = "version: 2\n" + block
                if "profiles" not in data:
                    block += "\nprofiles:\n  doc-example-profile: {}\n"
                with self.subTest(doc=doc, block=block.splitlines()[1][:60]):
                    self.use(block)
                    checked += 1
        self.assertGreaterEqual(checked, 20)


if __name__ == "__main__":
    unittest.main()
