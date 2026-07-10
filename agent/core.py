"""
Agent 工厂：
- 知乎链路：browser-use Agent（浏览器自动化）
- WPS / 微信链路：LangChain ReAct Agent（纯工具调用，无需浏览器）
"""
import os
import asyncio
from typing import Any, Dict

from browser_use import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.tools.service import Tools

from langchain_core.prompts import PromptTemplate
from langchain import hub
from langchain.agents import create_react_agent, AgentExecutor
from langchain_core.tools import tool as langchain_tool
from langchain_core.tools import render_text_description

from config import get_llm, EDGE_EXECUTABLE_PATH, EDGE_USER_DATA_DIR, set_agent_mode
from tools.playbook import get_playbook_selector, execute_playwright_action
from tools.image_gen import generate_and_insert_svg_image
from tools.human_in_loop import ask_human_for_intervention
from tools.auto_memory import create_auto_memory_callback
from tools.wps import wps_create_document_and_export_pdf
from tools.wps_playbook import get_wps_template
from tools.wechat import wechat_search_and_follow, wechat_send_message


# ═══════════════════════════════════════════════════════════
# 知乎 Agent 知识库（保持 browser-use）
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
# WPS ReAct Agent 知识库
# ═══════════════════════════════════════════════════════════

WPS_SYSTEM_PROMPT = """你是 WPS 文档助手。用户用自然语言告诉你想要什么样的文档，你来完成。

## 可用工具
- wps_create_document_and_export_pdf(title="标题", body_md="正文", output_dir="输出目录（留空=桌面）")
  启动 WPS → 新建文档 → 写入标题与正文 → 设置字体/段落/编号格式 → 保存 .docx → 导出 PDF。
- get_wps_template(template_type="类型")
  查询模板缓存，获取上次同类文档的排版参数和内容骨架。类型可选：周报/会议纪要/报告/通知/计划/总结/简历/文章。

## 执行策略
1. 先调 get_wps_template 查缓存。命中直接复用参数。未命中返回明确指引（含默认排版值），直接创作。
2. 最多调一次 get_wps_template。返回结果后，无论命中与否，下一步必须直接调 wps_create_document_and_export_pdf。
3. 完成后直接回答用户结果（文件路径），不需要再调用任何工具。

## 严格禁止
- 禁止调 get_playbook_selector（那是知乎专用工具，WPS链路没有）
- 禁止 navigate/click/input/scroll 等任何浏览器操作
- 禁止对同一模板类型重复调用 get_wps_template 超过 1 次

## 参数说明
- title：文章标题（纯文本，不要带引号或格式标记）
- body_md：正文，用 Markdown 组织：
  - ## 开头 = 小节标题（黑体小三加粗）
  - - 开头 = 列表项（自动编号 + 缩进）
  - **文字** = 粗体, *文字* = 斜体
  - 一、xx / 引言 / 结语 → 自动识别为小节标题
  - 普通段落 = 宋体小四，首行缩进 2 字符，固定行距
- output_dir：可以不传，默认放桌面

## 参数（可从模板继承）
- title_font=黑体, title_size=小二, heading_font=黑体, heading_size=小三
- body_font=宋体, body_size=小四, line_spacing=28

## 工作方式
1. 从用户的话里理解：做什么类型的文档、什么主题、有没有排版要求
2. 先查模板 → 命中则复用参数，未命中则用默认或用户指定的
3. 创作标题和正文（内容要充实，至少 2~3 个小节，300 字以上）
4. 一次性调用 wps_create_document_and_export_pdf 完成
5. 返回文件路径给用户，任务结束
"""


# ═══════════════════════════════════════════════════════════
# 微信 ReAct Agent 知识库
# ═══════════════════════════════════════════════════════════

WECHAT_SYSTEM_PROMPT = """你是微信桌面客户端自动化助手，通过 Windows UI 自动化操作微信客户端。

## 可用工具
- wechat_search_and_follow(keyword="名称", message="私信内容(可选)", account_type="服务号")
  搜索公众号/服务号 → 关注 → 可选发送私信。一步完成。
- wechat_send_message(contact_name="名称", message="消息内容")
  给已在聊天列表中的联系人/公众号发消息。

## 调用格式
这些工具是 Python 函数，直接按工具名和参数调用即可，不要包在 evaluate 或 navigate 里。

## 工作方式
1. 从用户的话里提取：搜索什么、要不要关注、发什么消息
2. 如果用户要搜索+关注+发私信，直接调用 wechat_search_and_follow 一次完成
3. 如果用户只是给已知联系人发消息，调用 wechat_send_message
4. 返回执行结果给用户，任务结束

⚠️ 不要操作浏览器。禁止使用 navigate/click/input/scroll/evaluate。
⚠️ 如果未找到目标或关注失败，如实汇报状态。
"""


# ═══════════════════════════════════════════════════════════
# 知乎工具注册（browser-use）
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


async def create_zhihu_agent(task: str) -> Agent:
    """知乎链路：浏览器 + 知乎知识 prompt → 用户自然语言透传"""
    set_agent_mode("zhihu")
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


# ═══════════════════════════════════════════════════════════
# WPS / 微信工具：适配 LangChain ReAct（返回值字符串化）
# ═══════════════════════════════════════════════════════════

def _extract_result(value: Any) -> str:
    """把 browser-use ActionResult 或任意返回值转成字符串给 ReAct 观察"""
    if hasattr(value, "extracted_content"):
        return str(value.extracted_content)
    if hasattr(value, "content"):
        return str(value.content)
    return str(value)


@langchain_tool
def wps_create_document_and_export_pdf_langchain(
    title: str,
    body_md: str,
    output_dir: str = "",
    title_font: str = "黑体",
    title_size: str = "小二",
    heading_font: str = "黑体",
    heading_size: str = "小三",
    body_font: str = "宋体",
    body_size: str = "小四",
    line_spacing: str = "28",
) -> str:
    """启动 WPS 新建文字文档，写入标题和正文（Markdown 格式），设置字体/段落/编号，
    保存 .docx 并导出 PDF。完成后返回文件路径。"""
    result = wps_create_document_and_export_pdf(
        title=title,
        body_md=body_md,
        output_dir=output_dir,
        title_font=title_font,
        title_size=title_size,
        heading_font=heading_font,
        heading_size=heading_size,
        body_font=body_font,
        body_size=body_size,
        line_spacing=line_spacing,
    )
    return _extract_result(result)


@langchain_tool
def get_wps_template_langchain(template_type: str) -> str:
    """查询指定类型（周报/会议纪要/报告/通知/计划/总结/简历/文章）的 WPS 文档模板缓存，
    返回上次使用的排版参数和章节结构骨架。"""
    return get_wps_template(template_type)


@langchain_tool
def wechat_search_and_follow_langchain(
    keyword: str,
    message: str = "",
    account_type: str = "服务号",
) -> str:
    """在微信桌面客户端搜索指定名称的公众号/服务号并关注，可选发送私信。
    搜索+关注+发私信一步完成。若只需关注不发消息，message 留空即可。"""
    result = wechat_search_and_follow(
        keyword=keyword,
        message=message,
        account_type=account_type,
    )
    return _extract_result(result)


@langchain_tool
def wechat_send_message_langchain(contact_name: str, message: str) -> str:
    """给微信聊天列表中已有的联系人/公众号发送文字消息。仅用于已关注的账号，不需要再搜索。"""
    result = wechat_send_message(contact_name=contact_name, message=message)
    return _extract_result(result)


# ═══════════════════════════════════════════════════════════
# ReAct Agent 工厂（WPS / 微信）
# ═══════════════════════════════════════════════════════════

def _create_react_agent_executor(tools, system_prompt: str, max_iterations: int = 6):
    """构造 LangChain ReAct Agent 执行器"""
    llm = get_llm()
    # ReAct 模板必须包含 {tools} / {tool_names} / {input} / {agent_scratchpad}
    prompt_template = system_prompt + """

可用的工具如下：
{tools}

工具名称：{tool_names}

请按 ReAct 格式思考：
Question: 用户的问题
Thought: 你的思考
Action: 工具名称
Action Input: 工具的 JSON 参数
Observation: 工具返回结果
...（重复 Thought/Action/Action Input/Observation 直到完成）
Final Answer: 最终回答

现在开始：
Question: {input}
{agent_scratchpad}
"""
    prompt = PromptTemplate(
        template=prompt_template,
        input_variables=["input", "agent_scratchpad", "tools", "tool_names"],
    ).partial(
        tools=render_text_description(tools),
        tool_names=", ".join([t.name for t in tools]),
    )
    agent = create_react_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=max_iterations,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )


async def create_wps_agent(task: str):
    """WPS 链路：LangChain ReAct Agent + WPS 工具"""
    set_agent_mode("wps")
    tools = [
        wps_create_document_and_export_pdf_langchain,
        get_wps_template_langchain,
    ]
    executor = _create_react_agent_executor(
        tools, WPS_SYSTEM_PROMPT, max_iterations=6
    )
    return executor, task


async def create_wechat_agent(task: str):
    """微信链路：LangChain ReAct Agent + 微信工具"""
    set_agent_mode("wechat")
    tools = [
        wechat_search_and_follow_langchain,
        wechat_send_message_langchain,
    ]
    executor = _create_react_agent_executor(
        tools, WECHAT_SYSTEM_PROMPT, max_iterations=8
    )
    return executor, task
