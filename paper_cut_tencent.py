from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from config import BASE_DIR, PROCESSED_DIR, ensure_runtime_dirs, get_int_setting, get_setting


CUT_DIR = BASE_DIR / "cuts"
MAX_IMAGE_BASE64_BYTES = 10 * 1024 * 1024
DEFAULT_API_IMAGE_MAX_SIDE = 1800
DEFAULT_API_IMAGE_JPEG_QUALITY = 86


class TencentPaperCutConfigError(RuntimeError):
    pass


def _create_client():
    try:
        from tencentcloud.common import credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import ocr_client
    except ImportError as exc:
        raise TencentPaperCutConfigError(
            "未安装腾讯云 OCR SDK，请先运行 pip install -r requirements.txt。"
        ) from exc

    secret_id = get_setting("TENCENT_SECRET_ID")
    secret_key = get_setting("TENCENT_SECRET_KEY")
    region = get_setting("TENCENT_OCR_REGION", "ap-guangzhou")

    if not secret_id or not secret_key:
        raise TencentPaperCutConfigError(
            "未配置 TENCENT_SECRET_ID 或 TENCENT_SECRET_KEY。"
            "请放到本地 .env 或 Streamlit Secrets，不要写进代码。"
        )

    http_profile = HttpProfile()
    http_profile.endpoint = "ocr.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile

    cred = credential.Credential(secret_id, secret_key)
    return ocr_client.OcrClient(cred, region, client_profile)


def _image_base64(image_path: str) -> str:
    image_bytes = Path(image_path).read_bytes()
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    if len(encoded.encode("utf-8")) > MAX_IMAGE_BASE64_BYTES:
        raise ValueError("图片 Base64 超过腾讯云 10M 限制，请压缩图片或换一张更小的图片。")
    return encoded


def _prepare_api_image(image_path: str, task_id: str) -> tuple[str, dict[str, Any]]:
    max_side = get_int_setting("TENCENT_OCR_MAX_SIDE", DEFAULT_API_IMAGE_MAX_SIDE)
    jpeg_quality = get_int_setting("TENCENT_OCR_JPEG_QUALITY", DEFAULT_API_IMAGE_JPEG_QUALITY)
    max_side = max(900, min(max_side, 2600))
    jpeg_quality = max(60, min(jpeg_quality, 95))

    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    original_size = image.size
    scale = min(1.0, max_side / max(original_size))
    if scale < 1.0:
        resized_size = (
            max(1, int(original_size[0] * scale)),
            max(1, int(original_size[1] * scale)),
        )
        image = image.resize(resized_size, Image.Resampling.LANCZOS)

    api_image_path = PROCESSED_DIR / f"{task_id}_ocr_input.jpg"
    api_image_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(api_image_path, format="JPEG", quality=jpeg_quality, optimize=True)

    return str(api_image_path), {
        "source_image_path": image_path,
        "api_image_path": str(api_image_path),
        "original_size": list(original_size),
        "api_size": list(image.size),
        "scale": scale,
        "jpeg_quality": jpeg_quality,
        "max_side": max_side,
        "bytes": api_image_path.stat().st_size,
    }


def _coord_points(coord: Any) -> list[tuple[int, int]]:
    if isinstance(coord, list):
        points: list[tuple[int, int]] = []
        for item in coord:
            points.extend(_coord_points(item))
        return points

    if not isinstance(coord, dict):
        return []

    if "X" in coord and "Y" in coord:
        return [(int(coord["X"]), int(coord["Y"]))]

    points = []
    for key in ("LeftTop", "RightTop", "RightBottom", "LeftBottom"):
        value = coord.get(key)
        if isinstance(value, dict) and "X" in value and "Y" in value:
            points.append((int(value["X"]), int(value["Y"])))
    return points


def _coord_to_bbox(coord: Any) -> tuple[int, int, int, int] | None:
    points = _coord_points(coord)
    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


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


def _collect_text_parts(payload: Any) -> list[str]:
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []

    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            parts.extend(_collect_text_parts(item))
        return parts

    if not isinstance(payload, dict):
        return []

    parts: list[str] = []
    text = payload.get("Text")
    if isinstance(text, str) and text.strip():
        parts.append(text.strip())

    for key in ("Question", "Option", "Answer", "Figure", "Table", "Parse", "ResultList"):
        parts.extend(_collect_text_parts(payload.get(key)))
    return parts


def _dedupe_join(parts: list[str]) -> str:
    seen = set()
    unique_parts = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        unique_parts.append(part)
    return "\n".join(unique_parts)


def _normalize_question_info(
    raw_response: dict[str, Any],
    image_path: str,
    task_id: str,
) -> list[dict[str, Any]]:
    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    questions: list[dict[str, Any]] = []
    question_info = raw_response.get("QuestionInfo") or []

    for page_index, page in enumerate(question_info, start=1):
        result_list = page.get("ResultList") or []
        for subject_index, item in enumerate(result_list, start=1):
            coord = item.get("Coord") or []
            bbox = _coord_to_bbox(coord)
            crop_path = None
            if bbox:
                crop_path = _crop_question(
                    image,
                    bbox,
                    CUT_DIR / f"{task_id}_p{page_index}_q{subject_index}.png",
                )

            text = _dedupe_join(_collect_text_parts(item))
            questions.append(
                {
                    "question_no": str(subject_index),
                    "page_index": page_index,
                    "subject_index": subject_index,
                    "ids": [],
                    "is_multipage": False,
                    "text": text,
                    "bbox": bbox,
                    "coord": coord,
                    "crop_path": crop_path,
                    "question": item.get("Question") or [],
                    "option": item.get("Option") or [],
                    "answer": item.get("Answer") or [],
                    "figure": item.get("Figure") or [],
                    "table": item.get("Table") or [],
                    "source": "tencent_question_split",
                }
            )

    return questions


def normalize_question_split_response(
    raw: dict[str, Any],
    *,
    image_path: str,
    task_id: str,
    source_image_path: str | None = None,
    api_image_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = raw.get("Response", raw)
    questions = _normalize_question_info(response, image_path=image_path, task_id=task_id)
    ocr_text = "\n\n".join(
        f"{item['question_no']}. {item['text']}"
        for item in questions
        if item.get("text")
    )

    raw_path = CUT_DIR / f"{task_id}_paper_cut.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "task_id": task_id,
        "status": "success",
        "provider": "tencent",
        "image_path": image_path,
        "source_image_path": source_image_path or image_path,
        "api_image_meta": api_image_meta or {},
        "request_id": response.get("RequestId"),
        "question_count": len(questions),
        "split_strategy": "tencent_question_split",
        "ocr_text": ocr_text,
        "questions": questions,
        "raw_path": str(raw_path),
        "raw": raw,
    }


def recognize_question_split(
    image_path: str,
    *,
    use_new_model: bool = True,
    enable_image_crop: bool = False,
    task_id: str | None = None,
) -> dict[str, Any]:
    ensure_runtime_dirs()
    task_id = task_id or str(uuid.uuid4())
    api_image_path, api_image_meta = _prepare_api_image(image_path, task_id)

    client = _create_client()

    from tencentcloud.ocr.v20181119 import models

    request = models.QuestionSplitOCRRequest()
    request.from_json_string(
        json.dumps(
            {
                "ImageBase64": _image_base64(api_image_path),
                "UseNewModel": use_new_model,
                "EnableImageCrop": enable_image_crop,
                "EnableOnlyDetectBorder": False,
            }
        )
    )

    try:
        response = client.QuestionSplitOCR(request)
    except Exception as exc:
        raise RuntimeError(f"腾讯云试卷切题识别失败：{exc}") from exc

    raw = json.loads(response.to_json_string())
    return normalize_question_split_response(
        raw,
        image_path=api_image_path,
        source_image_path=image_path,
        api_image_meta=api_image_meta,
        task_id=task_id,
    )
