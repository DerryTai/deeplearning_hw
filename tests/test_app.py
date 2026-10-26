import unittest

from app import create_app, format_trace, start_chat


class AppTests(unittest.TestCase):
    def test_greeting_and_trace_are_visible(self):
        self.assertIn("50嵐", start_chat()[0]["content"])
        rendered = format_trace([{"step": 1, "tool": "find_drink", "arguments": {"name": "珍珠奶茶"},
                                  "result": {"found": True, "items": [{"price": 55}]}}])
        self.assertIn("find_drink", rendered)
        self.assertIn("珍珠奶茶", rendered)
        self.assertIn("55", rendered)

    def test_app_constructs_without_api_key(self):
        self.assertIsNotNone(create_app())


if __name__ == "__main__":
    unittest.main()
