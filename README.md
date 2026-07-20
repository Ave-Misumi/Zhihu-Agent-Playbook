# Zhihu-Agent-Playbook

基于 browser-use + Playwright + Windows UI 自动化的多链路 Agent，**自然语言驱动**——你只需说出你想做什么，Agent 自己理解并完成。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)
[![LangChain](https://img.shields.io/badge/LangChain-1.3.13+-teal.svg)](https://python.langchain.com/)

---

## 三条链路

| 链路 | 能力 | 技术 |
|---|---|---|
| **知乎** | 登录 → 写文章（配图）→ 发布 → 搜索 → 评论收藏 | browser-use + Playwright |
| **WPS** | 启动 WPS → 新建文档 → 写内容 → 排版 → 保存 .docx + 导出 PDF | pywin32 COM |
| **微信** | 启动微信 → 搜索公众号/服务号 → 关注 → 发送私信 | pyautogui + OpenCV 视觉定位 |

意图自动识别：关键词匹配路由，无需手动选择模式。

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
```

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
1. **自动学习**（`tools/auto_memory.py`）：通过 `register_new_step_callback` 钩子，每步操作完成后自动扫描当前页的 `selector_map`，提取高价值元素（按钮/输入框/链接）的选择器写入 playbook。无需 LLM 手动调用。
2. **模糊检索**（`tools/playbook.py`）：LLM 用自然语言描述要操作的元素 → 中文分词 + Jaccard 相似度 + 子串匹配 + 中文字数加权 → 得分 ≥0.20 直接返回选择器，多候选时列出前 5 个让 LLM 挑选。
3. **瞬发执行**（`execute_playwright_action`）：缓存命中后绕过 browser-use 的截图→LLM→探索循环，直接用 Playwright `page.locator(selector).click()`，单步从 10s+ 降至毫秒级。

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

### 核心技术

纯 Win32 + pyautogui 操作微信桌面客户端，**无需扫码（依赖微信已登录状态）**，无需 admin 权限。

- **窗口管理**：`FindWindowW` / `SetForegroundWindow` 枚举和切换微信窗口。
- **键盘输入**：`Ctrl+F` 打开搜索 → 剪贴板粘贴关键词 → 回车搜索。
- **鼠标点击**：pyautogui 物理点击模拟，定位搜索结果和按钮。

### 视觉定位（v2）

新版微信 Qt 渲染，UIA 控件树为全空容器（无法读取界面文字），所有定位依赖截图 + OpenCV：

| 步骤 | 视觉策略 |
|---|---|
| 搜索结果 | 截图 → OCR 检测服务号名称 |
| 关注按钮 | 模板匹配 → 颜色+OCR 双验证（绿色区域 + 含「关注」文字）→ 纯颜色检测 |
| 私信按钮 | 模板匹配 → 颜色+OCR 双验证 → 纯颜色检测 |
| 输入框区域 | OCR 找切换入口 → 亮色矩形轮廓检测（宽高比 2-20） |

**严格失败策略**：任意一步视觉定位失败 → 直接抛出明确错误，杜绝静默点到错误位置。

**按钮模板**（可选，提升定位精度）：

```bash
# 列出微信窗口
python tools/save_wechat_template.py --list

# 进入服务号详情页后截取模板
python tools/save_wechat_template.py <hwnd> 180 340 80 40 follow_button.png
python tools/save_wechat_template.py <hwnd> 330 340 80 40 send_msg_button.png
```

模板保存到 `assets/wechat_templates/`，下次运行优先匹配。

### 流程验证器（`tools/wechat_verify.py`）

Qt 微信是全黑盒——无法轮询 DOM、无法读取属性——所有状态确认都靠截图 + OCR 区域检测：

| 验证点 | 检测方式 | 失败处理 |
|---|---|---|
| 搜索结果可见 | ROI OCR（关键词/服务号/公众号） | 返回 ❌ 原因 + OCR dump |
| 详情页进入 | ROI OCR（关注/已关注/私信/全部/贴图） | 同上 |
| 关注成功 | 上半区 OCR（已关注/发消息/私信） | 微调 y+20 重试一次 |
| 聊天窗口进入 | 底部 OCR（产品介绍/操作视频/发送）+ 详情特征消失判断 | 明确区分"仍在详情页" vs "已进入" |
| 输入框可见 | OCR 宽泛 → Canny 边缘水平线检测 → 亮度差异比对 | 三重策略兜底 |
| 消息发送成功 | 原始图 OCR → 气泡预处理（CLAHE+锐化+二值化）→ 自适应阈值 | 右半区聚焦 + 短文本片段匹配 |

失败时自动保存 `debug_screenshots/verify_fail_*.png`，标注 ROI 范围和实际 OCR 文字。

### 微信 Playbook（坐标缓存）

**痛点**：微信视觉定位每次要截图 + 颜色检测 + OCR 验证，单步 2-3s。同一个服务号反复操作时，按钮位置完全不变——重复视觉检测纯属浪费。

**缓存内容**：保存到 `memory/wechat_playbook.json`，每条记录包含：
- `search_result`：搜索结果中目标服务号的点击位置（客户区百分比坐标）
- `follow` / `send_msg`：关注/私信按钮位置（百分比坐标）
- `detail_window`：详情窗口特征（标题关键词、最小宽高）
- `client_w` / `client_h`：缓存时的窗口尺寸（用于尺寸校验）

**工作流程**：
1. **查询优先**：`_click_first_search_result` 和两个按钮点击函数均以 `lookup_*` 开头——缓存命中直接 `_click_at`，完全跳过截图+OCR（2-3s → 毫秒级）。
2. **自动保存**：视觉定位成功后自动调用 `save_*` 写入缓存。
3. **容错机制**：
   - **30 天过期**：超过 30 天未更新的缓存自动失效。
   - **尺寸校验**：当前窗口与缓存的尺寸偏差 >15% → 自动失效（避免拉伸导致坐标偏移）。
   - **失败记录**：`save_search_miss()` 记录 OCR 搜索失败次数，防止反复走慢速视觉流程却无果。

**收益**：首次运行某服务号 ~6s（截图+OCR），二次运行 ~0.3s（playbook 直接点击），提速 20 倍。

**百分比坐标**：存储 `(x_pct, y_pct)` 而非绝对像素，解决用户调整微信窗口大小后的坐标失效问题。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：意图路由 + 三链路分发
├── config.py                  # LLM API / 输出解析容错（含 BridgeLLM）
├── .env / .env.example        # 环境变量
├── requirements.txt           # Python 依赖
├── agent/
│   └── core.py                # Agent 工厂：知乎 / WPS / 微信 + system_prompt
├── tools/
│   ├── wps.py                 # WPS COM：新建/排版/保存/导出 PDF
│   ├── wps_playbook.py        # WPS 模板缓存：排版参数+内容骨架复用
│   ├── wechat.py              # 微信自动化：搜索/关注/发私信
│   ├── wechat_vision.py       # 微信视觉辅助：截图/模板匹配/颜色检测/OCR
│   ├── wechat_verify.py       # 微信流程验证器：每步截图+OCR 断点验证
│   ├── wechat_ocr.py          # 微信聊天气泡文字预处理（CLAHE+二值化）
│   ├── wechat_playbook.py     # 微信 Playbook：百分比坐标缓存 30 天自动失效
│   ├── save_wechat_template.py # 微信按钮模板保存工具
│   ├── playbook.py            # 知乎 DOM 选择器查询/缓存执行
│   ├── image_gen.py           # SVG 配图生成 + 编辑器插入
│   ├── human_in_loop.py       # 人机协同：登录/验证码暂停
│   └── auto_memory.py         # 自动记忆：每步后扫描 DOM 写入 playbook
├── assets/
│   └── wechat_templates/      # 微信按钮模板（关注/私信）
├── memory/
│   ├── zhihu_playbook.json    # 知乎 DOM 选择器缓存
│   ├── wechat_playbook.json   # 微信搜索结果+按钮坐标缓存
│   └── wps_templates.json     # WPS 模板缓存
└── debug_screenshots/         # 验证失败自动截图（调试用，定期清理）
```

---

## 技术栈

- **browser-use 0.13.3** — 浏览器自动化 Agent 框架
- **Playwright 1.61.0** — 浏览器自动化执行引擎
- **LangChain 1.3.13+** — LLM 调用与 ReAct Agent（已升级至 `create_agent` / LangGraph）
- **pywin32** — WPS COM 接口、Win32 窗口枚举与消息
- **pyautogui** — 微信 UI 物理键盘鼠标模拟（Qt 渲染兼容）
- **OpenCV 5.0** — 微信视觉定位（截图、模板匹配、颜色检测）
- **本机 Microsoft Edge** — 复用登录态

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
