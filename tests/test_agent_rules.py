"""AGENT_RULES has one source (SPEC 14.1, test 63): the constant in bin/ccenv.
`ccenv instructions`, AGENTS.md and skills/ccenv/SKILL.md reproduce it
verbatim, and the shipped agent texts never promise an in-session approval
path. The skills' allowed-tools stay narrow."""

import re
import unittest

from ccenv_testlib import REPO, Sandbox

AGENT_TEXTS = ("AGENTS.md", "skills/ccenv/SKILL.md", "skills/aws-handoff/SKILL.md", "docs/agents.md")
BANNED = ("this will prompt", "After approval", "ccenv request")


def frontmatter(path):
    text = (REPO / path).read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path}: no YAML frontmatter"
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)


def allowed_tools(fm):
    return [t.strip() for t in re.findall(r"[A-Za-z]+\([^)]*\)", fm.get("allowed-tools", ""))]


class AgentRulesSingleSource(Sandbox):
    def test_instructions_prints_rules_exactly(self):
        r = self.cli("instructions")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.m.AGENT_RULES + "\n")

    def test_agents_md_and_skill_contain_rules_verbatim(self):
        rules = self.m.AGENT_RULES
        self.assertTrue(rules.startswith("## Credentials (ccenv)\n"))
        for path in ("AGENTS.md", "skills/ccenv/SKILL.md"):
            text = (REPO / path).read_text()
            self.assertIn(rules + "\n", text, f"{path}: AGENT_RULES block is not verbatim; "
                                              "paste the constant from bin/ccenv")
            self.assertEqual(text.count("## Credentials (ccenv)"), 1, path)

    def test_no_in_session_approval_path(self):
        for path in AGENT_TEXTS:
            text = (REPO / path).read_text()
            for bad in BANNED:
                self.assertNotIn(bad, text, f"{path} mentions {bad!r}")


class SkillFrontmatter(unittest.TestCase):
    def test_ccenv_skill_pre_allows_only_read_only_commands(self):
        fm = frontmatter("skills/ccenv/SKILL.md")
        self.assertEqual(fm["name"], "ccenv")
        self.assertNotIn("disable-model-invocation", fm)          # model-invocable
        tools = allowed_tools(fm)
        self.assertTrue(tools)
        for t in tools:
            self.assertRegex(t, r"^Bash\(ccenv (status|list)( \*)?\)$")

    def test_aws_handoff_skill_is_user_invoked_and_never_runs_ccenv(self):
        fm = frontmatter("skills/aws-handoff/SKILL.md")
        self.assertEqual(fm["name"], "aws-handoff")
        self.assertEqual(fm["disable-model-invocation"], "true")
        tools = allowed_tools(fm)
        self.assertTrue(tools)
        for t in tools:
            self.assertRegex(t, r"^Bash\(aws sts get-caller-identity( \*)?\)$")
        body = (REPO / "skills/aws-handoff/SKILL.md").read_text()
        self.assertIn("own terminal", body)
        self.assertIn("ccenv login", body)


if __name__ == "__main__":
    unittest.main()
