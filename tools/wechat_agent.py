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
import cv2

from .wechat_vision import (
    capture_window, _ocr_image, find_text_center,
    find_green_button, find_button_with_text,
    _WECHAT_GREEN_HSV_LOW, _WECHAT_GREEN_HSV_HIGH,
)

try:
    from .wechat_playbook import (
        lookup_search_result,
        save_search_result,
        lookup_button,
        save_button,
    )
    _HAS_PLAYBOOK = True
except ImportError:
    _HAS_PLAYBOOK = False

# ═══════════════════════════════════════════
# Win32 常量
# ═══════════════════════════════════════════

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

# 窗口查找 —— 直接复用 wechat.py 里完整的登录辅助逻辑
# （二维码扫码提示、绿色登录按钮点击、手机确认弹窗等待 等全部场景都已覆盖）
def _find_wechat_hwnd() -> int | None:
    """复用旧版 _get_wechat_hwnd 的完整登录逻辑。"""
    from .wechat import _get_wechat_hwnd
    return _get_wechat_hwnd()


# ═══════════════════════════════════════════
# 状态缓存
# ═══════════════════════════════════════════

_CACHED_HWND: int | None = None
_ACTIVE_HWND: int | None = None  # 当前操作窗口
_LAST_KEYWORD: str = ""  # 上次搜索的关键词，用于 playbook

# ═══════════════════════════════════════════
# 重试计数器（防止无限循环）
# ═══════════════════════════════════════════
_RETRY_COUNTS: dict[str, int] = {}  # key: step_name, value: 次数
_MAX_RETRIES = 3  # 单步最多重试次数


def _bump_retry(step_name: str) -> bool:
    """递增步数，返回 False 表示已达上限。"""
    global _RETRY_COUNTS
    count = _RETRY_COUNTS.get(step_name, 0)
    if count >= _MAX_RETRIES:
        return False
    _RETRY_COUNTS[step_name] = count + 1
    return True


def _clear_retry(step_name: str):
    global _RETRY_COUNTS
    _RETRY_COUNTS.pop(step_name, None)


def _request_human_assist(action_label: str, hwnd: int, keyword: str) -> tuple[int, int] | None:
    """三次自动定位失败后，请求人工辅助点击并记录坐标到 Playbook。

    使用轮询 GetAsyncKeyState 检测左键按下（避免 64 位钩子溢出），
    点击后的屏幕坐标会转为客户区坐标并保存到 Playbook。

    返回 (client_x, client_y) 或 None（用户取消/超时）。
    """
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win_x, win_y = rect.left, rect.top

    print("\n" + "=" * 60)
    print(f"🤚 【人工辅助】自动定位「{action_label}」失败 3 次")
    print(f"   请手动点击目标位置，或按 Ctrl+C 跳过...")
    print(f"   （在 60 秒内点击窗口内目标按钮即可）")
    print(f"=" * 60)

    # 等待左键松开（避免捕获当前按下的键）
    while ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
        time.sleep(0.05)
    time.sleep(0.3)

    deadline = time.time() + 60.0
    while time.time() < deadline:
        if ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = pt.x, pt.y
            # 防抖：等按键松开
            time.sleep(0.3)

            # 转为客户区坐标
            client_x = sx - win_x
            client_y = sy - win_y - 30
            if client_y < 0:
                client_y = 0

            print(f"✅ 人工点击位置: 屏幕({sx}, {sy}) → 客户区({client_x}, {client_y})")

            # 保存到 Playbook
            img = capture_window(hwnd, client_only=True)
            if img is not None:
                ih, iw = img.shape[:2]
                from .wechat_playbook import save_button, save_search_result
                if action_label == "search_result":
                    save_search_result(keyword, client_x, client_y, iw, ih)
                else:
                    save_button(keyword, action_label, client_x, client_y, iw, ih)
                print(f"[PLAYBOOK] 人工辅助 → 已保存 {action_label}: keyword={keyword}, ({client_x},{client_y})")

            return (client_x, client_y)
        time.sleep(0.05)

    print("\n[WARN] 超时（60 秒），人工辅助已取消")
    return None


def _get_hwnd() -> int | None:
    """获取当前操作窗口。首次调用=登录/启动。"""
    global _CACHED_HWND, _ACTIVE_HWND
    if _ACTIVE_HWND:
        try:
            if user32.IsWindow(_ACTIVE_HWND):
                return _ACTIVE_HWND
        except Exception:
            pass
        _ACTIVE_HWND = None
    # 回退到主窗口
    if _CACHED_HWND:
        try:
            if user32.IsWindow(_CACHED_HWND):
                _ACTIVE_HWND = _CACHED_HWND
                return _CACHED_HWND
        except Exception:
            pass
        _CACHED_HWND = None
    hwnd = _find_wechat_hwnd()
    if hwnd:
        _CACHED_HWND = hwnd
        _ACTIVE_HWND = hwnd
    return hwnd


def _refresh_hwnd() -> int | None:
    """搜索/点击弹出新窗口后，检测并切换到新窗口。
    如果没找到新窗口，保持当前 _ACTIVE_HWND 不变。"""
    global _CACHED_HWND, _ACTIVE_HWND
    time.sleep(1.0)
    new_hwnd = _find_detail_window(old_hwnd=_CACHED_HWND, timeout=3)
    if new_hwnd:
        print(f"[WECHAT-AGENT] 检测到新窗口: hwnd={new_hwnd}")
        _ACTIVE_HWND = new_hwnd
        _ensure_foreground(new_hwnd)
        return new_hwnd
    # 没找到新窗口 → 保持当前窗口不变
    if _ACTIVE_HWND and user32.IsWindow(_ACTIVE_HWND):
        print(f"[WECHAT-AGENT] 无新窗口，继续操作当前窗口: hwnd={_ACTIVE_HWND}")
        return _ACTIVE_HWND
    # 连当前窗口都没了 → 回主窗口
    if _CACHED_HWND and user32.IsWindow(_CACHED_HWND):
        _ACTIVE_HWND = _CACHED_HWND
        _ensure_foreground(_CACHED_HWND)
    return _ACTIVE_HWND


def _find_detail_window(old_hwnd: int = 0, timeout: int = 3) -> int | None:
    """查找独立弹出的微信详情/聊天窗口。
    微信 Qt 版点击搜索结果后可能弹出独立窗口，标题含「服务号」或 class 为 WeChatMainWndForPC。
    排除 old_hwnd（主窗口）。"""
    import subprocess as _sp
    # 获取微信进程 PID
    pids = []
    for name in ("WeChat.exe", "Weixin.exe"):
        try:
            out = _sp.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                shell=True, text=True
            )
            for line in out.strip().split('\n'):
                if name in line:
                    try:
                        pids.append(int(line.replace('"', '').split(',')[1].strip()))
                    except ValueError:
                        pass
        except Exception:
            pass

    start = time.time()
    while time.time() - start < timeout:
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        @WNDENUMPROC
        def _enum(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            if hwnd == old_hwnd:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            ww = r.right - r.left
            hh = r.bottom - r.top
            if ww < 300 or hh < 300:
                return True
            # 检查是否属于微信进程
            wp = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wp))
            if pids and wp.value not in pids:
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            title_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title_buf, 256)
            title = title_buf.value
            cls = cls_buf.value
            # 匹配：标题含「服务号」或 class 是 WeChatMainWndForPC
            if "服务号" in title or "公众号" in title or cls == "WeChatMainWndForPC":
                found.append((hwnd, ww * hh))
            return True

        user32.EnumWindows(_enum, 0)
        if found:
            found.sort(key=lambda c: -c[1])
            return found[0][0]
        time.sleep(0.5)
    return None


def _reset_to_main():
    """返回主窗口（如按 Esc 返回后）。"""
    global _ACTIVE_HWND
    if _CACHED_HWND:
        _ACTIVE_HWND = _CACHED_HWND
        _ensure_foreground(_CACHED_HWND)
    return _ACTIVE_HWND


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

    # 搜索结果（微信搜索框 Enter 后跳转到的结果页）
    # 特征：有"相关搜索"、搜索词高亮、"工具"、"服务号"标签等
    has_search = any(kw in all_text for kw in ["搜一搜", "搜索网络", "相关搜索", "搜索结果"])
    if has_search:
        return "search_results", "搜索结果"

    # 服务号/公众号详情页
    detail_tabs = ["全部", "贴图", "文章", "视频号", "服务"]
    has_detail_tabs = sum(1 for kw in detail_tabs if kw in all_text) >= 2
    if has_detail_tabs or ("服务号" in all_text and any(kw in all_text for kw in ["关注", "已关注", "私信", "发消息"])):
        return "detail", "服务号/公众号详情页"

    # 聊天窗口
    has_url = "https://" in all_text or "http://" in all_text
    has_service_chat = any(kw in all_text for kw in ["Hi", "等你很久了", "联系", "介绍", "大家都在搜"])
    # 服务号自动回复链接 → 优先判为聊天
    if has_service_chat and has_url:
        return "chat", "聊天窗口（含自动回复链接）"
    chat_bottom = ["发送", "按住说话", "可以描述", "产品介绍", "操作视频", "功能"]
    chat_count = sum(1 for kw in chat_bottom if kw in all_text)
    if chat_count >= 2 or (has_service_chat and chat_count >= 1):
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
    # 不拉前台 —— _observe() 是只读操作，窗口状态由前置动作管理

    # OCR 全部文字
    ocr_results = _ocr_image(img)

    # 页面分类
    page_type, page_desc = _classify_page(ocr_results, img_w, img_h)

    # 收集可见文字（精选关键文字，去重、按置信度排序）
    # 只保留中高置信度文字，避免噪声项淹没 LLM
    visible_texts = []
    seen = set()
    for text, _, conf in sorted(ocr_results, key=lambda r: -r[2]):
        t = text.strip()
        if t and t not in seen and conf >= 0.35 and len(t) >= 2:
            seen.add(t)
            visible_texts.append(t)
            if len(visible_texts) >= 12:
                break

    # ── 强制注入关注状态关键词 ──
    # 这些词对决策至关重要，即使置信度偏低也不应被挤出
    # 同时在原始全量（有空格/无空格拼接）上匹配，防止 OCR 分词拆分
    critical_kws = ["已关注", "取消关注", "私信", "发消息", "关注"]
    all_text_joined = " ".join([t[0] for t in ocr_results])
    all_text_raw = all_text_joined.replace(" ", "")  # 消除 OCR 分词产生的空格
    for kw in critical_kws:
        if kw in all_text_raw and kw not in seen:
            visible_texts.insert(0, kw)
            seen.add(kw)

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

    # ── 组装输出（精简，只发关键信息） ──
    lines = []
    lines.append(f"【页面】{page_desc} | {img_w}×{img_h}")

    if green_positions:
        lines.append(f"【绿色按钮】{' | '.join(green_positions[:3])}")

    # 精选 TOP 8 个高置信度关键词，不要 dump 全部 OCR
    if visible_texts:
        top8 = visible_texts[:8]
        lines.append(f"【关键词】{', '.join(top8)}")

    # 给出 LLM 友好的判断
    lines.append("")
    if page_type == "login":
        lines.append("【判断】需要先登录。")
    elif page_type == "main":
        lines.append("【判断】在主窗口，可以执行搜索。")
    elif page_type == "search_results":
        has_service = any(kw in " ".join(visible_texts) for kw in ["服务号", "火眼"])
        if has_service:
            lines.append("【判断】搜索结果中已出现目标服务号，可以 wechat_click_first_result 进入。")
        else:
            lines.append("【判断】搜索结果已显示，检查是否找到目标服务号。")
    elif page_type == "detail":
        # 关注状态判断：OCR 文字 + 绿色按钮双重验证
        has_followed_text = any("已关注" in t or "取消关注" in t for t in visible_texts)
        # 绿色按钮列表里有「关注」→ 未关注；没有 → 已关注（按钮变灰/消失）
        has_green_follow = any("关注" in gp and "已关注" not in gp for gp in green_positions)
        has_follow_btn = any(
            ("关注" in t and "已关注" not in t and "取消关注" not in t)
            for t in visible_texts
        )
        has_msg_btn = any(kw in " ".join(visible_texts) for kw in ["私信", "发消息"])
        if has_followed_text or not has_green_follow:
            if has_msg_btn:
                lines.append("【判断】已关注此服务号。可以点击「私信」发送消息。")
            else:
                lines.append("【判断】已关注此服务号。如需发消息，请点击「私信」（如按钮可见）。")
        elif has_follow_btn:
            lines.append("【判断】尚未关注，可以点击「关注」。如果点击两次仍为「关注」状态，应跳过关注步骤。")
        elif has_msg_btn:
            lines.append("【判断】可以点击「私信」发送消息。")
        else:
            lines.append("【判断】在详情页，请根据绿en按钮决定操作。")
    elif page_type == "chat":
        lines.append("【判断】已在聊天窗口，可以直接发送消息。")
    else:
        lines.append("【判断】未知页面，请根据可见文字判断当前状态。")

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
# 工具函数（给 Agent 调用）
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
    global _LAST_KEYWORD, _RETRY_COUNTS
    _LAST_KEYWORD = keyword  # 记录关键词，供后续 playbook 使用

    if not _bump_retry("wechat_search"):
        return (
            "❌ [FATAL] wechat_search 已重试 3 次仍未成功\n"
            "【建议】请先确认微信已登录并处于主窗口，然后重新运行。"
        )
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    # 先确保窗口可见（ShowWindow SW_RESTORE），但不抢焦点防止打断搜索浮层
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.2)
    # 仅 SetForegroundWindow 一次，用于接收 Ctrl+F
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

    _open_search()
    time.sleep(0.8)  # 等搜索框完全弹出
    _type_text(keyword)
    time.sleep(1.0)  # 等搜索下拉建议加载
    # 微信搜索：第一下 Enter 选中下拉建议，第二下 Enter 跳转结果页
    pyautogui.press('enter')
    time.sleep(0.6)
    pyautogui.press('enter')
    time.sleep(1.5)  # 等结果页渲染
    _clear_retry("wechat_search")
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

    if not _bump_retry("click_first_result"):
        return (
            "❌ [FATAL] 点击第一条搜索结果已重试 3 次仍未成功\n"
            "【建议】检查搜索结果页面是否变化，或手动点击。"
        )

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"

    img_h, img_w = img.shape[:2]

    # ── Playbook 优先 ──
    if _HAS_PLAYBOOK and _LAST_KEYWORD:
        cached = lookup_search_result(_LAST_KEYWORD, img_w, img_h)
        if cached:
            from .wechat import _window_client_to_screen
            sx, sy = _window_client_to_screen(hwnd, cached[0], cached[1])
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.click(sx, sy)
            time.sleep(2.0)
            _refresh_hwnd()
            _clear_retry("click_first_result")
            return f"✅ 已从缓存点击第一条搜索结果\n\n{_observe()}"

    # ── 视觉定位：找真正的搜索结果入口 ──
    # 找到第一个「服务号」标签 → 向下偏移 60px → 居中点击
    ocr_results = _ocr_image(img)
    service_candidates = []
    for text, bbox, conf in ocr_results:
        t = text.strip()
        if "服务号" not in t:
            continue
        if conf < 0.25:
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        if cy < 100:
            continue
        service_candidates.append((cy, cx, conf))

    if service_candidates:
        # 取最靠上的「服务号」标签
        service_candidates.sort()
        cy, cx, _ = service_candidates[0]
        # 点击位置：卡片宽度约 img_w 的 80%，居中点击，高度在标签下方约 60px
        target_x = img_w // 2
        target_y = int(cy + 60)
        print(f"[AGENT] 找到「服务号」标签 at ({int(cx)}, {int(cy)}), 点击卡片中心: ({target_x}, {target_y})")
    else:
        # 视觉定位失败 → 人工辅助
        print("[AGENT] 未找到「服务号」标签，请求人工辅助...")
        kw = _LAST_KEYWORD if _LAST_KEYWORD else "unknown"
        result = _request_human_assist("search_result", hwnd, kw)
        if result is None:
            return "❌ 人工辅助已取消，未点击搜索结果"
        target_x, target_y = result

    from .wechat import _window_client_to_screen
    sx, sy = _window_client_to_screen(hwnd, target_x, target_y)
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)

    # Playbook：缓存搜索结果点击位置
    if _HAS_PLAYBOOK and _LAST_KEYWORD:
        save_search_result(_LAST_KEYWORD, target_x, target_y, img_w, img_h)

    time.sleep(2.0)
    _refresh_hwnd()

    _clear_retry("click_first_result")
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

    if not _bump_retry(f"click_button:{target}"):
        return (
            f"❌ [FATAL] 点击「{target}」按钮已重试 3 次仍未成功\n"
            "【建议】放弃此操作，继续下一步或汇报用户。"
        )

    # 不拉前台！PrintWindow 后台截图 + pyautogui 屏幕坐标点击都不需要窗口在前台
    # 拉前台反而会把主窗口盖到弹出窗口上面

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
        _clear_retry("click_button:返回")
        return f"✅ 已返回\n\n{_observe()}"

    # ── 关注按钮（绿色）──
    if target == "关注":
        # Playbook 优先
        if _HAS_PLAYBOOK and _LAST_KEYWORD:
            cached = lookup_button(_LAST_KEYWORD, "follow", img_w, img_h)
            if cached:
                sx, sy = _window_client_to_screen(hwnd, cached[0], cached[1])
                pyautogui.click(sx, sy)
                time.sleep(1.5)
                # 鼠标移开，避免挡住文字
                safe_sx, safe_sy = _window_client_to_screen(hwnd, 50, 50)
                pyautogui.moveTo(safe_sx, safe_sy, duration=0.1)
                time.sleep(0.5)
                # 检测取消关注弹窗
                img2 = capture_window(hwnd, client_only=True)
                if img2 is not None:
                    popup_texts = [t[0] for t in _ocr_image(img2) if t[2] >= 0.25]
                    if any("不再关注" in t for t in popup_texts) or any("仍要关注" in t for t in popup_texts):
                        print("[AGENT] 检测到「取消关注」确认弹窗 → 已关注，关闭弹窗")
                        pyautogui.press('escape')
                        time.sleep(0.5)
                        _clear_retry("click_button:关注")
                        return f"✅ 检测到取消关注弹窗，说明已关注，弹窗已关闭\n\n{_observe()}"
                # 直接截图 OCR 校验（不依赖 _observe 的 top-12 筛选）
                img3 = capture_window(hwnd, client_only=True)
                if img3 is not None:
                    raw_ocr = [t[0] for t in _ocr_image(img3) if t[2] >= 0.15]
                    raw_joined = "".join(raw_ocr)
                    if any(kw in raw_joined for kw in ["已关注", "取消关注", "私信"]):
                        _clear_retry("click_button:关注")
                        return f"✅ 从缓存点击「关注」成功\n\n{_observe()}"
                # 兜底：_observe 校验
                obs = _observe()
                if "已关注" in obs or "私信" in obs or "取消关注" in obs:
                    _clear_retry("click_button:关注")
                    return f"✅ 从缓存点击「关注」成功\n\n{obs}"
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
        time.sleep(1.5)

        # ── 鼠标移开，避免挡住「已关注」/「取消关注」文字 ──
        # 移到窗口左上角安全区域（远离所有按钮）
        safe_sx, safe_sy = _window_client_to_screen(hwnd, 50, 50)
        pyautogui.moveTo(safe_sx, safe_sy, duration=0.1)
        time.sleep(0.5)

        # ── 检测「取消关注」确认弹窗 ──
        # 如果点击的是未关注状态 → 关注成功；如果点的是已关注状态 → 弹出"不再关注"/"仍要关注"
        img2 = capture_window(hwnd, client_only=True)
        if img2 is not None:
            popup_texts = [t[0] for t in _ocr_image(img2) if t[2] >= 0.25]
            if any("不再关注" in t for t in popup_texts) or any("仍要关注" in t for t in popup_texts):
                # 弹出取消关注确认窗 → 说明已经关注了，点「仍要关注」或 Esc 取消弹窗
                print("[AGENT] 检测到「取消关注」确认弹窗 → 已关注，关闭弹窗")
                pyautogui.press('escape')
                time.sleep(0.5)
                _clear_retry("click_button:关注")
                return f"✅ 检测到取消关注弹窗，说明已关注，弹窗已关闭\n\n{_observe()}"

        # Playbook：缓存关注按钮位置
        if _HAS_PLAYBOOK and _LAST_KEYWORD:
            save_button(_LAST_KEYWORD, "follow", cx, cy, img_w, img_h)

        # 验证：鼠标移开避免挡字，再重截图
        safe_sx, safe_sy = _window_client_to_screen(hwnd, 50, 50)
        pyautogui.moveTo(safe_sx, safe_sy, duration=0.1)
        time.sleep(0.5)

        # 检测取消关注弹窗
        img2 = capture_window(hwnd, client_only=True)
        if img2 is not None:
            popup_texts = [t[0] for t in _ocr_image(img2) if t[2] >= 0.25]
            if any("不再关注" in t for t in popup_texts) or any("仍要关注" in t for t in popup_texts):
                print("[AGENT] 检测到「取消关注」确认弹窗 → 已关注，关闭弹窗")
                pyautogui.press('escape')
                time.sleep(0.5)
                _clear_retry("click_button:关注")
                return f"✅ 检测到取消关注弹窗，说明已关注，弹窗已关闭\n\n{_observe()}"

        obs = _observe()
        if "已关注" in obs or "私信" in obs or "取消关注" in obs:
            _clear_retry("click_button:关注")
            return f"✅ 点击「关注」成功\n\n{obs}"
        else:
            # 微调 y+20 重试
            sy2 = sy + 20
            sx2, sy2_sc = _window_client_to_screen(hwnd, cx, cy + 20)
            pyautogui.moveTo(sx2, sy2_sc, duration=0.1)
            pyautogui.click(sx2, sy2_sc)
            time.sleep(1.5)
            # 鼠标移开
            pyautogui.moveTo(safe_sx, safe_sy, duration=0.1)
            time.sleep(0.5)
            # 再检测弹窗
            img3 = capture_window(hwnd, client_only=True)
            if img3 is not None:
                popup_texts3 = [t[0] for t in _ocr_image(img3) if t[2] >= 0.25]
                if any("不再关注" in t for t in popup_texts3) or any("仍要关注" in t for t in popup_texts3):
                    print("[AGENT] 检测到「取消关注」确认弹窗(y+20) → 已关注，关闭弹窗")
                    pyautogui.press('escape')
                    time.sleep(0.5)
                    _clear_retry("click_button:关注")
                    return f"✅ 检测到取消关注弹窗，说明已关注，弹窗已关闭\n\n{_observe()}"
            obs = _observe()
            return f"⚠️ 点击「关注」后状态未明确变化\n\n{obs}"

    # ── 私信/发消息按钮（非绿色）──
    if target in ("私信", "发消息"):
        # Playbook 优先
        if _HAS_PLAYBOOK and _LAST_KEYWORD:
            cached = lookup_button(_LAST_KEYWORD, "send_msg", img_w, img_h)
            if cached:
                sx, sy = _window_client_to_screen(hwnd, cached[0], cached[1])
                pyautogui.click(sx, sy)
                time.sleep(2.0)
                _refresh_hwnd()
                return f"✅ 从缓存点击「私信」\n\n{_observe()}"
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
                # Playbook：缓存私信按钮位置
                if _HAS_PLAYBOOK and _LAST_KEYWORD:
                    save_button(_LAST_KEYWORD, "send_msg", cx, cy, img_w, img_h)
                _refresh_hwnd()
                _clear_retry(f"click_button:{kw}")
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

    if not _bump_retry("type_and_send"):
        return (
            "❌ [FATAL] 输入发送消息已重试 3 次仍未成功\n"
            "【建议】放弃发送，汇报用户当前状态。"
        )

    # 不拉前台：PrintWindow 后台截图 + pyautogui 屏幕坐标点击都不需要窗口在前台

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"

    img_h, img_w = img.shape[:2]
    from .wechat import _window_client_to_screen

    # ── Step 0: 检测是否需要展开键盘 ──
    roi_bottom = img[int(img_h * 0.7):, :]
    has_input = any(
        kw in " ".join(t[0] for t in _ocr_image(roi_bottom) if t[2] >= 0.2)
        for kw in ["发送", "可以描述", "输入", "按住"]
    )
    if not has_input:
        print("[AGENT] 未检测到输入框，点击右下角键盘图标...")
        kb_clicked = False
        kb_x, kb_y = None, None

        # ── 策略 0: Playbook 缓存（最优先，复用上次成功位置）──
        if _HAS_PLAYBOOK and _LAST_KEYWORD:
            cached = lookup_button(_LAST_KEYWORD, "keyboard_toggle", img_w, img_h)
            if cached:
                kb_x, kb_y = cached[0], cached[1]
                print(f"[AGENT] Playbook 缓存键盘图标: ({kb_x}, {kb_y})")

        # ── 策略 1: 模板匹配 keyboard_toggle.png ──
        if kb_x is None:
            template_path = Path(__file__).parent.parent / "assets" / "wechat_templates" / "keyboard_toggle.png"
            if template_path.exists():
                from .wechat_vision import find_template_center
                center = find_template_center(img, str(template_path), confidence=0.65)
                if center:
                    kb_x, kb_y = center
                    print(f"[AGENT] 模板匹配定位键盘图标: ({kb_x}, {kb_y})")
            else:
                print(f"[AGENT] 模板文件不存在: {template_path}")

        # ── 策略 2: OCR 找"键盘"或"切换" ──
        if kb_x is None:
            roi_corner = img[img_h - 120:img_h, img_w - 180:img_w]
            corner_texts = _ocr_image(roi_corner)
            for t, bbox, c in corner_texts:
                if any(kw in t for kw in ["键盘", "切换", "输入", "文字"]):
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    kb_x = img_w - 180 + int(sum(xs) / len(xs))
                    kb_y = img_h - 120 + int(sum(ys) / len(ys))
                    print(f"[AGENT] OCR 定位键盘图标: 「{t}」 at ({kb_x}, {kb_y})")
                    break

        # ── 策略 3: 右下角轮廓检测 ──
        if kb_x is None:
            roi_corner = img[img_h - 100:img_h, img_w - 100:img_w]
            gray = cv2.cvtColor(roi_corner, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for ct in contours:
                xc, yc, wc, hc = cv2.boundingRect(ct)
                if 15 <= wc <= 60 and 15 <= hc <= 60 and wc * hc >= 200:
                    kb_x = img_w - 100 + xc + wc // 2
                    kb_y = img_h - 100 + yc + hc // 2
                    print(f"[AGENT] 轮廓定位键盘图标: area={wc*hc}, at ({kb_x}, {kb_y})")
                    break

        # ── 执行点击 ──
        if kb_x is not None:
            sx, sy = _window_client_to_screen(hwnd, kb_x, kb_y)
            pyautogui.click(sx, sy)
            time.sleep(0.8)
            kb_clicked = True
            # 保存到 Playbook
            if _HAS_PLAYBOOK and _LAST_KEYWORD:
                save_button(_LAST_KEYWORD, "keyboard_toggle", kb_x, kb_y, img_w, img_h)
            # 验证：重截图看输入框是否出现
            img_check = capture_window(hwnd, client_only=True)
            if img_check is not None:
                bottom_text = " ".join(t[0] for t in _ocr_image(img_check[int(img_check.shape[0] * 0.7):, :]) if t[2] >= 0.15)
                if any(kw in bottom_text for kw in ["发送", "可以描述", "输入", "按住"]):
                    print(f"[AGENT] 键盘图标点击后输入框已出现")
                    img = img_check
                    img_h, img_w = img.shape[:2]
                else:
                    print(f"[AGENT] 键盘图标点击后输入框仍未出现，继续定位输入框...")
                    img = img_check
                    img_h, img_w = img.shape[:2]
        else:
            # 全部失败 → 人工辅助
            print("[AGENT] 键盘图标视觉定位失败，请求人工辅助...")
            kw = _LAST_KEYWORD if _LAST_KEYWORD else "unknown"
            result = _request_human_assist("keyboard_toggle", hwnd, kw)
            if result is None:
                return "❌ 人工辅助已取消，未点击键盘图标"
            kb_x, kb_y = result
            sx, sy = _window_client_to_screen(hwnd, kb_x, kb_y)
            pyautogui.click(sx, sy)
            time.sleep(0.5)
            kb_clicked = True
            # 重截图
            img = capture_window(hwnd, client_only=True)
            img_h, img_w = img.shape[:2]

    # ── Step 1: 定位输入框 ──
    roi_bottom = img[int(img_h * 0.65):, :]
    input_found = False

    # Playbook 优先
    if _HAS_PLAYBOOK and _LAST_KEYWORD and not input_found:
        cached = lookup_button(_LAST_KEYWORD, "input_box", img_w, img_h)
        if cached:
            input_cx, input_cy = cached[0], cached[1]
            sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.click(sx, sy)
            input_found = True
            print(f"[AGENT] 从缓存定位输入框: ({input_cx}, {input_cy})")

    # 策略1: 检测底部白色矩形输入框（微信输入框通常是白色圆角矩形，占底部大部分宽度）
    if not input_found:
        gray = cv2.cvtColor(roi_bottom, cv2.COLOR_BGR2GRAY)
        # 二值化：找亮色区域（输入框背景）
        _, bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_rect = None
        best_area = 0
        for ct in contours:
            x, y, w, h = cv2.boundingRect(ct)
            area = w * h
            # 输入框特征：宽度占大部分、高度适中、在底部区域
            if w > img_w * 0.5 and 40 < h < 150 and area > best_area:
                best_area = area
                best_rect = (x, y, w, h)
        if best_rect:
            x, y, w, h = best_rect
            input_cx = x + w // 2
            input_cy = int(img_h * 0.65) + y + h // 2
            sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
            print(f"[AGENT] 通过白色矩形定位输入框: ({input_cx}, {input_cy}), size={w}x{h}")
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.click(sx, sy)
            input_found = True

    # 策略2: OCR 找"发送"按钮，输入框在按钮左侧
    if not input_found:
        send_center = find_text_center(roi_bottom, "发送", confidence=0.25)
        if send_center:
            sx_btn, sy_btn = send_center
            # 输入框在发送按钮左侧大面积区域，取左侧 40% 中央
            input_cx = int(img_w * 0.4)
            input_cy = int(img_h * 0.65) + sy_btn
            sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
            print(f"[AGENT] 通过「发送」按钮定位输入框: ({input_cx}, {input_cy})")
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.click(sx, sy)
            input_found = True

    # 策略3: 找输入框提示文字
    if not input_found:
        for kw in ["可以描述", "描述任务", "输入"]:
            tip_center = find_text_center(roi_bottom, kw, confidence=0.2)
            if tip_center:
                input_cx = tip_center[0]
                input_cy = int(img_h * 0.65) + tip_center[1]
                sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
                print(f"[AGENT] 通过提示文字「{kw}」定位输入框: ({input_cx}, {input_cy})")
                pyautogui.moveTo(sx, sy, duration=0.1)
                pyautogui.click(sx, sy)
                input_found = True
                break

    # 策略4: 所有 OCR/视觉策略都失败 → 人工辅助
    if not input_found:
        print("[AGENT] 视觉定位输入框失败，请求人工辅助...")
        kw = _LAST_KEYWORD if _LAST_KEYWORD else "unknown"
        result = _request_human_assist("input_box", hwnd, kw)
        if result is None:
            return "❌ 人工辅助已取消，未定位输入框"
        input_cx, input_cy = result
        sx, sy = _window_client_to_screen(hwnd, input_cx, input_cy)
        pyautogui.moveTo(sx, sy, duration=0.1)
        pyautogui.click(sx, sy)
        input_found = True

    # Playbook：缓存输入框位置
    if _HAS_PLAYBOOK and _LAST_KEYWORD and input_found:
        save_button(_LAST_KEYWORD, "input_box", input_cx, input_cy, img_w, img_h)

    time.sleep(0.3)

    # ── 输入消息 ──
    _type_text(message)
    time.sleep(0.5)

    # ── 发送 ──
    _press_enter()
    time.sleep(1.5)

    # ── 验证（重试 3 次，直接截图全量 OCR + 绿色气泡检测）──
    msg_short = message[:6] if len(message) > 6 else message
    msg_shorter = message[:4] if len(message) > 4 else message
    found = False
    obs = ""
    time.sleep(2.0)  # 微信消息渲染有动画，先等 2 秒
    for attempt in range(3):
        # 直接截图做全量 OCR（极低置信度阈值，确保不遗漏）
        verify_img = capture_window(hwnd, client_only=True)
        if verify_img is not None:
            all_texts = _ocr_image(verify_img)
            # 策略 A: 全量文字匹配（置信度≥0.05，几乎不过滤）
            full_text = " ".join(t[0] for t in all_texts if t[2] >= 0.05)
            if msg_short in full_text or msg_shorter in full_text:
                found = True
                print(f"[AGENT] 验证通过：OCR 检测到消息文字")
                break
            # 策略 B: 检测绿色消息气泡（自己发送的消息是绿色）
            hsv = cv2.cvtColor(verify_img, cv2.COLOR_BGR2HSV)
            lower_green = (35, 40, 40)
            upper_green = (85, 255, 255)
            green_mask = cv2.inRange(hsv, lower_green, upper_green)
            green_ratio = cv2.countNonZero(green_mask) / (verify_img.shape[0] * verify_img.shape[1])
            if green_ratio > 0.005:  # 有绿色区域（消息气泡）
                # 进一步检查绿色区域附近是否有文字
                print(f"[AGENT] 验证通过：检测到绿色消息气泡（ratio={green_ratio:.4f}）")
                found = True
                break
        print(f"[AGENT] 验证重试 {attempt + 1}/3...")
        time.sleep(1.5)

    obs = _observe()

    if found:
        _clear_retry("type_and_send")
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
