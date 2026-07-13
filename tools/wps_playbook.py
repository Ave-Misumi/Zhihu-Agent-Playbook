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


# ── 模板类型识别（标题优先 → 结构特征 → 关键词兜底） ──

def _guess_template_type(title: str, body_md: str) -> str:
    """从标题和正文推断文档类型。

    分四步（按优先级）：
    1. 标题行政类型词 → 直接返回（周报/会议纪要/通知/简历/计划/总结）
    2. 标题文章型标志词 → 直接返回「文章」（趋势/浅谈/如何/指南/展望 等，排在报告之前）
    3. 标题报告型词（纯管理报告，不含文章词时）
    4. 首段结构特征 + 章节标题兜底（不再搜全文，避免技术文章误判）
    """
    # ── 提取章节标题 ──
    section_heads = "\n".join(
        line.strip() for line in body_md.split("\n")
        if re.match(r"^##\s", line.strip()) or re.match(r"^(?:[一二三四五六七八九十]+)[、.．]", line.strip())
    )

    # ── 第一步：标题行政类型词 ──
    if re.search(r"周报", title):
        return "周报"
    if re.search(r"(会议|纪要)", title):
        return "会议纪要"
    if re.search(r"(通知|公告|通告)", title):
        return "通知"
    if re.search(r"简历|履历|求职", title):
        return "简历"
    if re.search(r"(计划|规划|方案|路线图)", title):
        return "计划"
    if re.search(r"(总结|回顾|复盘)", title):
        return "总结"

    # ── 第二步：标题文章型标志词（排在报告之前） ──
    # 这些词表明这是一篇分析/思考类文章，不是行政报告
    # 即使标题同时含「报告」（如"趋势报告"），也以文章型为优先
    if re.search(r"(趋势|发展|分析|深入|如何|指南|浅谈|浅析|探讨|展望|思考|解读|观察|洞察|变革|演进)", title):
        return "文章"

    # ── 第三步：标题报告型词（纯管理报告） ──
    if re.search(r"(报告|调研|评估|研究)", title):
        return "报告"

    # ── 第四步：首段结构特征 + 章节标题兜底 ──
    head = body_md[:500]
    if re.search(r"(时间[：:]|地点[：:]|参会[：:]|议题[：:]|主持人[：:]|记录人[：:])", head):
        return "会议纪要"
    if re.search(r"(本周|上周|下周)", head) and re.search(r"(进展|完成|计划|工作)", head):
        return "周报"
    if re.search(r"(各位|各部门|特此|根据.*规定|经.*决定)", head):
        return "通知"

    # 章节标题兜底（只匹配章节标题，避免正文中的普通词触发误判）
    if "会议" in section_heads:
        return "会议纪要"
    if "计划" in section_heads:
        return "计划"
    if "总结" in section_heads:
        return "总结"
    if "报告" in section_heads:
        return "报告"

    return "文章"


# ── 模板存储 ──

def _make_template_key(template_type: str, title: str) -> str:
    """生成复合键: 类型::标题（同类型不同主题不互相覆盖）"""
    return f"{template_type}:::{title}"


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
    """保存模板到缓存（用类型+标题复合键，不同主题不覆盖）"""
    templates = _load_templates()

    key = _make_template_key(template_type, title)
    templates[key] = {
        "type": template_type,
        "title": title,
        "skeleton": _extract_skeleton(body_md),
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
    templates[key]["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    _save_templates(templates)


# ── 公开工具 ──

async def get_wps_template(
    template_type: str,
) -> str:
    """
    查询 WPS 文档模板缓存。

    返回 JSON：该类型下所有历史模板列表（按更新时间倒序），每个含 title/skeleton/formatting/updated。
    未命中 → {"status":"no_template","message":"..."}

    LLM 应从中选择标题最匹配的模板复用其 formatting 参数。
    """
    templates = _load_templates()
    prefix = _make_template_key(template_type, "")
    matches = [
        v for k, v in templates.items()
        if k.startswith(prefix)
    ]
    matches.sort(key=lambda t: t.get("updated", ""), reverse=True)

    if matches:
        print(f"[WPS-PLAYBOOK] HIT  template_type={template_type} | {len(matches)} cached")
        for t in matches:
            fmt = t.get("formatting", {})
            print(f"  - \"{t.get('title','')}\" | "
                  f"{fmt.get('title_font')} {fmt.get('title_size')}/{fmt.get('body_font')} {fmt.get('body_size')}/{fmt.get('line_spacing')}pt | "
                  f"updated={t.get('updated')}")
        return json.dumps(matches, ensure_ascii=False, indent=2)

    print(f"[WPS-PLAYBOOK] MISS template_type={template_type} | returning guidance")
    return json.dumps({
        "status": "no_template",
        "message": f"没有「{template_type}」类型的缓存模板。请使用默认排版参数从头创作：标题黑体小二居中加粗，小节黑体小三加粗，正文宋体小四首行缩进2字符行距28磅。"
    }, ensure_ascii=False, indent=2)


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
        print(f"[WPS-PLAYBOOK] SAVED template_type={tt} | "
              f"formatting={title_font} {title_size}/{body_font} {body_size}/{line_spacing}pt")
    except Exception:
        pass  # 静默失败，不影响主流程
