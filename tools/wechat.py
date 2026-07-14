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
    """Ctrl+V 粘贴剪贴板内容到窗口，SendInput + pyautogui 双路径"""
    import pyautogui
    if not _ensure_foreground(hwnd):
        raise RuntimeError(f"[WECHAT] 无法切换到前台进行粘贴: hwnd={hwnd}")
    _send_key(hwnd, VK_V, ctrl=True)
    time.sleep(0.1)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.1)


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
# 键盘操作原语
# ═══════════════════════════════════════════════

def _open_search(hwnd: int) -> None:
    """Ctrl+F 打开搜索框并清空。使用 pyautogui 为主，SendInput 为补充。"""
    import pyautogui
    if not _ensure_foreground(hwnd):
        raise RuntimeError(f"[WECHAT] 无法将微信窗口切换到前台: hwnd={hwnd}")
    time.sleep(0.3)

    # 1. 点击微信客户区确保焦点
    _click_wechat_client(hwnd)

    # 2. 用 pyautogui 发送 Ctrl+F（最可靠的方式）
    print("[WECHAT-SEARCH] pyautogui Ctrl+F...")
    pyautogui.hotkey('ctrl', 'f')
    time.sleep(1.0)

    # 3. 再尝试 SendInput Ctrl+F（双保险）
    _send_key(hwnd, VK_F, ctrl=True)
    time.sleep(0.5)

    # 4. 清空已有内容：pyautogui Ctrl+A + Delete
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.15)
    pyautogui.press('delete')
    time.sleep(0.15)

    print("[WECHAT-SEARCH] 搜索框应当已打开并清空")


def _click_wechat_client(hwnd: int) -> None:
    """物理点击微信窗口客户区中间位置，确保 Qt 获得键盘焦点"""
    import pyautogui
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    cx = r.left + (r.right - r.left) // 2
    cy = r.top + 120  # 标题栏下方，搜索框附近
    print(f"[WECHAT-CLICK] 点击客户区 ({cx}, {cy})")
    pyautogui.moveTo(cx, cy, duration=0.2)
    time.sleep(0.1)
    pyautogui.click(cx, cy)
    time.sleep(0.3)
    # 再双击确保 Qt 获得焦点
    pyautogui.click(cx, cy)
    time.sleep(0.3)


def _verify_search_opened(hwnd: int) -> bool:
    """用 UIAutomation 检测微信搜索面板是否打开"""
    try:
        control = auto.ControlFromHandle(hwnd)
        def _get_children(node):
            try:
                return node.GetChildren()
            except Exception:
                return []
        for node, depth, _ in auto.WalkTree(control, getChildren=_get_children, includeTop=True, maxDepth=5):
            try:
                ctn = node.ControlTypeName
                name = node.Name or ''
                if ctn == 'EditControl' and '搜' in name:
                    print(f"[WECHAT-VERIFY] 发现搜索 Edit: name='{name}' depth={depth}")
                    return True
                if ctn == 'EditControl' and node.IsKeyboardFocusable:
                    print(f"[WECHAT-VERIFY] 发现可聚焦 Edit: name='{name}' depth={depth}")
                    return True
            except Exception:
                continue
        print("[WECHAT-VERIFY] 未检测到搜索 Edit 控件")
        return False
    except Exception as e:
        print(f"[WECHAT-VERIFY] UIA 异常: {e}")
        return False


def _search_keyword(hwnd: int, keyword: str) -> None:
    """输入关键词→回车搜索。SendInput + pyautogui 双路径"""
    import pyautogui
    # 路径1: SendInput
    _clipboard_put(keyword)
    _clipboard_paste(hwnd)
    time.sleep(0.3)
    _send_key(hwnd, VK_RETURN)
    time.sleep(0.5)
    # 路径2: pyautogui (不同输入管线，增加可靠性)
    _clipboard_put(keyword)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.3)
    pyautogui.press('enter')
    time.sleep(1.5)


def _navigate_to_first_result(hwnd: int) -> None:
    """Down按两次→Enter，SendInput + pyautogui 双路径"""
    import pyautogui
    for _ in range(2):
        _send_key(hwnd, VK_DOWN)
        time.sleep(0.15)
    pyautogui.press('down')
    time.sleep(0.1)
    pyautogui.press('down')
    time.sleep(0.1)
    _send_key(hwnd, VK_RETURN)
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.5)


def _click_follow_via_keyboard(hwnd: int) -> bool:
    """Tab 遍历到关注按钮→Enter，SendInput + pyautogui 双路径"""
    import pyautogui
    for _ in range(4):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.15)
    pyautogui.press('tab')
    time.sleep(0.1)
    pyautogui.press('tab')
    time.sleep(0.1)
    _send_key(hwnd, VK_RETURN)
    time.sleep(1)
    pyautogui.press('enter')
    time.sleep(2)
    return True


def _goto_message_input(hwnd: int) -> None:
    """导航到聊天输入框并清空"""
    for _ in range(8):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.12)
    time.sleep(0.3)
    _send_key(hwnd, VK_A, ctrl=True)
    time.sleep(0.1)
    _send_key(hwnd, VK_DELETE)
    time.sleep(0.1)


def _send_message(hwnd: int, message: str) -> None:
    """输入消息并发送"""
    _clipboard_put(message)
    _clipboard_paste(hwnd)
    time.sleep(0.3)
    _send_key(hwnd, VK_RETURN)
    time.sleep(0.5)


def _go_back_to_main(hwnd: int) -> None:
    """Escape 退出当前会话/资料页回到微信主界面"""
    _send_key(hwnd, VK_ESCAPE)
    time.sleep(0.5)


# ═══════════════════════════════════════════════
# 公开工具
# ═══════════════════════════════════════════════

async def wechat_search_and_follow(
    keyword: str,
    message: str = "",
    account_type: str = "服务号",
) -> str:
    """搜索公众号/服务号 → 关注 → 发送私信（键盘+剪贴板方案）"""
    errors = []
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return "微信未登录，请扫码登录后重试"

        # 1. Ctrl+F → 输入关键词 → 回车搜索
        print("[WECHAT-STEP1] 正在 Ctrl+F 打开搜索框...")
        _open_search(hwnd)
        print("[WECHAT-STEP1] Ctrl+F 已发送")
        _search_keyword(hwnd, keyword)
        print(f"[WECHAT-STEP1] 关键词「{keyword}」已粘贴并回车")

        # 等待搜索结果加载
        time.sleep(2)

        # 用 UIAutomation 验证搜索框是否打开
        search_opened = _verify_search_opened(hwnd)
        if search_opened:
            print("[WECHAT-VERIFY] 搜索面板已确认打开")
        else:
            print("[WECHAT-VERIFY] [WARN] 搜索面板未检测到！尝试备用方式 pyautogui Ctrl+F...")
            # 备用方案：用 pyautogui 发送 Ctrl+F（不同输入路径）
            import pyautogui
            pyautogui.hotkey('ctrl', 'f')
            time.sleep(1)
            _clipboard_put(keyword)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.3)
            pyautogui.press('enter')
            time.sleep(2)
            errors.append("搜索面板未确认打开，已尝试备用方式")

        # 2. 导航到第一条搜索结果 → Enter 打开
        print("[WECHAT-STEP2] 导航到第一条搜索结果...")
        _navigate_to_first_result(hwnd)
        print("[WECHAT-STEP2] 已按↓+Enter")

        # 3. 关注
        print("[WECHAT-STEP3] 尝试关注...")
        _click_follow_via_keyboard(hwnd)
        print("[WECHAT-STEP3] Tab+Enter 关注操作已发送")

        result_parts = [f"搜索「{keyword}」并尝试关注完成"]
        if errors:
            result_parts.append(f"注意事项: {'; '.join(errors)}")
        result = "，".join(result_parts)

        # 4. 发私信（如果需要）
        if message:
            print(f"[WECHAT-STEP4] 发送私信: {message[:20]}...")
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
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return "微信未登录"

        _open_search(hwnd)
        _search_keyword(hwnd, contact_name)
        _navigate_to_first_result(hwnd)

        _goto_message_input(hwnd)
        _send_message(hwnd, message)

        return f"已给「{contact_name}」发送消息"

    except Exception as e:
        return f"微信发消息失败: {e}"
