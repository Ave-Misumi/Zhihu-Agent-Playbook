# Zhihu-Agent-Playbook

基于 browser-use + Playwright + Windows UI 自动化的多链路 Agent，**自然语言驱动**——你只需说出你想做什么，Agent 自己理解并完成。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![browser-use](https://img.shields.io/badge/browser--use-0.13.3-green.svg)](https://github.com/browser-use/browser-use)
[![Playwright](https://img.shields.io/badge/Playwright-1.61.0-orange.svg)](https://playwright.dev/)

---

## 三条链路

| 链路 | 能力 | 技术 |
|---|---|---|
| **知乎** | 登录 → 写文章（配图）→ 发布 → 搜索 → 评论收藏 | browser-use + Playwright |
| **WPS** | 启动 WPS → 新建文档 → 写内容 → 排版 → 保存 .docx + 导出 PDF | pywin32 COM |
| **微信** | 启动微信 → 搜索公众号/服务号 → 关注 → 发送私信 | uiautomation |

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

# 微信（自动识别）
python main.py "帮我搜索微信服务号火眼审阅并关注发私信"
```

---

## 知乎链路

```
登录知乎 → 写文章 → SVG 配图 → 发布 → 搜索 → 评论 + 收藏 → done
```

**Playbook 加速**：首次 DOM 探索后缓存选择器到 `memory/zhihu_playbook.json`，后续毫秒级执行。

**平台知识**：写文章入口 `zhuanlan.zhihu.com/write`、创作助手弹窗关闭、Draft.js 编辑器 input 填入、禁止自我点赞和微信扫码登录。

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

每次文档生成成功后自动保存模板到 `memory/wps_templates.json`，缓存文档类型（周报/会议纪要/报告等）→ 排版参数 → 章节骨架。

下次同类任务：LLM 先查模板 → 复用排版参数和内容结构 → 只填充具体内容，跳过一次完整创作。

---

## 微信链路

```
启动微信 → 搜索公众号/服务号 → 关注 → 发送私信 → done
```

通过 Windows UI 自动化（uiautomation）操作微信桌面客户端，无需扫码登录（依赖微信已登录状态）。

---

## 项目结构

```
Zhihu-Agent-Playbook/
├── main.py                    # 入口：意图路由 + 三链路分发
├── config.py                  # LLM API / 浏览器配置 + 输出解析容错
├── .env / .env.example        # 环境变量
├── requirements.txt           # Python 依赖
├── agent/
│   └── core.py                # Agent 工厂：知乎 / WPS / 微信 + system_prompt
├── tools/
│   ├── wps.py                 # WPS COM：新建/排版/保存/导出 PDF
│   ├── wps_playbook.py        # WPS 模板缓存：排版参数+内容骨架复用
│   ├── wechat.py              # 微信 UI 自动化：搜索/关注/发私信
│   ├── playbook.py            # 知乎 DOM 选择器查询/缓存执行
│   ├── image_gen.py           # SVG 配图生成 + 编辑器插入
│   ├── human_in_loop.py       # 人机协同：登录/验证码暂停
│   └── auto_memory.py         # 自动记忆：每步后扫描 DOM 写入 playbook
└── memory/
    ├── zhihu_playbook.json    # DOM 选择器缓存
    └── wps_templates.json     # WPS 模板缓存
```

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

## 技术栈

- **browser-use 0.13.3** — Agent 框架
- **Playwright 1.61.0** — 浏览器自动化
- **pywin32** — WPS COM 接口
- **uiautomation** — 微信 UI 自动化
- **本机 Microsoft Edge** — 复用登录态

## License

MIT
