import unittest
from unittest.mock import patch

from app import (
    _build_report,
    _correct_rate,
    _paper_cut_question_by_no,
    _question_by_no,
    _question_options,
    _score_display,
    _task_rows,
)


class ScoreDisplayTests(unittest.TestCase):
    def test_score_display_and_rate_use_points(self):
        questions = [
            {"score": 10, "max_score": 20},
            {"score": 20, "max_score": 20},
        ]

        self.assertEqual(_score_display(questions), "30/40")
        self.assertEqual(_correct_rate(questions), "75%")

    def test_score_display_matches_report_example(self):
        questions = [
            {"score": 25, "max_score": 30},
            {"score": 20, "max_score": 30},
            {"score": 20, "max_score": 20},
            {"score": 10, "max_score": 20},
        ]

        self.assertEqual(_score_display(questions), "75/100")
        self.assertEqual(_correct_rate(questions), "75%")

    def test_empty_or_zero_max_score_returns_dash(self):
        self.assertEqual(_score_display([]), "-")
        self.assertEqual(_correct_rate([]), "-")
        self.assertEqual(_score_display([{"score": 0, "max_score": 0}]), "-")
        self.assertEqual(_correct_rate([{"score": 0, "max_score": 0}]), "-")

    def test_numeric_strings_are_supported(self):
        questions = [
            {"score": "10", "max_score": 20},
            {"score": 20, "max_score": "20"},
        ]

        self.assertEqual(_score_display(questions), "30/40")
        self.assertEqual(_correct_rate(questions), "75%")

    def test_report_uses_score_ratio_and_point_rate(self):
        report = _build_report(
            {
                "task_id": "demo",
                "status": "finished",
                "questions": [
                    {"score": 10, "max_score": 20},
                    {"score": 20, "max_score": 20},
                ],
            }
        )

        self.assertIn("- 总分：30/40", report)
        self.assertIn("- 正确率：75%", report)

    def test_task_rows_use_score_ratio_string_for_saved_result(self):
        with (
            patch("app.list_tasks", return_value=[]),
            patch(
                "app.list_results",
                return_value=[
                    {
                        "task_id": "demo",
                        "status": "finished",
                        "questions": [
                            {"score": 10, "max_score": 20},
                            {"score": 20, "max_score": 20},
                        ],
                    }
                ],
            ),
        ):
            rows = _task_rows()

        self.assertEqual(rows[0]["分数"], "30/40")

    def test_question_options_keep_result_order(self):
        questions = [
            {"question_no": "2"},
            {"question_no": "1"},
            {"question_no": "4"},
        ]

        self.assertEqual(_question_options(questions), ["2", "1", "4"])

    def test_question_by_no_matches_correction(self):
        questions = [
            {"question_no": "1", "score": 10},
            {"question_no": "2", "score": 20},
        ]

        self.assertEqual(_question_by_no(questions, "2")["score"], 20)
        self.assertEqual(_question_by_no(questions, "missing"), {})

    def test_paper_cut_question_by_no_matches_crop(self):
        paper_cut_questions = [
            {"question_no": "1", "crop_path": "q1.png"},
            {"question_no": "2", "crop_path": "q2.png"},
        ]

        self.assertEqual(_paper_cut_question_by_no(paper_cut_questions, "2")["crop_path"], "q2.png")
        self.assertEqual(_paper_cut_question_by_no(paper_cut_questions, "missing"), {})

    def test_historical_question_without_crop_is_supported(self):
        question = _paper_cut_question_by_no([{"question_no": "1"}], "1")

        self.assertEqual(question.get("crop_path"), None)


if __name__ == "__main__":
    unittest.main()
