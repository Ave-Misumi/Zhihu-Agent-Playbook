"""WPS (Windows 客户端) COM 自动化：新建文字文档 → 写入内容 → 格式排版 → 保存 .docx → 导出 PDF

设计原则：
  1. LLM 输出的 Markdown 格式不可靠（可能漏加 - 前缀、混用各种序号）。
  2. 因此解析器不依赖 LLM 写对格式，而是根据语义自动推断结构。
  3. 所有 Markdown 标记（**/* /``/##/-/1./一、）在解析阶段全部剥离，
     最终的序号和粗体由 COM 代码直接控制，不依赖 LLM 的输出格式。
"""
import os
import re
import time
import subprocess

import pythoncom
import win32com.client
from tools.wps_playbook import auto_save_wps_template

# ═══════════════════════════════════════════════
# 中文字号 → pt
# ═══════════════════════════════════════════════
CN_FONT_SIZE_MAP: dict[str, float] = {
    "初号": 42.0,  "小初": 36.0,
    "一号": 26.0,  "小一": 24.0,
    "二号": 22.0,  "小二": 18.0,
    "三号": 16.0,  "小三": 15.0,
    "四号": 14.0,  "小四": 12.0,
    "五号": 10.5,  "小五": 9.0,
    "六号": 7.5,   "小六": 6.5,
    "七号": 5.5,   "八号": 5.0,
}

# COM 常量
wdAlignParagraphCenter = 1
wdLineSpacingExactly   = 3
wdFormatDocumentDefault = 16
wdExportFormatPDF       = 17
wdCollapseEnd           = 0


def _parse_font_size(raw: str | float | int | None) -> float | None:
    """'小四' | '12' | '12pt' → pt 值"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s in CN_FONT_SIZE_MAP:
        return CN_FONT_SIZE_MAP[s]
    for suffix in ("pt", "磅"):
        if s.lower().endswith(suffix):
            try:
                return float(s[:-len(suffix)].strip())
            except ValueError:
                return None
    try:
        return float(s)
    except ValueError:
        return None


def _wps_app():
    """获取 WPS Application 对象（调用方需先 CoInitialize）"""
    try:
        return win32com.client.Dispatch("KWPS.Application")
    except Exception:
        return win32com.client.Dispatch("WPS.Application")


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════
async def wps_create_document_and_export_pdf(
    title: str,
    body_md: str,
    output_dir: str = "",
    title_font: str = "黑体",
    title_size: str = "小二",
    heading_font: str = "黑体",
    heading_size: str = "小三",
    body_font: str = "宋体",
    body_size: str = "小四",
    line_spacing: str = "28",
) -> str:
    """
    启动 WPS → 新建文档 → 写入标题与内容 → 设置排版 → 保存 .docx → 导出 PDF

    参数:
        title         文章标题
        body_md       正文（Markdown，格式宽松）
        output_dir    输出目录（留空 = 桌面）
        title_font    标题字体（默认黑体）
        title_size    标题字号（默认小二）
        heading_font  小节标题字体（默认黑体）
        heading_size  小节标题字号（默认小三）
        body_font     正文字体（默认宋体）
        body_size     正文字号（默认小四）
        line_spacing  正文行距（磅值，默认 28pt）
    """
    ttl_size_pt = _parse_font_size(title_size) or 18.0
    hdg_size_pt = _parse_font_size(heading_size) or 15.0
    bdy_size_pt = _parse_font_size(body_size) or 12.0
    lsp_pt      = _parse_font_size(line_spacing) or 28.0

    if not output_dir or output_dir.strip() in ("", "桌面", "Desktop", "desktop", "."):
        output_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    os.makedirs(output_dir, exist_ok=True)

    safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)
    docx_path = os.path.join(output_dir, f"{safe_name}.docx")
    pdf_path  = os.path.join(output_dir, f"{safe_name}.pdf")

    pythoncom.CoInitialize()
    wps = doc = None

    try:
        _kill_wps()

        wps = _wps_app()
        wps.Visible = True
        doc = wps.Documents.Add()

        # ── 标题页 ──
        _set_title_paragraph(doc, title, title_font, ttl_size_pt)

        # ── 解析并写入正文 ──
        sections = _parse_sections(body_md)
        h2_count = 0
        for sec in sections:
            if sec["kind"] == "h2":
                h2_count += 1
                _add_heading(doc, sec["text"], h2_count, heading_font, hdg_size_pt)
                _write_list_items(doc, sec["items"], body_font, bdy_size_pt, lsp_pt)
                for para in sec["paragraphs"]:
                    _add_paragraph(doc, para, body_font, bdy_size_pt, lsp_pt)
            elif sec["kind"] == "preamble":
                for para in sec["paragraphs"]:
                    _add_paragraph(doc, para, body_font, bdy_size_pt, lsp_pt)

        # ── 保存 ──
        _prune_empty_paragraphs(doc)
        docx_path = _safe_path(docx_path)
        pdf_path  = docx_path.rsplit(".", 1)[0] + ".pdf"
        doc.SaveAs(docx_path, FileFormat=wdFormatDocumentDefault)
        time.sleep(0.5)
        doc.ExportAsFixedFormat(pdf_path, ExportFormat=wdExportFormatPDF)
        doc.Close(SaveChanges=False)

        auto_save_wps_template(title, body_md, title_font, title_size,
                               heading_font, heading_size,
                               body_font, body_size, line_spacing)

        return (
            f"WPS 文档完成！\n"
            f"DOCX: {docx_path}\n"
            f"PDF:  {pdf_path}\n"
            f"排版: 标题 {title_font} {title_size}({ttl_size_pt}pt) | "
            f"小节 {heading_font} {heading_size}({hdg_size_pt}pt) | "
            f"正文 {body_font} {body_size}({bdy_size_pt}pt) | "
            f"行距 {lsp_pt}pt"
        )
    except Exception as e:
        if doc:
            try:
                doc.Close(SaveChanges=False)
            except Exception:
                pass
        return f"WPS 操作失败: {e}"
    finally:
        pythoncom.CoUninitialize()


# ═══════════════════════════════════════════════
# 正文解析 — 按小节分组，自动识别列表项
# ═══════════════════════════════════════════════

_SECTION_HEADING = re.compile(
    r"^(?:##|###)\s+"          # ## / ###
    r"|\b(?:引言|前言|导言|绪论|总结|结语|后记|附录|参考文献)\b"  # 中文关键词
    r"|^[一二三四五六七八九十]+[、.．]"    # 一、 二、
)
_LIST_MARKER = re.compile(
    r"^[-*]\s"                 # -  或 *
    r"|^\d+[.、)．]\s"          # 1. 2、 3）
)


def _strip_all_md(text: str) -> str:
    """剥离所有 Markdown 格式标记（** / * / ` / ~~），返回纯文本"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)       # **粗体**
    text = re.sub(r"\*(.+?)\*", r"\1", text)            # *斜体*
    text = re.sub(r"~~(.+?)~~", r"\1", text)            # ~~删除线~~
    text = text.replace("`", "")                         # 反引号
    return text.strip()


def _is_list_item(line: str) -> bool:
    """判断一行是否「看起来像列表项」。

    规则（优先级从高到低）：
    1. 有明确列表标记（-、*、1.、2、 等）→ 一定是
    2. 以「关键词：」「关键词: 」开头（如「多模态融合能力：xxx」）→ 认为是
    3. 否则不是列表项
    """
    if _LIST_MARKER.match(line):
        return True
    # 中文冒号句式：「某某某：说明文字」
    if re.match(r"^.{2,20}[：:]\s*\S", line):
        return True
    return False


def _parse_sections(body: str) -> list[dict]:
    """将 Markdown 正文解析为有序的「小节」列表。

    每个小节 = {"kind": "h2", "text": str, "items": [...], "paragraphs": [...]}
    小标题之前的正文 = {"kind": "preamble", "paragraphs": [...]}

    逻辑：
    - 遇到 ## / ### / 一、 / 引言 → 开启新小节
    - 小节内的行：如果是列表项 → 进 items；否则 → 进 paragraphs
    - 除非整个小节没有列表项，此时全部进 paragraphs
    """
    lines = body.split("\n")

    # 第一步：按 h2 分块
    chunks: list[tuple[str | None, list[str]]] = []  # (heading, [lines])
    current_heading: str | None = None
    current_lines: list[str] = []

    for raw in lines:
        line = raw.rstrip()
        if not line:
            continue

        # 去掉列表标记后判断是否为标题行
        stripped = _LIST_MARKER.sub("", line).strip()
        if _SECTION_HEADING.match(stripped):
            if current_heading is not None or current_lines:
                chunks.append((current_heading, current_lines))
            text = stripped
            # 如果匹配的是 ## 前缀，去掉它
            m = re.match(r"^(?:##|###)\s+", line)
            if m:
                text = line[m.end():].strip()
            # 如果 LLM 已经写了「一、xxx」，去掉前缀序号（代码会重新加）
            text = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", text)
            current_heading = _strip_all_md(text)
            current_lines = []
            continue

        current_lines.append(line)

    if current_heading is not None or current_lines:
        chunks.append((current_heading, current_lines))

    # 第二步：每个 chunk 内自动分类
    result: list[dict] = []
    for heading, chunk_lines in chunks:
        if heading is None:
            # 没有标题的块 → preamble
            paras = [_strip_all_md(l) for l in chunk_lines]
            result.append({"kind": "preamble", "paragraphs": paras})
        else:
            items = []
            paragraphs = []
            # 检测：这个 chunk 是否有「列表模式」（连续两个以上列表项）
            list_count = sum(1 for l in chunk_lines if _is_list_item(l))
            in_list_mode = list_count >= 2

            for l in chunk_lines:
                clean = _strip_all_md(_LIST_MARKER.sub("", l).strip())
                if not clean:
                    continue
                if in_list_mode and _is_list_item(l):
                    items.append(clean)
                else:
                    paragraphs.append(clean)

            result.append({
                "kind": "h2",
                "text": heading,
                "items": items,
                "paragraphs": paragraphs,
            })

    return result


# ═══════════════════════════════════════════════
# 段落构建 — 每个函数只负责一段
# ═══════════════════════════════════════════════

def _new_paragraph(doc):
    """在文档末尾插入一个格式干净的空白段落"""
    rng = doc.Range(doc.Content.End - 1, doc.Content.End)
    rng.Collapse(wdCollapseEnd)
    rng.InsertParagraphAfter()
    last = doc.Paragraphs.Last
    pf = last.Format
    pf.Alignment       = 0
    pf.SpaceBefore     = 0
    pf.SpaceAfter      = 0
    pf.LineSpacingRule = 0
    pf.FirstLineIndent = 0
    pf.LeftIndent      = 0


def _set_title_paragraph(doc, title: str, font: str, size_pt: float):
    """第一个段落：文档标题（居中加粗）"""
    p = doc.Paragraphs(1)
    p.Range.Text = title
    rng = p.Range
    rng.Font.Name = font
    rng.Font.Size = size_pt
    rng.Font.Bold = True
    rng.ParagraphFormat.Alignment = wdAlignParagraphCenter
    rng.ParagraphFormat.SpaceAfter = 12
    rng.ParagraphFormat.LineSpacingRule = 0


def _add_heading(doc, text: str, index: int, font: str, size_pt: float):
    """追加一个小节标题（自动加中文序号：一、二、三...）"""
    _new_paragraph(doc)
    heading_text = f"{_num2cn(index)}、{text}"
    p = doc.Paragraphs.Last
    p.Range.Text = heading_text
    rng = p.Range
    rng.Font.Name = font
    rng.Font.Size = size_pt
    rng.Font.Bold = True
    pf = p.Format
    pf.SpaceBefore = 10
    pf.SpaceAfter  = 6
    pf.LineSpacingRule = 0


def _add_paragraph(doc, text: str, font: str, size_pt: float, lsp_pt: float):
    """追加一个正文段落（首行缩进 2 字符，固定行距）"""
    _new_paragraph(doc)
    p = doc.Paragraphs.Last
    p.Range.Text = text
    rng = p.Range
    rng.Font.Name = font
    rng.Font.Size = size_pt
    pf = p.Format
    pf.LineSpacingRule = wdLineSpacingExactly
    pf.LineSpacing = lsp_pt
    pf.FirstLineIndent = size_pt * 2.0


def _write_list_items(doc, items: list[str], font: str, size_pt: float, lsp_pt: float):
    """追加一组编号列表项（1. 2. 3. ...）"""
    for i, text in enumerate(items, 1):
        _new_paragraph(doc)
        p = doc.Paragraphs.Last
        # 写入时手动加序号，不依赖 WPS 的自动编号（避免列表跨段断裂）
        p.Range.Text = f"{i}. {text}"
        rng = p.Range
        rng.Font.Name = font
        rng.Font.Size = size_pt
        pf = p.Format
        pf.LineSpacingRule = wdLineSpacingExactly
        pf.LineSpacing = lsp_pt
        pf.LeftIndent = size_pt * 3.0
        pf.FirstLineIndent = 0


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def _num2cn(num: int) -> str:
    """1~99 → 中文数字（一、二、...、九十九）"""
    digits = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if num <= 10:
        return "十" if num == 10 else digits[num]
    ten, one = divmod(num, 10)
    if ten == 1:
        return "十" + digits[one] if one else "十"
    result = digits[ten] + "十"
    if one:
        result += digits[one]
    return result


def _kill_wps():
    """关闭残留的 WPS 进程（避免 SaveAs 文件被锁）"""
    try:
        subprocess.run(["taskkill", "/f", "/im", "wps.exe"],
                       capture_output=True, timeout=5)
        time.sleep(0.8)
    except Exception:
        pass


def _safe_path(path: str) -> str:
    """如果文件已被占用，在文件名后加时间戳"""
    if not os.path.exists(path):
        return path
    try:
        with open(path, "rb"):
            pass
        return path
    except PermissionError:
        stem, ext = os.path.splitext(path)
        return f"{stem}_{time.strftime('%H%M%S')}{ext}"


def _prune_empty_paragraphs(doc):
    """删除文档末尾的空段落"""
    for idx in range(doc.Paragraphs.Count, 0, -1):
        p = doc.Paragraphs(idx)
        t = p.Range.Text.strip()
        if t and t not in ("\r", "\r\n", ""):
            break
        try:
            p.Range.Delete()
        except Exception:
            pass
