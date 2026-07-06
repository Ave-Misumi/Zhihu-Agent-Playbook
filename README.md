# Zhihu-Agent-Playbook

基于 browser-use + LangChain + Playwright 的知乎自动化 Agent。自动登录、撰写文章、配图、发布、搜索、评论与收藏。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)

---

## 核心设计

传统 RPA 写死 XPath，页面一改就失效。本项目的核心是**自学习操作手册（Playbook）**：

- **首次执行**：Agent 探索 DOM → 定位元素 → 运行中自动写入 `memory/zhihu_playbook.json`
- **后续执行**：查手册 → CSS 选择器命中 → 毫秒级 Playwright 执行

Agent 内置 **auto_memory** 机制：每步操作后自动扫描页面可交互元素（按钮、输入框等），将 CSS 选择器持久化到 playbook，无需手动记录。

---

## 功能流程

```
登录知乎 → 写文章(标题+正文+SVG配图) → 发布 → 搜索文章 → 评论+收藏
```

- 所有操作由 LLM 驱动，自动完成
- 遇到验证码 / 扫码登录时，调用 `ask_human_for_intervention` 暂停等待人工处理
- 严格禁止：给自己文章点赞（知乎限制）、微信扫码登录

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                      # 入口：构建任务、启动 Agent
├── config.py                    # 配置：LLM 接入 / 浏览器 / BridgeLLM 适配层
├── .env                         # 环境变量（API Key，不提交）
├── .env.example                 # 环境变量样例
├── .gitignore
├── requirements.txt             # Python 依赖
├── article_content.md           # 预写文章正文（可选，LLM 直接复制）
│
├── agent/
│   └── core.py                  # Agent 组装：注册工具、注入 system_prompt
│
├── tools/
│   ├── playbook.py              # 操作手册：查询 / 写入 / 待执行（四级模糊匹配）
│   ├── image_gen.py             # SVG 配图生成并注入编辑器
│   ├── human_in_loop.py         # 人机协同：验证码 / 扫码时暂停
│   └── auto_memory.py           # 运行时自动收集页面元素选择器
│
├── memory/
│   ├── zhihu_playbook.json      # 自动生成的选择器库
│   └── auto_memory_log.jsonl    # 选择器收集日志
│
├── test_*.py                    # 各阶段测试文件
├── fixes_*.md                   # 修复记录
└── DESIGN.pdf / DESIGN.md       # 架构文档
```

---

## 核心工具

| 工具 | 作用 |
|------|------|
| `generate_and_insert_svg_image` | 根据文章主题生成 SVG 配图，通过 JS 注入知乎富文本编辑器 |
| `ask_human_for_intervention` | 暂停 Agent，等待人工完成扫码 / 验证码后按 Enter 继续 |
| `get_playbook_selector` | 查询 playbook 中缓存的 CSS 选择器（支持四级模糊匹配） |
| `save_to_playbook` | 手动将选择器写入 playbook（auto_memory 已自动收集，一般不需要手动调用） |
| `execute_playwright_action` | 直接 Playwright 点击 / 输入（命中 playbook 后极速执行） |

---

## 快速开始

### 1. 环境要求

- Python 3.11+
- Microsoft Edge（本项目默认复用本机 Edge 的 Cookie，无需重复登录）

### 2. 克隆仓库

```bash
git clone https://github.com/Ave-Misumi/Zhihu-Agent-Playbook.git
cd Zhihu-Agent-Playbook
```

### 3. 创建虚拟环境并安装依赖

```bash
python -m venv venv
.\venv\Scripts\activate      # Windows
# source venv/bin/activate   # macOS / Linux

pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m playwright install chromium
```

### 4. 配置环境变量

```bash
copy .env.example .env
```

编辑 `.env`，填入 LLM 接入信息：

```env
LLM_BASE_URL=https://your-api-endpoint.com/compatible-mode/v1
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=your-model-name
```

支持任意 OpenAI 兼容接口（阿里云百炼、SiliconFlow、Ollama、OpenAI 等）。

### 5. 运行

```bash
# 默认任务（读取 article_content.md 作为文章正文）
python main.py

# 自定义任务
python main.py "登录知乎，写一篇关于AI的文章并发表"
```

---

## BridgeLLM 适配层

`config.py` 中的 `BridgeLLM` 将 langchain 的 `ChatOpenAI` 包装为 browser-use 可接受的 LLM 对象。非 OpenAI 模型（如 GLM-5.1、Qwen-3.6）的 JSON 输出格式不标准，BridgeLLM 做以下容错：

- **多级 JSON 提取**：`<tool_call>` XML 解析 → `{}` 括号计数 → `_pre_repair_json` token 级修复
- **自动补全**：`_auto_close_json` 补全未闭合引号和大括号
- **字段修复**：`_sanitize_actions` 自动处理缺失字段、类型错误、幻觉工具名映射
- **重复 key 合并**：LLM 偶发输出两个 `action` 字段，自动合并

---

## Playbook 模糊匹配

`tools/playbook.py` 使用四级 fallback 策略查找选择器：

1. **精确匹配** — key 完全一致
2. **模糊单候选** — `_fuzzy_match` 找到唯一高分项
3. **模糊多候选** — 分数接近的候选项一并返回
4. **全 key 列表** — 无匹配时返回所有可用 key，供 LLM 自行选择

---

## 配置说明

| 变量 | 说明 |
|------|------|
| `LLM_BASE_URL` | LLM API 端点（OpenAI 兼容格式） |
| `LLM_API_KEY` | API 密钥 |
| `LLM_MODEL` | 模型名称 |

---

## 技术栈

- **browser-use 0.13.3** — Agent 框架（`Agent` + `BrowserSession` + `Tools`）
- **Playwright 1.61.0** — 浏览器自动化底层
- **LangChain** — LLM 编排（`ChatOpenAI` → `BridgeLLM`）
- **json_repair** — 容错 JSON 解析
- **本机 Microsoft Edge** — 复用已登录 Cookie

---

## License

MIT
