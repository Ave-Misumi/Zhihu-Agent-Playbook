"""
Agent 工厂：创建知乎自动化 Agent 或 WPS 文档 Agent。

两个链路共享同一个执行框架（browser-use Agent），区别：
- 知乎 Agent：注册浏览器操作 + 知乎专用工具，system_prompt 包含知乎平台知识
- WPS Agent：仅注册 wps 工具，system_prompt 引导 LLM 理解用户意图后调用 COM
"""
from browser_use import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.tools.service import Tools

from config import get_llm, EDGE_EXECUTABLE_PATH, EDGE_USER_DATA_DIR
from tools.playbook import get_playbook_selector, execute_playwright_action
from tools.image_gen import generate_and_insert_svg_image
from tools.human_in_loop import ask_human_for_intervention
from tools.auto_memory import create_auto_memory_callback
from tools.wps import wps_create_document_and_export_pdf
from tools.wechat import wechat_search_and_follow, wechat_send_message

# ═══════════════════════════════════════════════════════════
# 知乎 Agent 知识库
# ═══════════════════════════════════════════════════════════

ZHIHU_SYSTEM_PROMPT = """你是一个知乎浏览器自动化助手，可以用浏览器基础操作完成知乎上的各种任务。

## 可用能力
- click / input / navigate / scroll / wait / evaluate：浏览器基础操作
- generate_and_insert_svg_image：为文章生成配图并插入编辑器
- ask_human_for_intervention：遇到登录/验证码/异常时暂停求助
- 文章正文由你根据主题自行创作（100~200 字），标题和正文都用 input 填入

## 平台知识
- 写文章入口：直接 navigate 到 https://zhuanlan.zhihu.com/write
- 进入写文章页后会弹出「创作助手」对话框，先关掉（点 aria-label="关闭创作助手" 的按钮）再操作
- 正文区域是一个 contenteditable 的 div（Draft.js 编辑器），直接用 input(index=..., text=...) 填入
- 点「发布」按钮后可能弹出成功提示——关掉即可，不需要等待确认
- 关闭发布弹窗后你仍然在编辑页，如果需要回首页，请 navigate 到 https://www.zhihu.com
- 首页顶部有搜索框，输入标题后回车可以搜索文章
- 搜索不到已发布文章时可以尝试滚动浏览结果，或直接 navigate 到文章 URL

## 禁忌
- 禁止给自己的文章/内容点赞（知乎不允许）
- 禁止使用微信扫码登录——请用 Cookie/手机验证码登录，卡住则 ask_human_for_intervention
- 不要调用 playbook 查询工具——直接用基础操作

## 行为准则
- 读懂用户的自然语言，拆解为先后步骤，逐一执行
- 弹窗优先直接关闭，不要截屏分析浪费步骤
- 同一个 wait 操作不要连续超过 2 次
- 所有任务完成后调用 done(success=true)，不要继续浏览
"""


# ═══════════════════════════════════════════════════════════
# WPS Agent 知识库
# ═══════════════════════════════════════════════════════════

WPS_SYSTEM_PROMPT = """你是 WPS 文档助手。用户用自然语言告诉你想要什么样的文档，你来完成。

## 唯一可用工具
- wps_create_document_and_export_pdf(title="标题", body_md="正文", output_dir="输出目录（留空=桌面）")

调用它会：启动 WPS → 新建文档 → 写入标题与正文 → 设置字体/段落/编号格式 → 保存 .docx → 导出 PDF。

## 参数说明
- title：文章标题（纯文本，不要带引号或格式标记）
- body_md：正文，用 Markdown 组织：
  - ## 开头 = 二级标题（格式化为黑体小三加粗）
  - - 开头 = 列表项（自动编号 + 缩进）
  - **文字** = 粗体
  - *文字* = 斜体
  - 一、xx / 引言 / 结语 → 自动识别为小节标题
  - 普通段落 = 宋体小四，首行缩进 2 字符，固定行距
- output_dir：可以不传，默认放桌面

## 工作方式
1. 从用户的话里理解：主题是什么、要什么风格、有没有特别要求（字号、字体等）
2. 自己创作标题和正文（内容要充实，至少 2~3 个小节，300 字以上）
3. 一次性调用 wps_create_document_and_export_pdf 完成
4. 返回文件路径后，立即 done(success=true)

⚠️ 不要操作浏览器（不要 navigate/click/input），唯一能用的就是这个 WPS 工具。
"""


# ═══════════════════════════════════════════════════════════
# 微信 Agent 知识库
# ═══════════════════════════════════════════════════════════

WECHAT_SYSTEM_PROMPT = """你是微信桌面客户端自动化助手，通过 Windows UI 自动化操作微信客户端。

## 可用工具
- wechat_search_and_follow(keyword="名称", message="私信内容(可选)", account_type="服务号")
  搜索公众号/服务号 → 关注 → 可选发送私信。一步完成。

- wechat_send_message(contact_name="名称", message="消息内容")
  给已在聊天列表中的联系人/公众号发消息。

## 工作方式
1. 从用户的话里提取：搜索什么、要不要关注、发什么消息
2. 如果用户要搜索+关注+发私信，直接调用 wechat_search_and_follow 一次完成
3. 如果用户只是给已知联系人发消息，调用 wechat_send_message
4. 完成后 done(success=true)

⚠️ 不要操作浏览器，唯一能用的就是这两个微信工具。
⚠️ 如果未找到目标或关注失败，如实汇报状态。
"""


# ═══════════════════════════════════════════════════════════
# 工具注册
# ═══════════════════════════════════════════════════════════

def _make_browser_profile(headless: bool = False) -> BrowserProfile:
    """统一构造浏览器配置"""
    args = ["--disable-blink-features=AutomationControlled"]
    if headless:
        args.append("--window-size=1,1")
    return BrowserProfile(
        executable_path=EDGE_EXECUTABLE_PATH,
        user_data_dir=EDGE_USER_DATA_DIR,
        headless=headless,
        args=args,
    )


def create_zhihu_tools() -> Tools:
    """知乎链路的全套工具"""
    tools = Tools()
    tools.registry.action(
        description="从操作手册中获取指定页面元素的CSS选择器或XPath。"
    )(get_playbook_selector)
    tools.registry.action(
        description="直接通过Playwright执行点击或输入操作（命中操作手册后极速执行）。"
    )(execute_playwright_action)
    tools.registry.action(
        description="根据文章主题生成SVG配图并插入到网页富文本编辑器中。"
    )(generate_and_insert_svg_image)
    tools.registry.action(
        description="遇到验证码、登录卡住或未知异常时暂停等待人工干预。"
    )(ask_human_for_intervention)
    return tools


# ═══════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════

async def create_zhihu_agent(task: str) -> Agent:
    """知乎链路：浏览器 + 知乎知识 prompt → 用户自然语言透传"""
    return Agent(
        task=task,
        llm=get_llm(),
        browser_session=BrowserSession(browser_profile=_make_browser_profile(headless=False)),
        tools=create_zhihu_tools(),
        extend_system_message=ZHIHU_SYSTEM_PROMPT,
        max_steps=40,
        llm_timeout=180,
        use_vision=False,
        register_new_step_callback=create_auto_memory_callback(),
    )


async def create_wps_agent(task: str) -> Agent:
    """WPS 链路：headless 占位浏览器 + 仅 WPS 工具 → 用户自然语言透传"""
    wps_tools = Tools()
    wps_tools.registry.action(
        description="启动WPS新建文字文档，写入标题和正文(Markdown格式)，设置字体/段落/编号，保存.docx并导出PDF。完成后返回文件路径。这是唯一可用的工具。"
    )(wps_create_document_and_export_pdf)

    return Agent(
        task=task,
        llm=get_llm(),
        browser_session=BrowserSession(browser_profile=_make_browser_profile(headless=True)),
        tools=wps_tools,
        extend_system_message=WPS_SYSTEM_PROMPT,
        max_steps=5,
        llm_timeout=120,
        use_vision=False,
    )


async def create_wechat_agent(task: str) -> Agent:
    """微信链路：headless 占位浏览器 + 仅微信 UI 自动化工具 → 用户自然语言透传"""
    wechat_tools = Tools()
    wechat_tools.registry.action(
        description="在微信桌面客户端搜索指定名称的公众号/服务号并关注，可选发送私信。搜索+关注+发私信一步完成。若只需关注不发消息，message留空即可。"
    )(wechat_search_and_follow)
    wechat_tools.registry.action(
        description="给微信聊天列表中已有的联系人/公众号发送文字消息。仅用于已关注的账号，不需要再搜索。"
    )(wechat_send_message)

    return Agent(
        task=task,
        llm=get_llm(),
        browser_session=BrowserSession(browser_profile=_make_browser_profile(headless=True)),
        tools=wechat_tools,
        extend_system_message=WECHAT_SYSTEM_PROMPT,
        max_steps=5,
        llm_timeout=120,
        use_vision=False,
    )
