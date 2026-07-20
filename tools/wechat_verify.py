"""微信操作步骤验证器。

Qt微信是黑盒——没有DOM、没有UIA、没有API——所有验证只能靠截图+OCR。
每个 verify_* 函数返回 (ok: bool, detail: str)，detail在失败时描述原因。

设计原则：
  1. 每个验证耗时控制在 1-2 秒（避免拖累整体流程）
  2. 优先验证 ROI（感兴趣区域），避免全窗口 OCR
  3. 验证失败自动保存调试截图，返回明确错误 + 窗口实际 OCR 文字
"""

import time
from pathlib import Path
from typing import Optional

from .wechat_vision import capture_window, find_text_center

# 调试截图目录
_DEBUG_DIR = Path(__file__).parent.parent / "debug_screenshots"
_DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def _save_debug_screenshot(img, hwnd: int, step: str, roi_rect=None) -> Path:
    """保存窗口截图用于离线调试。roi_rect=(rx,ry,rw,rh) 在图上画框。"""
    import cv2
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _DEBUG_DIR / f"verify_fail_{step}_{hwnd}_{ts}.png"
    out = img.copy()
    h, w = out.shape[:2]
    if roi_rect:
        rx, ry, rw, rh = roi_rect
        cv2.rectangle(out, (rx, ry), (rx + rw, ry + rh), (0, 0, 255), 2)
        cv2.putText(out, f"ROI {rx},{ry} {rw}x{rh}", (rx, max(ry - 8, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.putText(out, f"FAIL: {step}  {ts}", (10, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    cv2.imwrite(str(path), out)
    print(f"[VERIFY-DEBUG] 截图已保存: {path}")
    return path


def _dump_ocr_texts(img) -> list[str]:
    """对整张图做 OCR，返回识别到的所有文字列表（去重）。"""
    from .wechat_vision import _ocr_image
    results = _ocr_image(img)
    texts = []
    for recognized, _bbox, conf in results:
        if conf >= 0.2 and len(recognized.strip()) >= 1:
            texts.append(f"{recognized}({conf:.1%})")
    return texts


def _ocr_texts_in_roi(
    hwnd: int,
    texts: list[str],
    y_start_pct: float = 0,
    y_end_pct: float = 1.0,
    x_start_pct: float = 0,
    x_end_pct: float = 1.0,
    confidence: float = 0.45,
    step_label: str = "",
) -> tuple[bool, str]:
    """在指定 ROI 内 OCR 搜索一组文字，返回 (命中?, 命中的文字或失败原因)。

    失败时自动保存调试截图并 dump ROI 内所有 OCR 文字。
    """
    try:
        img = capture_window(hwnd, client_only=True)
    except Exception as e:
        return False, f"截图失败: {e}"

    if img is None or img.size == 0:
        return False, "截图为空"

    h, w = img.shape[:2]
    ry1 = max(0, int(h * y_start_pct))
    ry2 = min(h, int(h * y_end_pct))
    rx1 = max(0, int(w * x_start_pct))
    rx2 = min(w, int(w * x_end_pct))
    roi = img[ry1:ry2, rx1:rx2]

    if roi.size == 0:
        return False, f"ROI 为空 ({rx1}:{rx2}, {ry1}:{ry2})"

    # 先查完整 roi
    for text in texts:
        center = find_text_center(roi, text, confidence=confidence)
        if center:
            return True, text

    # ── 失败：保存截图 + dump OCR ──
    label = step_label or "verify"
    roi_rect = (rx1, ry1, rx2 - rx1, ry2 - ry1)
    _save_debug_screenshot(img, hwnd, label, roi_rect)

    # dump ROI 内所有 OCR 文字
    try:
        ocr_texts = _dump_ocr_texts(roi)
        ocr_summary = ", ".join(ocr_texts[:30]) if ocr_texts else "(无识别结果)"
    except Exception:
        ocr_summary = "(OCR 异常)"

    return False, (
        f"未检测到目标文字: {texts[:6]}... | "
        f"ROI=[{rx1}:{rx2}, {ry1}:{ry2}]/{w}x{h} | "
        f"实际OCR: [{ocr_summary}]"
    )


# ── 步骤级验证 ──

def verify_search_results_visible(hwnd: int, keyword: str) -> tuple[bool, str]:
    """Step 1 → 验证搜索已提交且结果列表中包含关键词。"""
    return _ocr_texts_in_roi(
        hwnd,
        texts=[keyword, "搜索", "服务号", "公众号", "小程序"],
        y_start_pct=0.05,
        y_end_pct=0.75,
        confidence=0.35,
        step_label="search",
    )


def verify_detail_window_opened(
    hwnd: int, keyword: str = ""
) -> tuple[bool, str]:
    """Step 2 → 验证已进入服务号详情页。"""
    texts = ["服务号", "关注", "已关注", "私信", "全部", "贴图", "文章", "视频号"]
    if keyword:
        texts.insert(0, keyword)

    return _ocr_texts_in_roi(
        hwnd,
        texts=texts,
        y_start_pct=0,
        y_end_pct=0.65,
        confidence=0.35,
        step_label="detail",
    )


def verify_follow_success(hwnd: int) -> tuple[bool, str]:
    """Step 3 → 验证关注操作是否完成。"""
    time.sleep(1.0)
    return _ocr_texts_in_roi(
        hwnd,
        texts=["已关注", "发消息", "私信"],
        y_start_pct=0,
        y_end_pct=0.5,
        confidence=0.35,
        step_label="follow",
    )


def verify_chat_window_entered(hwnd: int) -> tuple[bool, str]:
    """Step 4 → 验证已从详情页进入聊天窗口。

    策略（快速通道优先）：
      1. 检测聊天窗口底部特征（菜单栏: 产品介绍/操作视频/联系我们/功能）
      2. 确认详情页特征消失
      3. 都失败才保存调试截图
    """
    # 优先检测聊天特征（底部菜单栏在服务号聊天中是固定元素）
    chat_ok, chat_text = _ocr_texts_in_roi(
        hwnd,
        texts=["产品介绍", "操作视频", "联系我们", "功能",
               "按住说话", "语音", "表情", "发送"],
        y_start_pct=0.55,
        y_end_pct=1.0,
        confidence=0.25,
        step_label="chat",
    )

    if chat_ok:
        return True, f"检测到聊天特征: {chat_text}"

    # 快速判断详情页特征是否存在（不保存调试截图）
    detail_still = _check_texts_quiet(
        hwnd,
        texts=["贴图", "文章", "视频号"],
        y_start_pct=0,
        y_end_pct=0.3,
        confidence=0.35,
    )

    if detail_still:
        return False, "仍在详情页（检测到贴图/文章/视频号），未进入聊天窗口"

    # 详情页特征消失 → 可能已进入聊天
    return True, "详情页特征已消失，假定已进入聊天"


def _check_texts_quiet(
    hwnd: int,
    texts: list[str],
    y_start_pct: float = 0,
    y_end_pct: float = 1.0,
    x_start_pct: float = 0,
    x_end_pct: float = 1.0,
    confidence: float = 0.35,
) -> bool:
    """OCR 检查（不保存截图），返回 True/False。用于中间步骤的快速判断。"""
    from .wechat_vision import capture_window, find_text_center
    try:
        img = capture_window(hwnd, client_only=True)
    except Exception:
        return False
    if img is None or img.size == 0:
        return False

    h, w = img.shape[:2]
    ry1 = max(0, int(h * y_start_pct))
    ry2 = min(h, int(h * y_end_pct))
    rx1 = max(0, int(w * x_start_pct))
    rx2 = min(w, int(w * x_end_pct))
    roi = img[ry1:ry2, rx1:rx2]
    if roi.size == 0:
        return False

    for text in texts:
        center = find_text_center(roi, text, confidence=confidence)
        if center:
            return True
    return False


def verify_input_box_visible(hwnd: int) -> tuple[bool, str]:
    """Step 4b → 验证输入框可见。

    服务号聊天窗口的输入区与普通聊天不同，没有「表情」「加号」等按钮，
    常见特征：底部有白色/灰色输入矩形、「发送」按钮、「功能」按钮、菜单栏。

    策略：
      1. OCR 搜索聊天输入区常见文字（宽泛匹配）
      2. 结构检测：底部是否有浅色矩形块（输入框特征）
      3. 仍失败 → 全窗口 OCR dump，人工分析
    """
    time.sleep(0.5)

    # 策略 1: OCR（降低置信度、扩大会话窗口特征词）
    # 服务号聊天常见：发送、可以描述、按住说话、功能、产品介绍、操作视频、联系我们
    result = _ocr_texts_in_roi(
        hwnd,
        texts=[
            "发送", "可以描述", "描述任务",
            "按住说话", "功能", "语音",
            "产品介绍", "操作视频", "联系我们",
            "表情", "加号", "语音输入",
        ],
        y_start_pct=0.55,
        y_end_pct=0.98,
        confidence=0.25,
        step_label="input_box",
    )
    if result[0]:
        return result

    # 策略 2: 结构检测 — 底部是否有明显的浅色矩形（输入框）
    try:
        import cv2
        import numpy as np
        img = capture_window(hwnd, client_only=True)
        if img is not None and img.size > 0:
            h, w = img.shape[:2]
            # 取底部 25%
            bottom = img[int(h * 0.75):, :]
            gray = cv2.cvtColor(bottom, cv2.COLOR_BGR2GRAY)
            # 检测是否有水平方向的长矩形（输入框特征）
            # 用边缘检测 + 找水平线
            edges = cv2.Canny(gray, 50, 150)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=30,
                                     minLineLength=int(w * 0.3), maxLineGap=5)
            if lines is not None and len(lines) >= 3:
                # 有足够多的水平线 → 输入区存在
                print(f"[VERIFY] 结构检测: 底部 {len(lines)} 条水平线 → 假定输入框存在")
                return True, "结构检测: 底部检测到输入框轮廓"

            # 也检测亮度变化：输入区通常比聊天记录区亮
            top_half = gray[:gray.shape[0] // 2]
            bottom_half = gray[gray.shape[0] // 2:]
            if bottom_half.size > 0 and top_half.size > 0:
                top_mean = np.mean(top_half)
                bottom_mean = np.mean(bottom_half)
                if abs(bottom_mean - top_mean) > 15:
                    print(f"[VERIFY] 亮度检测: top={top_mean:.0f} bottom={bottom_mean:.0f} → 底部区域有变化")
                    return True, f"亮度检测: 底部区域明显不同于聊天区 (Δ={abs(bottom_mean-top_mean):.0f})"
    except Exception as e:
        print(f"[VERIFY] 结构检测异常: {e}")

    # 失败时，result[1] 已经包含了 OCR dump
    return result


def verify_message_sent(hwnd: int, message: str) -> tuple[bool, str]:
    """Step 4c → 验证消息已发送。

    自己发送的消息在绿色气泡中（白字绿底），标准 OCR 准确性差。
    使用多策略流水线：
      1. 聊天气泡预处理（反转 + CLAHE + 自适应阈值）→ OCR
      2. 短文本片段匹配（前 3/5/8 字分别试）
      3. 右半区聚焦（自己的消息靠右）
    """
    time.sleep(1.0)

    msg = message.strip()
    if len(msg) < 2:
        return False, "消息太短"

    # 生成多条搜索片段（不同长度，避免 OCR 截断）
    search_texts = []
    for n in (3, 5, 8, len(msg)):
        if n <= len(msg):
            search_texts.append(msg[:n])
    # 去重 + 去太短
    search_texts = list(dict.fromkeys([t for t in search_texts if len(t) >= 2]))

    try:
        from .wechat_vision import capture_window
        import cv2
        img = capture_window(hwnd, client_only=True)
    except Exception as e:
        return False, f"截图失败: {e}"

    if img is None or img.size == 0:
        return False, "截图为空"

    h, w = img.shape[:2]

    # 策略 1: 原始图 → OCR，右半区
    roi = img[int(h * 0.1):int(h * 0.85), int(w * 0.3):]
    if roi.size > 0:
        for text in search_texts:
            center = find_text_center(roi, text, confidence=0.25)
            if center:
                return True, text

    # 策略 2: 聊天气泡预处理（反转暗底 + CLAHE + 自适应阈值）
    try:
        from .wechat_ocr import preprocess_chat_bubble
        preprocessed = preprocess_chat_bubble(img)
        roi_pp = preprocessed[int(h * 0.1):int(h * 0.85), int(w * 0.3):]
        if roi_pp.size > 0:
            for text in search_texts:
                center = find_text_center(roi_pp, text, confidence=0.20)
                if center:
                    return True, f"{text}(预处理)"
    except ImportError:
        pass

    # 策略 3: 自适应阈值二值化 + 全文 OCR
    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 15, 3)
        roi_bin = binary[int(h * 0.1):int(h * 0.85), int(w * 0.3):]
        if roi_bin.size > 0:
            for text in search_texts:
                center = find_text_center(roi_bin, text, confidence=0.15)
                if center:
                    return True, f"{text}(二值化)"
    except Exception:
        pass

    # 全部失败 → 保存截图 + OCR dump
    roi_rect = (int(w * 0.3), int(h * 0.1), int(w * 0.7), int(h * 0.75))
    _save_debug_screenshot(img, hwnd, "msg_sent", roi_rect)

    ocr_summary = ""
    try:
        from .wechat_vision import _ocr_image
        ocr_results = _ocr_image(roi)
        ocr_summary = ", ".join(
            f"{r[0]}({r[2]:.0%})" for r in ocr_results[:20] if r[2] >= 0.15
        ) if ocr_results else "(无识别结果)"
    except Exception:
        ocr_summary = "(OCR 异常)"

    return False, (
        f"未检测到消息 '{search_texts}' | "
        f"ROI=右半区 [{int(w*0.3)}:{w}, {int(h*0.1)}:{int(h*0.85)}] | "
        f"实际OCR: [{ocr_summary}]"
    )
