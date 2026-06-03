from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from config import DEBUG_DIR, PROCESSED_DIR, ensure_runtime_dirs


MAX_DETECT_SIDE = 1000
MAX_OUTPUT_SIDE = 2200
MAX_PREVIEW_SIDE = 1100
MIN_TEXT_SIDE = 1600
ENHANCE_PRESETS = {
    "soft": {
        "clip_limit": 1.4,
        "sharpen_amount": 0.45,
        "denoise": 8,
        "normalize_background": False,
        "background_sigma": 25,
        "gamma": 1.0,
        "text_darkening": 0.95,
        "paper_brightening": 1.02,
    },
    "standard": {
        "clip_limit": 1.8,
        "sharpen_amount": 0.65,
        "denoise": 10,
        "normalize_background": True,
        "background_sigma": 25,
        "gamma": 0.95,
        "text_darkening": 0.88,
        "paper_brightening": 1.04,
    },
    "strong": {
        "clip_limit": 2.4,
        "sharpen_amount": 1.1,
        "denoise": 0,
        "normalize_background": True,
        "background_sigma": 35,
        "gamma": 0.9,
        "text_darkening": 0.75,
        "paper_brightening": 1.06,
    },
}


def _resize_to_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image

    scale = max_side / longest
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def _upscale_for_text(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest >= MIN_TEXT_SIDE:
        return image

    scale = min(MIN_TEXT_SIDE / longest, 2.0)
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)


def _largest_true_run(mask: np.ndarray) -> tuple[int, int] | None:
    indexes = np.where(mask)[0]
    if len(indexes) == 0:
        return None

    best_start = int(indexes[0])
    best_end = int(indexes[0])
    current_start = int(indexes[0])
    current_end = int(indexes[0])

    for index in indexes[1:]:
        index = int(index)
        if index == current_end + 1:
            current_end = index
            continue

        if (current_end - current_start) > (best_end - best_start):
            best_start, best_end = current_start, current_end
        current_start = current_end = index

    if (current_end - current_start) > (best_end - best_start):
        best_start, best_end = current_start, current_end
    return best_start, best_end


def _crop_black_borders(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    bright = gray > 18
    row_ratio = bright.mean(axis=1)
    col_ratio = bright.mean(axis=0)

    row_run = _largest_true_run(row_ratio > 0.08)
    col_run = _largest_true_run(col_ratio > 0.08)
    if row_run is None or col_run is None:
        return image

    top, bottom = row_run
    left, right = col_run
    if (bottom - top) < image.shape[0] * 0.25 or (right - left) < image.shape[1] * 0.25:
        return image

    pad = max(2, int(min(image.shape[:2]) * 0.005))
    top = max(0, top - pad)
    bottom = min(image.shape[0] - 1, bottom + pad)
    left = max(0, left - pad)
    right = min(image.shape[1] - 1, right + pad)
    return image[top : bottom + 1, left : right + 1]


def _load_rgb_image(image_path: str) -> np.ndarray:
    image = Image.open(image_path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    return np.array(image)


def _stretch_contrast(gray: np.ndarray, low_percent: float = 1.0, high_percent: float = 99.0) -> np.ndarray:
    low, high = np.percentile(gray, (low_percent, high_percent))
    if high <= low:
        return gray
    stretched = (gray.astype(np.float32) - low) * (255.0 / (high - low))
    return np.clip(stretched, 0, 255).astype(np.uint8)


def _apply_gamma(gray: np.ndarray, gamma: float) -> np.ndarray:
    if gamma == 1.0:
        return gray
    normalized = gray.astype(np.float32) / 255.0
    adjusted = np.power(normalized, gamma) * 255.0
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def _save_rgb_image(path: Path, image: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if image.ndim == 2:
        Image.fromarray(image).save(path)
    else:
        Image.fromarray(image.astype(np.uint8), mode="RGB").save(path)
    return str(path)


def create_preview_image(image_path: str, task_id: str | None = None, suffix: str = "preview") -> str:
    ensure_runtime_dirs()
    task_id = task_id or str(uuid.uuid4())

    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    longest = max(image.size)
    if longest > MAX_PREVIEW_SIDE:
        scale = MAX_PREVIEW_SIDE / longest
        image = image.resize(
            (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))),
            Image.Resampling.LANCZOS,
        )

    preview_path = PROCESSED_DIR / f"{task_id}_{suffix}.jpg"
    image.save(preview_path, format="JPEG", quality=82, optimize=True)
    return str(preview_path)


def _annotation_font_candidates() -> list[Path]:
    return [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc"),
    ]


def _annotation_font(size: int) -> ImageFont.ImageFont:
    for path in _annotation_font_candidates():
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_bbox(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    try:
        return draw.textbbox(xy, text, font=font)
    except UnicodeEncodeError:
        return draw.textbbox(xy, text.encode("ascii", "ignore").decode("ascii") or "-", font=font)


def _draw_text_safe(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    try:
        draw.text(xy, text, font=font, fill=fill)
    except UnicodeEncodeError:
        fallback = text.encode("ascii", "ignore").decode("ascii") or "-"
        draw.text(xy, fallback, font=font, fill=fill)


def _question_key(item: dict[str, Any], fallback_index: int) -> str:
    raw = item.get("question_no") or item.get("subject_index") or fallback_index
    return str(raw).strip()


def _correction_by_question(corrections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(corrections, start=1):
        mapped[_question_key(item, index)] = item
    return mapped


def _matched_correction(
    question: dict[str, Any],
    question_index: int,
    corrections: list[dict[str, Any]],
    correction_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = _question_key(question, question_index)
    if key in correction_map:
        return correction_map[key]
    if question_index - 1 < len(corrections):
        return corrections[question_index - 1]
    return {}


def _numeric_score(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _annotation_status(correction: dict[str, Any]) -> tuple[str, tuple[int, int, int]]:
    score = _numeric_score(correction.get("score"))
    max_score = _numeric_score(correction.get("max_score"))
    if score is None or max_score is None or max_score <= 0:
        return "待判断", (108, 117, 125)

    if score >= max_score:
        return "满分", (30, 136, 68)
    if score >= max_score * 0.5:
        return "部分正确", (214, 143, 0)
    return "需订正", (200, 45, 45)


def _score_label(correction: dict[str, Any]) -> str:
    score = correction.get("score")
    max_score = correction.get("max_score")
    if score not in (None, "") and max_score not in (None, ""):
        return f"{score}/{max_score}"
    if score not in (None, ""):
        return str(score)
    return ""


def _normalized_bbox(bbox: Any, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        left, top, right, bottom = [int(float(value)) for value in bbox]
    except (TypeError, ValueError):
        return None

    left = max(0, min(left, width - 1))
    right = max(0, min(right, width - 1))
    top = max(0, min(top, height - 1))
    bottom = max(0, min(bottom, height - 1))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _label_position(
    bbox: tuple[int, int, int, int],
    label_width: int,
    label_height: int,
    image_width: int,
    image_height: int,
    padding: int,
) -> tuple[int, int]:
    left, top, right, bottom = bbox
    x = max(0, min(left, image_width - label_width - 1))

    if top - label_height - padding >= 0:
        y = top - label_height - padding
    elif bottom + padding + label_height < image_height:
        y = bottom + padding
    else:
        y = min(max(top + padding, 0), max(0, image_height - label_height - 1))

    return x, y


def create_annotated_correction_image(
    image_path: str,
    paper_cut_questions: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    *,
    task_id: str | None = None,
) -> str | None:
    ensure_runtime_dirs()
    task_id = task_id or str(uuid.uuid4())

    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    font_size = max(18, min(34, width // 42))
    font = _annotation_font(font_size)
    correction_map = _correction_by_question(corrections)
    thickness = max(4, width // 320)
    padding = max(8, width // 160)
    annotated_count = 0

    for index, question in enumerate(paper_cut_questions, start=1):
        bbox = _normalized_bbox(question.get("bbox"), width, height)
        if not bbox:
            continue

        correction = _matched_correction(question, index, corrections, correction_map)
        status_text, color = _annotation_status(correction)
        score = _score_label(correction)
        question_no = question.get("question_no") or question.get("subject_index") or index
        first_line = f"第{question_no}题 {status_text}"
        if score:
            first_line = f"{first_line} {score}"

        draw.rectangle(bbox, outline=color, width=thickness)

        first_bbox = _text_bbox(draw, (0, 0), first_line, font)
        text_width = first_bbox[2] - first_bbox[0]
        text_height = first_bbox[3] - first_bbox[1]
        label_width = text_width + padding * 2
        label_height = text_height + padding
        label_x, label_y = _label_position(bbox, label_width, label_height, width, height, padding)

        draw.rectangle(
            (label_x, label_y, label_x + label_width, label_y + label_height),
            fill=color,
            outline=color,
        )
        _draw_text_safe(draw, (label_x + padding, label_y + padding // 2), first_line, font=font, fill=(255, 255, 255))
        annotated_count += 1

    if annotated_count == 0:
        return None

    annotated_path = PROCESSED_DIR / f"{task_id}_annotated.jpg"
    image.save(annotated_path, format="JPEG", quality=92, optimize=True)
    return str(annotated_path)


def order_points(points: np.ndarray) -> np.ndarray:
    pts = points.reshape(4, 2).astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")

    point_sum = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(point_sum)]
    ordered[2] = pts[np.argmax(point_sum)]

    point_diff = np.diff(pts, axis=1)
    ordered[1] = pts[np.argmin(point_diff)]
    ordered[3] = pts[np.argmax(point_diff)]
    return ordered


def _scale_points(points: np.ndarray, scale_x: float, scale_y: float) -> np.ndarray:
    scaled = points.reshape(4, 2).astype("float32")
    scaled[:, 0] *= scale_x
    scaled[:, 1] *= scale_y
    return order_points(scaled)


def _valid_document_box(box: np.ndarray, image_width: int, image_height: int) -> bool:
    rect = order_points(box)
    top_left, top_right, bottom_right, bottom_left = rect
    width = max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))
    height = max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))
    if width < 1 or height < 1:
        return False

    image_area = image_width * image_height
    box_area = cv2.contourArea(rect.astype("float32"))
    if box_area < image_area * 0.20:
        return False

    aspect = max(width, height) / min(width, height)
    if aspect > 3.2:
        return False

    center = rect.mean(axis=0)
    if not (0 <= center[0] <= image_width and 0 <= center[1] <= image_height):
        return False

    return True


def _box_nearly_full_frame(box: np.ndarray, image_width: int, image_height: int) -> bool:
    rect = order_points(box)
    min_x = float(np.min(rect[:, 0]))
    max_x = float(np.max(rect[:, 0]))
    min_y = float(np.min(rect[:, 1]))
    max_y = float(np.max(rect[:, 1]))
    width_ratio = (max_x - min_x) / max(image_width, 1)
    height_ratio = (max_y - min_y) / max(image_height, 1)
    return width_ratio > 0.94 and height_ratio > 0.94


def _detect_corners_by_paper_mask(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
    saturation = hsv[:, :, 1]
    luminance = lab[:, :, 0]

    kernel_size = max(17, int(max(height, width) * 0.025))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))

    threshold_pairs = (
        (145, 90),
        (135, 85),
        (125, 100),
        (115, 110),
    )
    best_box = None
    best_area = 0.0

    for light_threshold, saturation_threshold in threshold_pairs:
        mask = ((luminance > light_threshold) & (saturation < saturation_threshold)).astype("uint8") * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < height * width * 0.15:
                continue

            box = cv2.boxPoints(cv2.minAreaRect(contour)).astype("float32")
            box[:, 0] = np.clip(box[:, 0], 0, width - 1)
            box[:, 1] = np.clip(box[:, 1], 0, height - 1)
            if not _valid_document_box(box, width, height):
                continue

            box_area = cv2.contourArea(order_points(box))
            if box_area > best_area:
                best_area = box_area
                best_box = box

    if best_box is None:
        return None
    return order_points(best_box)


def _fit_x_at_y(lines: list[tuple[int, int, int, int]], y: float) -> float:
    points = []
    for x1, y1, x2, y2 in lines:
        points.append((float(y1), float(x1)))
        points.append((float(y2), float(x2)))
    ys = np.array([point[0] for point in points], dtype=np.float32)
    xs = np.array([point[1] for point in points], dtype=np.float32)
    if np.ptp(ys) < 1:
        return float(np.median(xs))
    slope, intercept = np.polyfit(ys, xs, 1)
    return float(slope * y + intercept)


def _detect_corners_by_page_lines(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 40, 120)

    min_line_length = max(120, int(height * 0.24))
    lines = cv2.HoughLinesP(
        edged,
        1,
        np.pi / 180,
        threshold=max(70, int(width * 0.10)),
        minLineLength=min_line_length,
        maxLineGap=max(18, int(height * 0.035)),
    )
    if lines is None:
        return None

    vertical_lines: list[tuple[int, int, int, int]] = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        angle = abs(np.degrees(np.arctan2(dy, dx)))
        length = float(np.hypot(dx, dy))
        if angle < 78 or length < min_line_length:
            continue
        vertical_lines.append((int(x1), int(y1), int(x2), int(y2)))

    if len(vertical_lines) < 2:
        return None

    groups: list[dict[str, Any]] = []
    group_tolerance = max(12, int(width * 0.035))
    for line in sorted(vertical_lines, key=lambda item: (item[0] + item[2]) / 2):
        x_mid = (line[0] + line[2]) / 2
        for group in groups:
            if abs(x_mid - group["x"]) <= group_tolerance:
                group["lines"].append(line)
                all_x = [((ln[0] + ln[2]) / 2) for ln in group["lines"]]
                group["x"] = float(np.median(all_x))
                break
        else:
            groups.append({"x": float(x_mid), "lines": [line]})

    best_pair = None
    best_score = 0.0
    for left_index, left_group in enumerate(groups):
        for right_group in groups[left_index + 1 :]:
            left_x = float(left_group["x"])
            right_x = float(right_group["x"])
            pair_width = right_x - left_x
            if pair_width < width * 0.45 or pair_width > width * 0.95:
                continue
            if left_x > width * 0.55 or right_x < width * 0.45:
                continue

            line_count = len(left_group["lines"]) + len(right_group["lines"])
            score = pair_width * (1 + line_count * 0.15)
            if score > best_score:
                best_score = score
                best_pair = (left_group["lines"], right_group["lines"])

    if best_pair is None:
        return None

    left_lines, right_lines = best_pair
    top_y = 0.0
    bottom_y = float(height - 1)
    left_top = np.clip(_fit_x_at_y(left_lines, top_y), 0, width - 1)
    left_bottom = np.clip(_fit_x_at_y(left_lines, bottom_y), 0, width - 1)
    right_top = np.clip(_fit_x_at_y(right_lines, top_y), 0, width - 1)
    right_bottom = np.clip(_fit_x_at_y(right_lines, bottom_y), 0, width - 1)

    points = np.array(
        [
            [left_top, top_y],
            [right_top, top_y],
            [right_bottom, bottom_y],
            [left_bottom, bottom_y],
        ],
        dtype="float32",
    )
    if not _valid_document_box(points, width, height):
        return None
    return order_points(points)


def detect_document_corners(image: np.ndarray) -> np.ndarray | None:
    original_height, original_width = image.shape[:2]
    detection_image = _resize_to_max_side(image, MAX_DETECT_SIDE)
    detect_height, detect_width = detection_image.shape[:2]
    scale_x = original_width / detect_width
    scale_y = original_height / detect_height

    gray = cv2.cvtColor(detection_image, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(gray, 50, 150)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edged = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel, iterations=2)
    edged = cv2.dilate(edged, kernel, iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    min_area = detect_height * detect_width * 0.15

    for contour in contours[:8]:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue

        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4:
            points = approx.reshape(4, 2).astype("float32")
            if _valid_document_box(points, detect_width, detect_height):
                return _scale_points(points, scale_x, scale_y)

    mask_points = _detect_corners_by_paper_mask(detection_image)
    if mask_points is not None and not _box_nearly_full_frame(mask_points, detect_width, detect_height):
        return _scale_points(mask_points, scale_x, scale_y)

    line_points = _detect_corners_by_page_lines(detection_image)
    if line_points is not None:
        return _scale_points(line_points, scale_x, scale_y)

    if mask_points is None:
        return None
    return _scale_points(mask_points, scale_x, scale_y)


def four_point_transform(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    rect = order_points(points)
    top_left, top_right, bottom_right, bottom_left = rect

    width_a = np.linalg.norm(bottom_right - bottom_left)
    width_b = np.linalg.norm(top_right - top_left)
    max_width = max(int(width_a), int(width_b), 1)

    height_a = np.linalg.norm(top_right - bottom_right)
    height_b = np.linalg.norm(top_left - bottom_left)
    max_height = max(int(height_a), int(height_b), 1)

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )

    matrix = cv2.getPerspectiveTransform(rect, destination)
    return cv2.warpPerspective(image, matrix, (max_width, max_height))


def _normalize_luminance(channel: np.ndarray, sigma: float) -> np.ndarray:
    background = cv2.GaussianBlur(channel, (0, 0), sigma)
    return cv2.divide(channel, background, scale=255)


def _boost_document_tones(channel: np.ndarray, text_darkening: float, paper_brightening: float) -> np.ndarray:
    boosted = channel.astype(np.float32)
    dark_mask = boosted < 145
    light_mask = boosted > 175
    boosted[dark_mask] *= text_darkening
    boosted[light_mask] = 255 - ((255 - boosted[light_mask]) / paper_brightening)
    return np.clip(boosted, 0, 255).astype(np.uint8)


def enhance_document(image: np.ndarray, mode: str = "standard") -> np.ndarray:
    preset = ENHANCE_PRESETS.get(mode, ENHANCE_PRESETS["standard"])
    image = _upscale_for_text(image)
    rgb = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)

    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    luminance, a_channel, b_channel = cv2.split(lab)

    if preset["normalize_background"]:
        luminance = _normalize_luminance(luminance, float(preset["background_sigma"]))

    luminance = _stretch_contrast(luminance)
    luminance = _apply_gamma(luminance, float(preset["gamma"]))

    clahe = cv2.createCLAHE(clipLimit=float(preset["clip_limit"]), tileGridSize=(8, 8))
    luminance = clahe.apply(luminance)

    blurred = cv2.GaussianBlur(luminance, (0, 0), 1.2)
    amount = float(preset["sharpen_amount"])
    luminance = cv2.addWeighted(luminance, 1.0 + amount, blurred, -amount, 0)

    denoise = int(preset["denoise"])
    if denoise > 0:
        luminance = cv2.bilateralFilter(luminance, 5, denoise, denoise)

    luminance = _boost_document_tones(
        luminance,
        text_darkening=float(preset["text_darkening"]),
        paper_brightening=float(preset["paper_brightening"]),
    )

    enhanced_lab = cv2.merge((luminance, a_channel, b_channel))
    return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2RGB)


def _draw_debug_contour(image: np.ndarray, corners: np.ndarray | None, path: Path) -> str:
    debug_image = image.copy()
    if corners is not None:
        pts = corners.astype(np.int32).reshape((-1, 1, 2))
        cv2.polylines(debug_image, [pts], isClosed=True, color=(255, 0, 0), thickness=4)
    return _save_rgb_image(path, debug_image)


def process_document_image(image_path: str, task_id: str | None = None, enhance_mode: str = "standard") -> dict[str, Any]:
    ensure_runtime_dirs()
    task_id = task_id or str(uuid.uuid4())

    try:
        original = _crop_black_borders(_load_rgb_image(image_path))
        working = _resize_to_max_side(original, MAX_OUTPUT_SIDE)
        corners = detect_document_corners(working)

        if corners is not None:
            warped = four_point_transform(working, corners)
            status = "success"
            message = "已自动检测到文档边界，并完成透视校正。"
            corners_payload = corners.astype(int).tolist()
        else:
            warped = working
            status = "fallback"
            message = "未检测到稳定的文档四角，已对整张图片进行文字增强。"
            corners_payload = None

        enhanced = enhance_document(warped, mode=enhance_mode)
        warped_path = PROCESSED_DIR / f"{task_id}_warped.png"
        enhanced_path = PROCESSED_DIR / f"{task_id}_enhanced.png"
        debug_path = DEBUG_DIR / f"{task_id}_corners.png"

        return {
            "status": status,
            "original_path": image_path,
            "warped_path": _save_rgb_image(warped_path, warped),
            "enhanced_path": _save_rgb_image(enhanced_path, enhanced),
            "debug_path": _draw_debug_contour(working, corners, debug_path),
            "enhance_mode": enhance_mode,
            "corners": corners_payload,
            "message": message,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "original_path": image_path,
            "warped_path": None,
            "enhanced_path": None,
            "debug_path": None,
            "corners": None,
            "message": str(exc) or exc.__class__.__name__,
        }
