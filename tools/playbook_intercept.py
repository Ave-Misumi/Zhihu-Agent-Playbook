"""
Playbook Interception — Layer 1 + Layer 2

Layer 1 (执行层拦截): 拦截 multi_act 中的 click(index),自动查 playbook,
                    命中则用 page.locator(selector).click() 替代,更可靠。

Layer 2 (预LLM规则引擎): monkey-patch step(),在 _prepare_context 之前自动关闭
                       创作助手等弹窗。LLM 看到的是干净页面 → 省 1 步 LLM 调用。
"""
import types
import asyncio
from typing import Any

# ─── Layer 1 helpers ─────────────────────────────────────────────

def _extract_label_from_selector_map(selector_map: dict, index: int) -> str | None:
    """从 selector_map 中提取元素的语义标签"""
    node = selector_map.get(index)
    if not node:
        return None
    attrs = getattr(node, 'attributes', {}) or {}
    aria = attrs.get('aria-label', '').strip()
    if aria:
        return aria
    ph = attrs.get('placeholder', '').strip()
    if ph:
        return ph
    return None


def _lookup_playbook(selector_map: dict, index: int, url: str) -> str | None:
    """查 playbook:给定 selector_map index,返回命中的 CSS 选择器或 None"""
    from tools.playbook import load_playbook
    from tools.auto_memory import _url_to_page_name

    label = _extract_label_from_selector_map(selector_map, index)
    if not label:
        return None

    page = _url_to_page_name(url)
    pb = load_playbook()
    return pb.get(page, {}).get(label)


# ─── Layer 1: monkey-patch multi_act ─────────────────────────────

def _apply_layer1(agent: Any) -> None:
    """拦截 multi_act,将 click(index) 替换为 playbook CSS selector 直接点击"""

    original = agent.multi_act

    async def patched_multi_act(self, actions):
        from browser_use.tools.service import ActionResult

        cached = getattr(self.browser_session, '_cached_browser_state_summary', None)
        selector_map = cached.dom_state.selector_map if (cached and cached.dom_state) else {}
        url = ""
        try:
            url = await self.browser_session.get_current_page_url() or ""
        except Exception:
            pass

        # 分离 playbook 命中的 click 和剩余 action
        playbook_clicks: list[dict] = []  # {orig_index, selector, label, action}
        remaining = []

        for i, action in enumerate(actions):
            ad = action.model_dump(exclude_unset=True)
            if 'click' in ad and isinstance(ad['click'], dict) and 'index' in ad['click']:
                idx = ad['click']['index']
                selector = _lookup_playbook(selector_map, idx, url) if selector_map else None
                if selector:
                    label = _extract_label_from_selector_map(selector_map, idx) or str(idx)
                    playbook_clicks.append({'orig_index': i, 'selector': selector, 'label': label, 'action': action})
                    continue
            remaining.append(action)

        if not playbook_clicks:
            return await original(remaining)

        # 预执行 playbook 点击
        results = []
        page = await self.browser_session.get_current_page()

        for hit in playbook_clicks:
            try:
                loc = page.locator(hit['selector'])
                await loc.wait_for(state="visible", timeout=5000)
                await loc.click()
                results.append(ActionResult(
                    extracted_content=f"Clicked {hit['selector']} [playbook]"
                ))
                print(f"[Playbook:L1] ✓ click(index) → locator('{hit['selector']}') [{hit['label']}]")
            except Exception as e:
                print(f"[Playbook:L1] ✗ locator('{hit['selector']}') failed: {e}, fallback to index-click")
                # 重新加入 remaining,走原始流程
                remaining.append(hit['action'])

        if not remaining:
            return results

        try:
            rest = await original(remaining)
            return results + rest
        except Exception:
            if results:
                return results
            raise

    agent.multi_act = types.MethodType(patched_multi_act, agent)
    print("[Playbook] Layer 1 (multi_act interception) applied")


# ─── Layer 2: monkey-patch step() for pre-LLM rules ──────────────

_WRITE_PAGE_POPUPS = [
    # (selector, description)
    ('button[aria-label="关闭创作助手"]', '创作助手'),
    ('button[aria-label="关闭"]', '通用弹窗'),
    ('div[class*="Modal"] button[aria-label="关闭"]', 'Modal 弹窗'),
]

# ─── 发布成功检测 ─────────────────────────────────────────────
# 检测发布按钮点击后出现的成功提示
_publish_detected = False

def was_publish_detected() -> bool:
    return _publish_detected

def reset_publish_state():
    global _publish_detected
    _publish_detected = False


async def _try_close_popup(page, selector: str, name: str) -> bool:
    """尝试关闭一个弹窗,成功返回 True"""
    try:
        loc = page.locator(selector).first
        if await loc.count() > 0:
            await loc.wait_for(state="visible", timeout=3000)
            await loc.click()
            await asyncio.sleep(0.3)
            print(f"[Playbook:L2] Auto-closed {name} ({selector})")
            return True
    except Exception:
        pass
    return False


def _apply_layer2(agent: Any) -> None:
    """monkey-patch step(): 在 _prepare_context 之前运行动态规则"""

    original_step = agent.step

    async def patched_step(self, step_info=None):
        # Phase 0: Pre-LLM rule engine — 处理弹窗等确定性场景
        try:
            if self.browser_session:
                url = await self.browser_session.get_current_page_url() or ""
                page = await self.browser_session.get_current_page()
            else:
                url = ""
                page = None
        except Exception:
            url = ""
            page = None

        if page and url:
            closed_any = False

            # Rule 1: zhuanlan.zhihu.com/write → 关闭创作助手
            if 'zhuanlan.zhihu.com/write' in url:
                for sel, name in _WRITE_PAGE_POPUPS:
                    if await _try_close_popup(page, sel, name):
                        closed_any = True
                        break  # 每步只关一个,避免死循环

            # Rule 2: 检测发布成功提示（全局，不限URL）
            global _publish_detected
            try:
                page_text = await page.evaluate("() => document.body.innerText.substring(0, 500)")
                for pattern in ['发布成功', '已发布', '文章已发布', '发布完成']:
                    if pattern in (page_text or ''):
                        _publish_detected = True
                        print(f"[Playbook:L2] ✓ 检测到发布成功提示: {pattern}")
                        break
            except Exception:
                pass

            if closed_any:
                # 弹窗已关,L2 直接执行后续,不调 LLM → 省一步
                # 重新获取浏览器状态(弹窗关闭后 DOM 已变化)
                pass  # 让 Agent 继续走正常步骤,但 LLM 看到的是干净页面

        return await original_step(step_info)

    agent.step = types.MethodType(patched_step, agent)
    print("[Playbook] Layer 2 (step() pre-LLM rules) applied")


# ─── 统一入口 ───────────────────────────────────────────────────

def apply_all_patches(agent: Any):
    """
    对 Agent 实例同时应用 Layer 1 + Layer 2。

    Layer 1: 执行层—捕获 click(index) → playbook CSS selector → 直接点击
    Layer 2: 规则引擎—step() 前自动关闭创作助手等弹窗 → LLM 看到干净页面

    返回增强后的 step callback (含 AutoMemory),需传给 register_new_step_callback。
    """
    _apply_layer1(agent)
    _apply_layer2(agent)
    # 保留原有的 AutoMemory 回调,不需要额外增强
    print("[Playbook] ✓ L1+L2 interception active: click→selector + pre-LLM popup cleanup")
    return agent.register_new_step_callback
