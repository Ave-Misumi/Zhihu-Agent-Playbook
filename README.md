# Zhihu-Agent-Playbook

基于 browser-use + Playwright + LangChain + Windows UI 自动化的多链路 Agent，**自然语言驱动**——你只需说出你想做什么，Agent 自己理解并完成。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.6-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)
[![LangChain](https://img.shields.io/badge/LangChain-1.3.13+-teal.svg)](https://python.langchain.com/)

---

## 四条链路

| 链路 | 能力 | 技术 | 决策方式 |
|---|---|---|---|
| **知乎** | 登录 → 写文章（配图）→ 发布 → 搜索 → 评论收藏 | browser-use + Playwright | LLM 自主决策 |
| **通用浏览器** | 任意网站自动化：BOSS直聘、淘宝、京东、数据抓取、信息分析 | browser-use + Playwright | LLM 自主决策 |
| **WPS** | 启动 WPS → 新建文档 → 写内容 → 排版 → 保存 .docx + 导出 PDF | pywin32 COM | LLM 自主决策 |
| **微信** | 搜索公众号/服务号 → 关注 → 发送私信 | pyautogui + OpenCV + LLM Agent | LLM 自主决策 |

**意图自动识别**：关键词匹配路由，无需手动选择模式。

**所有链路均为 LLM 决策**，非传统 RPA 硬编码流程：
- LLM 自主规划步骤、选择工具、处理异常
- Playbook 仅作为加速缓存（DOM 选择器/坐标/模板），不控制流程

---

## 快速开始

### 1. 环境

```bash
git clone https://github.com/Ave-Misumi/Zhihu-Agent-Playbook.git
cd Zhihu-Agent-Playbook
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

**依赖**：`browser-use[core]`, `langchain-openai`, `langchain-community`, `playwright`, `json_repair`, `pywin32`, `opencv-python`, `pillow`, `numpy`, `easyocr`（首次运行自动安装）。

### 2. 配置

复制 `.env.example` → `.env`，填入 LLM 信息：

```env
LLM_BASE_URL=https://your-api/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=qwen-plus
```

支持任意 OpenAI 兼容接口。

### 3. 运行

```bash
# 知乎全流程（默认）
python main.py

# 知乎自定义
python main.py "帮我在知乎搜一下AI相关文章并收藏"

# WPS 文档（自动识别）
python main.py "帮我写篇AI文章排版导出PDF放桌面"

# WPS 文档（完整排版参数）
python main.py "用WPS写一篇《2026年AI Agent发展趋势》的文章，排版：标题黑体小二加粗居中，小节标题黑体小三加粗，正文宋体小四首行缩进两字符行距28磅，要点数字序号列出，保存word导出PDF放桌面"

# 微信（自动识别）
python main.py "帮我搜索微信服务号火眼审阅并关注发私信:这是一条测试信息"

# 通用浏览器 - BOSS直聘（自动识别）
python main.py "打开BOSS直聘，搜索深圳地区的AI产品经理岗位，分析前5条结果导出PDF报告"

```

---

## 架构概览

```
用户自然语言
    ↓
意图路由 (main.py) —— 关键词匹配 → 知乎 / 浏览器 / WPS / 微信
    ↓
┌─────────────┬─────────────┬─────────────┬─────────────┐
│  知乎链路    │ 浏览器链路   │  WPS 链路   │  微信链路   │
│ browser-use │ browser-use │ LangChain   │ LangChain   │
│  Agent      │  Agent      │ Tool Agent  │ Tool Agent  │
│ + Playbook  │ + PDF导出   │ + 模板缓存  │ + CV视觉    │
└─────────────┴─────────────┴─────────────┴─────────────┘
    ↓                    ↓           ↓           ↓
Playwright          Playwright    pywin32    pyautogui+OpenCV
```

**核心设计原则**：
1. **LLM 是决策者** —— 所有链路均由 LLM 自主规划步骤、选择工具、处理异常
2. **Playbook 是加速器** —— 缓存高频操作的选择器/坐标/模板，命中时跳过探索，未命中时 LLM 自主探索
3. **工具是原子操作** —— 每个工具只做一件事，复杂流程由 LLM 组合决策

---

## 知乎链路

```
登录知乎 → 写文章 → SVG 配图 → 发布 → 搜索 → 评论 + 收藏 → done
```

**平台知识**：写文章入口 `zhuanlan.zhihu.com/write`、创作助手弹窗关闭、Draft.js 编辑器 input 填入、禁止自我点赞和微信扫码登录。

### 知乎 Playbook（DOM 选择器缓存）

**痛点**：浏览器自动化最慢的步骤是 DOM 探索——每次操作都要截图 → LLM 分析 → 在 `selector_map` 里翻找合适的元素，单步 10s+。

**缓存内容**：按页面（`zhihu_write` / `zhihu_search` / `zhihu_article` …）分组，存储 `元素描述 → CSS 选择器` 键值对到 `memory/zhihu_playbook.json`。

**工作流程**：
1. **自动学习**（`tools/auto_memory.py`）：通过 `register_new_step_callback` 钩子，每步操作完成后自动扫描当前页的 `selector_map`，提取高价值元素（按钮/输入框/链接）的选择器写入 playbook。
2. **模糊检索**（`tools/playbook.py`）：LLM 用自然语言描述要操作的元素 → 中文分词 + Jaccard 相似度 + 子串匹配 + 中文字数加权 → 得分 ≥0.20 直接返回选择器。多候选时列出前 5 个让 LLM 挑选。
3. **瞬发执行**（`execute_playwright_action`）：缓存命中后绕过 browser-use 的截图→LLM→探索循环，直接用 Playwright `page.locator(selector).click()`，单步从 10s+ 降至毫秒级。

---

## 通用浏览器链路

```
导航到网站 → 搜索/浏览 → 提取信息 → 分析总结 → 导出PDF报告 → done
```

**适用场景**：BOSS直聘、淘宝、京东、任意网页的数据抓取和分析任务。

**链路特点**：
- 与知乎链路共用同一套 `browser-use` Agent 工厂
- 系统提示词针对通用网页任务优化（信息提取、翻页策略、报告生成）
- 内置 `wps_create_document_and_export_pdf` 工具，支持将分析结果导出为 PDF

### BOSS直聘示例

```bash
python main.py "打开BOSS直聘，搜索深圳地区的AI产品经理岗位，分析前5条结果导出PDF报告"
```

**执行流程**：
1. 导航到 `zhipin.com`
2. 搜索"AI产品经理"+"深圳"
3. 逐个点击职位进入详情页，提取完整信息
4. 分析薪资范围、经验要求、岗位职责
5. 调用 WPS 工具生成 PDF 报告

---

## WPS 链路

```
启动 WPS → 新建文档 → 写标题+正文 → 排版 → 保存 .docx + 导出 PDF
```

### 排版参数

| 参数 | 默认值 | 可指定值 |
|---|---|---|
| `title_font` | 黑体 | 宋体、微软雅黑、方正小标宋 |
| `title_size` | 小二 (18pt) | 二号、三号、20pt、20磅 |
| `heading_font` | 黑体 | 同上 |
| `heading_size` | 小三 (15pt) | 同上 |
| `body_font` | 宋体 | 同上 |
| `body_size` | 小四 (12pt) | 同上 |
| `line_spacing` | 28pt | 22、30、1.5倍等 |

### Markdown 正文

```markdown
## 小节标题 / 一、中文标题 / 引言 / 结语  → 黑体小三加粗
- 列表项                                → 自动编号 + 缩进
**粗体**  *斜体*                        → 加粗 / 倾斜
普通段落                                → 宋体小四，首行缩进 2 字符，固定行距
```

### WPS Playbook（模板缓存）

**痛点**：LLM 每次从零创作文档——决定标题层级、选择字体字号、组织章节结构——这些"格式决策"高度可复用，重复生成浪费 tokens 且风格不稳定。

**缓存内容**：保存到 `memory/wps_templates.json`，每条记录包含：
- `type`：自动识别的文档类型（周报/会议纪要/通知/简历/计划/文章/报告）
- `font`：标题字体/字号 + 小节标题字体/字号 + 正文字体/字号 + 行距
- `skeleton`：章节标题列表（从 `## 标题` / `一、标题` 提取的结构骨架）
- `content_md`：完整 Markdown 正文（用于同类型文档参考）

**工作流程**：
1. **自动保存**：每次 `wps_create_document_and_export_pdf` 成功后自动调用 `save_wps_template()`。
2. **类型识别**：从标题和正文自动推断文档类型——标题含"周报"→ 周报，含"会议"→ 会议纪要，含"趋势/浅谈/如何"→ 文章（先于报告），含"报告/分析/研究"→ 报告。
3. **复用查询**：下次生成文档时 LLM 调用 `get_wps_template(doc_type)` 查缓存，命中后直接复用排版参数和章节骨架，LLM 只需填充具体内容段落。

**收益**：同类文档从"全量创作"变为"骨架填空"，LLM 输出量减半，排版风格保持稳定。

---

## 微信链路

```
启动微信 → 搜索公众号/服务号 → 关注 → 发送私信 → done
```

### 架构：LLM Agent + CV

微信链路采用 **LLM Agent 决策 + CV 视觉感知** 的分层架构：

```
用户自然语言
     ↓
LLM（决策者）→ 调用工具 → 观察结果 → 决定下一步
     ↓ 调用工具
原子工具（截图 + OCR + 颜色检测 + 执行）
     ↓ 返回自然语言描述
LLM 阅读描述 → 进入下一步
```

- **LLM 不看像素** —— 只阅读 CV 系统提取的文字描述和按钮列表
- **CV 不做决策** —— 只负责截图、OCR、颜色检测、模板匹配，生成结构化描述
- **失败不崩溃** —— 工具返回错误描述让 LLM 决策重试或跳过

### 可用工具

| 工具 | 作用 | 说明 |
|---|---|---|
| `wechat_observe` | 截图 + OCR + 分析 | 返回页面类型、可见按钮、关键词、LLM 友好建议 |
| `wechat_search(keyword)` | 搜索关键词 | Ctrl+F → 粘贴 → 双 Enter（选建议 + 跳转结果） |
| `wechat_click_first_result` | 点击第一条搜索结果 | Playbook 优先 → 视觉找「服务号」标签 → 向下偏移 60px 点击卡片 |
| `wechat_click_button(target)` | 点击关注/私信/返回 | 视觉定位 + 颜色+OCR 双验证 |
| `wechat_type_and_send(message)` | 输入并发送消息 | 自动定位输入框 → 粘贴文字 → Enter 发送 |
| `wechat_click_text(target, n=1)` | 点击指定文字 | OCR 定位第 n 个匹配文字并点击 |
| `wechat_scroll(direction, amount)` | 滚动页面 | 方向 up/down，滚轮刻度数 |
| `wechat_like_moments_post(n=1)` | 朋友圈点赞 | 自动找「两个点」按钮 → 点「赞」 |

### 视觉定位策略

新版微信 Qt 渲染，UIA 控件树为全空容器，所有定位依赖截图 + OpenCV：

#### 关注按钮（绿色）
1. Playbook 缓存 → 直接点击
2. 模板匹配（`follow_button.png`，conf≥0.75）
3. 颜色+OCR 双验证（HSV 绿色区域 + OCR 含「关注」文字，上半区 55%）
4. 纯颜色检测
5. 纯 OCR 全图搜索（conf≥0.25）

#### 私信按钮（非绿色）
1. Playbook 缓存 → 直接点击
2. OCR 搜索关键词（「私信」「发消息」「进入公众号」「聊天」，conf≥0.25）

#### 键盘图标（右下角）
1. Playbook 缓存
2. 模板匹配（`keyboard_toggle.png`，conf≥0.65）
3. 右下角 OCR 搜索（「键盘」「切换」「输入」）
4. 右下角轮廓检测（15-60px 方形，面积≥200）
5. 人工辅助

#### 输入框
1. Playbook 缓存
2. 白色矩形检测（底部亮色区域，宽度>窗口 50%，高度 40-150px）
3. 「发送」按钮左侧 40% 中央
4. 提示文字（「可以描述」「描述任务」「输入」，conf≥0.2）
5. 人工辅助

#### 搜索结果
1. Playbook 缓存
2. OCR 找「服务号」标签 → 向下偏移 60px → 居中点击卡片
3. 人工辅助

### 关键机制

#### 重试计数器
每个工具独立计数，同一操作连续失败 **3 次**返回 `[FATAL]` 报错退出，防止 LLM 死循环。

#### 人工辅助接管
自动定位 3 次失败后，弹出 60 秒人工辅助窗口：
- 用 `GetAsyncKeyState` 轮询检测鼠标左键
- 点击位置自动转为客户区坐标并写入 Playbook，下次可直接复用

#### 关注状态检测
- OCR 检测「已关注」「取消关注」→ 视为已关注
- 绿色按钮无「关注」文字 → 视为已关注（按钮已变灰/消失）
- 点击关注后自动检测「取消关注」确认弹窗 → 自动 Esc 关闭

#### 消息发送验证
- 初始等待 2 秒（消息渲染动画）
- 重试 3 次，间隔 1.5 秒
- 策略 A：全量 OCR（conf≥0.05）匹配消息文字
- 策略 B：HSV 绿色气泡检测（Hue 35-85，绿色占比>0.5%）

#### 严格失败策略
任意视觉定位失败 → 返回明确错误描述 + OCR dump，绝不静默点到错误位置。**所有硬编码坐标已全部移除**。

### 微信 Playbook（坐标缓存）

**缓存 6 类位置**：`search_result`（搜索结果卡片）、`follow`（关注按钮）、`send_msg`（私信按钮）、`keyboard_toggle`（键盘图标）、`input_box`（输入框）。保存到 `memory/wechat_playbook.json`。

**容错机制**：
- 百分比坐标（`x_pct, y_pct`），解决窗口缩放差异
- **30 天过期**自动失效
- **窗口尺寸偏差 >15%** 自动失效
- 缓存命中直接 `pyautogui.click()`，完全跳过截图+OCR

**收益**：首次运行 ~6s（截图+OCR），二次运行 ~0.3s（playbook 直接点击），提速 **20 倍**。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                     # 入口：意图路由 + 四链路分发 + Token 统计
├── config.py                   # LLM API / BridgeLLM 适配 / Token 统计
├── .env / .env.example         # 环境变量
├── requirements.txt            # Python 依赖
├── agent/
│   └── core.py                 # Agent 工厂：知乎 / 浏览器 / WPS / 微信 + system_prompt
├── tools/
│   ├── wps.py                  # WPS COM：新建/排版/保存/导出 PDF
│   ├── wps_playbook.py         # WPS 模板缓存：排版参数+内容骨架复用
│   ├── wechat_agent.py         # 微信 Agent 层：8 个原子工具 + 重试/人工辅助/Playbook
│   ├── wechat_vision.py        # 微信视觉辅助：截图/模板匹配/颜色检测/OCR
│   ├── wechat_verify.py        # 微信流程验证器：每步截图+OCR 断点验证
│   ├── wechat_ocr.py           # 微信聊天气泡文字预处理（CLAHE+二值化）
│   ├── wechat_playbook.py      # 微信 Playbook：6 类百分比坐标缓存，30 天自动失效
│   ├── save_wechat_template.py # 微信按钮模板保存工具
│   ├── playbook.py             # 知乎 DOM 选择器查询/缓存执行
│   ├── playbook_intercept.py   # 知乎 Playbook 拦截：自动注入选择器加速
│   ├── auto_memory.py          # 自动记忆：每步后扫描 DOM 写入 playbook
│   ├── image_gen.py            # SVG 配图生成 + 编辑器插入
│   ├── zhihu_body.py           # 知乎正文输入（HTML + 自动配图）
│   └── human_in_loop.py        # 人机协同：登录/验证码暂停
├── assets/
│   └── wechat_templates/       # 微信按钮模板（关注/私信/键盘图标）
├── memory/
│   ├── zhihu_playbook.json     # 知乎 DOM 选择器缓存
│   ├── wechat_playbook.json    # 微信坐标缓存（6 类位置，百分比坐标）
│   └── wps_templates.json      # WPS 模板缓存
└── debug_screenshots/          # 验证失败自动截图（调试用，定期清理）
```

---

## 技术栈

- **browser-use 0.13.6** — 浏览器自动化 Agent 框架（LLM 自主决策）
- **Playwright 1.61.0** — 浏览器自动化执行引擎
- **LangChain 1.3.13+** — LLM 调用与 Tool Calling Agent（`create_agent` / LangGraph StateGraph）
- **pywin32** — WPS COM 接口、Win32 窗口枚举与消息
- **pyautogui** — 微信 UI 物理键盘鼠标模拟（Qt 渲染兼容）
- **OpenCV 5.0** — 微信视觉定位（截图、模板匹配、颜色检测、轮廓检测）
- **EasyOCR / PaddleOCR** — 微信界面文字识别
- **本机 Microsoft Edge** — 浏览器链路复用登录态

---

## 字号速查

| 号数 | pt | 号数 | pt |
|---|---|---|---|
| 初号 | 42 | 小初 | 36 |
| 一号 | 26 | 小一 | 24 |
| 二号 | 22 | **小二** | **18** |
| 三号 | 16 | **小三** | **15** |
| 四号 | 14 | **小四** | **12** |
| 五号 | 10.5 | 小五 | 9 |

（加粗 = 默认值）

---

## License

MIT
