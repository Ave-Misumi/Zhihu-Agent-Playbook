"""WPS 模板 Playbook：缓存文档排版参数和内容骨架，加速同类任务。

与知乎 playbook 根本不同：
- 知乎瓶颈 = DOM 探索（截图→LLM→找元素，10s+/步）→ 缓存选择器
- WPS 瓶颈 = LLM 创作（从零生成标题+正文+排版参数）→ 缓存模板

工作方式：
1. 每次 wps_create_document_and_export_pdf 成功后自动保存模板
2. LLM 下次遇到同类任务先调 get_wps_template 查缓存
3. 命中 → 直接复用排版参数和内容结构，LLM 只填充具体内容
"""
import json
import os
import re

PLAYBOOK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory")
TEMPLATE_PATH = os.path.join(PLAYBOOK_DIR, "wps_templates.json")


def _load_templates() -> dict:
    """加载已有模板缓存"""
    if not os.path.exists(TEMPLATE_PATH):
        return {}
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_templates(templates: dict) -> None:
    os.makedirs(PLAYBOOK_DIR, exist_ok=True)
    with open(TEMPLATE_PATH, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


# ── 从 body_md 提取结构骨架 ──

def _extract_skeleton(body_md: str) -> list[str]:
    """从正文提取结构骨架（章节标题列表）"""
    skeleton = []
    for line in body_md.split("\n"):
        line = line.strip()
        # ## 标题 / ### 标题 / 中文序号标题
        if re.match(r"^(?:#{1,3}\s|(?:[一二三四五六七八九十]+)[、.．]|引言|前言|总结|结语|附录)", line):
            skeleton.append(line)
        elif line.startswith("- "):
            # 只保留第一个列表项作为示例
            if not any(s.startswith("- ") for s in skeleton):
                skeleton.append(line)
    return skeleton


# ── 模板类型识别 ──

def _guess_template_type(title: str, body_md: str) -> str:
    """从标题和正文推断文档类型"""
    combined = f"{title}\n{body_md}"
    patterns = {
        "周报":    ["周报", "本周", "下周", "进展", "工作总结"],
        "会议纪要": ["会议", "纪要", "参会", "议题", "决议"],
        "报告":    ["报告", "调研", "分析", "评估", "研究"],
        "通知":    ["通知", "公告", "通告", "须知"],
        "简历":    ["简历", "履历", "求职", "应聘"],
        "计划":    ["计划", "规划", "方案", "路线图"],
        "总结":    ["总结", "回顾", "复盘", "年度", "季度"],
        "文章":    [],  # fallback
    }
    for ttype, keywords in patterns.items():
        if any(kw in combined for kw in keywords):
            return ttype
    return "文章"


# ── 模板存储 ──

def _save_template(
    template_type: str,
    title: str,
    body_md: str,
    title_font: str,
    title_size: str,
    heading_font: str,
    heading_size: str,
    body_font: str,
    body_size: str,
    line_spacing: str,
) -> None:
    """保存模板到缓存"""
    templates = _load_templates()

    templates[template_type] = {
        "type": template_type,
        "updated": "",  # 会被覆盖
        "example_title": title,
        "example_skeleton": _extract_skeleton(body_md),
        "formatting": {
            "title_font": title_font,
            "title_size": title_size,
            "heading_font": heading_font,
            "heading_size": heading_size,
            "body_font": body_font,
            "body_size": body_size,
            "line_spacing": line_spacing,
        },
    }

    from datetime import datetime
    templates[template_type]["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    _save_templates(templates)


# ── 公开工具 ──

async def get_wps_template(
    template_type: str,
) -> str:
    """
    查询特定类型的 WPS 文档模板缓存（排版参数 + 内容结构骨架）。

    参数:
        template_type: 文档类型，如 "周报"、"会议纪要"、"报告"、"通知"、"计划"、"总结"、"简历"、"文章"

    返回: 模板信息（JSON 文本），包含上次使用的排版参数和章节结构。
          未命中返回空对象 "{}"。
    """
    templates = _load_templates()
    tmpl = templates.get(template_type, {})
    return json.dumps(tmpl, ensure_ascii=False, indent=2)


def auto_save_wps_template(
    title: str,
    body_md: str,
    title_font: str = "黑体",
    title_size: str = "小二",
    heading_font: str = "黑体",
    heading_size: str = "小三",
    body_font: str = "宋体",
    body_size: str = "小四",
    line_spacing: str = "28",
) -> None:
    """每次 WPS 文档生成成功后自动调用，保存模板到缓存（同步）"""
    try:
        tt = _guess_template_type(title, body_md)
        _save_template(tt, title, body_md,
                       title_font, title_size,
                       heading_font, heading_size,
                       body_font, body_size, line_spacing)
    except Exception:
        pass  # 静默失败，不影响主流程
