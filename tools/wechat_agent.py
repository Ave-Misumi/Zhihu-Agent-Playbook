"""微信桌面自动化 Agent 层

==== 架构 ===

  LLM (决策者)
    ↓ 调用工具
  原子工具 (截图+OCR+执行)
    ↓ 返回自然语言描述
  LLM 阅读描述 → 决定下一步行动

LLM 不看像素 —— 只看 CV 系统提取的文字描述。
CV 不做决策 —— 只做截图、OCR、颜色检测，生成描述。

==== 工具列表 ===

  wechat_observe          — 截图+OCR+分析，描述当前窗口状态
  wechat_search           — 打开搜索并输入关键词
  wechat_click_first_result — 点击第一条搜索结果
  wechat_click_button     — 点击页面上的按钮（关注/私信/返回...）
  wechat_type_and_send    — 在聊天窗口输入并发送消息

==== 设计原则 ===

  1. 每个工具返回自然语言描述（LLM 可理解）
  2. 工具之间通过缓存的 hwnd 共享窗口状态
  3. 失败不 crash —— 返回错误描述让 LLM 决策
  4. 每步操作后自动观察，无需 LLM 显式调用 observe
"""

import time
import ctypes
import subprocess as _subprocess
from ctypes import wintypes
from pathlib import Path

import pyautogui
import numpy as np

from .wechat_vision import (
    capture_window, _ocr_image, find_text_center,
    find_green_button, find_button_with_text,
    _WECHAT_GREEN_HSV_LOW, _WECHAT_GREEN_HSV_HIGH,
)

# ═══════════════════════════════════════════
# Win32 常量
# ═══════════════════════════════════════════

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# 窗口查找
def _find_wechat_hwnd() -> int | None:
    """查找微信主窗口 hwnd（>=500×400、有标题、非TrayIcon）。"""
    pids = []
    for name in ("WeChat.exe", "Weixin.exe"):
        try:
            out = _subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                shell=True, text=True
            )
            for line in out.strip().split('\n'):
                if name in line:
                    parts = line.replace('"', '').split(',')
                    if len(parts) >= 2:
                        try:
                            pids.append(int(parts[1].strip()))
                        except ValueError:
                            pass
        except Exception:
            pass

    candidates = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    @WNDENUMPROC
    def _enum(hwnd, _lp):
        wp = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wp))
        if wp.value not in pids:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ww, hh = r.right - r.left, r.bottom - r.top
        if ww < 500 or hh < 400:
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if "TrayIcon" in cls_buf.value:
            return True
        candidates.append((hwnd, ww * hh))
        return True

    user32.EnumWindows(_enum, 0)
    if candidates:
        candidates.sort(key=lambda c: -c[1])
        return candidates[0][0]
    return None


# ═══════════════════════════════════════════
# 状态缓存
# ═══════════════════════════════════════════

_CACHED_HWND: int | None = None


def _get_hwnd() -> int | None:
    global _CACHED_HWND
    if _CACHED_HWND:
        try:
            if user32.IsWindow(_CACHED_HWND):
                return _CACHED_HWND
        except Exception:
            pass
    hwnd = _find_wechat_hwnd()
    if hwnd:
        _CACHED_HWND = hwnd
    return hwnd


def _refresh_hwnd() -> int | None:
    """强制重新查找窗口（搜索弹出新窗口后使用）。"""
    global _CACHED_HWND
    time.sleep(1.0)
    hwnd = _find_wechat_hwnd()
    if hwnd:
        _CACHED_HWND = hwnd
    return hwnd


def _ensure_foreground(hwnd: int):
    """把窗口拉到前台。"""
    try:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass


# ═══════════════════════════════════════════
# 页面分类
# ═══════════════════════════════════════════

def _classify_page(ocr_results: list, img_w: int, img_h: int) -> tuple[str, str]:
    """根据 OCR 文字判断当前窗口类型。

    Returns:
        (page_type, description)
        page_type: "login" | "main" | "search_results" | "detail" | "chat" | "unknown"
    """
    all_text = " ".join([t[0] for t in ocr_results])

    # 登录面板
    if any(kw in all_text for kw in ["二维码", "扫码登录", "扫描二维码"]):
        return "login", "登录面板（二维码）"
    if "需在手机上" in all_text:
        return "login", "登录面板（需在手机上完成登录）"
    if "确认登录" in all_text and "微信" in all_text:
        return "login", "登录面板（手机确认）"

    # 搜索结果
    has_search = any(kw in all_text for kw in ["搜一搜", "搜索网络", "搜索" "结果"])
    if has_search:
        return "search_results", "搜索结果"

    # 服务号/公众号详情页
    detail_tabs = ["全部", "贴图", "文章", "视频号", "服务"]
    has_detail_tabs = sum(1 for kw in detail_tabs if kw in all_text) >= 2
    if has_detail_tabs or ("服务号" in all_text and any(kw in all_text for kw in ["关注", "已关注", "私信", "发消息"])):
        return "detail", "服务号/公众号详情页"

    # 聊天窗口
    chat_bottom = ["发送", "按住说话", "可以描述", "产品介绍", "操作视频", "功能"]
    if sum(1 for kw in chat_bottom if kw in all_text) >= 2:
        return "chat", "聊天窗口"

    # 主窗口
    main_kws = ["微信", "通讯录", "聊天", "消息"]
    if sum(1 for kw in main_kws if kw in all_text) >= 3:
        return "main", "微信主窗口"

    return "unknown", "未知页面"


# ═══════════════════════════════════════════
# 核心观察函数
# ═══════════════════════════════════════════

def _observe() -> str:
    """截图当前微信窗口 → OCR → 分析 → 返回自然语言描述。

    LLM 通过阅读这个输出来决定下一步。
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return (
            "【状态】❌ 未检测到微信窗口\n"
            "【建议】请先启动微信并登录"
        )

    try:
        img = capture_window(hwnd, client_only=True)
    except Exception as e:
        return f"【状态】❌ 截图失败: {e}"

    if img is None or img.size == 0:
        return "【状态】❌ 截图为空"

    img_h, img_w = img.shape[:2]
    _ensure_foreground(hwnd)
    time.sleep(0.3)

    # OCR 全部文字
    ocr_results = _ocr_image(img)

    # 页面分类
    page_type, page_desc = _classify_page(ocr_results, img_w, img_h)

    # 收集可见文字（去重、按置信度排序）
    visible_texts = []
    seen = set()
    for text, _, conf in sorted(ocr_results, key=lambda r: -r[2]):
        t = text.strip()
        if t and t not in seen and conf >= 0.2:
            seen.add(t)
            visible_texts.append(t)

    # 绿色按钮检测
    green_positions = []
    try:
        from .wechat_vision import _find_green_candidates
        greens = _find_green_candidates(img, min_area=100, y_min=0, y_max=int(img_h * 0.55))
        for cx, cy, area, bw, bh in greens:
            # OCR 确认绿色区域内的文字
            rx = max(0, cx - bw // 2 - 5)
            ry = max(0, cy - bh // 2 - 5)
            rw = min(bw + 10, img_w - rx)
            rh = min(bh + 10, img_h - ry)
            roi = img[ry:ry + rh, rx:rx + rw]
            label = "?"
            if roi.size > 0:
                for t, _, c in _ocr_image(roi):
                    if c >= 0.25 and t.strip():
                        label = t.strip()
                        break
            green_positions.append(f"「{label}」(绿色, 位置~{cx},{cy})")
    except Exception:
        pass

    # ── 组装输出 ──
    lines = []
    lines.append(f"【页面】{page_desc}")
    lines.append(f"【窗口】{img_w}×{img_h}")

    if green_positions:
        lines.append(f"【绿色按钮】{' | '.join(green_positions[:3])}")

    if visible_texts:
        lines.append(f"【可见文字】{', '.join(visible_texts[:25])}")

    # 给出 LLM 友好的判断
    lines.append("")
    if page_type == "login":
        lines.append("【判断】需要先登录。")
    elif page_type == "main":
        lines.append("【判断】在主窗口，可以执行搜索。")
    elif page_type == "search_results":
        lines.append("【判断】搜索结果已显示，可以点击第一条。")
    elif page_type == "detail":
        has_followed = any("已关注" in t for t in visible_texts)
        has_follow_btn = any("关注" in t and "已关注" not in t for t in visible_texts)
        has_msg_btn = any(kw in " ".join(visible_texts) for kw in ["私信", "发消息"])
        if has_followed:
            lines.append("【判断】已关注此服务号。如需发消息，请点击「私信」。")
        elif has_follow_btn:
            lines.append("【判断】尚未关注，可以点击「关注」。")
        elif has_msg_btn:
            lines.append("【判断】可以点击「私信」发送消息。")
        else:
            lines.append("【判断】在详情页，请根据绿en按钮决定操作。")
    elif page_type == "chat":
        lines.append("【判断】已在聊天窗口，可以直接发送消息。")
    else:
        lines.append("【判断】未知页面，建议返回主窗口重试。")

    return "\n".join(lines)


# ═══════════════════════════════════════════
# 键盘操作底层
# ═══════════════════════════════════════════

def _type_text(text: str):
    """安全输入文本（中文用剪贴板，英文直接 type）。"""
    if any(ord(c) > 127 for c in text):
        import io
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except Exception:
            return
        time.sleep(0.15)
        pyautogui.hotkey('ctrl', 'v')
    else:
        pyautogui.write(text, interval=0.03)


def _open_search():
    """Ctrl+F 打开微信搜索。"""
    pyautogui.hotkey('ctrl', 'f')
    time.sleep(0.5)


def _press_enter():
    pyautogui.press('enter')
    time.sleep(0.5)


def _press_escape():
    pyautogui.press('escape')
    time.sleep(0.3)


# ═══════════════════════════════════════════
# 工具函数（给 Agent 调用的原子操作）
# ═══════════════════════════════════════════

def wechat_observe() -> str:
    """查看微信当前窗口状态。

    截图 + OCR + 分析，返回当前在哪个页面、有什么按钮和文字。
    每次调用工具后会自动观察，通常不需要手动调用。

    Returns:
        自然语言描述：页面类型、窗口大小、可见按钮、可见文字、建议操作
    """
    return _observe()


def wechat_search(keyword: str) -> str:
    """在微信中搜索关键词（公众号、服务号、联系人等）。

    打开搜索框 → 输入关键词 → 回车提交。

    Args:
        keyword: 搜索关键词，如 "火眼审阅"

    Returns:
        操作结果 + 当前窗口状态描述
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    _ensure_foreground(hwnd)
    _open_search()
    time.sleep(0.3)
    _type_text(keyword)
    time.sleep(0.3)
    _press_enter()

    # 等搜索结果渲染
    for _ in range(6):
        time.sleep(0.5)
        obs = _observe()
        if "搜索" in obs:
            break

    return f"✅ 已搜索「{keyword}」\n\n{_observe()}"


def wechat_click_first_result() -> str:
    """点击搜索结果的第一个条目，进入服务号详情页。

    自动检测新窗口、切换到前台。

    Returns:
        操作结果 + 当前窗口状态描述
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    _ensure_foreground(hwnd)

    # 截图 + OCR 找到第一条结果的位置
    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"

    img_h, img_w = img.shape[:2]

    # 策略：搜索关键词出现在搜索结果中的位置
    # 微信搜索结果中第一条通常在窗口 y=150~250 范围
    target_y = None
    target_x = None

    # 先试 OCR 定位有文字的区域
    ocr_results = _ocr_image(img)
    # 找 y 最小（最靠上）的非搜索框文字
    candidates = []
    for text, bbox, conf in ocr_results:
        t = text.strip()
        if len(t) < 2 or conf < 0.25:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        # 排除顶部搜索框区域
        if cy < 100:
            continue
        candidates.append((cy, cx, t, conf))

    if candidates:
        candidates.sort()
        cy, cx, _, _ = candidates[0]
        target_x = cx
        target_y = cy
        print(f"[AGENT] OCR 定位第一条结果: ({target_x}, {target_y})")
    else:
        # 兜底：固定 y
        target_x = img_w // 2
        target_y = 180
        print(f"[AGENT] OCR 未找到，兜底坐标: ({target_x}, {target_y})")

    # 坐标转换并点击
    from .wechat import _window_client_to_screen
    sx, sy = _window_client_to_screen(hwnd, target_x, target_y)
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)

    # 等新窗口弹出
    time.sleep(2.0)

    # 刷新 hwnd（可能在新窗口）
    _refresh_hwnd()

    return f"✅ 已点击第一条搜索结果\n\n{_observe()}"


def wechat_click_button(target: str) -> str:
    """点击当前页面上的按钮。

    自动用视觉检测定位按钮，支持关注、私信、返回等。

    Args:
        target: 按钮目标
            "关注"   → 绿色关注按钮
            "私信"   → 私信/发消息按钮
            "返回"   → 返回到主窗口

    Returns:
        操作结果 + 当前窗口状态描述
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    _ensure_foreground(hwnd)
    time.sleep(0.3)

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"

    img_h, img_w = img.shape[:2]
    from .wechat import _window_client_to_screen

    target = target.strip()

    # ── 返回 ──
    if target == "返回":
        pyautogui.press('escape')
        time.sleep(0.5)
        _refresh_hwnd()
        return f"✅ 已返回\n\n{_observe()}"

    # ── 关注按钮（绿色）──
    if target == "关注":
        # 策略 1: 颜色+OCR 双验证
        center = find_button_with_text(img, "关注", y_min=0, y_max=int(img_h * 0.55))
        if center:
            print(f"[AGENT] 颜色+OCR 定位关注: {center}")

        # 策略 2: 纯颜色检测
        if center is None:
            center = find_green_button(img, y_min=0, y_max=int(img_h * 0.55))

        # 策略 3: OCR 全图搜索
        if center is None:
            roi = img[:int(img_h * 0.55), :]
            center = find_text_center(roi, "关注", confidence=0.25)

        if center is None:
            # OCR dump 帮助 LLM 理解
            ocr_list = [t[0] for t in _ocr_image(img[:int(img_h * 0.55), :]) if t[2] >= 0.2]
            return (
                f"❌ 未找到「关注」按钮\n\n"
                f"【OCR 上半区文字】{', '.join(ocr_list[:15])}\n"
                f"【建议】可能已经关注了，尝试用 wechat_observe 查看详情"
            )

        cx, cy = center
        sx, sy = _window_client_to_screen(hwnd, cx, cy)
        pyautogui.moveTo(sx, sy, duration=0.1)
        pyautogui.click(sx, sy)
        time.sleep(2.0)

        # 验证：重试一次如果附近没变化
        obs = _observe()
        if "已关注" in obs or "私信" in obs:
            return f"✅ 点击「关注」成功\n\n{obs}"
        else:
            # 微调 y+20 重试
            sy2 = sy + 20
            pyautogui.moveTo(sx, sy2, duration=0.1)
            pyautogui.click(sx, sy2)
            time.sleep(2.0)
            obs = _observe()
            return f"⚠️ 点击「关注」后状态未明确变化\n\n{obs}"

    # ── 私信/发消息按钮（非绿色）──
    if target in ("私信", "发消息"):
        # 策略: OCR 全图搜索
        roi = img[:int(img_h * 0.55), :]
        for kw in ("私信", "发消息", "进入公众号", "聊天"):
            center = find_text_center(roi, kw, confidence=0.25)
            if center:
                cx, cy = center
                sx, sy = _window_client_to_screen(hwnd, cx, cy)
                print(f"[AGENT] OCR 定位「{kw}」: ({cx}, {cy})")
                pyautogui.moveTo(sx, sy, duration=0.1)
                pyautogui.click(sx, sy)
                time.sleep(2.0)
                _refresh_hwnd()
                return f"✅ 已点击「{kw}」\n\n{_observe()}"

        # 未找到
        ocr_list = [t[0] for t in _ocr_image(roi) if t[2] >= 0.2]
        return (
            f"❌ 未找到「私信」或「发消息」按钮\n\n"
            f"【OCR 上半区文字】{', '.join(ocr_list[:15])}\n"
            f"【建议】检查是否需要先关注，或用 wechat_observe 查看页面状态"
        )

    return f"❌ 未知 target: '{target}'。可选: 关注 / 私信 / 返回"


def wechat_type_and_send(message: str) -> str:
    """在当前聊天窗口中输入并发送消息。

    自动定位输入框、点击、输入文字、回车发送。

    Args:
        message: 要发送的消息内容

    Returns:
        操作结果 + 当前窗口状态描述
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    _ensure_foreground(hwnd)
    time.sleep(0.3)

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"

    img_h, img_w = img.shape[:2]
    from .wechat import _window_client_to_screen

    # ── 定位输入框 ──
    # 聊天输入框在窗口底部（下 25%）
    input_found = False

    # 策略 1: OCR 找"发送"按钮，输入框在左边
    roi_bottom = img[int(img_h * 0.6):, :]
    send_center = find_text_center(roi_bottom, "发送", confidence=0.25)
    if send_center:
        # 输入框在发送按钮左边
        sx_btn, sy_btn = send_center
        input_cx = max(50, sx_btn - 150)
        input_cy = int(img_h * 0.6) + sy_btn
        sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
        print(f"[AGENT] 通过「发送」按钮定位输入框: ({input_cx}, {input_cy})")
        pyautogui.moveTo(sx, sy, duration=0.1)
        pyautogui.click(sx, sy)
        input_found = True

    # 策略 2: 底部 15% 中间点击
    if not input_found:
        input_cx = img_w // 2
        input_cy = int(img_h * 0.88)
        sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
        print(f"[AGENT] 兜底输入框位置: ({input_cx}, {input_cy})")
        pyautogui.moveTo(sx, sy, duration=0.1)
        pyautogui.click(sx, sy)
        input_found = True

    time.sleep(0.3)

    # ── 输入消息 ──
    _type_text(message)
    time.sleep(0.5)

    # ── 发送 ──
    _press_enter()
    time.sleep(1.5)

    # ── 验证 ──
    obs = _observe()
    msg_short = message[:6] if len(message) > 6 else message
    found = msg_short in obs

    if found:
        return f"✅ 消息「{message[:20]}...」已发送\n\n{obs}"
    else:
        return f"⚠️ 消息已提交但未在聊天记录中检测到「{msg_short}」\n\n{obs}"


# ═══════════════════════════════════════════
# LangChain Tool 定义
# ═══════════════════════════════════════════

# 这些是给 LangChain create_agent 用的
# 格式：普通函数，会被 create_agent 自动转为 Tool

# 导出列表
AGENT_TOOLS = [
    wechat_observe,
    wechat_search,
    wechat_click_first_result,
    wechat_click_button,
    wechat_type_and_send,
]

# ═══════════════════════════════════════════
# 登录保障（被 agent/core.py 在创建 Agent 前调用）
# ═══════════════════════════════════════════

def ensure_wechat_login() -> bool:
    """确保微信已登录，返回 True/False。

    Agent 启动前调用，处理所有登录场景：
      - 微信未启动 → 启动并等待登录
      - 登录面板（二维码）→ 提示扫码
      - 登录面板（绿按钮+需手机确认）→ 点击+等待确认
    """
    import subprocess as subp

    # 复用 wechat.py 的完整登录逻辑
    from .wechat import _get_wechat_hwnd
    hwnd = _get_wechat_hwnd()
    if hwnd:
        global _CACHED_HWND
        _CACHED_HWND = hwnd
        return True
    return False
