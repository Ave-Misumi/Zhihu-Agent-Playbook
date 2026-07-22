"""知乎编辑器正文写入 — Draft.js 内部文本节点操作
直接操作 data-text="true" 元素，这是 Draft.js 实际监听的文本节点。
"""
import re
import asyncio
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


async def zhihu_body_input(
    browser_session: BrowserSession,
    html_content: str,
) -> ActionResult:
    """向知乎正文编辑器输入文章内容。

    **调用前必须先 click 正文编辑区域！**

    html_content: HTML 格式，如 '<p>段落1</p><p>段落2</p>'
    返回: 'OK:N' 成功 / 'E0' 编辑器未找到 / 'E1' 输入后字数检测为0
    """
    page = await browser_session.get_current_page()

    # 解析段落
    paragraphs = re.findall(r"<p>(.*?)</p>", html_content, re.DOTALL)
    if not paragraphs:
        paragraphs = [html_content]

    # 方案: 直接操作 Draft.js 内部结构
    result = await page.evaluate(f"""() => {{
        const editor = document.querySelector('[contenteditable="true"]');
        if (!editor) return {{error: 'E0'}};
        
        // Draft.js 内部结构:
        // editor > div[data-contents="true"] > div[data-block="true"] > ... > span[data-text="true"]
        
        // 1. 找到 data-contents 容器
        const contents = editor.querySelector('[data-contents="true"]');
        
        // 2. 找到所有 data-text="true" 的文本节点
        const textSpans = editor.querySelectorAll('[data-text="true"]');
        
        // 3. 清空现有文本
        textSpans.forEach(span => span.textContent = '');
        
        // 4. 获取或创建第一个文本块
        let firstBlock = editor.querySelector('[data-block="true"]');
        if (!firstBlock) {{
            // 创建新的 block 结构
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
        
        // 5. 找到第一个 data-text="true" 并设置文字
        const firstTextSpan = editor.querySelector('[data-text="true"]');
        if (firstTextSpan) {{
            const text = {paragraphs!r}.join('\\n');
            firstTextSpan.textContent = text;
            
            // 6. 触发 Draft.js 需要的事件
            // 先触发 textSpan 的 input
            firstTextSpan.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                inputType: 'insertText',
                data: text
            }}));
            
            // 再触发 editor 的 input
            editor.dispatchEvent(new InputEvent('input', {{
                bubbles: true,
                inputType: 'insertText',
                data: text
            }}));
            
            // 触发 compositionend（中文输入法结束）
            editor.dispatchEvent(new CompositionEvent('compositionend', {{
                bubbles: true,
                data: text
            }}));
        }}
        
        // 7. 检查字数
        const textContent = editor.textContent || '';
        const displayLen = textContent.trim().length;
        
        return {{
            hasContents: !!contents,
            textSpansCount: textSpans.length,
            displayLen: displayLen,
            preview: textContent.trim().substring(0, 50)
        }};
    }}""")
    
    print(f"[zhihu_body_input] Draft.js 内部节点操作: {result}")
    
    if result.get('error') == 'E0':
        return ActionResult(extracted_content="E0: 正文编辑器未找到")
    
    if result.get('displayLen', 0) > 0:
        return ActionResult(extracted_content=f"OK:{result['displayLen']}")
    
    # 如果失败，尝试备用方案: 模拟真实键盘输入
    print("[zhihu_body_input] 内部节点方案失败，尝试键盘模拟...")
    
    # 聚焦编辑器
    await page.evaluate("""() => {
        const editor = document.querySelector('[contenteditable="true"]');
        if (editor) {
            editor.focus();
            editor.click();
        }
    }""")
    await asyncio.sleep(0.3)
    
    # 清空
    await page.keyboard.press("Control+a")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.2)
    
    # 逐字符输入
    for char in ''.join(paragraphs):
        await page.keyboard.press(char)
        await asyncio.sleep(0.01)
    
    await asyncio.sleep(0.5)
    
    # 验证
    verify = await page.evaluate("""() => {
        const e = document.querySelector('[contenteditable="true"]');
        if (!e) return {error: 'E0'};
        const n = e.textContent.trim().length;
        return {len: n, ok: n > 0};
    }""")
    
    if verify.get('ok'):
        return ActionResult(extracted_content=f"OK:{verify['len']}")
    
    return ActionResult(extracted_content="E1: 所有方案均失败")
