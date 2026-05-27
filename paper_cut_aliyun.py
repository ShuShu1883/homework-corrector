from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from config import BASE_DIR, ensure_runtime_dirs, get_setting


CUT_DIR = BASE_DIR / "cuts"


class PaperCutConfigError(RuntimeError):
    pass


SUBJECT_OPTIONS = {
    "default": "默认",
    "Math": "数学",
    "PrimarySchool_Math": "小学数学",
    "JHighSchool_Math": "初中数学",
    "Chinese": "语文",
    "PrimarySchool_Chinese": "小学语文",
    "JHighSchool_Chinese": "初中语文",
    "English": "英语",
    "PrimarySchool_English": "小学英语",
    "JHighSchool_English": "初中英语",
    "Physics": "物理",
    "JHighSchool_Physics": "初中物理",
    "Chemistry": "化学",
    "JHighSchool_Chemistry": "初中化学",
    "Biology": "生物",
    "JHighSchool_Biology": "初中生物",
    "History": "历史",
    "JHighSchool_History": "初中历史",
    "Geography": "地理",
    "JHighSchool_Geography": "初中地理",
    "Politics": "政治",
    "JHighSchool_Politics": "初中政治",
}


def _create_client():
    try:
        from alibabacloud_ocr_api20210707.client import Client as OCRClient
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise PaperCutConfigError(
            "未安装阿里云 OCR SDK，请先运行 pip install -r requirements.txt。"
        ) from exc

    access_key_id = get_setting("ALIYUN_ACCESS_KEY_ID")
    access_key_secret = get_setting("ALIYUN_ACCESS_KEY_SECRET")
    endpoint = get_setting("ALIYUN_PAPER_CUT_ENDPOINT", "ocr-api.cn-hangzhou.aliyuncs.com")

    if not access_key_id or not access_key_secret:
        raise PaperCutConfigError(
            "未配置 ALIYUN_ACCESS_KEY_ID 或 ALIYUN_ACCESS_KEY_SECRET。"
            "请放到本地 .env 或 Streamlit Secrets，不要写进代码。"
        )

    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
    )
    config.endpoint = endpoint
    return OCRClient(config)


def _response_to_dict(response: Any) -> dict[str, Any]:
    body = getattr(response, "body", response)
    if hasattr(body, "to_map"):
        return body.to_map()
    if isinstance(body, dict):
        return body
    return dict(body or {})


def _parse_data(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {"raw_text": data}
    if isinstance(data, dict):
        return data
    return {"value": data}


def _points_to_bbox(points: list[dict[str, Any]]) -> tuple[int, int, int, int] | None:
    if not points:
        return None

    xs = [int(point["x"]) for point in points if "x" in point]
    ys = [int(point["y"]) for point in points if "y" in point]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _union_bbox(items: list[dict[str, Any]]) -> tuple[int, int, int, int] | None:
    boxes = []
    for item in items:
        points = item.get("pos") or []
        box = _points_to_bbox(points)
        if box:
            boxes.append(box)

    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _crop_question(image: Image.Image, bbox: tuple[int, int, int, int], path: Path) -> str:
    width, height = image.size
    padding = 12
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(width, bbox[2] + padding)
    bottom = min(height, bbox[3] + padding)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.crop((left, top, right, bottom)).save(path)
    return str(path)


def _normalize_subjects(data: dict[str, Any], image_path: str, task_id: str) -> list[dict[str, Any]]:
    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    questions: list[dict[str, Any]] = []

    for page_index, page in enumerate(data.get("page_list", []), start=1):
        subjects = page.get("subject_list") or []
        for subject_index, subject in enumerate(subjects, start=1):
            content_list = subject.get("content_list_info") or []
            words = subject.get("prism_wordsInfo") or []

            bbox = _union_bbox(content_list) or _union_bbox(words)

            crop_path = None
            if bbox:
                crop_path = _crop_question(
                    image,
                    bbox,
                    CUT_DIR / f"{task_id}_p{page_index}_q{subject_index}.png",
                )

            ids = subject.get("ids") or []
            question_no = ",".join(str(item) for item in ids) if ids else str(subject_index)
            questions.append(
                {
                    "question_no": question_no,
                    "page_index": page_index,
                    "subject_index": subject_index,
                    "ids": ids,
                    "is_multipage": bool(subject.get("is_multipage", False)),
                    "text": subject.get("text", ""),
                    "bbox": bbox,
                    "crop_path": crop_path,
                    "content_list_info": content_list,
                    "prism_wordsInfo": words,
                    "source": "aliyun_subject",
                }
            )

    return questions


def recognize_edu_paper_cut(
    image_path: str,
    *,
    cut_type: str = "question",
    image_type: str = "photo",
    subject: str = "default",
    output_oricoord: bool = True,
    task_id: str | None = None,
) -> dict[str, Any]:
    ensure_runtime_dirs()
    task_id = task_id or str(uuid.uuid4())

    client = _create_client()

    from alibabacloud_ocr_api20210707 import models as ocr_models
    from alibabacloud_tea_util import models as util_models

    with open(image_path, "rb") as image_stream:
        request = ocr_models.RecognizeEduPaperCutRequest(
            body=image_stream,
            cut_type=cut_type,
            image_type=image_type,
            subject=subject,
            output_oricoord=output_oricoord,
        )
        response = client.recognize_edu_paper_cut_with_options(
            request,
            util_models.RuntimeOptions(
                connect_timeout=10000,
                read_timeout=120000,
            ),
        )

    raw = _response_to_dict(response)
    if raw.get("Code"):
        raise RuntimeError(f"{raw.get('Code')}: {raw.get('Message', '阿里云切题识别失败')}")

    data = _parse_data(raw.get("Data"))
    questions = _normalize_subjects(data, image_path=image_path, task_id=task_id)
    split_strategy = "aliyun_subject"

    raw_path = CUT_DIR / f"{task_id}_paper_cut.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "task_id": task_id,
        "status": "success",
        "image_path": image_path,
        "cut_type": cut_type,
        "image_type": image_type,
        "subject": subject,
        "request_id": raw.get("RequestId"),
        "question_count": len(questions),
        "split_strategy": split_strategy,
        "questions": questions,
        "raw_path": str(raw_path),
        "raw": raw,
    }
