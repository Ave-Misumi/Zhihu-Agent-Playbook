import json
import os
from typing import Optional
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult

PLAYBOOK_PATH = os.path.join(os.path.dirname(__file__), "../memory/zhihu_playbook.json")


def load_playbook():
    if os.path.exists(PLAYBOOK_PATH):
        with open(PLAYBOOK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_playbook(data):
    os.makedirs(os.path.dirname(PLAYBOOK_PATH), exist_ok=True)
    with open(PLAYBOOK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_playbook_selector(page_name: str, element_description: str) -> str:
    """从操作手册中获取指定页面元素的CSS选择器或XPath。如果手册中有记录，请直接使用返回的选择器进行Playwright操作，无需再截图识别。"""
    playbook = load_playbook()
    page_data = playbook.get(page_name, {})
    selector = page_data.get(element_description)
    if selector:
        return f"找到先验知识: {selector}。请使用 page.locator('{selector}') 进行操作。"
    return "手册中暂无此元素记录，请使用浏览器默认工具进行探索。"


def save_to_playbook(page_name: str, element_description: str, css_selector_or_xpath: str) -> str:
    """将成功定位到的页面元素选择器记录到操作手册中，以便下次毫秒级直接执行。"""
    playbook = load_playbook()
    if page_name not in playbook:
        playbook[page_name] = {}
    playbook[page_name][element_description] = css_selector_or_xpath
    save_playbook(playbook)
    return f"已成功记录到操作手册: {page_name} -> {element_description}"


async def execute_playwright_action(
    browser_session: BrowserSession,
    selector: str,
    action: str,
    text: Optional[str] = None
) -> ActionResult:
    """直接通过Playwright执行点击或输入操作（用于命中操作手册后的极速执行）。"""
    page = await browser_session.get_current_page()
    try:
        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=5000)
        if action == "click":
            await locator.click()
        elif action == "fill" and text:
            await locator.fill(text)
        elif action == "type" and text:
            await locator.type(text)
        return ActionResult(extracted_content=f"成功通过手册执行: {action} on {selector}")
    except Exception as e:
        return ActionResult(error=f"手册执行失败(可能页面改版): {str(e)}。请降级为视觉/DOM探索。")
