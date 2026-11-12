"""README.md: its manifests load and resolve, and the outputs it shows match
what the tool prints (SPEC 0.1, test 18). Everything runs in the Sandbox
(temp HOME and config, fake binaries)."""

import re
import unittest

from actas_testlib import REPO, Sandbox, has_yaml

README = (REPO / "README.md").read_text()


def yaml_blocks(text):
    return [b for b in re.findall(r"```yaml\n(.*?)```", text, re.S) if b.startswith("version: 2")]


def text_block_after(text, marker):
    """The first fenced block after `marker`."""
    tail = text[text.index(marker):]
    return re.search(r"```[a-z]*\n(.*?)```", tail, re.S).group(1)


MINIMAL, ROLES = (yaml_blocks(README) + [None, None])[:2]


@unittest.skipUnless(has_yaml(), "needs PyYAML")
class ReadmeManifests(Sandbox):

    def test_two_complete_manifests(self):
        self.assertEqual(len(yaml_blocks(README)), 2)
        self.assertLessEqual(len(MINIMAL.strip().splitlines()), 15)    # SPEC 0.1

    def test_minimal_manifest_resolves(self):
        self.write_manifest(MINIMAL)
        m = self.reload()
        cfg = m.load(quiet=True)
        self.assertEqual(cfg.problems.items, [])
        p = m.resolve(cwd=str(self.home / "code/client-a/api"))
        self.assertEqual(p["name"], "client-a")
        self.assertEqual(p["claude"]["provider"], "gateway")
        self.assertEqual(p["claude"]["token"], "keychain:actas/client-a")
        self.assertEqual(m.resolve(cwd=str(self.home / "code/personal"))["aws"]["profile"], "personal")

    def test_roles_example_composes_as_described(self):
        self.write_manifest(ROLES)
        m = self.reload()
        self.assertEqual(m.load(quiet=True).problems.items, [])
        base = m.resolve(cwd=str(self.home / "code/sandbox-x"))
        self.assertEqual((base["name"], base["level"]), ("personal", "readonly"))
        p = m.resolve("personal-power")
        self.assertEqual((p["level"], p["confirm"]), ("power", True))
        self.assertEqual(p["aws"], {"profile": "personal-poweruser", "region": "eu-west-1"})
        self.assertEqual(p["github"]["auth"], "sops:personal/gh_ro_pat")
        self.assertEqual(p["git"]["email"], "ada@example.com")
        self.assertIn("Bash(pulumi up *)", p["permissions"]["deny"])

    def test_status_why_lines_match(self):
        self.write_manifest(ROLES)
        r = self.cli("status", "personal-power", "--why")
        self.assertEqual(r.returncode, 0, r.stderr)
        shown = text_block_after(README, "`actas status personal-power --why`")
        for line in shown.strip().splitlines():
            self.assertIn(line, r.stdout)

    def test_agent_refusal_text_matches(self):
        self.write_manifest(ROLES)
        r = self.cli("exec", "personal-power", "--reason", "apply the reviewed plan", "--",
                     "terraform", "apply", env={"CLAUDECODE": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        shown = re.search(r"```text\n(actas-stop: .*?)```", README, re.S).group(1).strip()
        self.assertEqual(r.stderr.strip(), shown)
        self.assertEqual(self.calls("terraform"), [])

    def test_run_badge_matches(self):
        self.write_manifest(MINIMAL)
        work = self.home / "code/client-a/api"
        work.mkdir(parents=True)
        r = self.cli("--cwd", str(work), "run", cwd=str(work),
                     env={"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "ANTHROPIC_API_KEY": "sk-example"})
        self.assertEqual(r.returncode, 0, r.stderr)
        intro = re.search(r"\$ actas run\n(.*?)\n\n", README, re.S).group(1)
        for line in intro.splitlines():
            self.assertIn(line, r.stderr)


class ReadmeCommands(Sandbox):

    def test_command_tables_cover_every_public_command(self):
        _, sub = self.m.build_parser()
        public = {n for n in sub.choices if n not in self.m.HIDDEN | {"help", "whoami"}}
        everyday = README.split("**Everyday**", 1)[1].split("**Advanced**", 1)[0]
        advanced = README.split("**Advanced**", 1)[1].split("Exit codes:", 1)[0]
        documented = set(re.findall(r"^\| `actas ([a-z-]+)", everyday + advanced, re.M))
        self.assertEqual(documented, public)
        self.assertEqual(set(re.findall(r"^\| `actas ([a-z-]+)", everyday, re.M)),
                         {"init", "run", "exec", "status", "list", "login", "sync", "doctor"})

    def test_relative_links_exist(self):
        for target in set(re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", README)):
            self.assertTrue((REPO / target).exists(), f"README links to missing {target}")

    def test_changelog_has_both_releases(self):
        log = (REPO / "CHANGELOG.md").read_text()
        self.assertRegex(log, r"(?m)^## \[?2\.0\.0\]?")
        self.assertRegex(log, r"(?m)^## \[?1\.1\.0\]?")


if __name__ == "__main__":
    unittest.main()
