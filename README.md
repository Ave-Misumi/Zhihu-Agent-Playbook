# Zhihu-Agent-Playbook

基于 browser-use + LangChain + Playwright 的知乎自动化 Agent，核心设计理念是**操作手册（Playbook）先验知识库**——首次 DOM 探索后缓存选择器，后续毫秒级执行，拒绝重复造轮子。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)

---

## 核心设计理念

传统 RPA 写死 XPath，页面一改就失效。本项目采用**自学习 Playbook**：

- **首次执行**：Agent 探索 DOM → 找到元素 → 写入 `zhihu_playbook.json`
- **后续执行**：查手册 → 直接 Playwright → 毫秒级响应

速度提升 **100 倍以上**，且无需人工维护选择器。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：解析参数、启动 Agent
├── config.py                  # 配置：LLM API / 浏览器
├── requirements.txt           # Python 依赖
├── agent/
│   └── core.py                # Agent 组装：注册工具、注入策略
├── tools/
│   ├── playbook.py            # 操作手册：查询 / 写入 / 毫秒级执行
│   ├── image_gen.py           # SVG 配图生成
│   └── human_in_loop.py       # 人机协同：验证码 / 扫码暂停
└── memory/
    └── zhihu_playbook.json    # 运行时生成的 DOM 选择器库
```

---

## 核心工具

| 工具 | 作用 | 调用时机 |
|------|------|----------|
| `get_playbook_selector` | 查询手册，返回已缓存的 CSS 选择器 | 每次操作前 |
| `save_to_playbook` | DOM 探索成功后写入手册 | 首次命中元素后 |
| `execute_playwright_action` | 直接 Playwright 点击/输入 | 手册命中时 |
| `generate_and_insert_svg_image` | 生成 SVG 配图并注入编辑器 | 写文章时 |
| `ask_human_for_intervention` | 暂停等待人工处理 | 验证码/扫码时 |

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/Ave-Misumi/Zhihu-Agent-Playbook.git
cd Zhihu-Agent-Playbook
```

### 2. 创建虚拟环境

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # macOS/Linux
```

### 3. 安装依赖

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
playwright install chromium
```

### 4. 配置 API Key

```bash
# Windows PowerShell
$env:LLM_API_KEY = "sk-your-api-key-here"

# 或编辑 config.py 中的 LLM_API_KEY
```

支持任意 OpenAI 兼容接口：

| 提供商 | Base URL | 模型 |
|--------|----------|------|
| SiliconFlow | `https://api.siliconflow.cn` | `Qwen/Qwen2.5-72B-Instruct` |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama 本地 | `http://localhost:11434/v1` | `hermes3` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

### 5. 运行

```bash
# 自定义任务
python main.py "登录知乎，写一篇关于AI的文章并发表"

# 默认测试任务
python main.py
```

---

## 配置说明

`config.py` 支持环境变量覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `https://api.siliconflow.cn` | LLM API 地址 |
| `LLM_API_KEY` | `your-api-key-here` | API 密钥 |
| `LLM_MODEL` | `Qwen/Qwen2.5-72B-Instruct` | 模型名称 |

浏览器配置：
- `USE_BUILTIN_CHROMIUM = True` → Playwright 内置 Chromium（推荐）
- `USE_BUILTIN_CHROMIUM = False` → 本机 Edge（可复用登录态）

---

## 技术栈

- **browser-use 0.13.3** — Agent 框架
- **Playwright 1.61.0** — 浏览器自动化
- **LangChain** — LLM 编排
- **ChatBrowserUse** — OpenAI 兼容 LLM 封装

---

## License

MIT
