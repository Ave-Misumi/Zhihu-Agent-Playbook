# Zhihu-Agent-Playbook

基于 browser-use + LangChain + Playwright 的知乎自动化 Agent，核心设计理念是**操作手册（Playbook）先验知识库**——首次 DOM 探索后缓存选择器，后续毫秒级执行。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)

---

## 核心设计理念

传统 RPA 写死 XPath，页面一改就失效。本项目采用**自学习 Playbook**：

- **首次执行**：Agent 探索 DOM → 找到元素 → 写入 `zhihu_playbook.json`
- **后续执行**：查手册 → 直接 Playwright → 毫秒级响应

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：解析参数、启动 Agent
├── config.py                  # 配置：LLM API / 浏览器
├── .env                       # 环境变量（API Key 等，不提交）
├── .env.example               # 环境变量配置示例
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

| 工具 | 作用 |
|------|------|
| `get_playbook_selector` | 查询手册，返回已缓存的 CSS 选择器 |
| `save_to_playbook` | DOM 探索成功后写入手册 |
| `execute_playwright_action` | 直接 Playwright 点击/输入 |
| `generate_and_insert_svg_image` | 生成 SVG 配图并注入编辑器 |
| `ask_human_for_intervention` | 暂停等待人工处理验证码/扫码 |

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
.\venv\Scripts\activate     # Windows
# source venv/bin/activate  # macOS/Linux
```

### 3. 安装依赖

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 配置环境变量

复制配置示例文件并填入真实信息：

```bash
copy .env.example .env
```

编辑 `.env`，填入 LLM API 信息：

```env
LLM_BASE_URL=https://your-api-endpoint.com/v1
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=your-model-name
```

支持的 LLM 类型：任意 OpenAI 兼容接口（如阿里云百炼、SiliconFlow、Ollama 本地、OpenAI 等）。

### 5. 配置浏览器

本项目默认使用**本机 Microsoft Edge**，复用已登录状态，无需重复扫码。

浏览器默认路径已配置好，通常无需修改。

### 6. 运行

```bash
# 自定义任务
python main.py "登录知乎，写一篇关于AI的文章并发表"

# 默认测试任务
python main.py
```

---

## 配置说明

所有配置通过 `.env` 文件管理：

| 变量 | 说明 |
|------|------|
| `LLM_BASE_URL` | LLM API 端点（OpenAI 兼容） |
| `LLM_API_KEY` | API 密钥 |
| `LLM_MODEL` | 模型名称 |

---

## 技术栈

- **browser-use 0.13.3** — Agent 框架
- **Playwright 1.61.0** — 浏览器自动化
- **LangChain** — LLM 编排
- **本机 Microsoft Edge** — 浏览器

---

## License

MIT
