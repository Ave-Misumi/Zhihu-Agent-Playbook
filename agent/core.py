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
from tools.image_gen import generate_and_paste_image
from tools.human_in_loop import ask_human_for_intervention
from tools.zhihu_body import zhihu_body_input, zhihu_body_input_with_image
from tools.playbook import get_playbook_selector, execute_playwright_action
from tools.auto_memory import create_auto_memory_callback
from tools.playbook_intercept import apply_all_patches
from tools.wps import wps_create_document_and_export_pdf
from tools.wps_playbook import get_wps_template
from tools.wechat_agent import (
    wechat_observe,
    wechat_search,
    wechat_click_first_result,
    wechat_click_button,
    wechat_type_and_send,
    wechat_click_text,
    wechat_scroll,
    wechat_like_moments_post,
)


# ═══════════════════════════════════════════════════════════
# 知乎 Agent 知识库(保持 browser-use)
# ═══════════════════════════════════════════════════════════

ZHIHU_SYSTEM_PROMPT = """你是一个知乎浏览器自动化助手,可以用浏览器基础操作完成知乎上的各种任务。

## 可用能力
- click / input / navigate / scroll / wait / evaluate:浏览器基础操作
- **get_playbook_selector(page_name, element_description)**:查询操作手册,获取已知元素的CSS选择器。page_name 取值:zhihu/zhihu_write/zhihu_article/zhihu_search/zhihu_login。element_description 用自然语言描述你要操作的元素(如"发布按钮"、"加粗"、"标题输入框")。
- **execute_playwright_action(selector, action, text?)**:用CSS选择器直接执行操作。action=click/fill/type,text仅fill/type时传。命中手册后比click(index)更快更可靠！
- zhihu_body_input_with_image(html_content="<p>正文</p>", article_topic="标题"):**仅用于写文章正文！** 在知乎写作编辑器(contenteditable)中输入HTML正文并自动生成SVG配图→PNG→Ctrl+V粘贴。⚠️ 评论/搜索/标题/私信等场景禁止使用此工具！
- ask_human_for_intervention:遇到验证码/异常时暂停求助
- 文章正文由你根据主题自行创作(100~200 字)
- 配图:调用 zhihu_body_input_with_image 时自动生成,无需单独配图
- **评论/回复**:用 click 点评论框 → 用 input 工具输入文字 → 用 click 点发送,禁止调 zhihu_body_input_with_image

## 操作手册加速（重要！）
系统已缓存知乎各页面的元素选择器（操作手册）。**遇到需要点击/输入的场景，优先查手册！**

**使用流程（2步）：**
1. get_playbook_selector(page_name="zhihu_write", element_description="加粗") → 返回 CSS 选择器
2. execute_playwright_action(selector="button[aria-label=\"加粗\"]", action="click") → 直接执行

**何时用手册 vs 基础操作：**
- **优先用手册**:当你要操作的元素有明确语义描述时(如"发布按钮"、"加粗"、"标题输入框"、"清除格式")
- **用click(index)**:当手册查不到、或元素是动态内容(如文章列表中的某一篇)时
- **手册查不到时**:返回会列出该页面已收录的元素,你可以从中选择;如果仍然没有,直接用click(index)基础操作

**手册覆盖的页面和常见元素：**
- zhihu_write(写作编辑器,28个元素):加粗/斜体/列表/引用/分割线/代码块/图片/链接/公式/表格/附件/导入/草稿备份/创作助手/标题输入框等
- zhihu_article(文章编辑页,38个元素):同写作编辑器 + 收藏/点赞/评论等
- zhihu(首页,16个元素):创作按钮/通知/私信/头像等
- zhihu_search(搜索页,13个元素):搜索框/返回首页/关闭提示等
- zhihu_login(登录页,6个元素):手机号输入/登录按钮等

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
- **正文输入+配图 - 一步到位！**:
  **第1步**: click(index=正文区域 contenteditable div 的 index) — 必须先点！
  **第2步**: zhihu_body_input_with_image(html_content="<p>段落一</p><p>段落二</p><p>段落三</p>", article_topic="文章标题")
  返回 "OK:N|IMG:..." = 正文+配图都成功 / "OK:N|IMG_FAIL:..." = 正文成功但配图失败 / "E0" = 编辑器没找到 / "E1" = 输入失败
  ⚠️ 正文和配图就靠这个工具！不要用 evaluate 自己写 JS！不要单独调用配图工具！
- 点「发布」按钮后可能弹出成功提示--关掉即可,不需要等待确认
- 关闭发布弹窗后你仍然在编辑页,如果需要回首页,请 navigate 到 https://www.zhihu.com
- 首页顶部有搜索框,输入标题后回车可以搜索文章
- 搜索不到已发布文章时可以尝试滚动浏览结果,或直接 navigate 到文章 URL

## 配图规则
- **配图已内置到正文输入工具中！** 调用 zhihu_body_input_with_image 时会自动生成配图
- 配图是根据 article_topic 和 html_content 内容智能生成的，会提取关键词并匹配配色方案
- 配图流程: SVG生成 → 浏览器Canvas转PNG → 系统剪贴板 → Ctrl+V粘贴到编辑器
- **不要自己写 SVG 代码**，不要用 evaluate 插入图片，不要单独调用配图工具
- 如果返回 IMG_FAIL，可以忽略，正文已成功输入，直接继续发布

## 禁忌
- 禁止给自己的文章/内容点赞(知乎不允许)
- **禁止进创作中心!** 写文章只用 navigate 到 zhuanlan.zhihu.com/write,不要在首页点创作中心按钮
- **禁止用 evaluate 自己写 JS 输入正文或插入图片！只能用 zhihu_body_input_with_image 工具！**
- execute_playwright_action 仅用于手册命中后的点击/输入,不要用它执行复杂JS(用evaluate代替)

## 行为准则
- 读懂用户的自然语言,拆解为先后步骤,逐一执行
- **操作手册优先！** 每到一个新页面，如果有需要点击/输入的元素，**第一步就调用 get_playbook_selector 查手册**。手册命中 → execute_playwright_action 直接执行，跳过 DOM 探索。手册没命中 → 才用 click(index)。
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

WECHAT_SYSTEM_PROMPT = """你是微信桌面自动化助手。你在微信窗口中有两个原子操作能力，通过观察→决策→行动→再观察的循环完成任务。

## 核心规则（必读）

1. **直接行动，不要写计划**：收到任务后**直接调用工具**开始执行，不要先写长篇计划。
2. **每次只调一个工具**：每个回复只调一个工具（ReAct 模式），不要一次调多个。
3. **读完返回再决定下一步**：每步操作后仔细阅读返回的页面状态，根据当前状态决定下一步。
4. **失败就换策略**：同一工具连续返回错误，不要无脑重试——换一种方式、跳过、或汇报给用户。
5. **重复 3 次未成功直接退出**：程序内置了重试计数，同一工具连续失败 3 次程序会自动退出。

## 可用工具

**观察：**
- wechat_observe()
  截图+OCR+分析当前页面。返回【页面】【绿色按钮】【可见文字】【判断】。
  所有其他工具调用后也会自动附带观察结果，一般不需要单独调用。

**原子点击：**
- wechat_click_text(target, n=1)
  在当前截图中 OCR 定位第 n 个匹配文字的区域，并点击它。
  target 可以是任意可见文字：如 "发现" "朋友圈" "赞" "..." "评论" "关注" "私信" "消息" "返回" 等。
  n 默认 1（点击第一个匹配项）；如果有多个同名文字，用 n 指定第几个。
  点击后自动附带 wechat_observe 结果，告诉你页面变成了什么样。

**原子滚动：**
- wechat_scroll(direction, amount)
  在当前窗口内容区滚轮滚动。direction="up" 或 "down"，amount 为滚轮刻度数。
  滚动后自动附带 wechat_observe 结果。

**服务号专用（组合工具）：**
- wechat_search(keyword="名称")
  在微信中 Ctrl+F 打开搜索，输入关键词。返回当前页面状态。
- wechat_click_first_result()
  点击搜索结果第一个条目，进入详情页。返回页面状态。
- wechat_click_button(target="关注"|"私信"|"返回")
  智能定位并点击按钮（模板+颜色+OCR），返回操作结果 + 页面状态。
- wechat_type_and_send(message="内容")
  在聊天窗口中输入消息并发送。返回发送结果 + 页面状态。

## 决策方式

你是真正的 Agent，通过**观察→决策→行动→再观察**循环完成任务：

1. 每步操作后仔细阅读返回的【页面】【绿色按钮】【可见文字】【判断】字段
2. 根据当前页面状态和任务目标，自己决定下一步用什么工具
3. 操作失败时读 OCR 输出判断原因，自己想办法

**重要**：wechat_click_text 和 wechat_scroll 是原子工具——每次只做一件事。
你需要像人一样：看→想→点→看变化→再决定。不要一步到位，要逐步推进。

**导航规则（关键！）：**
- 如果当前页面是「服务号/公众号详情页」或「聊天窗口」→ **第一步必须是 wechat_click_button("返回") 回到主窗口**
- 回到主窗口后，**左侧侧边栏直接找「朋友圈」**——微信 PC 版不需要点「发现」，侧边栏就有朋友圈入口
- 不要试图在详情页/聊天窗口里直接找「朋友圈」，它们不在那里

## 典型任务路径示例

**搜索+关注+私信：**
  1. wechat_search("火眼审阅") → 看返回是"搜索结果"页面 → 下一步
  2. wechat_click_first_result() → 看返回是"详情页" → 下一步
  3. 看【判断】：已关注 → 跳过 4；"尚未关注" → wechat_click_button("关注")
  4. wechat_click_button("私信") → 进入聊天窗口
  5. wechat_type_and_send("消息内容") → 完成

**朋友圈点赞（LLM 自主决策，不是 RPA）：**
  **关键：必须先回到微信主窗口！**
  1. wechat_observe() → 看【页面】是什么
     - 如果是「服务号/公众号详情页」或「聊天窗口」→ **必须先返回主窗口**
     - 用 wechat_click_button("返回") 点左上角返回箭头，回到主窗口
     - 如果一次没回去，再点一次返回，直到【页面】显示「微信主窗口」
  2. 在主窗口 → **左侧侧边栏直接找「朋友圈」图标/文字**
     - wechat_click_text("朋友圈") → 看返回，是否进入了朋友圈
     - 如果侧边栏 OCR 没识别到「朋友圈」→ 尝试滚动侧边栏或用 wechat_scroll 在左侧区域滚动
  3. 在朋友圈 → **不要滚动！第一条动态就在顶部**
  4. wechat_like_moments_post(n=1) → 自动完成：找「两个点」按钮 → 点击 → 点「赞」
  5. 看返回结果：
     - 「已给第1条朋友圈点赞」→ 任务完成
     - 「未找到「赞」按钮」→ 可能已点赞过，或菜单没弹出，尝试 wechat_click_text("赞") 手动点
     - 失败 → 汇报给用户

**每个细节由你决策**：
- 如果当前页面不是主窗口/朋友圈 → **第一步永远是 wechat_click_button("返回") 回到主窗口**
- 微信 PC 主窗口侧边栏**直接有「朋友圈」**，不需要点「发现」
- 点了"..."但菜单没弹出 → 可能是点击位置不准，试试「评论」旁边的区域

## 状态判断关键字

- 「取消关注」→ 已关注 ✅
- 「已关注」→ 已关注 ✅
- 「关注」（无前后缀）→ 尚未关注
- 「朋友圈」「封面」「相册」→ 在朋友圈
- 「微信」「通讯录」「聊天」→ 在主窗口（侧边栏可见）
- 「取消赞」→ 已经点过赞

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
        description="向知乎正文编辑器输入HTML文章内容并自动生成配图。传入html_content='<p>段落1</p><p>段落2</p>...'和article_topic='文章标题'。返回OK:N|IMG:M(正文+配图都成功)或OK:N|IMG_FAIL:...(正文成功配图失败)。正文和配图就靠这个工具写！不要用evaluate自己写JS！"
    )(zhihu_body_input_with_image)
    tools.registry.action(
        description="向知乎正文编辑器输入HTML文章内容（仅正文，不配图）。传入html_content='<p>段落1</p><p>段落2</p>...'。返回OK:N/E0/E1。仅在特殊情况下使用，正常情况下请用zhihu_body_input_with_image。"
    )(zhihu_body_input)
    tools.registry.action(
        description="根据文章主题生成SVG配图并粘贴到编辑器（单独配图工具）。传入article_topic='标题'和article_content='正文内容'。一般不需要单独调用，zhihu_body_input_with_image已内置配图功能。"
    )(generate_and_paste_image)
    tools.registry.action(
        description="查询知乎操作手册，获取页面元素的CSS选择器。传入page_name(如zhihu/zhihu_write/zhihu_article/zhihu_search/zhihu_login)和element_description(自然语言描述你想操作的元素，如'发布按钮'、'标题输入框'、'加粗')。返回CSS选择器供execute_playwright_action使用。优先调用此工具查找已知元素，比逐个点击index更快！"
    )(get_playbook_selector)
    tools.registry.action(
        description="通过CSS选择器直接执行浏览器操作（命中操作手册后的极速执行）。传入selector(CSS选择器)、action(click/fill/type)、text(仅fill/type时需要)。比用click(index)更可靠，因为不受DOM重排影响。"
    )(execute_playwright_action)
    tools.registry.action(
        description="遇到验证码、登录卡住或未知异常时暂停等待人工干预。"
    )(ask_human_for_intervention)
    return tools


async def create_zhihu_agent(task: str) -> Agent:
    """知乎链路:纯LLM决策模式
    
    只提供浏览器基础操作 + 通用工具,所有决策由LLM自行完成。
    应用 Playbook Interception Layer 1+2:
      L1: 执行层拦截 click(index) → 自动查 playbook 用 CSS selector 直接点击
      L2: 规则引擎自动关闭创作助手弹窗,节省 LLM 步骤
    """
    set_agent_mode("zhihu")
    agent = Agent(
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
    # 应用 Playbook Interception Layer 1 + Layer 2
    enhanced_callback = apply_all_patches(agent)
    agent.register_new_step_callback = enhanced_callback
    return agent


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
wechat_click_text_langchain = langchain_tool(wechat_click_text)
wechat_scroll_langchain = langchain_tool(wechat_scroll)
wechat_like_moments_post_langchain = langchain_tool(wechat_like_moments_post)


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
        wechat_click_text_langchain,
        wechat_scroll_langchain,
        wechat_like_moments_post_langchain,
    ]
    agent_graph = _create_tool_agent_executor(
        tools, WECHAT_SYSTEM_PROMPT, max_iterations=20
    )
    return agent_graph, task
