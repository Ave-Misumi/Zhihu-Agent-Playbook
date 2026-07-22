"""
Agent 工厂:
- 知乎链路:browser-use Agent(浏览器自动化)
- WPS / 微信链路:LangChain ReAct Agent(纯工具调用,无需浏览器)
"""
import os
import signal
import psutil

from browser_use import Agent
from browser_use.browser.session import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.tools.service import Tools

from langchain.agents import create_agent
from langchain_core.tools import tool as langchain_tool

from config import get_llm, get_raw_llm, EDGE_EXECUTABLE_PATH, EDGE_USER_DATA_DIR, set_agent_mode
from tools.image_gen import generate_and_insert_svg_image
from tools.human_in_loop import ask_human_for_intervention
from tools.zhihu_body import zhihu_body_input
from tools.auto_memory import create_auto_memory_callback
from tools.wps import wps_create_document_and_export_pdf
from tools.wps_playbook import get_wps_template
from tools.wechat_agent import (
    wechat_observe,
    wechat_search,
    wechat_click_first_result,
    wechat_click_button,
    wechat_type_and_send,
)


# ═══════════════════════════════════════════════════════════
# 知乎 Agent 知识库(保持 browser-use)
# ═══════════════════════════════════════════════════════════

ZHIHU_SYSTEM_PROMPT = """你是一个知乎浏览器自动化助手,可以用浏览器基础操作完成知乎上的各种任务。

## 可用能力
- click / input / navigate / scroll / wait / evaluate:浏览器基础操作
- generate_and_insert_svg_image(article_topic="主题"):程序自带SVG生图工具,调用后自动在编辑器中插入矢量配图。不要自己写SVG/evaluate
- ask_human_for_intervention:遇到验证码/异常时暂停求助
- 文章正文由你根据主题自行创作(100~200 字)
- 配图数量:每篇文章生成1~3张SVG配图,插入到正文适当位置

## 登录（关键！不要卡住）
- 知乎需要登录才能写文章/评论/收藏
- 每个任务开始后,先 navigate 到 https://www.zhihu.com
- **到了首页后 wait 3秒**,等页面完全加载
- 然后快速判断登录状态:
  - 页面右上角有你的头像/用户名,且**没有"登录"按钮** → 已登录,直接下一步!
  - 页面有"登录"/"注册"按钮,或弹出登录弹窗 → 未登录
- **判断不超过1步,不要反复截图分析**
- 已登录 → 立刻 navigate 到 https://zhuanlan.zhihu.com/write
- 未登录 → 点击"登录"按钮:
  1. 优先等 Cookie 自动登录(Cookie已保存在浏览器中,可能自动填充)
  2. Cookie 生效了 → navigate 到 zhuanlan.zhihu.com/write
  3. Cookie 没生效 → 点击"密码登录" → 尝试用已保存的账号密码
  4. 不行就 ask_human_for_intervention","登录方式":"手机扫码" 暂停等人工
- **不要进创作中心!** 不要点创作中心按钮! 写文章直接 navigate 到 https://zhuanlan.zhihu.com/write

## 平台知识
- 写文章入口:**直接 navigate 到 https://zhuanlan.zhihu.com/write,不要从首页点创作中心绕路!**
- 进入写文章页后会弹出「创作助手」对话框,先关掉(点 aria-label="关闭创作助手" 的按钮)再操作
- **标题输入**: 知乎标题是 React 组件,藏在多层 Shadow DOM 里,普通 input/click 无效。
  必须用 evaluate 执行以下 JS（先点击标题区域,再用原生 setter 绕过 React 事件系统）:
  ```js
  (() => {
    // 穿透 Shadow DOM 找到标题 textarea
    const findDeep = (root, sel) => {
      const el = root.querySelector(sel);
      if (el) return el;
      for (const c of root.querySelectorAll('*')) {
        if (c.shadowRoot) { const f = findDeep(c.shadowRoot, sel); if (f) return f; }
      }
      return null;
    };
    const title = findDeep(document, '[placeholder*="标题"]');
    if (!title) return 'NOT_FOUND';
    title.focus(); title.click();
    // 用原生 setter 才能绕过 React 的受控组件
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(title, '文章标题');
    title.dispatchEvent(new Event('input', {bubbles:true}));
    title.dispatchEvent(new Event('change', {bubbles:true}));
    title.dispatchEvent(new Event('blur'));
    return 'OK';
  })();
  ```
  如果返回 NOT_FOUND → wait 2秒再试一次,还是 NOT_FOUND 就 ask_human_for_intervention
- **正文输入 - 两步走！**:
  **第1步**: click(index=正文区域 contenteditable div 的 index) — 必须先点！
  **第2步**: zhihu_body_input(html_content="<p>段落一</p><p>段落二</p><p>段落三</p>")
  返回 "OK:N" = 成功 / "E0" = 编辑器没找到(回到第1步) / "E1" = 输入了但检测为空 / "E2" = 焦点没拿到(手动再点一次正文区域)
  ⚠️ 正文就靠这个工具！不要用 evaluate 自己写 JS！不能用粘贴！不能塞 HTML 到 evaluate！
- 点「发布」按钮后可能弹出成功提示--关掉即可,不需要等待确认
- 关闭发布弹窗后你仍然在编辑页,如果需要回首页,请 navigate 到 https://www.zhihu.com
- 首页顶部有搜索框,输入标题后回车可以搜索文章
- 搜索不到已发布文章时可以尝试滚动浏览结果,或直接 navigate 到文章 URL

## 配图规则
- **必须调用 generate_and_insert_svg_image 工具！** 这是程序自带的 SVG 生图工具
- 调用方式: generate_and_insert_svg_image(article_topic="文章主题")
- 正文写完、发布前插入配图，每篇文章生成 1~3 张
- 在正文 JS 执行前或后调用都可以，工具会自动在富文本编辑器中插入图片
- **不要自己写 SVG 代码**，不要用 evaluate 或 innerHTML 插入图片，只用这个工具
- 插入配图后等待 2 秒让编辑器渲染完成，再检查发布按钮是否可用

## 禁忌
- 禁止给自己的文章/内容点赞(知乎不允许)
- 不要调用 playbook 查询工具--直接用基础操作
- **禁止进创作中心!** 写文章只用 navigate 到 zhuanlan.zhihu.com/write,不要在首页点创作中心按钮

## 行为准则
- 读懂用户的自然语言,拆解为先后步骤,逐一执行
- **弹窗优先直接关闭!** 任何弹窗/对话框/提示窗,先找关闭按钮或X图标点掉,不要截屏分析,不要犹豫
- 常见需要关闭的弹窗:创作助手、更新提示、广告推广、活动邀请、签到打卡、新功能引导
- 关闭方式:优先点 aria-label 含"关闭"的按钮 → 点右上角X → 点"我知道了"/"跳过" → Esc
- 每进入新页面后 wait 2秒,观察是否有弹窗,有关闭掉再继续
- 同一个 wait 操作不要连续超过 2 次
- 所有任务完成后调用 done(success=true),不要继续浏览
"""


# ═══════════════════════════════════════════════════════════
# WPS ReAct Agent 知识库
# ═══════════════════════════════════════════════════════════

WPS_SYSTEM_PROMPT = """你是 WPS 文档助手。用户用自然语言告诉你想要什么样的文档,你来完成。

## 可用工具
- wps_create_document_and_export_pdf(title="标题", body_md="正文", output_dir="输出目录(留空=桌面)")
  启动 WPS → 新建文档 → 写入标题与正文 → 设置字体/段落/编号格式 → 保存 .docx → 导出 PDF。
- get_wps_template(template_type="类型")
  查询模板缓存,获取上次同类文档的排版参数和内容骨架。类型可选:周报/会议纪要/报告/通知/计划/总结/简历/文章。

## 执行策略
1. 先调 get_wps_template 查缓存。命中直接复用参数。未命中返回明确指引(含默认排版值),直接创作。
2. 最多调一次 get_wps_template。返回结果后,无论命中与否,下一步必须直接调 wps_create_document_and_export_pdf。
3. 完成后直接回答用户结果(文件路径),不需要再调用任何工具。

## 严格禁止
- 禁止调 get_playbook_selector(那是知乎专用工具,WPS链路没有)
- 禁止 navigate/click/input/scroll 等任何浏览器操作
- 禁止对同一模板类型重复调用 get_wps_template 超过 1 次

## 参数说明
- title：文章标题（纯文本，不要带引号或格式标记）
- body_md：正文内容，用自然语言写就行，格式要求很宽松：
  - 小节标题用 `## 标题` 格式。
  - 正文以**自然段落为主**（首行缩进 2 字符），像写文章一样流畅叙述。
  - 只有在真正需要逐条列举时（如列数据、对比优劣、分步骤），才用 `- 要点` 格式。
  - 每个小节正文 1~2 段叙述 + 视需要 0~4 条要点，不要全写成列表。
  - 不要使用 `**粗体**` `*斜体*` 等 Markdown 标记。
  - 程序会自动识别结构和添加子标题序号。
- output_dir：可以不传，默认放桌面

## 参数（可从模板继承）
- title_font=黑体, title_size=小二, heading_font=黑体, heading_size=小三
- body_font=宋体, body_size=小四, line_spacing=28

## 工作方式
1. 从用户的话里理解：做什么类型的文档、什么主题、有没有排版要求
2. 先查模板 → 命中则复用参数，未命中则用默认或用户指定的
3. 创作标题和正文（内容要充实，至少 3 个小节，300 字以上）
4. 一次性调用 wps_create_document_and_export_pdf 完成
5. 返回文件路径给用户，任务结束

## 严格禁止
- 禁止调 get_playbook_selector（那是知乎专用工具，WPS链路没有）
- 禁止 navigate/click/input/scroll 等任何浏览器操作
- 禁止对同一模板类型重复调用 get_wps_template 超过 1 次
"""


# ═══════════════════════════════════════════════════════════
# 微信 ReAct Agent 知识库
# ═══════════════════════════════════════════════════════════

WECHAT_SYSTEM_PROMPT = """你是微信桌面自动化助手。你可以在微信中搜索服务号/公众号、关注、发私信。

## 核心规则（必读）

1. **直接行动，不要写计划**：收到任务后**直接调用工具**开始执行，不要先写长篇计划。
2. **每次只调一个工具**：每个回复只调一个工具（ReAct 模式），不要一次调多个。
3. **读完返回再决定下一步**：每步操作后仔细阅读返回结果，根据当前状态决定下一步。
4. **失败就换策略**：同一工具连续返回错误，不要无脑重试——换一种方式、跳过、或汇报给用户。
5. **重复 3 次未成功直接退出**：程序内置了重试计数，同一工具连续失败 3 次程序会自动退出。

## 可用工具

- wechat_search(keyword="名称")
  在微信中搜索关键词。返回当前页面状态描述。

- wechat_click_first_result()
  点击搜索结果第一个条目，进入详情页。返回当前页面状态描述。

- wechat_click_button(target="关注"|"私信"|"返回")
  点击页面上的按钮。返回操作结果 + 页面状态。

- wechat_type_and_send(message="内容")
  在聊天窗口中输入并发送消息。返回发送结果 + 页面状态。

- wechat_observe()
  截图+OCR+分析当前页面。返回页面类型、可见按钮和文字。
  其他工具调用后也会自动附带观察结果，一般不需要单独调用。

## 决策方式

你是真正的 Agent：

1. 每步操作后仔细阅读返回的【页面】【绿色按钮】【可见文字】【判断】字段
2. 根据当前状态决定下一步，不是固定流程
3. 操作失败时读 OCR 输出判断原因，自己想办法

## 常见决策路径（仅作参考，不强制）

典型"搜索+关注+私信"流程：
  1. wechat_search("火眼审阅") → 看返回是"搜索结果"页面 → 下一步
  2. wechat_click_first_result() → 看返回是"详情页" → 下一步
  3. 看【判断】：已关注（"取消关注"或"已关注"可见）→ 跳过 4，直接进 5
  4. 【判断】说"尚未关注" → wechat_click_button("关注")
     → 重试一次仍显示"关注" → 跳过，可能是已关注状态
  5. wechat_click_button("私信") → 看返回是"聊天窗口"
     → 如果私信点击后仍在详情页 → 可能是聊天窗和详情页同 hwnd，尝试 wechat_observe 确认
  6. wechat_type_and_send("消息内容") → 看返回确认发送成功

**每个细节由你决策**：如果"关注"文字是绿色但"取消关注"随处可见 → 已经关注了，不要死循环点关注。
如果私信点了但 OCR 仍显示详情页元素 → 先观察确认页面到底变没变，不要反复点同一个按钮。

## 状态判断关键字

- 「取消关注」→ 已关注 ✅
- 「已关注」→ 已关注 ✅
- 「关注」（无前后缀）→ 尚未关注，需点击
- 「私信」→ 可以发消息
- 「发消息」→ 可以发消息
- 「发送」「按住说话」「可以描述」→ 在聊天窗口

## 严格禁止
- 禁止操作浏览器 (navigate/click/input/scroll/evaluate)
- 禁止假设操作成功而不检查返回结果
- 禁止一次调用多个工具而不看中间结果
"""


# ═══════════════════════════════════════════════════════════
# 知乎工具注册(browser-use)
# ═══════════════════════════════════════════════════════════

def _kill_stale_edge_for_profile(user_data_dir: str):
    """杀掉占用指定 user_data_dir 的残留 Edge 进程，避免 SingletonLock 导致回退到临时目录。"""
    killed = 0
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if proc.info.get('name', '').lower() in ('msedge.exe', 'msedge'):
                cmd_str = ' '.join(cmdline)
                if user_data_dir in cmd_str:
                    os.kill(proc.info['pid'], signal.SIGTERM)
                    killed += 1
                    print(f"[CLEANUP] 已终止残留 Edge 进程 PID={proc.info['pid']} (占用 {user_data_dir})")
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            pass
    if killed > 0:
        print(f"[CLEANUP] 共清理 {killed} 个残留 Edge 进程")
    return killed


def _make_browser_profile(headless: bool = False) -> BrowserProfile:
    """统一构造浏览器配置 — 启动前清理占用同一 user_data_dir 的残留进程。"""
    profile_dir = str(EDGE_USER_DATA_DIR)
    _kill_stale_edge_for_profile(profile_dir)

    args = ["--disable-blink-features=AutomationControlled"]
    if headless:
        args.append("--window-size=1,1")
    return BrowserProfile(
        executable_path=EDGE_EXECUTABLE_PATH,
        user_data_dir=profile_dir,
        headless=headless,
        args=args,
    )


def create_zhihu_tools() -> Tools:
    """知乎链路:纯LLM决策 + 编辑器专用工具"""
    tools = Tools()
    tools.registry.action(
        description="向知乎正文编辑器输入HTML文章内容。传入html_content='<p>段落1</p><p>段落2</p>...',返回OK:N/E0/E1。正文就靠这个工具写，不要用evaluate自己写JS！"
    )(zhihu_body_input)
    tools.registry.action(
        description="根据文章主题生成SVG配图并插入到网页富文本编辑器中。"
    )(generate_and_insert_svg_image)
    tools.registry.action(
        description="遇到验证码、登录卡住或未知异常时暂停等待人工干预。"
    )(ask_human_for_intervention)
    return tools


async def create_zhihu_agent(task: str) -> Agent:
    """知乎链路:纯LLM决策模式
    
    只提供浏览器基础操作 + 通用工具,所有决策由LLM自行完成。
    不注册任何专用RPA工具(如ensure_zhihu_logged_in/zhihu_editor_input_title等)。
    """
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
# WPS / 微信工具:直接注册为 LangChain 工具
# ═══════════════════════════════════════════════════════════

# 把原工具函数包装成 LangChain 工具(docstring 会被自动用作工具描述)
wps_create_document_and_export_pdf_langchain = langchain_tool(wps_create_document_and_export_pdf)
get_wps_template_langchain = langchain_tool(get_wps_template)
wechat_observe_langchain = langchain_tool(wechat_observe)
wechat_search_langchain = langchain_tool(wechat_search)
wechat_click_first_result_langchain = langchain_tool(wechat_click_first_result)
wechat_click_button_langchain = langchain_tool(wechat_click_button)
wechat_type_and_send_langchain = langchain_tool(wechat_type_and_send)


# ═══════════════════════════════════════════════════════════
# ReAct / Tool Calling Agent 工厂(WPS / 微信)
# ═══════════════════════════════════════════════════════════

def _create_tool_agent_executor(tools, system_prompt: str, max_iterations: int = 6):
    """构造 LangChain Agent (LangGraph StateGraph, LangChain 1.x 新 API)"""
    llm = get_raw_llm()
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )


def _get_latest_wps_template(task: str) -> dict | None:
    """同步读取最新一条 WPS 模板（不含 IO 和 LLM 调用），失败返回 None"""
    try:
        import json, os, re
        from tools.wps_playbook import _load_templates, _guess_template_type, _make_template_key
        templates = _load_templates()
        if not templates:
            return None
        m = re.search(r"[《「"'"](.+?)[》」"'"']", task)
        title_guess = m.group(1) if m else task[:20]
        tt = _guess_template_type(title_guess, "")
        prefix = _make_template_key(tt, "")
        matches = [v for k, v in templates.items() if k.startswith(prefix)]
        return matches[0] if matches else None
    except Exception:
        return None


def _build_wps_prompt(task: str) -> str:
    """构造 WPS system prompt，如果缓存命中则预填排版参数，省掉工具往返"""
    tmpl = _get_latest_wps_template(task)
    if tmpl and "formatting" in tmpl:
        fmt = tmpl["formatting"]
        skeleton_str = " → ".join(tmpl.get("skeleton", []) or [])
        cached_section = (
            "## 已有模板（任务「" + tmpl.get("title", "") + "」，" + tmpl.get("updated", "") + "）\n"
            "参考排版参数如下，但 **用户要求优先** —— 若用户指定了不同格式，以用户为准：\n"
            "  默认 title_font=" + fmt.get("title_font", "黑体") + "  title_size=" + fmt.get("title_size", "小二") + "\n"
            "  默认 heading_font=" + fmt.get("heading_font", "黑体") + "  heading_size=" + fmt.get("heading_size", "小三") + "\n"
            "  默认 body_font=" + fmt.get("body_font", "宋体") + "  body_size=" + fmt.get("body_size", "小四") + "\n"
            "  默认 line_spacing=" + fmt.get("line_spacing", "28") + "\n"
            "参考章节结构：" + skeleton_str + "\n"
        )
        s1 = "1. 先调 get_wps_template 查缓存。命中直接复用参数。"
        s2 = "2. 最多调一次 get_wps_template。返回结果后,无论命中与否,下一步必须直接调 wps_create_document_and_export_pdf。"
        r1 = "1. 模板已命中（见上方），默认排版参数如上。若用户指定了不同格式则用用户的。"
        r2 = "2. 直接调 wps_create_document_and_export_pdf 完成创作。"
    else:
        s1 = "1. 先调 get_wps_template 查缓存。命中直接复用参数。"
        s2 = "2. 最多调一次 get_wps_template。返回结果后,无论命中与否,下一步必须直接调 wps_create_document_and_export_pdf。"
        r1 = "1. 模板缓存未命中，使用默认排版参数（标题黑体小二居中加粗，小节黑体小三加粗，正文宋体小四首行缩进2字符行距28磅）。"
        r2 = "2. 先调一次 get_wps_template 查缓存，然后直接调 wps_create_document_and_export_pdf。"
        cached_section = ""

    prompt = WPS_SYSTEM_PROMPT
    prompt = prompt.replace(s1, r1, 1)
    prompt = prompt.replace(s2, r2, 1)
    if cached_section:
        prompt = prompt.replace("## 执行策略", cached_section + "## 执行策略", 1)
    return prompt


async def create_wps_agent(task: str):
    """WPS 链路:LangChain Tool Calling Agent + WPS 工具

    启动时自动注入最新模板的排版参数到 system prompt，
    非首次任务 LLM 直接复用，跳过 get_wps_template 往返调用。
    """
    set_agent_mode("wps")
    tools = [
        wps_create_document_and_export_pdf_langchain,
        get_wps_template_langchain,
    ]
    # 注入最新模板：命中则预填参数，省掉一次工具往返
    prompt = _build_wps_prompt(task)
    agent_graph = _create_tool_agent_executor(
        tools, prompt, max_iterations=6
    )
    return agent_graph, task


async def create_wechat_agent(task: str):
    """微信链路:LangChain Tool Calling Agent + 微信工具"""
    set_agent_mode("wechat")
    tools = [
        wechat_observe_langchain,
        wechat_search_langchain,
        wechat_click_first_result_langchain,
        wechat_click_button_langchain,
        wechat_type_and_send_langchain,
    ]
    agent_graph = _create_tool_agent_executor(
        tools, WECHAT_SYSTEM_PROMPT, max_iterations=20
    )
    return agent_graph, task
