import unittest
from unittest.mock import patch

import requests

from core.llm_manager import (
    fetch_openai_completion_logprobs,
    fetch_openai_next_token_logprobs,
)


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}: {self.text}")


class TestOpenAILogprobExtraction(unittest.TestCase):
    def test_falls_back_from_modern_to_legacy_for_llamacpp(self):
        calls = []

        def fake_post(url, json, timeout):
            calls.append(json)
            if json["logprobs"] is True:
                return _Response(400, text="modern schema rejected")
            return _Response(
                200,
                {
                    "choices": [
                        {
                            "logprobs": {
                                "top_logprobs": [
                                    {" yes": -0.25, " no": {"logprob": -1.5}}
                                ]
                            }
                        }
                    ]
                },
            )

        with patch("core.llm_manager.requests.post", side_effect=fake_post):
            result = fetch_openai_next_token_logprobs(
                "http://localhost:8080", "The answer is", "local.gguf", top_k=5
            )

        self.assertEqual(result[" yes"], -0.25)
        self.assertEqual(result[" no"], -1.5)
        self.assertIs(calls[0]["logprobs"], True)
        self.assertEqual(calls[1]["logprobs"], 5)

    def test_parses_modern_nested_content_top_logprobs_for_ollama(self):
        response = _Response(
            200,
            {
                "choices": [
                    {
                        "logprobs": {
                            "content": [
                                {
                                    "token": " yes",
                                    "logprob": -0.1,
                                    "top_logprobs": [
                                        {"token": " yes", "logprob": -0.1},
                                        {"token": " no", "logprob": -2.0},
                                    ],
                                }
                            ]
                        }
                    }
                ]
            },
        )

        with patch("core.llm_manager.requests.post", return_value=response) as post:
            result = fetch_openai_next_token_logprobs(
                "http://localhost:11434/api", "The answer is", "llama3", top_k=5
            )

        self.assertEqual(result, {" yes": -0.1, " no": -2.0})
        self.assertTrue(post.call_args.kwargs["json"]["logprobs"])
        self.assertEqual(post.call_args.kwargs["json"]["top_logprobs"], 5)

    def test_exact_echo_completion_logprobs_from_legacy_schema(self):
        calls = []

        def fake_post(url, json, timeout):
            calls.append(json)
            if json["logprobs"] is True:
                return _Response(422, text="modern schema rejected")
            return _Response(
                200,
                {
                    "choices": [{
                        "logprobs": {
                            "tokens": ["The ", "cat", " sat"],
                            "token_logprobs": [None, -0.2, -0.3],
                        }
                    }]
                },
            )

        with patch("core.llm_manager.requests.post", side_effect=fake_post):
            result = fetch_openai_completion_logprobs(
                "http://localhost:8080", "The ", "cat sat", "local.gguf"
            )

        self.assertTrue(result.exact)
        self.assertEqual(result.mode, "openai_echo_legacy")
        self.assertEqual(result.token_count, 2)
        self.assertAlmostEqual(result.score, -0.25)
        self.assertEqual(calls[1]["max_tokens"], 0)
        self.assertTrue(calls[1]["echo"])

    def test_exact_echo_completion_logprobs_rejects_mismatched_echo(self):
        response = _Response(
            200,
            {
                "choices": [{
                    "logprobs": {
                        "tokens": ["The ", "cat"],
                        "token_logprobs": [None, -0.2],
                    }
                }]
            },
        )

        with patch("core.llm_manager.requests.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "LOGPROBS_UNSUPPORTED"):
                fetch_openai_completion_logprobs(
                    "http://localhost:11434/api", "The ", "dog", "llama3"
                )


if __name__ == "__main__":
    unittest.main()
