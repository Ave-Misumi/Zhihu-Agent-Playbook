from browser_use import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.tools.service import Tools

from config import get_llm, EDGE_EXECUTABLE_PATH, EDGE_USER_DATA_DIR
from tools.playbook import get_playbook_selector, save_to_playbook, execute_playwright_action
from tools.image_gen import generate_and_insert_svg_image
from tools.human_in_loop import ask_human_for_intervention


def create_custom_tools() -> Tools:
    """合并所有自定义工具到一个 Tools 实例"""
    tools = Tools()
    
    tools.registry.action(
        description="从操作手册中获取指定页面元素的CSS选择器或XPath。如果手册中有记录，请直接使用返回的选择器进行Playwright操作，无需再截图识别。"
    )(get_playbook_selector)
    
    tools.registry.action(
        description="将成功定位到的页面元素选择器记录到操作手册中，以便下次毫秒级直接执行。"
    )(save_to_playbook)
    
    tools.registry.action(
        description="直接通过Playwright执行点击或输入操作（用于命中操作手册后的极速执行）。"
    )(execute_playwright_action)
    
    tools.registry.action(
        description="根据文章主题，生成一张SVG格式的配图，并将其插入到当前网页的富文本编辑器中。"
    )(generate_and_insert_svg_image)
    
    tools.registry.action(
        description="当遇到验证码、需要扫码登录或页面出现未知异常时，调用此工具暂停程序，等待人工干预。"
    )(ask_human_for_intervention)
    
    return tools


async def create_zhihu_agent(task: str):
    custom_tools = create_custom_tools()

    browser_profile = BrowserProfile(
        executable_path=EDGE_EXECUTABLE_PATH,
        user_data_dir=EDGE_USER_DATA_DIR,
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )

    browser_session = BrowserSession(browser_profile=browser_profile)

    system_prompt = """
    你是一个高效的知乎自动化 Agent。你的核心原则是【速度优先，拒绝重复造轮子】。

    【执行策略】：
    1. 每次操作前，必须先调用 `get_playbook_selector` 查询操作手册。
    2. 如果手册中有 CSS 选择器/XPath，直接调用 `execute_playwright_action` 毫秒级执行，绝对不要再去截图或分析 DOM！
    3. 如果手册中没有，或者执行报错（页面改版），则使用浏览器默认工具进行 DOM 探索，成功后务必调用 `save_to_playbook` 更新手册。
    4. 写文章时，必须调用 `generate_and_insert_svg_image` 生成配图。
    5. 遇到验证码或登录失效，立即调用 `ask_human_for_intervention`。
    """

    agent = Agent(
        task=task,
        llm=get_llm(),
        browser_session=browser_session,
        tools=custom_tools,
        extend_system_message=system_prompt,
        max_steps=50,
    )
    return agent
