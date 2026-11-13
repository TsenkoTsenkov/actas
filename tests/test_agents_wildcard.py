"""agents_may_use: "*" (every profile at the same or a lower level, no
confirm), the list in `defaults:` (a profile's own list replaces it), the
key in v1 manifests, and the not_allowed hint that says how to allow a hop."""

import json
import unittest

from test_m4 import AGENT, REC_PID, T1, Base

V2 = {
    "version": 2,
    "defaults": {"agents_may_use": ["*"]},
    "profiles": {
        "a": {"match": "%(tmp)s/code/std", "github": {"auth": "gh:ada-a"}},
        "b": {"github": {"auth": "gh:ada-b"}},
        "lo": {"match": "%(tmp)s/code/ro", "level": "readonly"},
        "pw": {"level": "power"},
        "conf": {"confirm": True},
        "narrow": {"agents_may_use": []},
        "nulled": {"agents_may_use": None},
        "pick": {"agents_may_use": ["b"]},
    },
}

# A dict (written as JSON): it loads without PyYAML or yq.
V1 = {
    "version": 1,
    "defaults": {"provider": "subscription", "agents_may_use": ["*"]},
    "environments": {
        "p1": {"match": "%(tmp)s/code/std", "github": {"account": "ada-one"}},
        "p2": {"github": {"account": "ada-two"}},
        "p3": {"agents_may_use": ["p1"]},
    },
}


class Wild(Base):
    doc = V2

    def setUp(self):
        super().setUp()
        self.write_manifest(self.doc)
        self.reload()
        self.patch()

    def rec(self, profile, allowed=None):
        d = self.m.SESSIONS
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.json"):
            f.unlink()
        body = {"pid": REC_PID, "start": T1, "profile": profile, "level": "x",
                "root": str(self.m.ROOT), "state": str(self.m.STATE)}
        if allowed is not None:
            body["allowed"] = allowed
        (d / f"{REC_PID}.json").write_text(json.dumps(body))

    def hop(self, session, target, allowed=None):
        self.execs.clear()
        self.rec(session, allowed)
        return self.call("exec", target, "--", "git", "push", env=AGENT, cwd=self.tmp / "code" / "ro")

    def assert_allowed(self, session, target, allowed=None):
        code, _, err = self.hop(session, target, allowed)
        self.assertEqual(code, 0, f"{session} -> {target}: {err}")
        self.assertEqual(self.execs[-1][1]["ACTAS"], target)

    def assert_refused(self, session, target, reason, allowed=None):
        code, _, err = self.hop(session, target, allowed)
        self.assertEqual(code, 3, f"{session} -> {target}: {err}")
        self.assertEqual(self.execs, [])
        self.assertEqual(err.splitlines()[0], f"actas-stop: code=needs_user reason={reason} profile={target}")
        return err


class V2Wildcard(Wild):
    def test_effective_lists(self):
        profs = {n: self.m.merge_profile(n, self.m.load()) for n in V2["profiles"]}
        self.assertEqual(profs["a"]["agents_may_use"], ["*"])          # from defaults
        self.assertEqual(profs["narrow"]["agents_may_use"], [])        # own [] replaces defaults
        self.assertEqual(profs["nulled"]["agents_may_use"], [])        # so does an explicit null
        self.assertEqual(profs["pick"]["agents_may_use"], ["b"])

    def test_star_allows_sideways_and_down(self):
        self.assert_allowed("a", "b")
        self.assert_allowed("a", "lo")
        self.assert_allowed("b", "a")

    def test_star_keeps_level_and_confirm_rules(self):
        self.assert_refused("a", "pw", "elevation")
        self.assert_refused("a", "conf", "confirm")

    def test_own_list_replaces_defaults(self):
        self.assert_refused("narrow", "b", "not_allowed")
        self.assert_refused("nulled", "b", "not_allowed")
        self.assert_refused("pick", "a", "not_allowed")
        self.assert_allowed("pick", "b")

    def test_hint_says_how_to_allow(self):
        err = self.assert_refused("narrow", "b", "not_allowed")
        self.assertIn("For the user, not the agent", err)
        self.assertIn("add b (or \"*\"", err)
        self.assertIn("of profiles.narrow (its own list; defaults.agents_may_use does not apply", err)
        self.assertNotIn("start a new session", err)

    def test_hint_for_a_profile_using_the_defaults_list(self):
        self.write_manifest({"version": 2, "defaults": {"agents_may_use": ["b"]},
                             "profiles": {"a": {}, "b": {}, "c": {}}})
        err = self.assert_refused("a", "c", "not_allowed")
        self.assertIn("in defaults (applies to every profile without its own list)", err)

    def test_no_hint_when_allowing_would_not_help(self):
        self.write_manifest({"version": 2, "profiles": {"a": {}, "pw": {"level": "power"}}})
        err = self.assert_refused("a", "pw", "not_allowed")
        self.assertNotIn("For the user, not the agent", err)

    def test_nested_record_keeps_its_list(self):
        self.assert_allowed("narrow", "b", allowed=["*"])           # outer session had "*"
        err = self.assert_refused("a", "b", "not_allowed", allowed=["a"])
        self.assertIn("nested agent session keeps the agents_may_use list", err)
        self.assertIn("start a new session", err)

    def test_no_session_is_still_refused(self):
        self.execs.clear()
        for f in self.m.SESSIONS.glob("*.json") if self.m.SESSIONS.exists() else []:
            f.unlink()
        code, _, err = self.call("exec", "b", "--", "true", env=dict(AGENT, ACTAS=None),
                                 cwd=self.tmp / "code" / "ro")
        self.assertEqual(code, 3, err)
        self.assertIn("reason=no_session", err.splitlines()[0])

    def test_humans_unaffected(self):
        self.execs.clear()
        code, _, err = self.call("exec", "b", "--", "true", ps=self.ps(), env={"ACTAS": "a"})
        self.assertEqual(code, 0, err)

    def test_doctor_notes_the_wildcard(self):
        code, out, err = self.call("doctor")
        text = out + err
        self.assertIn('"*" for a, b, lo, pw:', text)          # not conf: agents never use it
        self.assertNotIn("unknown profile '*'", text)


class V2Validation(Wild):
    def errors_for(self, doc):
        self.write_manifest(doc)
        code, out, err = self.call("doctor")
        return code, out + err

    def test_bad_entries(self):
        code, text = self.errors_for({"version": 2, "profiles": {"a": {"agents_may_use": ["*x"]}}})
        self.assertNotEqual(code, 0)
        self.assertIn("invalid name '*x'", text)
        self.assertIn('use "*" alone', text)

    def test_defaults_entries_checked_once(self):
        code, text = self.errors_for({"version": 2, "defaults": {"agents_may_use": ["ghost"]},
                                      "profiles": {"a": {}, "b": {}}})
        self.assertEqual(text.count("unknown profile 'ghost'"), 1, text)
        self.assertIn("defaults.agents_may_use", text)

    def test_role_still_rejects_it(self):
        code, text = self.errors_for({"version": 2, "roles": {"r": {"agents_may_use": ["*"]}},
                                      "profiles": {"a": {"use": ["r"]}}})
        self.assertNotEqual(code, 0)
        self.assertIn("agents_may_use belongs to profiles", text)

    def test_defaults_still_reject_match(self):
        code, text = self.errors_for({"version": 2, "defaults": {"match": "~/x"}, "profiles": {"a": {}}})
        self.assertNotEqual(code, 0)
        self.assertIn("not allowed in defaults", text)


class V1Wildcard(Wild):
    doc = V1

    def test_v1_loads_and_translates(self):
        cfg = self.m.load()
        self.assertEqual(cfg.version, 1)
        self.assertEqual(self.m.merge_profile("p1", cfg)["agents_may_use"], ["*"])
        self.assertEqual(self.m.merge_profile("p3", cfg)["agents_may_use"], ["p1"])

    def test_v1_gate(self):
        self.assert_allowed("p1", "p2")
        self.assert_allowed("p3", "p1")
        err = self.assert_refused("p3", "p2", "not_allowed")
        self.assertIn("agents_may_use of environments.p3, or in defaults", err)

    def test_v1_bad_value(self):
        bad = json.loads(json.dumps(V1))
        bad["defaults"]["agents_may_use"] = "nope"
        self.write_manifest(bad)
        code, out, err = self.call("doctor")
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("agents_may_use", out + err)


if __name__ == "__main__":
    unittest.main()
