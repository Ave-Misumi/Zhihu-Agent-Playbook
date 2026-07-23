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
            # 匹配：标题含「服务号」「公众号」「朋友圈」或 class 是 WeChatMainWndForPC
            if any(kw in title for kw in ("服务号", "公众号", "朋友圈", "Moments")) or cls == "WeChatMainWndForPC":
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
    # 严格特征：必须有详情页专属 tab（全部/贴图/文章/视频号/服务 中至少2个）
    # 或者同时满足：有「已关注」+「私信/发消息」按钮（不是聊天消息里的文字）
    detail_tabs = ["全部", "贴图", "文章", "视频号", "服务"]
    has_detail_tabs = sum(1 for kw in detail_tabs if kw in all_text) >= 2
    has_followed_and_msg = "已关注" in all_text and any(kw in all_text for kw in ["私信", "发消息"])
    if has_detail_tabs or has_followed_and_msg:
        return "detail", "服务号/公众号详情页"

    # 主窗口（优先判断！聊天窗口的特征主窗口也可能有）
    # 特征：左侧聊天列表 + 右侧聊天内容
    main_kws = ["微信", "通讯录", "聊天", "消息"]
    has_main_kws = sum(1 for kw in main_kws if kw in all_text) >= 3
    # 聊天列表特征：有多个联系人/群聊名称 + 时间戳 + 消息预览
    has_chat_list = any(kw in all_text for kw in ["昨天", "星期", "分钟前"]) and \
                    sum(1 for kw in ["微信", "通讯录", "发现", "我"] if kw in all_text) >= 1
    # 底部输入框特征（主窗口也有）
    has_input_area = "发送" in all_text or "按住说话" in all_text
    if has_main_kws or (has_chat_list and has_input_area):
        return "main", "微信主窗口（侧边栏可见）"

    # 聊天窗口（严格特征：真正的聊天窗口有服务号自动回复特征，或明确的输入提示）
    has_url = "https://" in all_text or "http://" in all_text
    has_service_chat = any(kw in all_text for kw in ["Hi", "等你很久了", "联系", "介绍", "大家都在搜"])
    # 服务号自动回复链接 → 优先判为聊天
    if has_service_chat and has_url:
        return "chat", "聊天窗口（含自动回复链接）"
    # 严格聊天特征：有「按住说话」或明确的输入提示 + 服务号特征
    chat_strict = ["按住说话", "可以描述", "描述任务", "输入"]
    has_chat_strict = sum(1 for kw in chat_strict if kw in all_text) >= 1
    if has_service_chat and has_chat_strict:
        return "chat", "聊天窗口"

    # 朋友圈/Moments 页面
    # 特征1: 有朋友圈内容（点赞、评论、时间等），且没有「发送」按钮（主窗口才有）
    moments_content = ["点赞", "评论", "封面", "相册", "昨天", "分钟前", "小时前", "刚刚", "天前"]
    has_moments_content = sum(1 for kw in moments_content if kw in all_text) >= 2
    # 朋友圈窗口通常没有底部输入框的「发送」按钮
    no_chat_input = "发送" not in all_text
    if has_moments_content and no_chat_input:
        return "moments", "朋友圈"
    # 特征2: 明确有「朋友圈」标题 + 时间词（朋友圈动态必有时间戳）
    has_time = any(kw in all_text for kw in ["分钟前", "小时前", "昨天", "天前", "刚刚"])
    if "朋友圈" in all_text and has_time and no_chat_input:
        return "moments", "朋友圈"
    # 兜底：明确有「朋友圈」标题 + 封面/相册
    if "朋友圈" in all_text and any(kw in all_text for kw in ["封面", "相册", "拍一拍"]):
        return "moments", "朋友圈"

    # 发现页（左侧导航含 "朋友圈" "视频号" "看一看" 等）
    discovery_kws = ["朋友圈", "视频号", "看一看", "搜一搜", "小程序"]
    has_discovery = sum(1 for kw in discovery_kws if kw in all_text) >= 3
    if has_discovery and "发现" in all_text:
        return "discovery", "发现页"

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
        lines.append("【判断】在微信主窗口。左侧是聊天列表，右侧是聊天内容。点击左侧侧边栏的「朋友圈」图标（相机/光圈样式）可进入朋友圈。")
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
    elif page_type == "discovery":
        lines.append("【判断】在发现页，可以点击「朋友圈」进入。（注：微信 PC 版主窗口侧边栏通常直接有「朋友圈」，无需经过发现页）")
    elif page_type == "moments":
        has_like = any(kw in " ".join(visible_texts) for kw in ["赞", "评论", "...", "⋯"])
        lines.append("【判断】在朋友圈。每条动态右下角有「两个点」按钮（⋯），点击后弹出「点赞」和「评论」选项。要点赞请先点击「两个点」按钮，再点击「点赞」。（注意：不要滚动，第一条动态就在顶部）")
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
        # 策略1: OCR 找左上角的返回箭头（「←」「<」「返回」「‹」等）
        # 微信详情页的返回按钮通常在左上角 15% 宽度 × 15% 高度区域
        roi_top_left = img[:int(img_h * 0.2), :int(img_w * 0.25)]
        back_hit = None
        for back_text in ("<", "←", "‹", "返回"):
            back_hit = find_text_center(roi_top_left, back_text, confidence=0.2)
            if back_hit:
                break
        if back_hit is None:
            # 策略2: 固定坐标 —— 微信返回箭头通常在左上角 (35, 35) 附近
            # 但不同版本/分辨率有差异，尝试多个候选位置
            back_candidates = [
                (int(img_w * 0.03), int(img_h * 0.04)),   # 左上角 3%, 4%
                (int(img_w * 0.05), int(img_h * 0.06)),   # 5%, 6%
                (35, 35),                                  # 绝对坐标（小窗口）
                (50, 40),                                  # 稍偏右
            ]
            print(f"[AGENT] 返回: OCR 未找到箭头，尝试 {len(back_candidates)} 个候选位置")
        else:
            print(f"[AGENT] 返回: OCR 定位到返回箭头 {back_hit}")
            back_candidates = [back_hit]

        # 逐个尝试候选位置，验证是否真正返回了
        for i, (cx, cy) in enumerate(back_candidates):
            sx, sy = _window_client_to_screen(hwnd, cx, cy)
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.click(sx, sy)
            time.sleep(1.0)
            _refresh_hwnd()
            obs = _observe()
            # 验证：如果页面不再是详情页（没有详情页特征），说明返回成功
            is_detail = ("已关注" in obs or "关注" in obs) and ("私信" in obs or "发消息" in obs or "取消关注" in obs)
            is_main = "微信" in obs and ("通讯录" in obs or "聊天" in obs)
            if not is_detail or is_main:
                print(f"[AGENT] 返回成功！候选位置 {i+1}/{len(back_candidates)}: ({cx},{cy})")
                _clear_retry("click_button:返回")
                return f"✅ 已返回（位置 {i+1}）\n\n{obs}"
            print(f"[AGENT] 返回失败，候选位置 {i+1}/{len(back_candidates)}: ({cx},{cy})，页面仍是详情页")

        # 所有候选位置都失败 → 尝试 Esc
        print("[AGENT] 所有候选位置都失败，尝试 Esc...")
        pyautogui.press('escape')
        time.sleep(0.5)
        _refresh_hwnd()
        obs = _observe()
        is_detail = ("已关注" in obs or "关注" in obs) and ("私信" in obs or "发消息" in obs or "取消关注" in obs)
        if not is_detail:
            _clear_retry("click_button:返回")
            return f"✅ 已返回（Esc）\n\n{obs}"

        # 还是失败 → 尝试 Alt+Left（浏览器式返回）
        print("[AGENT] Esc 也失败，尝试 Alt+Left...")
        pyautogui.keyDown('alt')
        pyautogui.keyDown('left')
        pyautogui.keyUp('left')
        pyautogui.keyUp('alt')
        time.sleep(0.5)
        _refresh_hwnd()
        obs = _observe()
        _clear_retry("click_button:返回")
        return f"⚠️ 已尝试多种方式返回\n\n{obs}"

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
# 原子操作：通用点击 + 滚动（LLM 决策，不做多步封装）
# ═══════════════════════════════════════════

def wechat_click_text(target: str, n: int = 1) -> str:
    """在当前窗口中找到第 n 个匹配文字的 OCR 区域并点击。

    原子操作：只做「截图→OCR→定位→点击」一步，不做多步导航。
    适用场景：点击导航栏、按钮、菜单项、任何 OCR 能识别到的文字。
    点击后自动调用 wechat_observe 返回新页面状态，LLM 据此决策下一步。

    Args:
        target: 要点击的文字，如 "发现" "朋友圈" "赞" "..." "评论" "取消关注"
        n: 匹配第 n 个出现的文字（1-indexed，从上到下、从左到右排序）。默认 1。

    Returns:
        操作结果 + 点击后的窗口状态（自动附带 wechat_observe 结果）
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    if not _bump_retry(f"click_text:{target}:{n}"):
        return (
            f"❌ [FATAL] 点击「{target}」已重试 3 次仍未成功\n"
            f"【建议】放弃此操作，汇报当前状态给用户。"
        )

    _ensure_foreground(hwnd)
    time.sleep(0.3)

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"
    img_h, img_w = img.shape[:2]
    ocr_results = _ocr_image(img)

    from .wechat import _window_client_to_screen

    # ── 优先：朋友圈图标模板匹配 ──
    # 微信 PC 版侧边栏的「朋友圈」是纯图标（相机/光圈样式），没有文字标签
    # 先尝试模板匹配，比 OCR 更可靠
    if target in ("朋友圈", "朋友圈图标"):
        template_path = Path(__file__).parent.parent / "assets" / "wechat_templates" / "friendcircle.png"
        if template_path.exists():
            from .wechat_vision import find_template_center
            center = find_template_center(img, str(template_path), confidence=0.65)
            if center:
                cx, cy = center
                print(f"[AGENT] click_text: 模板匹配「朋友圈」图标 → ({cx},{cy})")
                sx, sy = _window_client_to_screen(hwnd, cx, cy)
                pyautogui.moveTo(sx, sy, duration=0.1)
                pyautogui.click(sx, sy)
                time.sleep(2.0)  # 朋友圈窗口弹出需要更久
                _refresh_hwnd()  # 检测并切换到新窗口
                _clear_retry(f"click_text:{target}:{n}")
                return f"✅ 已点击「朋友圈」图标({cx},{cy})\n\n{_observe()}"
            else:
                print(f"[AGENT] 朋友圈图标模板匹配失败 (confidence<0.65)，回退到 OCR")
        else:
            print(f"[AGENT] 朋友圈图标模板不存在: {template_path}，回退到 OCR")

    # 收集所有匹配文字的区域，按 (y, x) 排序（从上到下，从左到右）
    candidates = []
    for text, bbox, conf in ocr_results:
        t = text.strip()
        if conf < 0.15:
            continue
        if target in t:
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys))
            candidates.append((cy, cx, conf, t))

    if not candidates:
        ocr_list = [t[0] for t in ocr_results if t[2] >= 0.15]
        return (
            f"❌ 未找到文字「{target}」\n"
            f"【OCR 文字({len(ocr_list)}条)】{', '.join(ocr_list[:20])}\n"
            f"【建议】检查 target 是否有误，或用「(部分匹配)」搜索。"
        )

    candidates.sort(key=lambda c: (c[0], c[1]))  # 按 y, x 排序

    if n > len(candidates):
        return (
            f"⚠️ 找到 {len(candidates)} 处「{target}」，但请求第 {n} 处\n"
            f"【候选列表】(y, x): {[(c[0], c[1]) for c in candidates[:10]]}\n"
            f"【建议】减小 n 值（当前 n={n} > 总数 {len(candidates)}）。"
        )

    cy, cx, conf, matched = candidates[n - 1]
    print(f"[AGENT] click_text: 第{n}处「{target}」→ OCR=\"{matched}\" conf={conf:.2f} client=({cx},{cy})")

    sx, sy = _window_client_to_screen(hwnd, cx, cy)
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)
    time.sleep(1.0)

    _clear_retry(f"click_text:{target}:{n}")
    return f"✅ 已点击第{n}处「{matched}」({cx},{cy})\n\n{_observe()}"


def wechat_scroll(direction: str = "down", amount: int = 1) -> str:
    """在当前窗口中滚动。

    原子操作：只在当前位置滚动鼠标滚轮，不做多步判断。
    滚动后自动调用 wechat_observe 返回新页面状态，LLM 据此决策。

    Args:
        direction: 滚动方向 "up"（向上/向底部）或 "down"（向下/向顶部）。默认 "down"。
        amount: 滚轮刻度数（正值，每刻度约 120px 等效）。默认 1。

    Returns:
        操作结果 + 滚动后的窗口状态
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    _ensure_foreground(hwnd)
    time.sleep(0.2)

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"
    img_h, img_w = img.shape[:2]

    # 在内容区域中央滚动
    from .wechat import _window_client_to_screen
    cx = int(img_w * 0.55)
    cy = int(img_h * 0.5)
    sx, sy = _window_client_to_screen(hwnd, cx, cy)

    pyautogui.moveTo(sx, sy, duration=0.05)
    clicks = amount * 600  # 每单位 ~600px 滚动量
    if direction == "up":
        clicks = -clicks  # 正值=向上滚
    else:
        clicks = -clicks  # 负值=向下滚
    pyautogui.scroll(clicks)
    time.sleep(0.5)

    print(f"[AGENT] scroll: direction={direction}, amount={amount}")
    return f"✅ 已向{('上' if direction == 'up' else '下')}滚动 {amount} 次\n\n{_observe()}"


def wechat_like_moments_post(n: int = 1) -> str:
    """给朋友圈第 n 条动态点赞。

    操作流程：
    1. 找到第 n 条动态右下角的「两个点」按钮（⋯ 或更多选项）
    2. 点击「两个点」按钮，弹出菜单
    3. 点击菜单中的「赞」按钮

    Args:
        n: 给第 n 条动态点赞（1-indexed，从上到下）。默认 1（第一条）。

    Returns:
        操作结果 + 点赞后的页面状态
    """
    hwnd = _get_hwnd()
    if hwnd is None:
        return "❌ 微信未启动或未登录"

    if not _bump_retry(f"like_moments:{n}"):
        return "❌ [FATAL] 点赞已重试 3 次仍未成功"

    _ensure_foreground(hwnd)
    time.sleep(0.3)

    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 截图失败"
    img_h, img_w = img.shape[:2]

    from .wechat import _window_client_to_screen

    # ── Step 0: 先向下滚动一小段，确保第一条动态完整显示（时间戳和按钮可见）──
    cx = int(img_w * 0.5)
    cy = int(img_h * 0.5)
    sx, sy = _window_client_to_screen(hwnd, cx, cy)
    pyautogui.moveTo(sx, sy, duration=0.05)
    pyautogui.scroll(-300)  # 向下滚动 300px，让第一条动态底部露出
    time.sleep(0.5)

    # 重新截图
    img = capture_window(hwnd, client_only=True)
    if img is None:
        return "❌ 滚动后截图失败"
    img_h, img_w = img.shape[:2]

    # ── Step 1: 找第 n 条动态 ──
    # 朋友圈动态之间有时间戳分隔，用时间词定位每条动态
    ocr_results = _ocr_image(img)
    time_keywords = ["分钟前", "小时前", "昨天", "天前", "刚刚"]

    # 收集所有时间戳的位置（y 坐标），按 y 排序
    post_positions = []
    debug_times = []
    for text, bbox, conf in ocr_results:
        if conf < 0.1:  # 放宽置信度阈值
            continue
        for kw in time_keywords:
            if kw in text:
                ys = [p[1] for p in bbox]
                cy = int(sum(ys) / len(ys))
                post_positions.append(cy)
                debug_times.append((text, conf, cy))
                break

    print(f"[AGENT] like_moments: OCR 识别到的时间戳: {debug_times}")
    post_positions = sorted(set(post_positions))

    if not post_positions:
        # 兜底：如果没有时间戳，假设第一条动态在页面中上部
        print(f"[AGENT] like_moments: 未识别到时间戳，使用默认位置")
        post_positions = [int(img_h * 0.4)]

    if n > len(post_positions):
        return f"⚠️ 只找到 {len(post_positions)} 条动态，无法点赞第 {n} 条"

    target_y = post_positions[n - 1]
    print(f"[AGENT] like_moments: 第{n}条动态在时间戳 y={target_y}")

    # ── Step 2: 在该动态区域找「两个点」按钮 ──
    # 「两个点」按钮是灰色小圆点图标，位于时间戳右侧固定位置
    # 用颜色检测找灰色圆点区域（BGR 格式）
    dot_y_min = max(0, target_y - 30)
    dot_y_max = min(img_h, target_y + 80)
    dot_x_min = int(img_w * 0.75)  # 右侧 75% 区域
    dot_roi = img[dot_y_min:dot_y_max, dot_x_min:img_w]

    dot_hit = None

    if dot_roi.size > 0:
        # 模板匹配找「两个点」按钮
        template_path = Path(__file__).parent.parent / "assets" / "wechat_templates" / "like_front.png"
        if template_path.exists():
            from .wechat_vision import find_template_center
            import cv2
            # 方法1：直接模板匹配（低阈值）
            center = find_template_center(dot_roi, str(template_path), confidence=0.4)
            if center:
                cx, cy = center
                dot_cx = dot_x_min + cx
                dot_cy = dot_y_min + cy
                dot_hit = (dot_cx, dot_cy)
                print(f"[AGENT] like_moments: 模板匹配找到按钮 at ({dot_cx},{dot_cy})")
            else:
                # 方法2：多尺度模板匹配（支持不同大小的按钮）
                tpl = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
                roi = dot_roi
                if tpl is not None and roi.size > 0:
                    best_score = 0
                    best_loc = None
                    best_scale = 1.0
                    # 尝试 0.5x 到 2.0x 的缩放
                    for scale in [0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0]:
                        new_w = int(tpl.shape[1] * scale)
                        new_h = int(tpl.shape[0] * scale)
                        if new_w < 10 or new_h < 10:
                            continue
                        if new_w > roi.shape[1] or new_h > roi.shape[0]:
                            continue
                        resized = cv2.resize(tpl, (new_w, new_h))
                        res = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
                        _, max_val, _, max_loc = cv2.minMaxLoc(res)
                        if max_val > best_score:
                            best_score = max_val
                            best_loc = max_loc
                            best_scale = scale
                    
                    if best_score >= 0.3:
                        th, tw = int(tpl.shape[0] * best_scale), int(tpl.shape[1] * best_scale)
                        dot_cx = dot_x_min + best_loc[0] + tw // 2
                        dot_cy = dot_y_min + best_loc[1] + th // 2
                        dot_hit = (dot_cx, dot_cy)
                        print(f"[AGENT] like_moments: 多尺度匹配找到按钮 at ({dot_cx},{dot_cy}) (score={best_score:.2f}, scale={best_scale})")
                    else:
                        print(f"[AGENT] like_moments: 多尺度匹配未找到 (best_score={best_score:.2f})")
                else:
                    print(f"[AGENT] like_moments: 无法读取模板")
        else:
            print(f"[AGENT] like_moments: 模板文件不存在: {template_path}")

    if dot_hit is None:
        return "❌ 未找到「两个点」按钮（模板匹配失败）"

    # ── Step 3: 点击「两个点」按钮 ──
    sx, sy = _window_client_to_screen(hwnd, dot_hit[0], dot_hit[1])
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)
    time.sleep(0.8)

    # ── Step 4: 点击弹出的「赞」按钮 ──
    img2 = capture_window(hwnd, client_only=True)
    if img2 is None:
        return "❌ 点击后截图失败"

    # 在按钮附近区域找「赞」——菜单在「两个点」按钮左侧弹出
    # 搜索区域：「两个点」按钮左侧 250px（覆盖整个菜单），同一高度附近
    like_roi = img2[max(0, dot_hit[1] - 30):min(img_h, dot_hit[1] + 50),
                    max(0, dot_hit[0] - 250):max(0, dot_hit[0] - 10)]
    like_ocr = _ocr_image(like_roi)
    like_hit = None
    debug_likes = []
    for text, bbox, conf in like_ocr:
        if conf < 0.1:
            continue
        debug_likes.append((text, conf))
        # OCR 可能识别为 "O 赞" 或 "赞"，用包含判断
        if "赞" in text or "O 赞" in text:
            ys = [p[1] for p in bbox]
            xs = [p[0] for p in bbox]
            like_cx = max(0, dot_hit[0] - 250) + int(sum(xs) / len(xs))
            like_cy = max(0, dot_hit[1] - 30) + int(sum(ys) / len(ys))
            like_hit = (like_cx, like_cy)
            print(f"[AGENT] like_moments: 找到「赞」按钮 at ({like_cx},{like_cy}) (OCR='{text}')")
            break

    print(f"[AGENT] like_moments: 菜单区域OCR结果: {debug_likes}")

    if like_hit is None:
        # 如果没找到「赞」，可能是已经点过赞了，或者菜单没弹出
        _clear_retry(f"like_moments:{n}")
        return f"⚠️ 未找到「赞」按钮（可能已点赞或菜单未弹出）\n\n{_observe()}"

    sx, sy = _window_client_to_screen(hwnd, like_hit[0], like_hit[1])
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)
    time.sleep(0.5)

    # ── Step 5: 验证点赞是否成功 ──
    # 再次点击「两个点」按钮，检查菜单里是否出现「取消」字样
    sx, sy = _window_client_to_screen(hwnd, dot_hit[0], dot_hit[1])
    pyautogui.moveTo(sx, sy, duration=0.1)
    pyautogui.click(sx, sy)
    time.sleep(0.5)

    img3 = capture_window(hwnd, client_only=True)
    if img3 is not None:
        verify_roi = img3[max(0, dot_hit[1] - 30):min(img_h, dot_hit[1] + 50),
                         max(0, dot_hit[0] - 250):max(0, dot_hit[0] - 10)]
        verify_ocr = _ocr_image(verify_roi)
        has_cancel = any("取消" in text for text, _, conf in verify_ocr if conf >= 0.1)
        if has_cancel:
            _clear_retry(f"like_moments:{n}")
            return f"✅ 已给第{n}条朋友圈点赞（验证：菜单显示「取消」）\n\n{_observe()}"
        else:
            # 菜单里没有「取消」，说明点赞可能没成功
            print(f"[AGENT] like_moments: 验证失败，菜单里没有「取消」。OCR结果: {[(t, c) for t, _, c in verify_ocr if c >= 0.1]}")

    _clear_retry(f"like_moments:{n}")
    return f"⚠️ 已尝试给第{n}条朋友圈点赞，但验证未通过（未检测到「取消」字样）\n\n{_observe()}"


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
    wechat_click_text,
    wechat_scroll,
    wechat_like_moments_post,
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
