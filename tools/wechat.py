"""微信（Windows 客户端）uiautomation 自动化工具集

功能：
  - wechat_search_and_follow   打开微信 → 搜索指定名称的公众号/服务号 → 关注 → 发私信
  - wechat_send_message        给已关注的公众号发送文字消息

依赖：uiautomation (pip install uiautomation)
"""
import os
import time

import uiautomation as auto
from browser_use.tools.service import ActionResult


# 微信主窗口特征
_WECHAT_CLASS = "WeChatMainWndForPC"
_SESSION_CLASS = "SessionChatWnd"
_SEARCH_PLACEHOLDER = "搜索"
_FOLLOW_TEXT = "关注"
_SERVED_TEXT = "服务号"


def _launch_wechat() -> auto.WindowControl | None:
    """启动微信（如果未运行），返回主窗口"""
    paths = [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"),
                     "Tencent", "WeChat", "WeChat.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
                     "Tencent", "WeChat", "WeChat.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            os.startfile(p)
            break
    else:
        raise RuntimeError("未找到 WeChat.exe")

    # 等待主窗口出现（最多 30s）
    for _ in range(30):
        wx = auto.WindowControl(Name="微信", ClassName=_WECHAT_CLASS)
        if wx.Exists(maxSearchSeconds=1):
            return wx
        time.sleep(1)
    raise RuntimeError("微信启动超时（30s）")


def _get_wechat_window() -> auto.WindowControl:
    """获取微信主窗口（若未运行则启动）"""
    wx = auto.WindowControl(Name="微信", ClassName=_WECHAT_CLASS)
    if not wx.Exists(maxSearchSeconds=2):
        wx = _launch_wechat()
    wx.SetFocus()
    time.sleep(0.5)
    return wx


def _get_search_edit(wx: auto.WindowControl) -> auto.EditControl:
    """定位顶部搜索框"""
    for _ in range(3):
        edit = wx.EditControl(Name=_SEARCH_PLACEHOLDER)
        if edit.Exists(maxSearchSeconds=1):
            return edit
        # 尝试先点一下微信窗口内部确保焦点
        wx.Click()
        time.sleep(0.3)
    raise RuntimeError("找不到微信搜索框")


def _find_public_account_in_list(wx: auto.WindowControl, name: str) -> auto.Control | None:
    """在搜索结果列表中找一个公众号/服务号"""
    # 微信搜索结果在右侧区域，结构是树形列表
    # 先用 name 模糊匹配
    candidates = []
    t0 = time.time()
    while time.time() - t0 < 8:
        # 递归查找包含目标名称的文本控件
        for ctrl in wx.GetChildren():
            _collect_matches(ctrl, name, candidates)
        if candidates:
            break
        time.sleep(1)
    return candidates[0] if candidates else None


def _collect_matches(ctrl: auto.Control, name: str, out: list):
    """递归收集 name 匹配的控件"""
    try:
        ctrl_name = ctrl.Name or ""
        if name in ctrl_name:
            out.append(ctrl)
            return
        for child in ctrl.GetChildren():
            _collect_matches(child, name, out)
    except Exception:
        pass


def _click_follow_button(parent: auto.Control) -> bool:
    """在控件及子树中找「关注」按钮并点击"""
    for ctrl in parent.GetChildren():
        try:
            if ctrl.Name and _FOLLOW_TEXT in ctrl.Name and ctrl.ControlTypeName == "ButtonControl":
                ctrl.Click()
                time.sleep(1.5)
                return True
        except Exception:
            pass
        try:
            if _click_follow_button(ctrl):
                return True
        except Exception:
            pass
    return False


def _find_and_click_served_entry(wx: auto.WindowControl, name: str) -> bool:
    """搜索结果中可能有多条（公众号、联系人、文章等），需点「服务号」类型的那一条"""
    # 简化策略：先点第一条匹配的结果进入会话，
    # 然后检查是否已经是会话窗口（关注后会有对话框）
    # 实际更稳的做法是用深度搜索找到列表中「服务号」标记，但 UI 结构差异大
    # 暂时用点击第一条匹配结果的方式
    item = _find_public_account_in_list(wx, name)
    if item is None:
        # 兜底：直接点搜索框输入后回车
        return False

    try:
        item.Click()
        time.sleep(1.5)
        # 看是否弹出了公众号详情 / 会话窗口
        return True
    except Exception:
        return False


def _get_chat_input(session: auto.WindowControl) -> auto.EditControl | None:
    """获取会话窗口的消息输入框"""
    for _ in range(3):
        edits = session.GetChildren()
        # 找到最大的 EditControl
        best = None
        max_area = 0
        for c in edits:
            try:
                if c.ControlTypeName == "EditControl":
                    r = c.BoundingRectangle
                    area = r.width() * r.height()
                    if area > max_area:
                        max_area = area
                        best = c
            except Exception:
                pass
        if best:
            return best
        time.sleep(0.5)
    return None


# ═══════════════════════════════════════════════
# 公开工具
# ═══════════════════════════════════════════════

async def wechat_search_and_follow(
    keyword: str,
    message: str = "",
    account_type: str = "服务号",
) -> ActionResult:
    """
    在微信桌面客户端中搜索指定公众号/服务号并关注，可选发送私信。

    参数:
        keyword      要搜索的公众号/服务号名称
        message      关注后发送的私信内容（可选，留空则不发送）
        account_type 要关注的类型："服务号" | "公众号" | "不限"

    返回: 操作结果描述
    """
    try:
        wx = _get_wechat_window()

        # Step 1: 定位搜索框 → 输入关键词 → 回车搜索
        edit = _get_search_edit(wx)
        edit.Click()
        time.sleep(0.3)
        edit.SendKeys("{Ctrl}a{Delete}")  # 清空
        time.sleep(0.2)
        edit.SendKeys(keyword)
        time.sleep(0.3)
        edit.SendKeys("{Enter}")
        time.sleep(2)

        # Step 2: 在搜索结果列表中找到目标
        item = _find_public_account_in_list(wx, keyword)
        if item is None:
            return ActionResult(error=f"未在搜索结果中找到「{keyword}」")

        item.Click()
        time.sleep(2)

        # Step 3: 关注
        # 点开之后通常会进入一个公众号资料页/会话窗口
        # 找当前活跃的会话窗口
        session = None
        for _ in range(10):
            s = auto.WindowControl(ClassName=_SESSION_CLASS)
            if s.Exists(maxSearchSeconds=1):
                session = s
                break
            time.sleep(1)

        if session is None:
            return ActionResult(error="未能打开公众号会话/资料页")

        # 尝试点「关注」按钮
        followed = _click_follow_button(session)

        msg = f"搜索「{keyword}」成功"
        if followed:
            msg += "，已关注"
        else:
            msg += "（关注状态未确认，请手动检查）"

        # Step 4: 发送私信（如果需要）
        if message:
            if not followed:
                # 给一点点时间让页面加载
                time.sleep(2)
            # 找输入框
            inp = _get_chat_input(session)
            if inp is None:
                # 可能还在资料页，尝试从微信主窗口重新点进去
                edit = _get_search_edit(wx)
                edit.SendKeys("{Ctrl}a{Delete}")
                edit.SendKeys(keyword)
                edit.SendKeys("{Enter}")
                time.sleep(2)
                # 这次直接找会话
                s2 = auto.WindowControl(ClassName=_SESSION_CLASS)
                if s2.Exists(maxSearchSeconds=2):
                    inp = _get_chat_input(s2)

            if inp:
                inp.Click()
                time.sleep(0.3)
                inp.SendKeys(message)
                time.sleep(0.5)
                inp.SendKeys("{Enter}")
                msg += f"，已发送私信：「{message[:30]}{'...' if len(message)>30 else ''}」"
            else:
                msg += "，但未能发送私信（找不到输入框）"

        return ActionResult(extracted_content=msg)

    except Exception as e:
        return ActionResult(error=f"微信操作失败: {e}")


async def wechat_send_message(
    contact_name: str,
    message: str,
) -> ActionResult:
    """
    给微信中的某个联系人/公众号发送文字消息。
    前提是该联系人已经在聊天列表中（已关注/已加好友）。

    参数: contact_name 联系人/公众号名称; message 要发送的文字
    """
    try:
        wx = _get_wechat_window()

        # 搜索联系人
        edit = _get_search_edit(wx)
        edit.Click()
        time.sleep(0.3)
        edit.SendKeys("{Ctrl}a{Delete}")
        time.sleep(0.2)
        edit.SendKeys(contact_name)
        time.sleep(0.3)
        edit.SendKeys("{Enter}")
        time.sleep(2)

        # 等会话窗口出现
        session = None
        for _ in range(8):
            s = auto.WindowControl(ClassName=_SESSION_CLASS)
            if s.Exists(maxSearchSeconds=1):
                session = s
                break
            time.sleep(1)

        if session is None:
            return ActionResult(error=f"未能打开「{contact_name}」的会话窗口")

        inp = _get_chat_input(session)
        if inp is None:
            return ActionResult(error=f"找不到「{contact_name}」的消息输入框")

        inp.Click()
        time.sleep(0.3)
        inp.SendKeys(message)
        time.sleep(0.5)
        inp.SendKeys("{Enter}")

        return ActionResult(extracted_content=f"已给「{contact_name}」发送消息")

    except Exception as e:
        return ActionResult(error=f"微信发消息失败: {e}")
