import unittest

from llm_corrector import (
    _build_derive_payload,
    _build_grade_payload,
    _correction_consistency_issues,
    _derive_answers_prompt,
    _grade_prompt,
    _normalize_correction,
    _parse_derived_answers,
    _prepare_questions_for_llm,
    _validate_correct_answers,
)


class LLMCorrectorInputTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 数据清洗
    # ------------------------------------------------------------------

    def test_answer_area_is_not_mixed_into_recognized_text(self):
        questions = [
            {
                "question_no": "4",
                "text": "一个数的亿位是5，十万位是8，个位数是3，其余各位是0，写作（）读作（）\n500080003",
                "question": [{"Text": "一个数的亿位是5，十万位是8，个位数是3，其余各位是0，写作（）读作（）"}],
                "answer": [{"Text": "500080003"}],
                "source": "tencent_question_split",
            }
        ]

        prepared = _prepare_questions_for_llm(questions)

        self.assertEqual(prepared[0]["question_no"], "4")
        self.assertIn("亿位是5", prepared[0]["recognized_text"])
        self.assertNotIn("500080003", prepared[0]["recognized_text"])
        self.assertEqual(prepared[0]["student_answer_area"], "500080003")

    def test_nested_answer_process_is_kept_as_student_answer_area(self):
        questions = [
            {
                "question_no": "2",
                "question": [
                    {
                        "Text": "",
                        "ResultList": [
                            {
                                "Question": [{"Text": "450×36"}],
                                "Answer": [{"Text": "=(500-50)×36\n=18000-1800\n=16860"}],
                            }
                        ],
                    }
                ],
                "answer": [],
                "source": "tencent_question_split",
            }
        ]

        prepared = _prepare_questions_for_llm(questions)

        self.assertEqual(prepared[0]["recognized_text"], "450×36")
        self.assertIn("16860", prepared[0]["student_answer_area"])

    # ------------------------------------------------------------------
    # 阶段一 prompt：推导答案
    # ------------------------------------------------------------------

    def test_derive_prompt_marks_answer_as_not_standard(self):
        ocr_result = {
            "ocr_text": "480÷60=7",
            "questions": [
                {
                    "question_no": "1",
                    "question": [{"Text": "480÷60="}],
                    "answer": [{"Text": "7"}],
                    "text": "480÷60=7",
                    "source": "tencent_question_split",
                }
            ],
        }

        prompt = _derive_answers_prompt(ocr_result)

        self.assertIn("不是标准答案", prompt)
        self.assertIn("student_answer_area", prompt)
        self.assertIn('"recognized_text": "480÷60="', prompt)
        self.assertIn('"student_answer_area": "7"', prompt)
        # 阶段一只要求推导答案，不要求评分
        self.assertIn("correct_answer", prompt)
        self.assertIn("question_understanding", prompt)
        self.assertNotIn("is_correct", prompt)  # 阶段一不管对错

    def test_derive_answers_parsed_correctly(self):
        raw = {
            "questions": [
                {
                    "question_no": "1",
                    "question_understanding": "口算 480÷60",
                    "correct_answer": "8",
                    "max_score": 40,
                    "solution_steps": ["480÷60=8", "验算：60×8=480"],
                    "knowledge_points": ["除法", "口算"],
                    "uncertain_note": "",
                }
            ]
        }

        parsed = _parse_derived_answers(raw)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["correct_answer"], "8")
        self.assertEqual(parsed[0]["max_score"], 40)

    # ------------------------------------------------------------------
    # 阶段二 prompt：批改评分
    # ------------------------------------------------------------------

    def test_grade_prompt_includes_derived_answers(self):
        ocr_result = {
            "ocr_text": "480÷60=7",
            "questions": [
                {
                    "question_no": "1",
                    "question": [{"Text": "480÷60="}],
                    "answer": [{"Text": "7"}],
                    "text": "480÷60=7",
                    "source": "tencent_question_split",
                }
            ],
        }

        derived_answers = [
            {
                "question_no": "1",
                "question_understanding": "口算 480÷60",
                "correct_answer": "8",
                "max_score": 40,
                "solution_steps": ["480÷60=8"],
                "knowledge_points": ["除法"],
                "uncertain_note": "",
            }
        ]

        prompt = _grade_prompt(ocr_result, derived_answers)

        # 阶段二 prompt 必须包含已验证的标准答案
        self.assertIn("已验证的标准答案", prompt)
        self.assertIn("标准正确答案：8", prompt)
        self.assertIn("不要重新计算答案", prompt)
        # 必须包含评分字段
        self.assertIn("is_correct", prompt)

    def test_grade_payload_temperature_is_zero(self):
        payload = _build_grade_payload(
            model="deepseek-v4-pro",
            ocr_result={"ocr_text": "", "questions": []},
            derived_answers=[],
            max_tokens=1024,
        )

        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_derive_payload_temperature_is_zero(self):
        payload = _build_derive_payload(
            model="deepseek-v4-pro",
            ocr_result={"ocr_text": "", "questions": []},
            max_tokens=1024,
        )

        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    # ------------------------------------------------------------------
    # 一致性校验
    # ------------------------------------------------------------------

    def test_consistency_issues_catch_self_contradictory_scores(self):
        issues = _correction_consistency_issues(
            {
                "questions": [
                    {
                        "question_no": "2",
                        "is_correct": True,
                        "score": 30,
                        "max_score": 30,
                        "deduction_reason": "结果错误，扣分。",
                    }
                ]
            }
        )

        self.assertTrue(issues)

    def test_consistency_issues_require_reason_when_score_is_deducted(self):
        issues = _correction_consistency_issues(
            {
                "questions": [
                    {
                        "question_no": "3",
                        "is_correct": False,
                        "score": 15,
                        "max_score": 20,
                        "deduction_reason": "",
                        "uncertain_reason": "",
                    }
                ]
            }
        )

        self.assertTrue(issues)

    def test_consistency_issues_reject_self_correction_text_in_answer(self):
        issues = _correction_consistency_issues(
            {
                "questions": [
                    {
                        "question_no": "2",
                        "is_correct": False,
                        "score": 20,
                        "max_score": 30,
                        "correct_answer": "之前判断不对，需修正为最终答案。",
                        "deduction_reason": "结果错误。",
                    }
                ]
            }
        )

        self.assertTrue(issues)

    # ------------------------------------------------------------------
    # 跨阶段校验：阶段二答案必须与阶段一一致
    # ------------------------------------------------------------------

    def test_validate_correct_answers_detects_mismatch(self):
        derived = [
            {
                "question_no": "1",
                "correct_answer": "25×4=100，360÷90=4，125×8=1000，480÷60=8，36×2=72，810÷9=90",
            }
        ]

        correction = {
            "questions": [
                {
                    "question_no": "1",
                    "correct_answer": "25×4=100，360÷90=4，125×8=1000，480÷60=7，36×2=72，810÷9=90",
                }
            ]
        }

        issues = _validate_correct_answers(derived, correction)
        self.assertTrue(issues, "阶段二的 480÷60=7 应该被检测到与阶段一的 480÷60=8 不一致")

    def test_validate_correct_answers_passes_when_match(self):
        derived = [
            {
                "question_no": "1",
                "correct_answer": "8",
            }
        ]

        correction = {
            "questions": [
                {
                    "question_no": "1",
                    "correct_answer": "8",
                }
            ]
        }

        issues = _validate_correct_answers(derived, correction)
        self.assertFalse(issues)

    # ------------------------------------------------------------------
    # 总分以逐题之和为准
    # ------------------------------------------------------------------

    def test_normalize_correction_recalculates_score(self):
        """LLM 声明总分 90，但逐题之和是 75 → 以 75 为准"""
        payload = {
            "score": 90,
            "questions": [
                {"question_no": "1", "score": 30, "max_score": 50},
                {"question_no": "2", "score": 45, "max_score": 50},
            ],
        }
        result = _normalize_correction(payload)
        self.assertEqual(result["score"], 75)
        self.assertIn("总分 75/100", result["score_breakdown"])

    def test_normalize_correction_total_not_forced_to_100(self):
        """LLM 定的满分总和是 80，学生得了 60 → 总分就是 60"""
        payload = {
            "score": 100,  # LLM 乱写的
            "questions": [
                {"question_no": "1", "score": 30, "max_score": 40},
                {"question_no": "2", "score": 30, "max_score": 40},
            ],
        }
        result = _normalize_correction(payload)
        self.assertEqual(result["score"], 60)
        self.assertIn("总分 60/80", result["score_breakdown"])

    def test_consistency_issues_catch_score_sum_mismatch(self):
        """声明总分 80 但逐题之和是 70 → 检测到"""
        issues = _correction_consistency_issues(
            {
                "score": 80,
                "questions": [
                    {"question_no": "1", "is_correct": True, "score": 30, "max_score": 50},
                    {"question_no": "2", "is_correct": True, "score": 40, "max_score": 50},
                ],
            }
        )
        self.assertTrue(any("总分" in issue for issue in issues))

    def test_grade_prompt_includes_score_sum_rule(self):
        ocr_result = {"ocr_text": "", "questions": []}
        derived = [
            {
                "question_no": "1",
                "question_understanding": "口算 480÷60",
                "correct_answer": "8",
                "max_score": 40,
                "solution_steps": [],
                "knowledge_points": [],
                "uncertain_note": "",
            }
        ]
        prompt = _grade_prompt(ocr_result, derived)
        self.assertIn("总分必须等于逐题得分之和", prompt)

    def test_derive_prompt_no_longer_forces_100(self):
        """阶段一 prompt 不再要求 max_score 合计约 100"""
        ocr_result = {"ocr_text": "", "questions": []}
        prompt = _derive_answers_prompt(ocr_result)
        self.assertNotIn("合计约 100", prompt)
        self.assertIn("max_score 之和即为卷面总分", prompt)


if __name__ == "__main__":
    unittest.main()
