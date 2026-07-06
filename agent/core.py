from browser_use import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.tools.service import Tools

from config import get_llm, EDGE_EXECUTABLE_PATH, EDGE_USER_DATA_DIR
from tools.playbook import get_playbook_selector, execute_playwright_action
from tools.image_gen import generate_and_insert_svg_image
from tools.human_in_loop import ask_human_for_intervention
from tools.auto_memory import create_auto_memory_callback


def create_custom_tools() -> Tools:
    """合并所有自定义工具到一个 Tools 实例"""
    tools = Tools()

    tools.registry.action(
        description="从操作手册中获取指定页面元素的CSS选择器或XPath。如果手册中有记录，请直接使用返回的选择器进行Playwright操作，无需再截图识别。"
    )(get_playbook_selector)

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
你是一个知乎自动化 Agent，速度优先。

⚠️ 任务执行流程（严格按序，不可跳转）：
1. 登录知乎
2. 打开写文章 → 关创作助手弹窗 → 填标题/正文(input) → 配图(generate_and_insert_svg_image) → 发布
3. 关闭发布成功弹窗 → **必须 navigate 回知乎首页** → 搜索已发布文章
4. 找到文章 → 评论 + 收藏
5. 立即 done

知乎已知固定流程：
- 进 zhuanlan.zhihu.com/write → 等 2s → 关「创作助手」弹窗(aria-label="关闭创作助手")
- 发布后会弹成功提示 → 点关闭按钮(aria-label="关闭")关掉
- ⚠️ 关弹窗后还在文章编辑页！必须执行 navigate(url="https://www.zhihu.com") 回首页！
- 回首页后找搜索框搜文章标题。搜不到就用 navigate 跳文章 URL
- ⚠️ 严禁在 navigate 回首页/搜索之前做任何评论或收藏操作

规则：
- 直接用 click/input/navigate/scroll/wait/evaluate。不要调 playbook 工具。
- 文章 100 字左右，LLM 自创。
- 遇到验证码/登录卡住 → ask_human_for_intervention。
- 严禁自我点赞。禁止微信扫码登录。
- 全完成后立即 done(success=true)，不继续浏览。
"""

    agent = Agent(
        task=task,
        llm=get_llm(),
        browser_session=browser_session,
        tools=custom_tools,
        extend_system_message=system_prompt,
        max_steps=50,
        llm_timeout=180,
        use_vision=False,
        register_new_step_callback=create_auto_memory_callback(),
    )
    return agent
