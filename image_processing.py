from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from config import DEBUG_DIR, PROCESSED_DIR, ensure_runtime_dirs


MAX_DETECT_SIDE = 1000
MAX_OUTPUT_SIDE = 2200
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
        "clip_limit": 4.0,
        "sharpen_amount": 1.8,
        "denoise": 0,
        "normalize_background": True,
        "background_sigma": 45,
        "gamma": 0.72,
        "text_darkening": 0.62,
        "paper_brightening": 1.14,
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
            points[:, 0] *= scale_x
            points[:, 1] *= scale_y
            return order_points(points)

    return None


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
        original = _load_rgb_image(image_path)
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
