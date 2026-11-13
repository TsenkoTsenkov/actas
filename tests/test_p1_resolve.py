"""Manifest composition and directory resolution:

- claude.settings.permissions.deny/ask union through `use:` and are sticky,
  like permissions.deny/ask; claude.settings.permissions.allow still replaces.
- A glob match pattern also covers the directories below the ones it matches.
- The no-match hint suggests a pattern that matches the directory, and
  `status` reports "no profile" instead of failing.
"""

import json
import os
import unittest

from actas_testlib import Sandbox

CS = lambda **perms: {"claude": {"settings": {"permissions": perms}}}  # noqa: E731


class SettingsPermissionsCompose(Sandbox):
    manifest = {
        "version": 2,
        "defaults": CS(ask=["Bash(git push *)"]),
        "roles": {"guard": CS(deny=["Bash(rm -rf *)"], ask=["Bash(npm publish *)"],
                              allow=["Bash(ls *)"])},
        "profiles": {
            "p": dict(CS(deny=["Read(./.env)"], allow=["Bash(cat *)"]), use=["guard"]),
            "plain": {"use": ["guard"]},
        },
    }

    def settings(self, name):
        cfg = self.m.load(quiet=True)
        return self.m.settings_for(self.m.merge_profile(name, cfg))["permissions"]

    def test_role_and_profile_deny_ask_are_unioned(self):
        perms = self.settings("p")
        for rule in ("Bash(rm -rf *)", "Read(./.env)"):
            self.assertIn(rule, perms["deny"])
        for rule in ("Bash(npm publish *)", "Bash(git push *)"):
            self.assertIn(rule, perms["ask"])
        pol = self.m.agent_policy(self.m.merge_profile("p", self.m.load(quiet=True)))
        self.assertIn("Bash(rm -rf *)", pol["deny"])
        self.assertIn("Read(./.env)", pol["deny"])

    def test_allow_still_replaces(self):
        # Composing never grants more than one layer says: a profile's allow
        # list replaces the role's.
        self.assertEqual(self.settings("p")["allow"], ["Bash(cat *)"])
        self.assertEqual(self.settings("plain")["allow"], ["Bash(ls *)"])

    def test_provenance_names_each_layer(self):
        prof = self.m.merge_profile("p", self.m.load(quiet=True))
        rec = prof["_prov"][("claude", "settings", "permissions", "deny")]
        self.assertEqual(rec["from"], "p")
        self.assertEqual(rec["was"]["from"], "guard")

    def test_dropping_a_sticky_list_is_an_error(self):
        for body, where in (({"claude": {"settings": None}}, "claude.settings"),
                            ({"claude": None}, "claude"),
                            ({"claude": {"settings": {"permissions": None}}}, "claude.settings.permissions"),
                            (CS(deny=None), "claude.settings.permissions.deny")):
            doc = json.loads(json.dumps(self.manifest))
            doc["profiles"]["q"] = dict(body, use=["guard"])
            self.write_manifest(doc)
            m = self.reload()
            with self.assertRaises(m.ManifestError) as cm:
                m.merge_profile("q", m.load(quiet=True))
            self.assertIn(f"cannot replace {where}", str(cm.exception))
            self.assertIn("(added by guard)", str(cm.exception))

    def test_nulling_allow_is_fine(self):
        doc = json.loads(json.dumps(self.manifest))
        doc["profiles"]["q"] = dict(CS(allow=None), use=["guard"])
        self.write_manifest(doc)
        m = self.reload()
        s = m.settings_for(m.merge_profile("q", m.load(quiet=True)))
        self.assertNotIn("allow", s["permissions"])
        self.assertIn("Bash(rm -rf *)", s["permissions"]["deny"])

    def test_a_bare_string_rule_is_one_rule(self):
        doc = json.loads(json.dumps(self.manifest))
        doc["profiles"]["q"] = dict(CS(deny="Bash(curl *)"), use=["guard"])
        self.write_manifest(doc)
        m = self.reload()
        prof = m.merge_profile("q", m.load(quiet=True))
        self.assertEqual(prof["claude"]["settings"]["permissions"]["deny"],
                         ["Bash(rm -rf *)", "Bash(curl *)"])


class GlobCoversSubdirectories(Sandbox):
    manifest = {
        "version": 2,
        "profiles": {
            "infra-ro": {"match": "~/code/*/infra", "level": "readonly"},
            "dev": {"match": "~/code"},
            "star": {"match": "~/w/x/*"},
            "q": {"match": "~/q/?b"},
        },
    }

    def name(self, rel, m=None):
        m = m or self.m
        return m.locate(m.load(quiet=True), str(self.home / rel))[0]

    def test_below_a_mid_pattern_glob(self):
        self.assertEqual(self.name("code/a/infra"), "infra-ro")
        self.assertEqual(self.name("code/a/infra/modules/vpc"), "infra-ro")
        self.assertEqual(self.name("code/a/infrastructure"), "dev")      # a name prefix is not below
        self.assertEqual(self.name("code/a/other"), "dev")
        self.assertEqual(self.name("q/ab/deep"), "q")

    def test_trailing_star_unchanged(self):
        self.assertEqual(self.name("w/x/y/z"), "star")
        self.assertIsNone(self.name("w/x"))                              # X/* is not X

    def test_status_cli(self):
        (self.home / "code/a/infra/modules").mkdir(parents=True)
        r = self.cli("status", "--json", "--cwd", str(self.home / "code/a/infra/modules"))
        out = json.loads(r.stdout)
        self.assertEqual((out["profile"], out["cwd"]["pattern"]), ("infra-ro", "~/code/*/infra"))

    def test_v1_manifest_too(self):
        self.write_manifest({"environments": {
            "infra": {"match": ["~/code/*/infra"]}, "dev": {"match": ["~/code"]}}})
        m = self.reload()
        self.assertEqual(self.name("code/a/infra/modules", m), "infra")

    def test_nested_note_sees_profiles_below_a_glob(self):
        doc = json.loads(json.dumps(self.manifest))
        doc["profiles"]["vpc"] = {"match": "~/code/a/infra/vpc"}
        self.write_manifest(doc)
        m = self.reload()
        cfg = m.load(quiet=True)
        notes = m.nested_profiles(cfg, m.merge_profile("infra-ro", cfg))
        self.assertTrue(any(n.endswith("→ vpc") for n in notes), notes)


class NoMatch(Sandbox):
    manifest = {"version": 2, "profiles": {"p": {"match": "~/code/x/**"}, "q": {"match": "~/other"}}}

    def setUp(self):
        super().setUp()
        self.x = self.home / "code" / "x"
        (self.x / "src" / "lib").mkdir(parents=True)

    def suggested(self, text):
        start = text.index("`match: [") + len("`match: [")
        return json.loads(text[start:text.index("]`", start)])

    def test_status_json_reports_no_profile(self):
        r = self.cli("status", "--json", "--cwd", str(self.x))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["profile"])
        self.assertIsNone(out["matched_by"])
        self.assertIsNone(out["level"])
        self.assertFalse(out["mismatch"])
        self.assertEqual(out["identities"], {})
        self.assertEqual(out["cwd"], {"path": "~/code/x", "profile": None, "matched_by": None,
                                      "pattern": None})
        self.assertIsNone(out["session"]["profile"])
        self.assertIn("no profile matches ~/code/x (known: p, q)", out["hint"])
        self.assertEqual(self.suggested(out["hint"]), "~/code/x")
        self.assertNotIn("**", out["hint"])

    def test_status_text(self):
        r = self.cli("status", "--cwd", str(self.x))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(none)", r.stdout)
        self.assertIn("~/code/x -> (no match)", r.stdout)
        self.assertIn('add `match: ["~/code/x"]` to a profile (it covers subdirectories too)', r.stdout)

    def test_why_probe_run_exec_still_fail(self):
        for args in (("status", "--why"), ("status", "--probe"), ("status", "--json", "--why"),
                     ("exec", "--", "true"), ("run",)):
            r = self.cli("--cwd", str(self.x), *args)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn('no profile matches ~/code/x (known: p, q); add `match: ["~/code/x"]`',
                          r.stderr, args)

    def test_suggestion_matches_the_directory_and_below(self):
        for d in (self.x, self.x / "src" / "lib"):
            text = self.m.no_match_text(self.m.load(quiet=True), str(d))
            doc = json.loads(json.dumps(self.manifest))
            doc["profiles"]["p"]["match"] = self.suggested(text)
            self.write_manifest(doc)
            for where in (d, d / "deeper"):
                r = self.cli("status", "--json", "--cwd", str(where))
                self.assertEqual(json.loads(r.stdout)["profile"], "p", (d, where))
            self.write_manifest(self.manifest)

    def test_suggests_the_repository_root(self):
        (self.x / ".git").mkdir()
        text = self.m.no_match_text(self.m.load(quiet=True), str(self.x / "src" / "lib"))
        self.assertIn("no profile matches ~/code/x/src/lib", text)
        self.assertEqual(self.suggested(text), "~/code/x")
        (self.x / ".git").rmdir()
        (self.x / ".git").write_text("gitdir: elsewhere\n")             # a worktree
        text = self.m.no_match_text(self.m.load(quiet=True), str(self.x / "src"))
        self.assertEqual(self.suggested(text), "~/code/x")

    def test_never_suggests_home_or_above_as_repository(self):
        (self.home / ".git").mkdir()                                      # a dotfiles repo
        text = self.m.no_match_text(self.m.load(quiet=True), str(self.x / "src"))
        self.assertEqual(self.suggested(text), "~/code/x/src")
        text = self.m.no_match_text(self.m.load(quiet=True), str(self.home))
        self.assertEqual(self.suggested(text), "~")                      # quoted: YAML ~ is null
        outside = str(self.tmp / "outside")
        text = self.m.no_match_text(self.m.load(quiet=True), outside)
        self.assertEqual(self.suggested(text), outside)

    def test_session_with_explicit_unmatched_cwd(self):
        r = self.cli("status", "--json", "--cwd", str(self.x), env={"ACTAS": "q"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIsNone(out["profile"])
        self.assertEqual(out["session"]["profile"], "q")
        # Without --cwd the session's profile is reported as before.
        r = self.cli("status", "--json", cwd=str(self.x), env={"ACTAS": "q", "PWD": str(self.x)})
        self.assertEqual(json.loads(r.stdout)["profile"], "q", r.stderr)

    def test_fallback_still_applies(self):
        doc = json.loads(json.dumps(self.manifest))
        doc["fallback"] = "q"
        self.write_manifest(doc)
        r = self.cli("status", "--json", "--cwd", str(self.x))
        out = json.loads(r.stdout)
        self.assertEqual((out["profile"], out["matched_by"]), ("q", "fallback"))
        self.assertNotIn("hint", out)


if __name__ == "__main__":
    unittest.main()
