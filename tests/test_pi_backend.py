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

    def test_pi_subprocess_transient_failures_follow_auto_retry_contract(self):
        config = ("https://test.invalid/v1", "token-sentinel", "book-translator")
        with patch("omniroute.config", return_value=config), patch.dict(
            "os.environ", {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}
        ):
            for code in ("pi_nonzero_exit", "pi_timeout", "pi_subprocess_failure"):
                with self.subTest(code=code):
                    with patch("pi_backend.complete", side_effect=pi_backend.PiTransportFailure(code)):
                        with self.assertRaises(omniroute.TransientModelError) as caught:
                            omniroute.complete("REVIEWER", "rules", "source")
                    self.assertEqual(caught.exception.code, "transport")
                    self.assertNotIn("token-sentinel", str(caught.exception))
            for code in ("pi_binary_missing", "pi_invalid_configuration",
                         "pi_empty_or_oversize_output"):
                with self.subTest(code=code):
                    with patch("pi_backend.complete", side_effect=pi_backend.PiTransportFailure(code)):
                        with self.assertRaises(omniroute.ModelError) as caught:
                            omniroute.complete("TRANSLATOR", "rules", "source")
                    self.assertNotIsInstance(caught.exception, omniroute.TransientModelError)

    def test_dispatch_is_opt_in(self):
        config = ("https://test.invalid/v1", "token-sentinel", "book-translator")
        with patch("omniroute.config", return_value=config), patch.dict("os.environ", {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}):
            with patch("pi_backend.complete", return_value=('{"ok":true}\n', {"response_chars": 11})):
                result = omniroute.complete("REVIEWER", "rules", "source")
        self.assertEqual(str(result), '{"ok":true}\n')
        with patch("omniroute.config", return_value=config), patch.dict("os.environ", {"BOOK_TRANSLATOR_LLM_BACKEND": "invalid"}):
            with self.assertRaisesRegex(omniroute.ModelError, "unsupported model backend"):
                omniroute.complete("REVIEWER", "rules", "source")


    def event_stream(self, usage=None, *, reason="stop", extra=(), settled=True):
        message = {"role": "assistant", "stopReason": reason,
                   "content": [{"type": "text", "text": '{"ok":true}'}],
                   "usage": usage, "model": "private-model-sentinel"}
        events = [{"type": "session"}, {"type": "agent_start"}, {"type": "turn_start"},
                  {"type": "message_end", "message": {"role": "user", "content": "secret-prompt"}},
                  {"type": "message_end", "message": message}, *extra,
                  {"type": "agent_end", "willRetry": False}]
        if settled:
            events.append({"type": "agent_settled", "aborted": False})
        return "\n".join(json.dumps(e) for e in events) + "\n"

    def test_json_mode_extracts_one_message_and_usage(self):
        usage = {"input": 10, "output": 3, "cacheRead": 5, "cacheWrite": 2,
                 "totalTokens": 20, "cost": {"total": 0}}
        with patch.dict("os.environ", {"BOOK_TRANSLATOR_PI_USAGE_EVENTS": "on"}), \
                patch("pi_backend.shutil.which", return_value="/bin/pi"), \
                patch("pi_backend.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout=self.event_stream(usage), stderr="")) as run:
            answer, diagnostics = pi_backend.complete("https://test.invalid/v1", "model", "key",
                                                       "rules", "user", role="REVIEWER")
        self.assertIn("--mode", run.call_args.args[0])
        self.assertIn("json", run.call_args.args[0])
        self.assertEqual(answer, '{"ok":true}\n')
        self.assertEqual(diagnostics["prompt_tokens"], 17)
        self.assertEqual(diagnostics["completion_tokens"], 3)
        self.assertEqual(diagnostics["total_tokens"], 20)
        self.assertNotIn("cost", diagnostics)
        self.assertNotIn("private-model-sentinel", str(diagnostics))
        self.assertNotIn("secret-prompt", str(diagnostics))

    def test_json_thinking_blocks_are_discarded_like_print_mode(self):
        events = [json.loads(line) for line in self.event_stream().split("\n") if line]
        message = next(e["message"] for e in events if e["type"] == "message_end"
                       and e["message"]["role"] == "assistant")
        message["content"].insert(0, {"type": "thinking", "thinking": "private-thinking-sentinel"})
        answer, diagnostics = pi_backend.parse_json_events("\n".join(json.dumps(e) for e in events))
        self.assertEqual(answer, '{"ok":true}\n')
        self.assertNotIn("private-thinking-sentinel", str(diagnostics))
        message["content"].append({"type": "toolCall", "name": "shell", "arguments": {}})
        with self.assertRaises(pi_backend.PiTransportFailure):
            pi_backend.parse_json_events("\n".join(json.dumps(e) for e in events))

    def test_missing_zero_or_inconsistent_usage_remains_unknown(self):
        for usage in (None, {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                             "totalTokens": 0},
                      {"input": 10, "output": 3, "cacheRead": 0, "cacheWrite": 0,
                       "totalTokens": 99}):
            _, diagnostics = pi_backend.parse_json_events(self.event_stream(usage))
            self.assertNotIn("total_tokens", diagnostics)

    def test_incomplete_stop_reasons_are_closed_and_sanitized(self):
        expected = [("length", "pi_stop_length"), ("error", "pi_stop_error"),
                    ("aborted", "pi_stop_aborted"), ("toolUse", "pi_stop_tool_use"),
                    ("secret-sentinel", "pi_incomplete_assistant_message"),
                    ({"private": "secret-sentinel"}, "pi_incomplete_assistant_message")]
        for reason, code in expected:
            events = [json.loads(line) for line in self.event_stream(reason=reason).splitlines()]
            msg = next(e["message"] for e in events if e["type"] == "message_end"
                       and e["message"]["role"] == "assistant")
            msg["errorMessage"] = "private-error-sentinel"
            with self.assertRaises(pi_backend.PiTransportFailure) as caught:
                pi_backend.parse_json_events("\n".join(json.dumps(e) for e in events))
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(str(caught.exception), code)

    def test_extra_turn_retry_and_incomplete_stream_fail_closed(self):
        for output in (self.event_stream(extra=[{"type": "turn_start"}]),
                       self.event_stream(extra=[{"type": "auto_retry_start"}]),
                       self.event_stream(extra=[{"type": "tool_execution_start"}]),
                       self.event_stream(settled=False),
                       self.event_stream(reason="length"),
                       "not-json-private-sentinel"):
            with self.assertRaises(pi_backend.PiTransportFailure) as caught:
                pi_backend.parse_json_events(output)
            self.assertNotIn("private-sentinel", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
