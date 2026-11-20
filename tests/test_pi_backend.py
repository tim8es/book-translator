"""Offline tests for the optional Pi backend; never call paid models."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import pi_backend
import omniroute


class PiBackendTests(unittest.TestCase):
    def test_isolated_turn_and_secret_scoping(self):
        def fake_run(argv, **kw):
            for flag in ("--no-tools", "--no-session", "--no-approve", "--print"):
                self.assertIn(flag, argv)
            self.assertNotIn("token-sentinel", " ".join(argv))
            self.assertNotIn("GITHUB_TOKEN", kw["env"])
            self.assertEqual(kw["env"]["OMNIROUTE_API_KEY"], "token-sentinel")
            directory = Path(kw["env"]["PI_CODING_AGENT_DIR"])
            models = json.loads((directory / "models.json").read_text())
            self.assertEqual(models["providers"]["book-omniroute"]["apiKey"], "$OMNIROUTE_API_KEY")
            self.assertEqual(models["providers"]["book-omniroute"]["api"], "openai-completions")
            cfg = json.loads((directory / "settings.json").read_text())
            self.assertFalse(cfg["retry"]["enabled"])
            self.assertEqual(cfg["retry"]["provider"]["maxRetries"], 0)
            self.assertFalse(cfg["compaction"]["enabled"])
            self.assertEqual(kw["input"], "Book excerpt")
            return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}', stderr="")
        with patch("pi_backend.shutil.which", return_value="/bin/pi"):
            with patch("pi_backend.subprocess.run", side_effect=fake_run):
                output, diagnostics = pi_backend.complete("https://test.invalid/v1", "book-translator", "token-sentinel", "Rules", "Book excerpt", role="TRANSLATOR")
        self.assertEqual(output, '{"ok": true}\n')
        self.assertEqual(diagnostics["response_format"], "pi_prompt_json")

    def test_missing_binary_is_closed(self):
        with patch("pi_backend.shutil.which", return_value=None):
            with self.assertRaisesRegex(pi_backend.PiTransportFailure, "pi_binary_missing"):
                pi_backend.complete("https://test.invalid/v1", "model", "token", "rules", "user", role="TRANSLATOR")

    def test_error_redacts_stderr(self):
        with patch("pi_backend.shutil.which", return_value="/bin/pi"):
            with patch("pi_backend.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="secret-prompt-sentinel")):
                with self.assertRaises(pi_backend.PiTransportFailure) as caught:
                    pi_backend.complete("https://test.invalid/v1", "model", "key", "rules", "user", role="REVIEWER")
        self.assertNotIn("secret-prompt-sentinel", str(caught.exception))

    def test_timeout_and_invalid_limit(self):
        with patch("pi_backend.shutil.which", return_value="/bin/pi"):
            with patch("pi_backend.subprocess.run", side_effect=subprocess.TimeoutExpired("pi", 900)):
                with self.assertRaisesRegex(pi_backend.PiTransportFailure, "pi_timeout"):
                    pi_backend.complete("https://test.invalid/v1", "model", "key", "rules", "user", role="REVIEWER")
        with patch("pi_backend.shutil.which", return_value="/bin/pi"), patch.dict("os.environ", {"PI_AGENT_MAX_TOKENS": "invalid"}):
            with self.assertRaisesRegex(pi_backend.PiTransportFailure, "pi_invalid_token_limits"):
                pi_backend.complete("https://test.invalid/v1", "model", "key", "rules", "user", role="REVIEWER")

    def test_dispatch_is_opt_in(self):
        config = ("https://test.invalid/v1", "token-sentinel", "book-translator")
        with patch("omniroute.config", return_value=config), patch.dict("os.environ", {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}):
            with patch("pi_backend.complete", return_value=('{"ok":true}\n', {"response_chars": 11})):
                result = omniroute.complete("REVIEWER", "rules", "source")
        self.assertEqual(str(result), '{"ok":true}\n')
        with patch("omniroute.config", return_value=config), patch.dict("os.environ", {"BOOK_TRANSLATOR_LLM_BACKEND": "invalid"}):
            with self.assertRaisesRegex(omniroute.ModelError, "unsupported model backend"):
                omniroute.complete("REVIEWER", "rules", "source")


if __name__ == "__main__":
    unittest.main()
