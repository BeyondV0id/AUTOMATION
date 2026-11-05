from __future__ import annotations

import re
import unittest
from pathlib import Path

from resume_pipeline.browser import BrowserError, Session, SessionConfig, load_config, open_session
from resume_pipeline.browser.ego_backend import as_function
from resume_pipeline.browser.flows import (
    NEXT_LABELS,
    SUBMIT_WORDS,
    click_next,
    detect_ats,
    dismiss_cookies,
    find_submit_buttons,
)


class FakeSession(Session):
    """Answers evaluate() from a queue of canned returns."""

    kind = "fake"
    supports_frames = False

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []
        self.waited = []

    def evaluate(self, js, arg=None, frame=0):
        self.calls.append(arg)
        if not self.answers:
            return None
        return self.answers.pop(0)

    def wait(self, ms):
        self.waited.append(ms)


class FrameGuardTest(unittest.TestCase):
    def test_ego_like_session_refuses_frame_work(self):
        with self.assertRaises(BrowserError) as caught:
            FakeSession().require_frames("fill")
        self.assertIn("playwright", str(caught.exception))

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(BrowserError):
            open_session(backend="netscape")

    def test_unknown_setting_is_rejected(self):
        with self.assertRaises(BrowserError):
            open_session(backend="playwright", turbo=True)


class ConfigTest(unittest.TestCase):
    def test_defaults_to_playwright(self):
        cfg = load_config(Path("does-not-exist.toml"))
        self.assertEqual(cfg.backend, "playwright")
        self.assertTrue(cfg.headless)

    def test_repo_config_parses(self):
        cfg = load_config()
        self.assertIn(cfg.backend, {"playwright", "ego", "auto"})
        self.assertTrue(cfg.ego_space)


class FunctionSourceTest(unittest.TestCase):
    def test_bare_expression_is_wrapped(self):
        self.assertEqual(as_function("document.title"), "() => (document.title)")

    def test_function_source_is_kept(self):
        source = "() => document.title"
        self.assertEqual(as_function(source), source)

    def test_async_source_is_kept(self):
        source = "async () => 1"
        self.assertEqual(as_function(source), source)


class CookieTest(unittest.TestCase):
    def test_clicks_until_no_match(self):
        session = FakeSession(["Accept all", "Got it", ""])
        self.assertEqual(dismiss_cookies(session), ["Accept all", "Got it"])
        self.assertEqual(len(session.calls), 2)

    def test_stops_on_exception(self):
        class Broken(FakeSession):
            def evaluate(self, js, arg=None, frame=0):
                raise RuntimeError("page gone")

        self.assertEqual(dismiss_cookies(Broken()), [])


class NextTest(unittest.TestCase):
    def test_returns_true_when_clicked(self):
        session = FakeSession(["Next"])
        self.assertTrue(click_next(session))
        self.assertEqual(session.waited, [700])

    def test_returns_false_when_nothing_matches(self):
        self.assertFalse(click_next(FakeSession([""])))


class SubmitGuardTest(unittest.TestCase):
    def test_submit_words_catch_the_dangerous_labels(self):
        pattern = re.compile(SUBMIT_WORDS, re.I)
        for label in ("Submit", "Submit application", "Apply now", "Finish application", "Complete application"):
            self.assertTrue(pattern.search(label), label)

    def test_next_labels_never_include_submit_words(self):
        self.assertIsNone(re.search(SUBMIT_WORDS, "Next", re.I))
        self.assertIsNone(re.search(SUBMIT_WORDS, "Continue", re.I))
        self.assertIsNotNone(re.match(NEXT_LABELS, "Save and continue", re.I))
        self.assertIsNone(re.match(NEXT_LABELS, "Submit", re.I))

    def test_find_submit_buttons_dedupes(self):
        session = FakeSession([["Submit", "Submit", "Apply now"]])
        self.assertEqual(find_submit_buttons(session), ["Submit", "Apply now"])


class AtsTest(unittest.TestCase):
    def test_detects_from_url(self):
        name, _ = detect_ats("https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(name, "greenhouse")

    def test_falls_back_to_document(self):
        session = FakeSession(["<html>ashbyhq.com</html>"])
        name, _ = detect_ats("https://example.com/apply", session)
        self.assertEqual(name, "ashby")

    def test_unknown_is_generic(self):
        self.assertEqual(detect_ats("https://example.com/apply")[0], "generic")


class EgoExeTest(unittest.TestCase):
    def test_bad_configured_exe_is_reported(self):
        from resume_pipeline.browser.ego_backend import find_exe

        with self.assertRaises(BrowserError):
            find_exe("C:/definitely/not/here/ego-browser.exe")


if __name__ == "__main__":
    unittest.main()
