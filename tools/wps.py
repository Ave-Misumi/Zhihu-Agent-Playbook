"""WPS (Windows 客户端) COM 自动化：新建文字文档 → 写入内容 → 格式排版 → 保存 .docx → 导出 PDF

修复列表（2026-07-08）:
  Bug-1  _append_* 前置 "\r\n" 继承上段落残留格式 → 改用 InsertBreak 或单独 reset
  Bug-2  _parse_body_md 不识别「一、xx」「引言」「结语」等中文结构 → 扩展 h2 匹配
  Bug-3  LineSpacingRule=4(multiple) 配合磅值被 WPS 误解为 exact → 改用 wdLineSpacingExactly
  Bug-4  末段落之后多余空行/残留 → 写入完成后做一次全局样式清理
"""
import os
import re
import time

import pythoncom
import win32com.client
from browser_use.tools.service import ActionResult

# ═══════════════════════════════════════════════
# 中文字号 → pt
# ═══════════════════════════════════════════════

CN_FONT_SIZE_MAP: dict[str, float] = {
    "初号": 42.0, "小初": 36.0,
    "一号": 26.0, "小一": 24.0,
    "二号": 22.0, "小二": 18.0,
    "三号": 16.0, "小三": 15.0,
    "四号": 14.0, "小四": 12.0,
    "五号": 10.5, "小五": 9.0,
    "六号": 7.5,  "小六": 6.5,
    "七号": 5.5,  "八号": 5.0,
}

# COM 常量
wdAlignParagraphCenter = 1
wdLineSpacingExactly   = 3          # 固定磅值（原来错用 4=multiple）
wdFormatDocumentDefault = 16
wdExportFormatPDF       = 17
wdCollapseEnd           = 0


def _parse_font_size(raw: str | float | int | None) -> float | None:
    """'小四' | '12' | '12pt' | '12磅' → pt 值"""
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
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch("KWPS.Application")
    except Exception:
        return win32com.client.Dispatch("WPS.Application")


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
) -> ActionResult:
    """
    参数:
        line_spacing  正文行距（磅值），默认 28pt ≈ 1.5 倍行距
        其余同上
    """
    ttl_size_pt = _parse_font_size(title_size) or 18.0
    hdg_size_pt = _parse_font_size(heading_size) or 15.0
    bdy_size_pt = _parse_font_size(body_size) or 12.0
    lsp_pt      = _parse_font_size(line_spacing) or 28.0

    if not output_dir:
        output_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    os.makedirs(output_dir, exist_ok=True)

    safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)
    docx_path = os.path.join(output_dir, f"{safe_name}.docx")
    pdf_path  = os.path.join(output_dir, f"{safe_name}.pdf")

    pythoncom.CoInitialize()
    wps = doc = None
    scale = bdy_size_pt / 12.0

    try:
        wps = _wps_app()
        wps.Visible = True
        doc = wps.Documents.Add()

        # ── 1. 标题 (第一个段落) ──
        doc.Paragraphs(1).Range.Text = title
        t_rng = doc.Paragraphs(1).Range
        t_rng.Font.Name  = title_font
        t_rng.Font.Size  = ttl_size_pt
        t_rng.Font.Bold  = True
        t_rng.ParagraphFormat.Alignment     = wdAlignParagraphCenter
        t_rng.ParagraphFormat.SpaceAfter    = 12
        # Bug-3 修复: 行距显式 reset（标题本身不需要特殊行距，用默认即可）
        # 如果前模板有 garbage → 强制调回 single
        t_rng.ParagraphFormat.LineSpacingRule = 0  # wdLineSpaceSingle

        # ── 2. 正文逐段写入 ──
        paras = _parse_body_md(body_md)
        for kind, text, attrs in paras:
            is_bold   = "bold" in attrs
            is_italic = "italic" in attrs

            # Bug-1 修复：不再在追加函数内写 "\r\n"，而是先插入一个空段落再填充内容，
            # 保证新段落起始格式不受上一段影响。
            _ensure_new_paragraph(doc)   # 光标移到下一段开头

            if kind == "h2":
                _format_heading(doc, text, font=heading_font, size=hdg_size_pt,
                                spacing_before=12 * scale, spacing_after=6 * scale)
            elif kind == "bullet":
                _format_bullet(doc, text, font=body_font, size=bdy_size_pt, bold=is_bold,
                               lsp_pt=lsp_pt)
            else:
                _format_paragraph(doc, text, font=body_font, size=bdy_size_pt,
                                  bold=is_bold, italic=is_italic,
                                  lsp_pt=lsp_pt, scale=scale)

        # ── 3. 删除文档末尾多余空行（Bug-4） ──
        _prune_trailing_empty_paragraphs(doc)

        # ── 4. 保存 .docx → PDF ──
        doc.SaveAs(docx_path, FileFormat=wdFormatDocumentDefault)
        time.sleep(0.5)
        doc.ExportAsFixedFormat(pdf_path, ExportFormat=wdExportFormatPDF)
        doc.Close(SaveChanges=False)

        return ActionResult(
            extracted_content=(
                f"WPS 文档完成！\n"
                f"DOCX: {docx_path}\n"
                f"PDF:  {pdf_path}\n"
                f"排版: 标题 {title_font} {title_size}({ttl_size_pt}pt) | "
                f"小节 {heading_font} {heading_size}({hdg_size_pt}pt) | "
                f"正文 {body_font} {body_size}({bdy_size_pt}pt) | "
                f"行距 {lsp_pt}pt"
            )
        )

    except Exception as e:
        if doc:
            try:
                doc.Close(SaveChanges=False)
            except Exception:
                pass
        return ActionResult(error=f"WPS 操作失败: {e}")
    finally:
        pythoncom.CoUninitialize()


# ═══════════════════════════════════════════════
# Markdown 解析（Bug-2 增强）
# ═══════════════════════════════════════════════

# 匹配中文序号标题：一、xxx  二、xxx  ……  十、xxx  结语/引言/前言/总结
_RE_CHINESE_HEADING = re.compile(
    r"^(?:引言|前言|导言|绪论|总结|结语|后记|附录|参考文献)"  # 特殊标题
    r"|"
    r"^(?:[一二三四五六七八九十]+)[、.．]\s*"                    # 一、xxx 等
)

_MD_H2 = re.compile(r"^(?:##|###)\s+")                         # ## / ###


def _parse_body_md(body: str):
    """(kind, text, attrs) 列表。kind in {h2, bullet, para}"""
    lines = body.split("\n")
    result = []
    for line in lines:
        line = line.rstrip()
        if not line:
            # 空行：插入一个空段（当作普通段落），使 Markdown 段落间距得以呈现
            result.append(("para", "", set()))
            continue

        # ## 或 ### 标题
        m = _MD_H2.match(line)
        if m:
            result.append(("h2", line[m.end():].strip(), set()))
            continue

        # 中文序号标题（一、 二、 引言 结语 等）
        if _RE_CHINESE_HEADING.match(line):
            result.append(("h2", line.strip(), set()))
            continue

        # 列表项
        if line.startswith("- ") or line.startswith("* "):
            result.append(("bullet", line[2:].strip(), set()))
            continue

        # 普通段落（粗体/斜体检测）
        attrs = set()
        if "**" in line:
            attrs.add("bold")
        if "*" in line.replace("**", ""):
            attrs.add("italic")
        result.append(("para", line, attrs))

    return result


# ═══════════════════════════════════════════════
# 段落工具函数（Bug-1 / Bug-3 修复）
# ═══════════════════════════════════════════════

def _ensure_new_paragraph(doc):
    """在文档末尾光标位置插入一个全新的、格式干净的段落。
    这替代了之前 _append_* 中的前置 \"\\r\\n\"，避免继承乱格式。"""
    end = doc.Range(doc.Content.End - 1, doc.Content.End)
    end.Collapse(wdCollapseEnd)          # 折叠到文档尾
    end.InsertParagraphAfter()           # 插入空 ¶
    # 光标现在落在新段落开头，后续 _format_* 将操作 Paragraphs.Last


def _format_heading(doc, text: str, font: str, size: float,
                    spacing_before: float, spacing_after: float):
    """格式化最后一段为小节标题"""
    last = doc.Paragraphs.Last
    last.Range.Text = text

    rng = last.Range
    rng.Font.Name  = font
    rng.Font.Size  = size
    rng.Font.Bold  = True
    pf = last.Format
    pf.SpaceBefore = spacing_before
    pf.SpaceAfter  = spacing_after
    # Bug-3 修复：标题段使用单倍行距，不设固定行距
    pf.LineSpacingRule = 0   # wdLineSpaceSingle


def _format_paragraph(doc, text: str, font: str, size: float,
                      bold: bool, italic: bool,
                      lsp_pt: float, scale: float):
    """格式化最后一段为正文段落"""
    last = doc.Paragraphs.Last
    last.Range.Text = text if text else "\u00A0"   # 空段用不换行空格占位

    rng = last.Range
    rng.Font.Name   = font
    rng.Font.Size   = size
    rng.Font.Bold   = bold
    rng.Font.Italic = italic

    pf = last.Format
    # Bug-3 修复：显式固定磅值
    pf.LineSpacingRule = wdLineSpacingExactly
    pf.LineSpacing     = lsp_pt

    # 首行缩进 2 字符（只对非空段落）
    if text:
        pf.FirstLineIndent = size * 2.0


def _format_bullet(doc, text: str, font: str, size: float, bold: bool, lsp_pt: float):
    """格式化最后一段为编号列表项"""
    last = doc.Paragraphs.Last
    last.Range.Text = text

    rng = last.Range
    rng.Font.Name  = font
    rng.Font.Size  = size
    rng.Font.Bold  = bold

    pf = last.Format
    pf.LineSpacingRule = wdLineSpacingExactly
    pf.LineSpacing     = lsp_pt
    pf.LeftIndent      = size * 3.0
    pf.FirstLineIndent = 0

    # 自动编号
    last.Range.ListFormat.ApplyNumberDefault()


def _prune_trailing_empty_paragraphs(doc):
    """删除文档末尾的无内容段落（Bug-4）"""
    for idx in range(doc.Paragraphs.Count, 0, -1):
        p = doc.Paragraphs(idx)
        text = p.Range.Text.strip()
        if text and text not in ("\r", "\r\n", ""):
            break                                          # 遇到有内容的段落，停止
        # 空段落 → 删除
        try:
            p.Range.Delete()
        except Exception:
            pass
