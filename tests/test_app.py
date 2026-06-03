import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import (
    _build_report,
    _clear_desktop_camera_state,
    _clear_mobile_capture_state,
    _correct_rate,
    _is_mobile_user_agent,
    _logout_session,
    _paper_cut_question_by_no,
    _project_description_markdown,
    _question_by_no,
    _question_options,
    _register_from_form,
    _result_error_message,
    _save_processing_upload,
    _score_display,
    _select_image_input,
    _show_image_processing_page,
    _show_paper_cut_page,
    _task_rows,
    _uploaded_file_signature,
    _write_question_detail,
)
from auth import AuthValidationError
from ui_theme import build_task_card_html, status_badge_html, task_card_button_key


class FakeUpload:
    def __init__(self, data: bytes, name: str | None = None, size: int | None = None) -> None:
        self._data = data
        if name is not None:
            self.name = name
        if size is not None:
            self.size = size

    def getbuffer(self):
        return memoryview(self._data)


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
            rows = _task_rows("alice")

        self.assertEqual(rows[0]["分数"], "30/40")

    def test_register_form_rejects_mismatched_passwords(self):
        with self.assertRaises(AuthValidationError):
            _register_from_form("alice", "secret1", "secret2")

    def test_logout_clears_session_state(self):
        state = {"username": "alice", "selected_task_id": "demo"}
        with patch("app.st.session_state", state):
            _logout_session()

        self.assertEqual(state, {})

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

    def test_status_badge_uses_matching_class(self):
        badge = status_badge_html("finished", "已完成")

        self.assertIn('class="status-pill status-finished"', badge)
        self.assertIn("已完成", badge)

    def test_task_card_escapes_dynamic_text(self):
        html = build_task_card_html(
            {
                "任务ID": '<script>alert("demo")</script>',
                "状态": "<完成>",
                "_status": "finished",
                "分数": "30/40",
                "创建时间": "2026-06-03T12:00:00",
                "更新时间": "2026-06-03T12:01:00",
            }
        )

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;完成&gt;", html)

    def test_task_card_button_keys_are_stable_and_unique(self):
        self.assertEqual(task_card_button_key("demo"), task_card_button_key("demo"))
        self.assertNotEqual(task_card_button_key("demo"), task_card_button_key("other"))

    def test_camera_signature_without_name_or_size_is_stable(self):
        upload = FakeUpload(b"camera-bytes")

        self.assertEqual(
            _uploaded_file_signature(upload, source="camera"),
            _uploaded_file_signature(upload, source="camera"),
        )

    def test_upload_and_camera_signatures_differ_for_same_content(self):
        data = b"same-image-bytes"
        uploaded = FakeUpload(data, name="homework.jpg", size=len(data))
        camera = FakeUpload(data)

        self.assertNotEqual(
            _uploaded_file_signature(uploaded, source="upload"),
            _uploaded_file_signature(camera, source="camera"),
        )

    def test_camera_processing_upload_defaults_to_jpg_suffix(self):
        upload = FakeUpload(b"camera-bytes")
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.UPLOAD_DIR", Path(temp_dir)):
                saved_path = Path(_save_processing_upload(upload, source="camera"))

        self.assertEqual(saved_path.suffix, ".jpg")

    def test_failed_result_error_is_not_duplicated(self):
        status = {"status": "failed", "error": "腾讯云切题 OCR 未识别到题目区域。"}
        result = {"status": "failed", "error": "腾讯云切题 OCR 未识别到题目区域。"}

        self.assertEqual(
            _result_error_message(status, result),
            "腾讯云切题 OCR 未识别到题目区域。",
        )

    def test_question_detail_does_not_render_confidence_metric(self):
        columns = [MagicMock(), MagicMock()]
        with (
            patch("app.st.columns", return_value=columns) as st_columns,
            patch("app.st.markdown"),
            patch("app.st.write"),
            patch("app._write_detail"),
        ):
            _write_question_detail(
                {"question_no": "3", "score": 0, "max_score": 10, "confidence": "high"},
                {},
            )

        st_columns.assert_called_once_with(2)
        metric_labels = [call.args[0] for column in columns for call in column.metric.call_args_list]
        self.assertEqual(metric_labels, ["本题得分", "本题满分"])
        self.assertNotIn("可信度", metric_labels)

    def test_image_processing_page_only_shows_enhanced_image(self):
        upload = FakeUpload(b"image", name="demo.jpg", size=5)
        with tempfile.TemporaryDirectory() as temp_dir:
            enhanced_path = Path(temp_dir) / "enhanced.png"
            enhanced_path.write_bytes(b"enhanced")

            with (
                patch("app.render_page_intro"),
                patch("app.render_steps"),
                patch("app._current_username", return_value="alice"),
                patch("app._select_image_input", return_value=(upload, "upload")),
                patch("app.st.segmented_control", return_value="strong"),
                patch("app._save_processing_upload", return_value=str(Path(temp_dir) / "original.jpg")),
                patch(
                    "app.process_document_image",
                    return_value={
                        "status": "success",
                        "message": "ok",
                        "enhanced_path": str(enhanced_path),
                        "enhance_mode": "strong",
                        "warped_path": str(Path(temp_dir) / "warped.png"),
                        "corners": [{"x": 1, "y": 1}],
                        "debug_path": str(Path(temp_dir) / "debug.png"),
                    },
                ),
                patch("app.create_preview_image", return_value=str(enhanced_path)),
                patch("app.st.success"),
                patch("app.st.markdown") as markdown,
                patch("app.st.image") as image,
                patch("app.st.expander") as expander,
                patch("app.st.download_button"),
            ):
                _show_image_processing_page()

        rendered_markdown = [call.args[0] for call in markdown.call_args_list]
        self.assertIn("#### 强力清晰", rendered_markdown)
        self.assertNotIn("#### 原图", rendered_markdown)
        self.assertNotIn("#### 透视校正", rendered_markdown)
        image.assert_called_once()
        expander.assert_not_called()

    def test_image_processing_failure_does_not_show_original_image(self):
        upload = FakeUpload(b"image", name="demo.jpg", size=5)
        with (
            patch("app.render_page_intro"),
            patch("app.render_steps"),
            patch("app._current_username", return_value="alice"),
            patch("app._select_image_input", return_value=(upload, "upload")),
            patch("app.st.segmented_control", return_value="strong"),
            patch("app._save_processing_upload", return_value="original.jpg"),
            patch(
                "app.process_document_image",
                return_value={"status": "failed", "message": "bad image"},
            ),
            patch("app.create_preview_image"),
            patch("app.st.error"),
            patch("app.st.image") as image,
        ):
            _show_image_processing_page()

        image.assert_not_called()

    def test_paper_cut_page_only_shows_enhanced_preview_before_results(self):
        upload = FakeUpload(b"paper", name="paper.jpg", size=5)
        with tempfile.TemporaryDirectory() as temp_dir:
            api_preview_path = Path(temp_dir) / "api_preview.png"
            api_preview_path.write_bytes(b"api")
            state = {
                "paper_cut_file_signature": "same",
                "paper_cut_result": {
                    "task_id": "task-1",
                    "request_id": "req-1",
                    "question_count": 0,
                    "questions": [],
                    "api_preview_path": str(api_preview_path),
                    "original_preview_path": str(Path(temp_dir) / "original_preview.png"),
                },
            }

            with (
                patch("app.st.session_state", state),
                patch("app.render_page_intro"),
                patch("app._select_image_input", return_value=(upload, "upload")),
                patch("app._uploaded_file_signature", return_value="same"),
                patch("app.st.columns", return_value=[MagicMock(), MagicMock()]),
                patch("app.st.toggle", return_value=True),
                patch("app.st.button", return_value=False),
                patch("app.st.markdown") as markdown,
                patch("app.st.image") as image,
                patch("app.st.caption"),
                patch("app.st.download_button"),
            ):
                _show_paper_cut_page()

        rendered_markdown = [call.args[0] for call in markdown.call_args_list]
        self.assertIn("#### 增强后送检图", rendered_markdown)
        self.assertIn("#### 切题结果", rendered_markdown)
        self.assertNotIn("#### 原图", rendered_markdown)
        image.assert_called_once_with(str(api_preview_path), width="stretch")

    def test_project_description_uses_formal_copy_without_demo_terms(self):
        text = _project_description_markdown()

        self.assertIn("### 系统定位", text)
        self.assertIn("### 部署说明", text)
        for forbidden in ["测试环境", "演示配置", "mock", "课程设计", "本地演示"]:
            self.assertNotIn(forbidden, text)

    def test_clearing_mobile_capture_state_removes_old_phone_photo(self):
        state = {
            "correction_mobile_token": "token",
            "correction_mobile_file": object(),
            "correction_mobile_signature": "signature",
        }
        with patch("app.st.session_state", state):
            _clear_mobile_capture_state("correction")

        self.assertEqual(state, {})

    def test_clearing_desktop_camera_state_removes_open_and_photo(self):
        state = {
            "correction_camera_open": True,
            "correction_camera": object(),
        }
        with patch("app.st.session_state", state):
            _clear_desktop_camera_state("correction")

        self.assertEqual(state, {})

    def test_mobile_user_agents_are_detected(self):
        mobile_agents = [
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile",
            "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)",
        ]

        for user_agent in mobile_agents:
            self.assertTrue(_is_mobile_user_agent(user_agent))

    def test_desktop_or_empty_user_agents_are_not_mobile(self):
        self.assertFalse(
            _is_mobile_user_agent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit Chrome/125 Safari/537.36"
            )
        )
        self.assertFalse(_is_mobile_user_agent(""))
        self.assertFalse(_is_mobile_user_agent(None))

    def test_mobile_image_input_hides_source_choices_and_clears_old_state(self):
        upload = FakeUpload(b"mobile")
        state = {
            "correction_camera_open": True,
            "correction_camera": object(),
            "correction_mobile_token": "token",
            "correction_mobile_file": object(),
            "correction_mobile_signature": "signature",
        }
        with (
            patch("app.st.session_state", state),
            patch("app.st.caption"),
            patch("app.st.file_uploader", return_value=upload) as file_uploader,
            patch("app.st.segmented_control") as segmented_control,
        ):
            selected, source = _select_image_input(
                key_prefix="correction",
                uploader_label="上传作业图片",
                camera_label="拍摄作业图片",
                owner_username="alice",
                mobile_client=True,
            )

        self.assertIs(selected, upload)
        self.assertEqual(source, "upload")
        self.assertNotIn("correction_camera_open", state)
        self.assertNotIn("correction_mobile_token", state)
        segmented_control.assert_not_called()
        file_uploader.assert_called_once()

    def test_desktop_image_input_keeps_three_source_choices(self):
        upload = FakeUpload(b"desktop")
        with (
            patch("app.st.session_state", {}),
            patch("app.st.segmented_control", return_value="上传图片") as segmented_control,
            patch("app.st.file_uploader", return_value=upload),
        ):
            selected, source = _select_image_input(
                key_prefix="correction",
                uploader_label="上传作业图片",
                camera_label="拍摄作业图片",
                owner_username="alice",
                mobile_client=False,
            )

        self.assertIs(selected, upload)
        self.assertEqual(source, "upload")
        self.assertEqual(
            segmented_control.call_args.args[1],
            ["上传图片", "电脑摄像头", "手机拍照"],
        )


if __name__ == "__main__":
    unittest.main()
