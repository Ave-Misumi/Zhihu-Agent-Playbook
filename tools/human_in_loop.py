import sys
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


async def ask_human_for_intervention(
    browser_session: BrowserSession,
    reason: str
) -> ActionResult:
    """当遇到验证码、需要扫码登录或页面出现未知异常时，调用此工具暂停程序，等待人工干预。"""
    print(f"\n[WARN] Agent 求助: {reason}")
    input("==> 请在浏览器中手动完成操作（如扫码、滑动验证码），完成后按 Enter 键继续...")
    return ActionResult(extracted_content="人工干预已完成，请继续执行后续任务。")
