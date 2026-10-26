import unittest

from tool_choice_test import CASES, grade_tool_choice


class ToolChoiceTests(unittest.TestCase):
    def test_at_least_ten_cases_and_grading(self):
        self.assertGreaterEqual(len(CASES), 10)
        self.assertTrue(grade_tool_choice(["find_drink"], {"find_drink"}))
        self.assertFalse(grade_tool_choice([], {"find_drink"}))
        self.assertTrue(grade_tool_choice([], set()))
        self.assertFalse(grade_tool_choice(["find_drink"], set()))


if __name__ == "__main__":
    unittest.main()
