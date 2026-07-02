# Zhihu-Agent-Playbook

> 基于 browser-use + LangChain + Playwright 的知乎自动化 Agent，核心设计理念是**操作手册（Playbook）先验知识库**——首次 DOM 探索后缓存选择器，后续毫秒级执行，拒绝重复造轮子。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 架构理念

```
┌──────────────────────────────────────────────────────┐
│                    Playbook 先验知识库                  │
│  ┌────────────────────────────────────────────┐      │
│  │  zhihu_playbook.json                       │      │
│  │  {                                         │      │
│  │    "知乎首页": {                            │      │
│  │      "写文章按钮": ".WriteIndex-btn",        │      │
│  │      "搜索框": "#Popover1-toggle input"      │      │
│  │    }                                       │      │
│  │  }                                         │      │
│  └────────────────────────────────────────────┘      │
│                                                     │
│   ① 首次执行：Agent → DOM探索 → 找到元素 → 写入手册    │
│   ② 后续执行：Agent → 查手册 → 直接 Playwright → 毫秒级 │
└──────────────────────────────────────────────────────┘
```

**核心理念**：不写死 RPA 脚本，而是让 Agent 自己"学会"操作页面，把经验沉淀到操作手册。手册命中时跳过 LLM 决策环节，直接调用 Playwright 执行，速度提升 **100 倍以上**。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：解析参数、启动 Agent、输出结果
├── config.py                  # 配置：LLM API / 浏览器 / 镜像源
├── requirements.txt           # Python 依赖
├── .gitignore                 # Git 忽略规则
├── agent/
│   └── core.py                # Agent 组装：注册工具、注入策略、启动浏览器
├── tools/
│   ├── playbook.py            # 操作手册工具：查询 / 写入 / 毫秒级执行
│   ├── image_gen.py           # SVG 配图生成：Base64 注入富文本编辑器
│   └── human_in_loop.py       # 人机协同：验证码 / 扫码 / 未知异常暂停
└── memory/
    └── zhihu_playbook.json    # 运行时自动生成的 DOM 选择器库（首次为空）
```

---

## 核心 Tool 详解

| 工具 | 作用 | 调用时机 |
|------|------|----------|
| `get_playbook_selector` | 查询操作手册，返回已缓存的 CSS 选择器 | **每次操作前必调** |
| `save_to_playbook` | DOM 探索成功后，将选择器写入手册 | 首次命中元素后 |
| `execute_playwright_action` | 直接通过 Playwright 点击/输入，跳过 LLM | 手册命中时 |
| `generate_and_insert_svg_image` | 根据主题生成 SVG 配图并注入知乎编辑器 | 写文章时 |
| `ask_human_for_intervention` | 暂停程序等待人工处理（验证码/扫码） | 遇到验证码时 |

### Playbook 工作流

```
用户任务 → Agent 分析下一步操作
                     │
            ┌────────▼────────┐
            │ get_playbook     │
            │ _selector()      │
            └────┬───────┬────┘
            命中 │       │ 未命中
            ┌────▼──┐  ┌─▼──────────┐
            │execute │  │ 浏览器默认工具 │
            │_action │  │ (截图+DOM分析) │
            │   ✨   │  └──┬──────────┘
            │ 毫秒级  │     │ 成功
            └────────┘  ┌──▼──────────┐
                        │ save_to       │
                        │ _playbook()   │
                        │ 写入手册 📝   │
                        └──────────────┘
```

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 推荐 3.11.9 |
| Playwright Chromium | 自带 | `playwright install chromium` |
| LLM API | OpenAI 兼容 | SiliconFlow / 百炼 / Ollama / 任意 |

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

# 或直接编辑 config.py 中的 LLM_API_KEY
```

支持以下 LLM 提供商（OpenAI 兼容接口即可）：

| 提供商 | Base URL | 模型示例 |
|--------|----------|----------|
| SiliconFlow | `https://api.siliconflow.cn` | `Qwen/Qwen2.5-72B-Instruct` |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Ollama 本地 | `http://localhost:11434/v1` | `hermes3` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

### 5. 运行

```bash
# 执行自定义任务
python main.py "登录知乎，写一篇关于AI的文章并发表，然后评论点赞"

# 使用默认测试任务（不传参数）
python main.py
```

---

## 配置说明

`config.py` 支持通过环境变量覆盖所有配置：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `LLM_BASE_URL` | `https://api.siliconflow.cn` | LLM API 地址 |
| `LLM_API_KEY` | `your-api-key-here` | API 密钥 |
| `LLM_MODEL` | `Qwen/Qwen2.5-72B-Instruct` | 模型名称 |
| `BROWSER_USE_DISABLE_EXTENSIONS` | `true` | 禁用扩展下载（加速启动） |

浏览器配置：
- `USE_BUILTIN_CHROMIUM = True` → 使用 Playwright 内置 Chromium（推荐，无冲突）
- `USE_BUILTIN_CHROMIUM = False` → 使用本机 Edge 浏览器（可复用登录态）

---

## 技术栈

```
browser-use 0.13.3     # Agent 框架
├── Playwright 1.61.0  # 浏览器自动化
├── CDP-Use            # Chrome DevTools Protocol
└── bubus              # 事件总线

LangChain              # LLM 编排
├── langchain-openai   # OpenAI 兼容接口
└── langchain-core     # 核心抽象

ChatBrowserUse         # browser-use 内置 LLM 封装（OpenAI 兼容）
```

---

## 设计决策

**Playbook 方案的收益：**
- 首次慢（30s DOM 探索 + LLM 分析），后续快（<50ms Playwright 直连）
- 选择器自动沉淀到 JSON，无需手动维护
- 页面改版后自动降级为 DOM 探索并更新手册