"""保存微信按钮模板，用于 wechat_vision 模板匹配。

用法：
  1. 手动打开微信，进入某个服务号详情页
  2. 运行脚本：python tools/save_wechat_template.py <hwnd> <x> <y> <w> <h> <name>
     参数为窗口客户区坐标和模板名
  3. 模板保存到 assets/wechat_templates/<name>.png

示例：
  python tools/save_wechat_template.py 123456 180 340 80 40 follow_button.png

如果不确定 hwnd，可以先运行：python tools/save_wechat_template.py --list
"""
import sys
import argparse
import ctypes
from ctypes import wintypes

from tools.wechat_vision import save_template_from_window, DEFAULT_TEMPLATE_DIR, capture_window

user32 = ctypes.windll.user32


def list_wechat_windows():
    """枚举所有可见的微信/Weixin 窗口。"""
    pids = []
    import subprocess
    for name in ("WeChat.exe", "Weixin.exe"):
        try:
            out = subprocess.check_output(
                f'tasklist /FI "IMAGENAME eq {name}" /FO CSV /NH',
                shell=True, text=True
            )
            for line in out.strip().split("\n"):
                if name in line:
                    pids.append(int(line.replace('"', '').split(',')[1].strip()))
        except Exception:
            pass

    results = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def _enum(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(256, length + 1))
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        results.append((hwnd, title, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(_enum, 0)
    return results


def main():
    parser = argparse.ArgumentParser(description="保存微信按钮模板")
    parser.add_argument("hwnd", type=str, help="窗口句柄（十进制），或 --list 模式传 list")
    parser.add_argument("x", type=int, nargs="?", help="客户区 x")
    parser.add_argument("y", type=int, nargs="?", help="客户区 y")
    parser.add_argument("w", type=int, nargs="?", help="宽度")
    parser.add_argument("h", type=int, nargs="?", help="高度")
    parser.add_argument("name", type=str, nargs="?", help="模板文件名")
    parser.add_argument("--list", action="store_true", help="列出微信窗口")
    args = parser.parse_args()

    if args.list or args.hwnd == "list":
        print("微信窗口列表：")
        for hwnd, title, left, top, w, h in list_wechat_windows():
            print(f"  hwnd={hwnd} title={title!r} rect=({left},{top},{w}x{h})")
        return

    if None in (args.x, args.y, args.w, args.h, args.name):
        parser.print_help()
        return

    hwnd = int(args.hwnd)
    region = (args.x, args.y, args.w, args.h)
    path = save_template_from_window(hwnd, region, args.name)
    print(f"已保存模板: {path}")

    # 同时保存一张完整的调试截图
    img = capture_window(hwnd, client_only=True)
    from tools.wechat_vision import visualize_detection
    debug_path = DEFAULT_TEMPLATE_DIR / f"debug_save_{args.name}"
    visualize_detection(img, (args.x + args.w // 2, args.y + args.h // 2), save_path=debug_path)
    print(f"调试截图: {debug_path}")


if __name__ == "__main__":
    main()
