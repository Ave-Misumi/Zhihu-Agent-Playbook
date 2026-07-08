"""WPS (Windows 客户端) COM 自动化：新建文字文档 → 写入内容 → 格式排版 → 保存 .docx → 导出 PDF"""
import os
import time
import pythoncom
import win32com.client
from browser_use.tools.service import ActionResult


def _wps_app():
    """获取或启动 WPS 文字 Application"""
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch("KWPS.Application")
    except Exception:
        return win32com.client.Dispatch("WPS.Application")


async def wps_create_document_and_export_pdf(
    title: str,
    body_md: str,
    output_dir: str = "",
) -> ActionResult:
    """
    启动 WPS → 新建文字文档 → 写入标题与正文 → 格式化排版 → 保存 .docx 并导出 PDF。

    参数:
        title:       文章标题（纯文本）
        body_md:     正文（支持简单 Markdown: ## 段落标题, **粗体**, - 列表项）
        output_dir:  输出目录，默认桌面
    """
    import re

    if not output_dir:
        output_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    os.makedirs(output_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", title)
    docx_path = os.path.join(output_dir, f"{safe_name}.docx")
    pdf_path = os.path.join(output_dir, f"{safe_name}.pdf")

    pythoncom.CoInitialize()
    wps = None
    doc = None
    try:
        # ── 1. 启动 WPS ──
        wps = _wps_app()
        wps.Visible = True

        # ── 2. 新建文档 ──
        doc = wps.Documents.Add()
        rng = doc.Range(0, 0)

        # ── 3. 写入标题 ──
        rng.Text = title + "\r\n"
        title_rng = doc.Paragraphs(1).Range
        title_rng.Font.Name = "黑体"
        title_rng.Font.Size = 22
        title_rng.Font.Bold = True
        title_rng.ParagraphFormat.Alignment = 1  # wdAlignParagraphCenter
        title_rng.ParagraphFormat.SpaceAfter = 12

        # ── 4. 逐段写入正文 ──
        body_range = doc.Range(doc.Content.End - 1, doc.Content.End - 1)
        body_range.Text = "\r\n"  # 空行分隔

        paras = _parse_body_md(body_md)
        for kind, text, attrs in paras:
            if kind == "h2":
                _append_heading(doc, text, level=2)
            elif kind == "bullet":
                _append_bullet(doc, text, bold=("bold" in attrs))
            else:
                _append_paragraph(doc, text, bold=("bold" in attrs), italic=("italic" in attrs))

        # ── 5. 保存 .docx ──
        doc.SaveAs(docx_path, FileFormat=16)  # wdFormatDocumentDefault
        time.sleep(0.5)

        # ── 6. 导出 PDF ──
        # wdExportFormatPDF = 17
        doc.ExportAsFixedFormat(pdf_path, ExportFormat=17)

        # ── 7. 关闭文档（不退出 WPS，保留用户工作环境） ──
        doc.Close(SaveChanges=False)

        return ActionResult(
            extracted_content=f"WPS 文档完成！\nDOCX: {docx_path}\nPDF:  {pdf_path}"
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


# ── 正文解析 ──

def _parse_body_md(body: str):
    """将简单 Markdown 解析为 (kind, text, attrs) 列表"""
    lines = body.split("\n")
    result = []
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            result.append(("h2", line[3:].strip(), {}))
        elif line.startswith("- ") or line.startswith("* "):
            result.append(("bullet", line[2:].strip(), {}))
        else:
            # 简单粗体/斜体检测
            attrs = []
            if "**" in line:
                attrs.append("bold")
            if "*" in line and "**" not in line:
                attrs.append("italic")
            result.append(("para", line, set(attrs)))
    return result


def _append_heading(doc, text: str, level: int = 2):
    """追加标题段落"""
    rng = doc.Range(doc.Content.End - 1, doc.Content.End - 1)
    rng.Text = "\r\n" + text + "\r\n"
    h_rng = doc.Paragraphs.Last.Range
    h_rng.Font.Name = "黑体"
    h_rng.Font.Size = 16 if level == 2 else 18
    h_rng.Font.Bold = True
    h_rng.ParagraphFormat.SpaceBefore = 12
    h_rng.ParagraphFormat.SpaceAfter = 6


def _append_paragraph(doc, text: str, bold: bool = False, italic: bool = False):
    """追加正文段落"""
    rng = doc.Range(doc.Content.End - 1, doc.Content.End - 1)
    rng.Text = "\r\n" + text + "\r\n"
    rng = doc.Paragraphs.Last.Range
    rng.Font.Name = "宋体"
    rng.Font.Size = 12
    rng.Font.Bold = bold
    rng.Font.Italic = italic
    rng.ParagraphFormat.LineSpacingRule = 4  # wdLineSpaceMultiple
    rng.ParagraphFormat.LineSpacing = 22  # 22pt 行距
    rng.ParagraphFormat.FirstLineIndent = 24  # 首行缩进 2 字符 @12pt


def _append_bullet(doc, text: str, bold: bool = False):
    """追加列表项（编号）"""
    rng = doc.Range(doc.Content.End - 1, doc.Content.End - 1)
    rng.Text = "\r\n" + text + "\r\n"
    rng = doc.Paragraphs.Last.Range
    rng.Font.Name = "宋体"
    rng.Font.Size = 12
    rng.Font.Bold = bold
    # 自动编号
    rng.ListFormat.ApplyNumberDefault()
    rng.ParagraphFormat.LeftIndent = 36
    rng.ParagraphFormat.FirstLineIndent = 0
