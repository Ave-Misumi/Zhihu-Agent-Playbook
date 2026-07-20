"""微信自动化视觉辅助模块

提供基于 OpenCV 的窗口截图、模板匹配、OCR 文字定位、颜色按钮检测。
用于在 Qt 渲染的微信窗口中定位：
  - 搜索结果中的目标服务号
  - 服务号详情页中的「关注」/「私信」按钮

设计原则：
  1. 优先模板匹配，速度快且对 Qt 位图按钮效果好
  2. 模板缺失时，使用颜色/文字特征兜底
  3. 所有坐标返回相对于窗口客户区的 (x, y)，便于上层转换到屏幕坐标
"""
import os
import time
import ctypes
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

# 如果本模块被其他代码导入时还没有 numpy，这里确保可用

try:
    import cv2
except ImportError as e:  # pragma: no cover
    raise RuntimeError("wechat_vision 需要 opencv-python，请执行: pip install opencv-python") from e

# Win32 依赖声明
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.restype = ctypes.c_bool
user32.IsWindow.argtypes = [wintypes.HWND]

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════
DEFAULT_TEMPLATE_DIR = Path(__file__).parent.parent / "assets" / "wechat_templates"
DEFAULT_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

# 微信绿色按钮的 HSV 范围（关注/发消息按钮的绿色）
_WECHAT_GREEN_HSV_LOW = np.array([35, 80, 80])
_WECHAT_GREEN_HSV_HIGH = np.array([90, 255, 255])


# ═══════════════════════════════════════════════
# 截图
# ═══════════════════════════════════════════════

def capture_window(hwnd: int, client_only: bool = False) -> np.ndarray:
    """截取指定窗口为 OpenCV BGR 图像 (numpy array)。

    Args:
        hwnd: 窗口句柄
        client_only: 是否只截客户区（不含标题栏和边框）。
                     微信的 Qt 控件从客户区开始渲染，建议 True。

    Returns:
        np.ndarray: BGR 图像
    """
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom

    if client_only:
        # 获取客户区在屏幕上的位置
        client_rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(client_rect))
        # 客户区左上角转屏幕坐标
        client_point = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(client_point))
        left, top = client_point.x, client_point.y
        right = left + (client_rect.right - client_rect.left)
        bottom = top + (client_rect.bottom - client_rect.top)

    pil_img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return img


def save_window_screenshot(hwnd: int, path: str | Path, client_only: bool = False) -> Path:
    """截图保存到文件，用于人工标注或调试。"""
    img = capture_window(hwnd, client_only=client_only)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


# ═══════════════════════════════════════════════
# 模板匹配
# ═══════════════════════════════════════════════

def load_template(path: str | Path) -> np.ndarray:
    """加载模板图片（BGR）。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"模板不存在: {path}")
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法读取模板图片: {path}")
    return img


def find_template(
    window_img: np.ndarray,
    template: np.ndarray | str | Path,
    confidence: float = 0.8,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> tuple[int, int, int, int, float] | None:
    """在窗口图像中查找模板位置。

    Returns:
        (x, y, w, h, similarity) 左上角坐标/宽高/匹配度，未找到返回 None
    """
    if isinstance(template, (str, Path)):
        template = load_template(template)

    h, w = template.shape[:2]
    if window_img.shape[0] < h or window_img.shape[1] < w:
        return None

    res = cv2.matchTemplate(window_img, template, method)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= confidence:
        x, y = max_loc
        return (x, y, w, h, max_val)
    return None


def find_template_center(
    window_img: np.ndarray,
    template: np.ndarray | str | Path,
    confidence: float = 0.8,
) -> tuple[int, int] | None:
    """返回模板中心点相对于窗口客户区的坐标。"""
    match = find_template(window_img, template, confidence)
    if match is None:
        return None
    x, y, w, h, _ = match
    return (x + w // 2, y + h // 2)


# ═══════════════════════════════════════════════
# 颜色检测：绿色按钮
# ═══════════════════════════════════════════════

def find_green_button(
    window_img: np.ndarray,
    min_area: int = 200,
    max_area: int = 20000,
    y_min: int = 0,
    y_max: int = 0,
) -> tuple[int, int] | None:
    """在窗口图像中查找绿色按钮的中心点。

    适用于微信详情页中的「关注」「发消息」等绿色按钮。
    返回相对于窗口客户区的 (x, y)，未找到返回 None。

    Args:
        y_min, y_max: 垂直搜索范围（像素，相对于客户区）。为 0 时不限制。
    """
    candidates = _find_green_candidates(window_img, min_area, max_area, y_min, y_max)
    if not candidates:
        return None
    # 优先选择面积最大的绿色按钮
    candidates.sort(key=lambda c: c[2], reverse=True)
    return candidates[0][0], candidates[0][1]


def _find_green_candidates(
    window_img: np.ndarray,
    min_area: int = 200,
    max_area: int = 20000,
    y_min: int = 0,
    y_max: int = 0,
) -> list[tuple[int, int, int, int, int]]:
    """返回所有绿色候选区域，每项 (cx, cy, area, w, h)。内部使用。"""
    img_h, _ = window_img.shape[:2]
    if y_max <= 0:
        y_max = img_h

    hsv = cv2.cvtColor(window_img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, _WECHAT_GREEN_HSV_LOW, _WECHAT_GREEN_HSV_HIGH)

    # 形态学闭运算，连接断裂的绿色像素
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if not (min_area <= area <= max_area):
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        # 垂直范围约束
        if y < y_min or y + h > y_max:
            continue
        aspect = w / max(h, 1)
        # 按钮宽高比通常在 1.5~5 之间（微信绿色按钮比较扁）
        if 1.0 <= aspect <= 6.0:
            candidates.append((x + w // 2, y + h // 2, area, w, h))

    return candidates


def find_button_with_text(
    window_img: np.ndarray,
    target_text: str,
    min_area: int = 200,
    max_area: int = 20000,
    y_min: int = 0,
    y_max: int = 0,
    padding: int = 8,
) -> tuple[int, int] | None:
    """颜色粗筛 + OCR 文字确认，精确定位包含指定文字的绿色按钮。

    流程：
      1. 在所有绿色候选区域中按面积排序
      2. 对每个候选区域扩大 padding 后做 OCR
      3. 返回包含 target_text 的第一个匹配按钮中心点

    Args:
        target_text: 按钮文字，如「关注」「发消息」「私信」
        padding: 候选区域外扩像素，避免 OCR 截断按钮文字

    Returns:
        相对于窗口客户区的 (cx, cy)，未找到返回 None
    """
    candidates = _find_green_candidates(window_img, min_area, max_area, y_min, y_max)
    if not candidates:
        return None

    # 按面积从大到小排序
    candidates.sort(key=lambda c: c[2], reverse=True)

    img_h, img_w = window_img.shape[:2]

    for cx, cy, area, bw, bh in candidates:
        # 裁剪按钮区域（外扩 padding）
        rx = max(0, cx - bw // 2 - padding)
        ry = max(0, cy - bh // 2 - padding)
        rw = min(bw + padding * 2, img_w - rx)
        rh = min(bh + padding * 2, img_h - ry)
        roi = window_img[ry:ry + rh, rx:rx + rw]

        if roi.size == 0:
            continue

        ocr_result = _ocr_image(roi)
        for recognized, _bbox, conf in ocr_result:
            if target_text in recognized:
                print(f"[VISION-TEXT] 绿色区域匹配「{recognized}」(target={target_text}, conf={conf:.2f})")
                return (cx, cy)

    return None


# ═══════════════════════════════════════════════
# OCR 文字定位（可选，需要 tesseract 或 easyocr）
# ═══════════════════════════════════════════════

def _try_paddle_ocr():
    try:
        from paddleocr import PaddleOCR
        return PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except Exception:
        return None


def _try_easyocr():
    try:
        import easyocr
        return easyocr.Reader(["ch_sim", "en"])
    except Exception:
        return None


class _OCRBackend:
    """OCR 后端惰性初始化。"""
    _instance = None
    _kind = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = _try_paddle_ocr()
            if cls._instance:
                cls._kind = "paddle"
            else:
                cls._instance = _try_easyocr()
                cls._kind = "easyocr"
        return cls._instance, cls._kind


def _ocr_image(img: np.ndarray) -> list:
    """返回 OCR 结果列表，每项格式统一为 (text, bbox, confidence)。"""
    backend, kind = _OCRBackend.get()
    if backend is None:
        return []

    h, w = img.shape[:2]
    if h < 10 or w < 10:
        return []

    if kind == "paddle":
        result = backend.ocr(img, cls=True)
        # PaddleOCR v2: result[0] 为列表
        lines = result[0] if result else []
        out = []
        for line in lines:
            if line is None:
                continue
            bbox, (text, conf) = line
            out.append((text, bbox, conf))
        return out

    if kind == "easyocr":
        result = backend.readtext(img)
        out = []
        for bbox, text, conf in result:
            out.append((text, bbox, conf))
        return out

    return []


def find_text_center(
    window_img: np.ndarray,
    text: str,
    confidence: float = 0.6,
) -> tuple[int, int] | None:
    """在窗口图像中通过 OCR 查找指定文本，返回文本区域中心点。

    注意：OCR 对中文识别有一定误差，text 可以传较短关键词。
    """
    ocr_result = _ocr_image(window_img)
    best_match = None
    best_score = 0.0

    for recognized, bbox, conf in ocr_result:
        if conf < confidence:
            continue
        if text in recognized:
            # 计算 bbox 中心
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys))
            if conf > best_score:
                best_score = conf
                best_match = (cx, cy)

    return best_match


# ═══════════════════════════════════════════════
# 高阶工具：一键定位
# ═══════════════════════════════════════════════

def find_button_by_vision(
    hwnd: int,
    template_name: str | None = None,
    use_color: bool = True,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
) -> tuple[int, int] | None:
    """综合利用模板匹配和颜色检测，定位按钮中心点。

    Args:
        hwnd: 窗口句柄
        template_name: 模板文件名（如 "follow_button.png"），优先使用
        use_color: 模板未匹配或不存在时，是否用颜色检测兜底
        template_dir: 模板存放目录

    Returns:
        相对于窗口客户区的 (x, y)，未找到返回 None
    """
    img = capture_window(hwnd, client_only=True)
    if img is None or img.size == 0:
        return None

    # 1. 模板匹配
    if template_name:
        template_path = Path(template_dir) / template_name
        if template_path.exists():
            center = find_template_center(img, template_path, confidence=0.75)
            if center:
                print(f"[VISION] 模板匹配成功: {template_name} -> {center}")
                return center

    # 2. 颜色检测兜底
    if use_color:
        center = find_green_button(img)
        if center:
            print(f"[VISION] 颜色检测定位到绿色按钮: {center}")
            return center

    return None


def save_template_from_window(
    hwnd: int,
    region: tuple[int, int, int, int],
    name: str,
    template_dir: str | Path = DEFAULT_TEMPLATE_DIR,
) -> Path:
    """从窗口截图中截取区域并保存为模板，供后续匹配。

    Args:
        hwnd: 窗口句柄
        region: 相对于窗口客户区的 (x, y, w, h)
        name: 模板文件名
    """
    img = capture_window(hwnd, client_only=True)
    x, y, w, h = region
    cropped = img[y:y+h, x:x+w]
    path = Path(template_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cropped)
    print(f"[VISION] 模板已保存: {path}")
    return path


# ═══════════════════════════════════════════════
# 调试辅助
# ═══════════════════════════════════════════════

def visualize_detection(
    window_img: np.ndarray,
    point: tuple[int, int] | None,
    save_path: str | Path = "vision_debug.png",
) -> Path:
    """在截图上标记检测点，保存用于调试。"""
    img = window_img.copy()
    if point:
        cv2.circle(img, point, 10, (0, 0, 255), 2)
        cv2.putText(img, f"({point[0]}, {point[1]})", (point[0] + 12, point[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    path = Path(save_path)
    cv2.imwrite(str(path), img)
    return path
