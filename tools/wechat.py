"""微信（Windows 客户端）自动化工具集

纯 Win32 SendInput / PostMessage + 剪贴板方案。
支持新版 Weixin（Qt 渲染，无原生子控件）和老版 WeChat。

功能：
  - wechat_search_and_follow   搜索公众号/服务号 → 关注 → 发私信
  - wechat_send_message        给已关注的公众号发送文字消息
"""
import os
import time
import ctypes
from ctypes import wintypes

import uiautomation as auto

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 使当前进程 DPI Aware，确保 GetWindowRect / SetCursorPos / 截图坐标一致
user32.SetProcessDPIAware()

# 64 位兼容：Win32 API restype/argtypes 声明
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.OpenClipboard.restype = ctypes.c_bool
user32.EmptyClipboard.restype = ctypes.c_bool
user32.CloseClipboard.restype = ctypes.c_bool

# 其他常用声明
user32.SetForegroundWindow.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.restype = ctypes.c_bool
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

# Win32 常量
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_DELETE = 0x2E
VK_A = 0x41
VK_F = 0x46
VK_V = 0x56
VK_ESCAPE = 0x1B
VK_DOWN = 0x28
VK_UP = 0x26
VK_LBUTTON = 0x01

SW_RESTORE = 9
SW_SHOWNORMAL = 1
SW_MINIMIZE = 6
SWP_NOZORDER = 0x0004
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# SendInput 结构体
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ki", KEYBDINPUT),
    ]

# 初始化 SendInput
_EXTRA = (ctypes.c_ulong * 1)(0)
user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def _send_key(hwnd: int, vk: int, ctrl: bool = False) -> int:
    """SendInput 全局按键（Qt 能正确处理），返回发送的 inputs 数量"""
    import pyautogui
    if not _ensure_foreground(hwnd):
        raise RuntimeError(f"[WECHAT] 无法将窗口切换到前台: hwnd={hwnd}, 当前前台={user32.GetForegroundWindow()}")
    time.sleep(0.05)

    inputs = []
    if ctrl:
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki.wVk = VK_CONTROL
        inp.ki.dwExtraInfo = _EXTRA
        inputs.append(inp)

    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.dwExtraInfo = _EXTRA
    inputs.append(inp)

    # Second copy for keyup
    inp2 = INPUT()
    inp2.type = INPUT_KEYBOARD
    inp2.ki.wVk = vk
    inp2.ki.dwFlags = KEYEVENTF_KEYUP
    inp2.ki.dwExtraInfo = _EXTRA
    inputs.append(inp2)

    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    time.sleep(0.05)

    if ctrl:
        inp_up = INPUT()
        inp_up.type = INPUT_KEYBOARD
        inp_up.ki.wVk = VK_CONTROL
        inp_up.ki.dwFlags = KEYEVENTF_KEYUP
        inp_up.ki.dwExtraInfo = _EXTRA
        user32.SendInput(1, ctypes.pointer(inp_up), ctypes.sizeof(INPUT))
        time.sleep(0.05)

    time.sleep(0.1)


def _clipboard_put(text: str) -> None:
    """写入文本到剪贴板 (CF_UNICODETEXT)"""
    encoded = text.encode('utf-16-le') + b'\x00\x00'
    user32.OpenClipboard(0)
    user32.EmptyClipboard()
    hGlobal = kernel32.GlobalAlloc(0x2000, len(encoded))
    pGlobal = kernel32.GlobalLock(hGlobal)
    ctypes.memmove(pGlobal, encoded, len(encoded))
    kernel32.GlobalUnlock(hGlobal)
    user32.SetClipboardData(13, hGlobal)  # CF_UNICODETEXT
    user32.CloseClipboard()


def _clipboard_paste(hwnd: int) -> None:
    """[DEPRECATED] 已改用 pyautogui.hotkey('ctrl','v') 单一路径"""
    import pyautogui
    pyautogui.hotkey('ctrl', 'v')


def _ensure_foreground(hwnd: int) -> bool:
    """确保窗口在前台且真正获得焦点，失败返回 False。

    策略：
      1. ShowWindow + SetForegroundWindow + AttachThreadInput 标准三步
      2. 休眠后再次检查 — 若仍未前台则 Alt+Tab 强切
      3. 最终验证 GetForegroundWindow == hwnd，失败则返回 False
    """
    # 先恢复窗口
    user32.ShowWindow(hwnd, SW_SHOWNORMAL)
    time.sleep(0.2)

    # 最多尝试 5 次
    for attempt in range(5):
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True

        if attempt < 3:
            # Attach thread input → SetForegroundWindow
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            if target_tid != fg_tid:
                user32.AttachThreadInput(fg_tid, target_tid, True)
            user32.SetForegroundWindow(hwnd)
            if target_tid != fg_tid:
                user32.AttachThreadInput(fg_tid, target_tid, False)
            time.sleep(0.3)
        else:
            # 靠 Alt 键强切前台
            import pyautogui
            # 先最小化当前窗口以减少干扰
            fg = user32.GetForegroundWindow()
            if fg and fg != hwnd:
                user32.ShowWindow(fg, SW_MINIMIZE)
                time.sleep(0.2)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.2)
            if user32.GetForegroundWindow() != hwnd:
                pyautogui.keyDown('alt')
                pyautogui.keyDown('tab')
                pyautogui.keyUp('tab')
                pyautogui.keyUp('alt')
                time.sleep(0.3)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.2)

    return user32.GetForegroundWindow() == hwnd


# ═══════════════════════════════════════════════
# 进程/窗口发现
# ═══════════════════════════════════════════════

def _is_wechat_running() -> bool:
    import subprocess
    for name in ("WeChat.exe", "Weixin.exe"):
        try:
            out = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                shell=True, text=True
            )
            if name in out:
                return True
        except Exception:
            pass
    return False


def _find_wechat_exe() -> str | None:
    paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"),
                     "Tencent", "Weixin", "Weixin.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                     "Tencent", "Weixin", "Weixin.exe"),
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"),
                     "Tencent", "WeChat", "WeChat.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                     "Tencent", "WeChat", "WeChat.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _find_wechat_hwnd() -> int | None:
    """Win32 枚举所有顶层窗口，找到微信主窗口（PID匹配+尺寸过滤+可见性优先）。"""
    import subprocess

    # 先通过进程名拿到所有 WeChat/Weixin 的 PID
    pids = []
    for name in ("WeChat.exe", "Weixin.exe"):
        try:
            out = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                shell=True, text=True
            )
            for line in out.strip().split("\n"):
                if name in line:
                    parts = line.replace('"', '').split(",")
                    if len(parts) >= 2:
                        try:
                            pids.append(int(parts[1].strip()))
                        except ValueError:
                            pass
        except Exception:
            pass
    if not pids:
        return None

    best = None
    best_score = -1

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        nonlocal best, best_score
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        # 必须可见且非最小化（尺寸 > 0）
        if not user32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top
        if w < 500 or h < 400:
            return True
        # 过滤最小化到托盘的窗口（典型位置 -32000,-32000）
        if r.left <= -30000 or r.top <= -30000:
            return True
        # 只保留有标题的窗口
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not any(kw in title for kw in ("微信", "Weixin")):
            return True
        # 评分：尺寸优先，标题/可见性加分
        score = (w * h // 10000) + (10 if title else 0) + 20
        if score > best_score:
            best_score = score
            best = hwnd
        return True

    user32.EnumWindows(_enum, 0)
    return best


# 微信主窗口句柄缓存，避免重复恢复导致窗口状态异常
_CACHED_HWND: int | None = None


def _get_wechat_hwnd() -> int | None:
    """获取微信主窗口句柄。

    策略：
      1. 若缓存句柄仍然有效且可见，直接复用（避免重复 Ctrl+Alt+W）。
      2. 否则如果已有微信进程运行，通过全局热键 Ctrl+Alt+W 恢复窗口。
      3. 如果没有运行，才全新启动微信。
    """
    import subprocess
    import pyautogui

    global _CACHED_HWND

    # 1. 优先使用缓存
    if _CACHED_HWND is not None:
        if user32.IsWindow(_CACHED_HWND) and user32.IsWindowVisible(_CACHED_HWND):
            r = wintypes.RECT()
            user32.GetWindowRect(_CACHED_HWND, ctypes.byref(r))
            ww = r.right - r.left
            hh = r.bottom - r.top
            if ww >= 500 and hh >= 400:
                print(f"[WECHAT] 复用缓存主窗口: hwnd={_CACHED_HWND}")
                if not _ensure_foreground(_CACHED_HWND):
                    print(f"[WECHAT] 缓存窗口无法切换到前台，丢弃缓存")
                    _CACHED_HWND = None
                    return None
                return _CACHED_HWND
        print(f"[WECHAT] 缓存窗口无效，重新获取...")
        _CACHED_HWND = None

    # 2. 已有运行则热键恢复
    if _is_wechat_running():
        print("[WECHAT] 检测到已登录微信，尝试通过 Ctrl+Alt+W 恢复主窗口...")
        pyautogui.keyDown('ctrl')
        pyautogui.keyDown('alt')
        pyautogui.keyDown('w')
        pyautogui.keyUp('w')
        pyautogui.keyUp('alt')
        pyautogui.keyUp('ctrl')
        time.sleep(2.5)

        hwnd = _find_wechat_hwnd()
        if hwnd:
            if not _ensure_foreground(hwnd):
                print("[WECHAT] Ctrl+Alt+W 恢复窗口成功但无法切前台")
                return None
            _CACHED_HWND = hwnd
            print(f"[WECHAT] 已恢复主窗口: hwnd={hwnd}")
            return hwnd
        print("[WECHAT] 热键恢复失败，将尝试全新启动...")
    else:
        print("[WECHAT] 未检测到运行中的微信，全新启动...")

    # 3. 只有未运行/恢复失败时才全新启动
    os.system('taskkill /F /IM Weixin.exe >nul 2>&1')
    os.system('taskkill /F /IM WeChat.exe >nul 2>&1')
    time.sleep(1)

    exe = _find_wechat_exe()
    if exe is None:
        raise RuntimeError("未找到微信可执行文件")
    os.startfile(exe)

    # 等 Qt 登录面板出现
    login_hwnd = None
    login_rect = None
    for i in range(30):
        time.sleep(1)
        pid = None
        for name in ("WeChat.exe", "Weixin.exe"):
            try:
                out = subprocess.check_output(
                    f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                    shell=True, text=True
                )
                for line in out.strip().split('\n'):
                    if name in line:
                        pid = int(line.replace('"', '').split(',')[1].strip())
                        break
            except Exception:
                pass
            if pid:
                break
        if not pid:
            continue

        # 枚举该 PID 的所有可见窗口
        result = {}
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @WNDENUMPROC
        def _enum(hwnd, _lp):
            wp = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wp))
            if wp.value != pid:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            ww, hh = r.right - r.left, r.bottom - r.top
            if ww < 100 or hh < 100:
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 256)
            title_buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title_buf, 256)
            result['cls'] = cls_buf.value
            result['hwnd'] = hwnd
            result['rect'] = (r.left, r.top, r.right, r.bottom)
            result['title'] = title_buf.value
            return False

        user32.EnumWindows(_enum, 0)
        if result:
            login_hwnd = result['hwnd']
            login_rect = result['rect']
            print(f"[WECHAT] 登录面板: hwnd={login_hwnd} cls={result['cls']} {login_rect[2]-login_rect[0]}x{login_rect[3]-login_rect[1]}")
            break
        if i % 5 == 4:
            print(f"[WECHAT] 等待登录面板... ({i+1}s)")

    if login_hwnd is None:
        print("[WECHAT] 登录面板未出现")
        return None

    # 点绿色"登录"按钮（位于面板底部 ~y=355）
    x, y, x2, y2 = login_rect
    w = x2 - x
    login_btn_x = x + w // 2
    login_btn_y = y + 358  # 面板 388px 中按钮在 y~340-375
    print(f"[WECHAT] 点击登录按钮 ({login_btn_x}, {login_btn_y})...")

    user32.SetForegroundWindow(login_hwnd)
    time.sleep(0.3)
    user32.SetCursorPos(login_btn_x, login_btn_y)
    time.sleep(0.1)
    import pyautogui
    pyautogui.click(login_btn_x, login_btn_y)
    time.sleep(2)

    # 等主窗口出现（>800x600）
    print("[WECHAT] 等待主聊天窗口...")
    for i in range(30):
        time.sleep(1)
        large_windows = []
        pid = None
        for name in ("WeChat.exe", "Weixin.exe"):
            try:
                out = subprocess.check_output(
                    f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                    shell=True, text=True
                )
                for line in out.strip().split('\n'):
                    if name in line:
                        parts = line.replace('"', '').split(',')
                        if len(parts) >= 2:
                            try:
                                pid = int(parts[1].strip())
                            except ValueError:
                                pass
            except Exception:
                pass
            if pid:
                break
        if not pid:
            continue

        best = None
        best_score = -1
        WNDENUMPROC2 = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @WNDENUMPROC2
        def _enum2(hwnd, _lp):
            nonlocal best, best_score
            wp = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wp))
            if wp.value != pid:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            ww, hh = r.right - r.left, r.bottom - r.top
            if ww < 500 or hh < 400:
                return True
            visible = user32.IsWindowVisible(hwnd)
            if not visible:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(max(256, length + 1))
            user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
            score = ww * hh // 10000 + (20 if visible else 0) + (10 if title else 0)
            if score > best_score:
                best_score = score
                best = hwnd
            return True

        user32.EnumWindows(_enum2, 0)
        if best:
            user32.SetForegroundWindow(best)
            time.sleep(1)
            print(f"[WECHAT] 主窗口就绪: hwnd={best}")
            return best
        if i % 5 == 4:
            print(f"[WECHAT] 等待主窗口... ({i+1}s)")

    print("[WECHAT] 主窗口未出现，可能需要手动登录")
    return None


# ═══════════════════════════════════════════════
# 键盘操作原语（单一 pyautogui 路径，杜绝重复操作）
# ═══════════════════════════════════════════════

# pyautogui 全局安全设置
import pyautogui
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


def _grab_focus(hwnd: int) -> None:
    """物理点击微信窗口客户区中间，确保 Qt 获得键盘焦点"""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx = r.left + (r.right - r.left) // 2
    cy = r.top + 120
    pyautogui.moveTo(cx, cy, duration=0.1)
    pyautogui.click(cx, cy)
    time.sleep(0.25)


def _open_search(hwnd: int) -> None:
    """Ctrl+F 打开微信搜索面板并清空已有内容"""
    if not _ensure_foreground(hwnd):
        raise RuntimeError(f"[WECHAT] 无法将微信窗口切换到前台: hwnd={hwnd}")
    _grab_focus(hwnd)
    time.sleep(0.3)

    pyautogui.hotkey('ctrl', 'f')
    time.sleep(0.8)

    # 清空搜索框已有内容
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.1)
    pyautogui.press('delete')
    time.sleep(0.1)


def _search_keyword(hwnd: int, keyword: str) -> None:
    """粘贴关键词 → 回车搜索（新版微信按 Enter 后打开独立搜索结果窗口）"""
    _clipboard_put(keyword)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(2.5)


def _navigate_to_first_result(hwnd: int) -> None:
    """[DEPRECATED] 已被 _click_search_result_by_name 替代。
    旧逻辑：盲操 ↓ 箭头选中第一条，但对服务号搜索不可靠。"""
    # 保留作为键盘兜底：在搜索结果窗口中使用 ↓ 导航
    pyautogui.press('down')
    time.sleep(0.3)
    pyautogui.press('down')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(3.0)


def _click_search_result_by_name(hwnd: int, keyword: str) -> bool:
    """在搜索结果中用 UIAutomation 查找并点击名称匹配的条目。

    新版微信 Qt 搜索结果以 ListItem/Text 控件呈现。
    本函数搜索主窗口子树，找到名称包含 keyword 的控件后点击其中心。
    返回 True 表示成功点击，False 表示未找到（调用方应 fallback）。
    """
    import pyautogui
    time.sleep(3.0)  # 等搜索结果窗口完全渲染

    try:
        main_ctrl = auto.ControlFromHandle(hwnd)
    except Exception as e:
        print(f"[WECHAT-UIA] 无法获取主窗口控件: {e}")
        return False

    candidates: list[auto.Control] = []
    MAX_DEPTH = 18

    def _walk(ctrl, depth=0):
        if depth > MAX_DEPTH:
            return
        try:
            name = ctrl.Name or ""
            if keyword in name:
                t = ctrl.ControlTypeName
                # 优先 TextControl / ListItem / Button；忽略 Window/Pane 等顶层容器
                if t in ("TextControl", "ListItemControl", "ButtonControl", "HyperlinkControl", "TreeItemControl"):
                    candidates.append(ctrl)
            for child in ctrl.GetChildren():
                _walk(child, depth + 1)
        except Exception:
            pass

    print(f"[WECHAT-UIA] 开始搜索控件树（keyword={keyword}）...")
    try:
        _walk(main_ctrl)
    except Exception as e:
        print(f"[WECHAT-UIA] 遍历异常: {e}")
        return False

    if not candidates:
        # 第二轮：放宽类型限制，任意控件名匹配即可
        print(f"[WECHAT-UIA] 严格匹配未找到，尝试宽松匹配（不限控件类型）...")
        def _walk_loose(ctrl, depth=0):
            if depth > MAX_DEPTH:
                return
            try:
                name = ctrl.Name or ""
                if keyword in name:
                    candidates.append(ctrl)
                for child in ctrl.GetChildren():
                    _walk_loose(child, depth + 1)
            except Exception:
                pass
        try:
            _walk_loose(main_ctrl)
        except Exception:
            pass

    if not candidates:
        print(f"[WECHAT-UIA] 未找到任何包含 '{keyword}' 的控件")
        return False

    # 按面积排序，优先点击较大的控件（通常是列表项容器而非小图标）
    candidates.sort(
        key=lambda c: c.BoundingRectangle.width() * c.BoundingRectangle.height(),
        reverse=True,
    )

    target = candidates[0]
    rect = target.BoundingRectangle
    cx = rect.left + rect.width() // 2
    cy = rect.top + rect.height() // 2
    print(
        f"[WECHAT-UIA] 点击目标: Name='{target.Name}' "
        f"Type={target.ControlTypeName} Pos=({cx},{cy}) "
        f"Size={rect.width()}x{rect.height()}"
    )
    pyautogui.moveTo(cx, cy, duration=0.15)
    pyautogui.click(cx, cy)
    time.sleep(3.0)
    return True


def _click_button_by_name(hwnd: int, button_text: str, fallback_tab_count: int = 4) -> bool:
    """在当前窗口中查找并点击指定文本的按钮。

    策略：
      1. 先用 UIA 在整个窗口子树中搜索名称含 button_text 的 ButtonControl
      2. 命中 → 点击其中心
      3. 未命中 → fallback 键盘 Tab 导航 + Enter

    返回 True 表示操作已执行。
    """
    import pyautogui

    try:
        main_ctrl = auto.ControlFromHandle(hwnd)
    except Exception as e:
        print(f"[WECHAT-BUTTON-UIA] 无法获取控件: {e}")
        # fallback
        for _ in range(fallback_tab_count):
            pyautogui.press('tab')
            time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(2.5)
        return True

    candidates: list[auto.Control] = []
    MAX_DEPTH = 18

    def _walk(ctrl, depth=0):
        if depth > MAX_DEPTH:
            return
        try:
            name = ctrl.Name or ""
            t = ctrl.ControlTypeName
            if button_text in name and t in ("ButtonControl", "TextControl", "HyperlinkControl"):
                candidates.append(ctrl)
            for child in ctrl.GetChildren():
                _walk(child, depth + 1)
        except Exception:
            pass

    print(f"[WECHAT-BUTTON-UIA] 搜索按钮 '{button_text}'...")
    try:
        _walk(main_ctrl)
    except Exception as e:
        print(f"[WECHAT-BUTTON-UIA] 遍历异常: {e}")

    if not candidates:
        print(f"[WECHAT-BUTTON-UIA] 未找到按钮 '{button_text}'，fallback 键盘...")
        for _ in range(fallback_tab_count):
            pyautogui.press('tab')
            time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(2.5)
        return True

    # 优先选面积最大的
    candidates.sort(
        key=lambda c: c.BoundingRectangle.width() * c.BoundingRectangle.height(),
        reverse=True,
    )
    target = candidates[0]
    rect = target.BoundingRectangle
    cx = rect.left + rect.width() // 2
    cy = rect.top + rect.height() // 2
    print(f"[WECHAT-BUTTON-UIA] 点击按钮: '{target.Name}' at ({cx},{cy})")
    pyautogui.moveTo(cx, cy, duration=0.1)
    pyautogui.click(cx, cy)
    time.sleep(2.5)
    return True


def _goto_message_input(hwnd: int) -> None:
    """在聊天页中 Tab 到输入框并清空"""
    for _ in range(8):
        pyautogui.press('tab')
        time.sleep(0.12)
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.1)
    pyautogui.press('delete')
    time.sleep(0.1)


def _send_message(hwnd: int, message: str) -> None:
    """粘贴消息文本 → 回车发送"""
    _clipboard_put(message)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.3)
    pyautogui.press('enter')
    time.sleep(0.5)


def _go_back_to_main(hwnd: int) -> None:
    """Escape 退出当前会话/资料页回到微信主界面"""
    pyautogui.press('escape')
    time.sleep(0.8)


# ═══════════════════════════════════════════════
# 公开工具
# ═══════════════════════════════════════════════

async def wechat_search_and_follow(
    keyword: str,
    message: str = "",
    account_type: str = "服务号",
) -> str:
    """搜索公众号/服务号 → 关注 → 发私信（pyautogui 物理键盘方案）

    流程分 4 步，每步有独立等待和状态检查：
      1. 打开搜索 + 输入关键词 + 回车
      2. 在搜索结果中导航到第一条 → 进入详情页
      3. 关注
      4. 如有 message 则发送私信
    """
    import pyautogui
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return "微信未登录，请扫码登录后重试"

        # ── Step 1: 打开搜索 → 输入 → 回车 ──
        print(f"[WECHAT-STEP1] 搜索关键词: {keyword}")
        _open_search(hwnd)
        _search_keyword(hwnd, keyword)
        print(f"[WECHAT-STEP1] 搜索已提交")

        # ── Step 2: 在搜索结果窗口中找到并点击目标 ──
        print(f"[WECHAT-STEP2] 在搜索结果中定位 '{keyword}'...")
        hit = _click_search_result_by_name(hwnd, keyword)
        if not hit:
            # Fallback: 键盘导航兜底
            print("[WECHAT-STEP2] UIA 未命中，fallback 到键盘导航...")
            _navigate_to_first_result(hwnd)
        print("[WECHAT-STEP2] 已点击搜索结果")

        # ── Step 3: 关注（在详情页中点击关注按钮）──
        print("[WECHAT-STEP3] 等待详情页加载并尝试关注...")
        time.sleep(2.0)
        # 尝试 UIA 找到并点击「关注」按钮
        _click_button_by_name(hwnd, "关注", fallback_tab_count=6)
        print("[WECHAT-STEP3] 关注操作已执行")

        result = f"搜索「{keyword}」并尝试关注完成"

        # ── Step 4: 发私信（如有 message 且关注成功/已关注则可进入聊天页）──
        if message:
            print(f"[WECHAT-STEP4] 查找「发消息」入口: {message[:30]}...")
            # 关注成功后详情页会出现「发消息」按钮，点击进入聊天
            time.sleep(1.5)
            _click_button_by_name(hwnd, "发消息", fallback_tab_count=4)
            time.sleep(2.0)

            _goto_message_input(hwnd)
            _send_message(hwnd, message)
            print("[WECHAT-STEP4] 消息已发送")
            result += "，已发送私信"

        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"微信操作失败: {e}"


async def wechat_send_message(
    contact_name: str,
    message: str,
) -> str:
    """给已关注的联系人/公众号发送消息"""
    import pyautogui
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return "微信未登录"

        _open_search(hwnd)
        _search_keyword(hwnd, contact_name)
        _navigate_to_first_result(hwnd)

        # 进入聊天后直接发消息
        _goto_message_input(hwnd)
        _send_message(hwnd, message)

        return f"已给「{contact_name}」发送消息"

    except Exception as e:
        return f"微信发消息失败: {e}"
