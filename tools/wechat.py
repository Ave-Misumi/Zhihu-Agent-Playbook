"""微信（Windows 客户端）自动化工具集

纯 Win32 PostMessage + 剪贴板方案。
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
from browser_use.tools.service import ActionResult

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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

# Win32 常量
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_DELETE = 0x2E
VK_A = 0x41
VK_F = 0x46
VK_V = 0x56
VK_ESCAPE = 0x1B

SW_RESTORE = 9
SW_SHOWNORMAL = 1
SW_MINIMIZE = 6


def _send_key(hwnd: int, vk: int, ctrl: bool = False) -> None:
    """向窗口发送一次按键"""
    if ctrl:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0)
        time.sleep(0.02)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    time.sleep(0.02)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
    time.sleep(0.02)
    if ctrl:
        user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0)
        time.sleep(0.02)


_CLIPBOARD_PUT_INITIALIZED = False

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
    """Ctrl+V 粘贴剪贴板内容到窗口"""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    _send_key(hwnd, VK_V, ctrl=True)
    time.sleep(0.3)


def _ensure_foreground(hwnd: int) -> None:
    """确保窗口在前台且可见"""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    user32.ShowWindow(hwnd, SW_SHOWNORMAL)
    time.sleep(0.1)


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
    """Win32 枚举所有顶层窗口，返回微信窗口句柄"""
    import subprocess

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
            return ctypes.c_bool(True)
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        if not title or not any(kw in title for kw in ("微信", "Weixin")):
            return ctypes.c_bool(True)
        style = user32.GetWindowLongW(hwnd, -16)
        is_visible = bool(style & 0x10000000)
        is_tool = bool(style & 0x00000080)
        score = (10 if is_visible else 0) + (5 if title else 0) + (0 if is_tool else 3)
        if score > best_score:
            best_score = score
            best = hwnd
        return ctypes.c_bool(True)

    user32.EnumWindows(_enum, 0)
    return best


def _get_wechat_hwnd() -> int | None:
    """获取微信窗口句柄，必要时恢复/启动"""

    hwnd = _find_wechat_hwnd()
    if hwnd:
        # 1) 确保不在最小化状态
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)

        # 2) SendMessage WM_SIZE → Qt 监听到这个会触发整个 resizeEvent 链
        #    包括 QWebEngineView::resizeEvent → 重新分配 GPU 缓冲区
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w = r.right - r.left
        h = r.bottom - r.top

        WM_SIZE = 0x0005
        SIZE_RESTORED = 0
        # lParam = MAKELPARAM(width, height)
        lparam = (h << 16) | (w & 0xFFFF)
        user32.SendMessageW(hwnd, WM_SIZE, SIZE_RESTORED, lparam)

        # 3) 再 InvalidateRect + UpdateWindow 触发 WM_PAINT
        user32.InvalidateRect(hwnd, None, True)
        user32.UpdateWindow(hwnd)

        # 4) 再 SendMessage WM_ACTIVATE 确保 Qt 认为窗口被激活
        WM_ACTIVATE = 0x0006
        WA_ACTIVE = 1
        user32.SendMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)

        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        print("[WECHAT] 窗口恢复完成（WM_SIZE + WM_ACTIVATE）")
        return hwnd

    # 窗口还没出来，尝试快捷键恢复
    if _is_wechat_running():
        print("[WECHAT] 进程运行但无窗口，Ctrl+Alt+W 恢复...")
        auto.SendKeys("{Ctrl}{Alt}w")
        time.sleep(2)
        hwnd = _find_wechat_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(1.5)
            return hwnd

    # 启动
    exe = _find_wechat_exe()
    if exe is None:
        raise RuntimeError("未找到微信可执行文件")
    print("[WECHAT] 启动微信...")
    os.startfile(exe)

    for i in range(60):
        hwnd = _find_wechat_hwnd()
        if hwnd:
            user32.SetForegroundWindow(hwnd)
            time.sleep(1)
            return hwnd
        # 检查登录窗口
        login = auto.WindowControl(Name="微信", ClassName="LoginWnd")
        qr = auto.WindowControl(Name="登录")
        if login.Exists(maxSearchSeconds=1) or qr.Exists(maxSearchSeconds=1):
            print("[WECHAT] 等待扫码登录...")
            for _ in range(120):
                hwnd = _find_wechat_hwnd()
                if hwnd:
                    user32.SetForegroundWindow(hwnd)
                    time.sleep(1)
                    return hwnd
                time.sleep(1)
            return None
        if i == 5:
            print("[WECHAT] 等待微信窗口...")
        time.sleep(1)

    return _find_wechat_hwnd()


# ═══════════════════════════════════════════════
# 键盘操作原语
# ═══════════════════════════════════════════════

def _open_search(hwnd: int) -> None:
    """Ctrl+F 打开搜索框并清空"""
    _ensure_foreground(hwnd)
    time.sleep(0.3)
    _send_key(hwnd, VK_F, ctrl=True)   # Ctrl+F
    time.sleep(0.4)
    # 清空已有内容：Ctrl+A, Delete
    _send_key(hwnd, VK_A, ctrl=True)
    time.sleep(0.1)
    _send_key(hwnd, VK_DELETE)
    time.sleep(0.1)


def _search_keyword(hwnd: int, keyword: str) -> None:
    """输入关键词→回车搜索"""
    _clipboard_put(keyword)
    _clipboard_paste(hwnd)
    time.sleep(0.3)
    _send_key(hwnd, VK_RETURN)
    time.sleep(1.5)


def _navigate_to_first_result(hwnd: int) -> None:
    """Tab 到搜索结果列表第一项→Enter 打开"""
    # 微信搜索结果是右侧面板，搜索框在下，结果列表在上
    # 通常需要 Shift+Tab 或多次 Tab 导航到结果区
    for _ in range(3):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.2)
    _send_key(hwnd, VK_RETURN)
    time.sleep(1.5)


def _click_follow_via_keyboard(hwnd: int) -> bool:
    """Tab 在资料/会话页找「关注」按钮。新版微信关注按钮通常在页面中段。"""
    # 先确保焦点在页面内容区（可能需要 Shift+Tab 回到顶部再 Tab 下来）
    for _ in range(2):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.15)
    time.sleep(0.5)

    # Tab 遍历大概 5-8 个元素到关注按钮
    for _ in range(10):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.15)
    # Enter 触发
    _send_key(hwnd, VK_RETURN)
    time.sleep(2)
    return True  # 无 UI 反馈，只能假设成功


def _goto_message_input(hwnd: int) -> None:
    """导航到聊天输入框并清空"""
    # 从会话页顶部 Tab 到底部输入框
    for _ in range(8):
        _send_key(hwnd, VK_TAB)
        time.sleep(0.12)
    time.sleep(0.3)
    # 清空
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
) -> ActionResult:
    """搜索公众号/服务号 → 关注 → 发送私信（键盘+剪贴板方案）"""
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return ActionResult(error="微信未登录，请扫码登录后重试")

        # 1. Ctrl+F → 输入关键词 → 回车搜索
        _open_search(hwnd)
        _search_keyword(hwnd, keyword)

        # 2. 导航到第一条搜索结果 → Enter 打开
        _navigate_to_first_result(hwnd)

        # 3. 关注
        _click_follow_via_keyboard(hwnd)

        result = f"搜索「{keyword}」并尝试关注完成"

        # 4. 发私信（如果需要）
        if message:
            _goto_message_input(hwnd)
            _send_message(hwnd, message)
            result += f"，已发送私信"

        return ActionResult(extracted_content=result)

    except Exception as e:
        return ActionResult(error=f"微信操作失败: {e}")


async def wechat_send_message(
    contact_name: str,
    message: str,
) -> ActionResult:
    """给已关注的联系人/公众号发送消息"""
    try:
        hwnd = _get_wechat_hwnd()
        if hwnd is None:
            return ActionResult(error="微信未登录")

        _open_search(hwnd)
        _search_keyword(hwnd, contact_name)
        _navigate_to_first_result(hwnd)

        _goto_message_input(hwnd)
        _send_message(hwnd, message)

        return ActionResult(extracted_content=f"已给「{contact_name}」发送消息")

    except Exception as e:
        return ActionResult(error=f"微信发消息失败: {e}")
