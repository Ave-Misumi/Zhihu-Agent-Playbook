"""扩展 OCR 辅助：处理彩色背景文字识别。

微信聊天气泡（绿色自己/白色对方）上的文字对标准 OCR 不友好。
本模块提供预处理流水线，提升彩色气泡文字的识别率。
"""
import numpy as np
import cv2


def preprocess_chat_bubble(img: np.ndarray) -> np.ndarray:
    """预处理聊天气泡图像，提升 OCR 识别率。

    策略：
      1. 灰度化
      2. CLAHE 自适应直方图均衡（增强对比度）
      3. 锐化（突出文字边缘）
      4. 自适应阈值二值化（黑白分明）

    返回预处理后的灰度图像。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # CLAHE 对比度增强
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # 轻微锐化
    blur = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
    sharpened = cv2.addWeighted(enhanced, 1.5, blur, -0.5, 0)

    # 自适应阈值 → 二值白底黑字
    binary = cv2.adaptiveThreshold(
        sharpened, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2,
    )

    return binary


def preprocess_for_ocr(img: np.ndarray, method: str = "chat") -> np.ndarray:
    """根据场景选择合适的预处理。

    Args:
        method: "chat" → 聊天气泡模式（灰度+CLAHE+锐化+二值化）
                "contrast" → 仅增强对比度
                "none" → 返回原图
    """
    if method == "none":
        return img
    if method == "chat":
        return preprocess_chat_bubble(img)
    if method == "contrast":
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(gray)
    return img


def adaptive_threshold_image(img: np.ndarray, block_size: int = 15, c: int = 3) -> np.ndarray:
    """自适应阈值，适合彩色背景上的文字（如聊天气泡）。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size, c,
    )


def invert_if_dark_text(img: np.ndarray) -> np.ndarray:
    """如果图像主要是暗色背景白字（如绿色气泡），反转使文字变暗背景变亮。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    mean_val = np.mean(gray)
    # 如果平均亮度偏低（暗色背景），反转
    if mean_val < 128:
        return cv2.bitwise_not(gray)
    return gray
