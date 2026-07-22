"""知乎编辑器正文写入 + 配图 — 合并为同一步骤

流程:
1. 先输入正文内容（Draft.js 内部文本节点操作）
2. 正文输入成功后，生成与内容相关的 SVG 配图 → PNG → 剪贴板 → Ctrl+V 粘贴
3. 返回整体结果
"""
import re
import json
import asyncio
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult

from tools.image_gen import generate_and_paste_image


def _parse_eval_result(result):
    """browser-use Page.evaluate() 总是返回字符串，需解析为 dict"""
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return {"error": "PARSE_FAIL", "raw": result}
    if isinstance(result, dict):
        return result
    return {"error": "UNEXPECTED_TYPE", "raw": str(result)}


async def _input_body_text(page, paragraphs: list[str]) -> dict:
    """通过 Draft.js 内部节点操作输入正文文本，返回结果字典"""
    # 使用 json.dumps 确保段落列表安全地传递到 JS 上下文
    # （避免 Python repr() 与 JS 字符串转义规则不一致的问题）
    paragraphs_json = json.dumps(paragraphs, ensure_ascii=False)
    result = await page.evaluate(f"""() => {{
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return {{error: 'E0'}};
        
        const contents = editor.querySelector('[data-contents="true"]');
        const textSpans = editor.querySelectorAll('[data-text="true"]');
        
        // 清空现有文本
        textSpans.forEach(span => span.textContent = '');
        
        // 获取或创建第一个文本块
        let firstBlock = editor.querySelector('[data-block="true"]');
        if (!firstBlock) {{
            firstBlock = document.createElement('div');
            firstBlock.setAttribute('data-block', 'true');
            const innerDiv = document.createElement('div');
            const textSpan = document.createElement('span');
            textSpan.setAttribute('data-text', 'true');
            innerDiv.appendChild(textSpan);
            firstBlock.appendChild(innerDiv);
            if (contents) {{
                contents.appendChild(firstBlock);
            }} else {{
                editor.appendChild(firstBlock);
            }}
        }}
        
        const firstTextSpan = editor.querySelector('[data-text="true"]');
        if (firstTextSpan) {{
            const text = {paragraphs_json}.join('\\n');
            firstTextSpan.textContent = text;
            
            firstTextSpan.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                inputType: 'insertText',
                data: text
            }}));
            
            editor.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                inputType: 'insertText',
                data: text
            }}));
            
            editor.dispatchEvent(new CompositionEvent('compositionend', {{
                bubbles: true,
                data: text
            }}));
        }}
        
        const textContent = editor.textContent || '';
        const displayLen = textContent.trim().length;
        
        return {{
            hasContents: !!contents,
            textSpansCount: textSpans.length,
            displayLen: displayLen,
            preview: textContent.trim().substring(0, 50)
        }};
    }}""")
    
    print(f"[zhihu_body] Draft.js 内部节点操作: {result}")
    return _parse_eval_result(result)


async def _input_body_keyboard(page, paragraphs: list[str]) -> dict:
    """备用方案: 模拟真实键盘输入"""
    print("[zhihu_body] 内部节点方案失败，尝试键盘模拟...")
    
    await page.evaluate("""() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (editor) {
            editor.focus();
            editor.click();
        }
    }""")
    await asyncio.sleep(0.3)
    
    await page.press("Control+a")
    await asyncio.sleep(0.1)
    await page.press("Delete")
    await asyncio.sleep(0.2)
    
    for char in ''.join(paragraphs):
        await page.press(char)
        await asyncio.sleep(0.01)
    
    await asyncio.sleep(0.5)
    
    verify = await page.evaluate("""() => {
        const e = document.querySelector('[contenteditable="true"]');
        if (!e) return {error: 'E0'};
        const n = e.textContent.trim().length;
        return {len: n, ok: n > 0};
    }""")
    
    return _parse_eval_result(verify)


async def zhihu_body_input_with_image(
    browser_session: BrowserSession,
    html_content: str,
    article_topic: str = "",
) -> ActionResult:
    """向知乎正文编辑器输入文章内容，并自动生成配图粘贴到正文中。
    
    将「输入正文」和「配图」合并为同一步骤：
    1. 先输入正文文本
    2. 正文输入成功后，根据标题和正文内容生成相关的 SVG 配图
    3. SVG → PNG → 系统剪贴板 → Ctrl+V 粘贴到正文
    
    html_content: HTML 格式正文，如 '<p>段落1</p><p>段落2</p>'
    article_topic: 文章标题/主题（用于生成与内容相关的配图）。如不传则从正文提取。
    
    返回:
      'OK:N|IMG:M' = 正文成功N字 + 配图M张
      'OK:N' = 正文成功但配图可能未插入
      'E0' = 编辑器未找到
      'E1' = 所有输入方案均失败
    """
    page = await browser_session.get_current_page()
    
    # ── 第1步: 输入正文 ──
    paragraphs = re.findall(r"<p>(.*?)</p>", html_content, re.DOTALL)
    if not paragraphs:
        paragraphs = [html_content]
    
    result = await _input_body_text(page, paragraphs)
    
    if result.get('error') == 'E0':
        return ActionResult(extracted_content="E0: 正文编辑器未找到")
    
    body_ok = result.get('displayLen', 0) > 0
    
    if not body_ok:
        # 备用方案: 键盘模拟
        result = await _input_body_keyboard(page, paragraphs)
        if result.get('error') == 'E0':
            return ActionResult(extracted_content="E0: 正文编辑器未找到")
        body_ok = result.get('ok', False)
    
    if not body_ok:
        return ActionResult(extracted_content="E1: 正文输入所有方案均失败")
    
    body_len = result.get('displayLen') or result.get('len', 0)
    print(f"[zhihu_body] 正文输入成功 ({body_len} 字)")
    
    # ── 第2步: 生成配图并粘贴 ──
    # 如果没有传 article_topic，从正文提取标题
    topic = article_topic.strip() if article_topic.strip() else paragraphs[0][:30] if paragraphs else "文章配图"
    content_text = ' '.join(paragraphs)
    
    print(f"[zhihu_body] 开始生成配图，主题: {topic}")
    
    img_result = await generate_and_paste_image(
        browser_session=browser_session,
        article_topic=topic,
        article_content=content_text,
    )
    
    img_text = img_result.extracted_content if img_result.extracted_content else ""
    
    # 解析配图结果
    if img_text.startswith("OK:"):
        return ActionResult(extracted_content=f"OK:{body_len}|IMG:{img_text}")
    else:
        # 配图失败但正文成功
        return ActionResult(extracted_content=f"OK:{body_len}|IMG_FAIL:{img_text}")


# ═══════════════════════════════════════════════════════════
# 向后兼容: 保留原 zhihu_body_input 函数（仅输入正文，不配图）
# ═══════════════════════════════════════════════════════════

async def zhihu_body_input(
    browser_session: BrowserSession,
    html_content: str,
) -> ActionResult:
    """向知乎正文编辑器输入文章内容（仅正文，不含配图）。
    
    html_content: HTML 格式，如 '<p>段落1</p><p>段落2</p>'
    返回: 'OK:N' 成功 / 'E0' 编辑器未找到 / 'E1' 输入后字数检测为0
    """
    page = await browser_session.get_current_page()
    
    paragraphs = re.findall(r"<p>(.*?)</p>", html_content, re.DOTALL)
    if not paragraphs:
        paragraphs = [html_content]
    
    result = await _input_body_text(page, paragraphs)
    
    if result.get('error') == 'E0':
        return ActionResult(extracted_content="E0: 正文编辑器未找到")
    
    if result.get('displayLen', 0) > 0:
        return ActionResult(extracted_content=f"OK:{result['displayLen']}")
    
    # 备用方案
    result = await _input_body_keyboard(page, paragraphs)
    if result.get('ok'):
        return ActionResult(extracted_content=f"OK:{result['len']}")
    
    return ActionResult(extracted_content="E1: 所有方案均失败")
