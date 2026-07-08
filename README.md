# Zhihu-Agent-Playbook

基于 browser-use + Playwright 的自动化 Agent，支持两条链路：

- **知乎链路**：登录 → 写文章（配图）→ 发布 → 搜索 → 评论收藏
- **WPS 链路**：启动 WPS 客户端 → 新建文档 → 写内容 → 格式排版 → 保存 .docx + 导出 PDF

**自然语言驱动**——你只需说出你想做什么，Agent 自己理解并完成。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)

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

支持任意 OpenAI 兼容接口（阿里云百炼、SiliconFlow、Ollama、OpenAI 等）。

### 3. 运行

```bash
# 知乎全流程（默认）
python main.py

# 知乎自定义任务
python main.py "帮我在知乎搜一下AI相关文章并收藏"

# WPS 文档生成（自动识别）
python main.py "帮我写篇AI Agent发展趋势的文章排版导出PDF放桌面"
```

---

## 两条链路

### 知乎链路

自然语言驱动。Agent 自动操作浏览器完成：

```
登录知乎 → 打开写文章 → 填标题/正文 → SVG 配图 → 发布 →
回首页搜索 → 找到文章 → 评论 + 收藏 → done
```

**可用工具**：`click`、`input`、`navigate`、`scroll`、`wait`、`evaluate`、`generate_and_insert_svg_image`、`ask_human_for_intervention`

**平台知识**（注入 system_prompt）：

| 要点 | 说明 |
|---|---|
| 写文章入口 | navigate 到 `zhuanlan.zhihu.com/write` |
| 创作助手弹窗 | 先关掉再操作 |
| 正文编辑器 | Draft.js，用 input 填入 |
| 搜索 | 首页顶部搜索框，搜不到直接 navigate 到文章 URL |
| 禁止项 | 自我点赞、微信扫码登录 |

### WPS 链路

自然语言驱动。Agent 调用 WPS COM 接口完成：

```
启动 WPS → 新建文字文档 → 写标题 + 正文 → 设字体/字号/段落/编号 → 保存 .docx → 导出 PDF
```

**唯一工具**：`wps_create_document_and_export_pdf`

**排版参数**（LLM 自动映射）：

| 参数 | 默认值 | 可指定值示例 |
|---|---|---|
| `title_font` | 黑体 | 宋体、微软雅黑、方正小标宋 |
| `title_size` | 小二 (18pt) | 二号、三号、20、20pt、20磅 |
| `heading_font` | 黑体 | 同 title_font |
| `heading_size` | 小三 (15pt) | 同 title_size |
| `body_font` | 宋体 | 同 title_font |
| `body_size` | 小四 (12pt) | 同 title_size |
| `line_spacing` | 28pt | 22、30、1.5倍等 |

**正文 Markdown 支持**：

```markdown
## 小节标题          → 黑体小三加粗
### 子标题           → 同上
一、中文序号标题     → 自动识别为小节标题
引言 / 结语          → 自动识别为小节标题
- 列表项            → 自动编号 + 缩进
**粗体**            → 加粗
*斜体*              → 倾斜
普通段落            → 宋体小四，首行缩进 2 字符，固定行距
```

**意图自动识别**：用户说了「WPS」「文档」「排版」「导出 PDF」「写篇」等关键词自动路由到 WPS 链路；提到「知乎」「搜索」「评论」则走知乎链路。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：意图路由 + 双链路分发
├── config.py                  # LLM API / 浏览器配置 + 输出解析容错
├── .env                       # 环境变量（不提交）
├── .env.example               # 配置模板
├── requirements.txt           # Python 依赖
├── agent/
│   └── core.py                # Agent 工厂：知乎 / WPS 双 Agent + system_prompt
├── tools/
│   ├── wps.py                 # WPS COM 自动化：新建/排版/保存/导出 PDF
│   ├── playbook.py            # 操作手册：DOM 选择器查询 / 缓存执行
│   ├── image_gen.py           # SVG 配图生成 + 知乎编辑器插入
│   ├── human_in_loop.py       # 人机协同：登录/验证码暂停
│   └── auto_memory.py         # 自动记忆：每步后扫描 DOM 写入 playbook
└── memory/
    └── zhihu_playbook.json    # 运行时生成的 DOM 选择器缓存
```

---

## WPS 字号速查

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

## 技术栈

- **browser-use 0.13.3** — Agent 框架
- **Playwright 1.61.0** — 浏览器自动化
- **pywin32** — WPS COM 接口
- **本机 Microsoft Edge** — 复用登录态，无需重复扫码
- **LangChain** — LLM 编排

## License

MIT
