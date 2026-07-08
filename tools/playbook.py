import json
import os
import re
from typing import Optional
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult

PLAYBOOK_PATH = os.path.join(os.path.dirname(__file__), "../memory/zhihu_playbook.json")


def load_playbook():
    if os.path.exists(PLAYBOOK_PATH):
        with open(PLAYBOOK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_playbook(data):
    os.makedirs(os.path.dirname(PLAYBOOK_PATH), exist_ok=True)
    with open(PLAYBOOK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _tokenize(text: str) -> set[str]:
    """
    Tokenize: Chinese-first, English-secondary. Filter noise (button/input/div).
    Fallback: if result is empty and text has content, keep all parts.
    """
    tokens = set()
    parts = re.split(r"[\s\-_,.:;=+()\[\]{}<>|/\\]+|(?<=[^\w])|(?=[^\w])", text)
    parts = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]

    for p in parts:
        # Skip pure lowercase 2-5 letter words (noise: button, input, div, etc)
        if re.match(r'^[a-z]{2,5}$', p):
            continue
        tokens.add(p)
        # Chinese 2-gram
        if re.match(r"^[\u4e00-\u9fff]{2,}$", p):
            for i in range(len(p) - 1):
                tokens.add(p[i:i+2])

    if not tokens:
        for p in parts:
            tokens.add(p)

    return tokens


def _fuzzy_match(query: str, candidates: dict[str, str]) -> tuple[float, str | None, list[tuple[str, float]] | None]:
    """
    Fuzzy match with 3-tier scoring.
    Returns (score, best_key, top_candidates) where top_candidates is a sorted list
    of (key, score) for the top N matches, or None if score gap is confident.
    """
    q_tokens = _tokenize(query)
    q_lower = query.lower()
    scored = []  # (score, key)

    for key in candidates:
        if key == query or key.lower() == q_lower:
            return (999.0, key, None)

        k_tokens = _tokenize(key)
        if not k_tokens:
            continue

        overlap = q_tokens & k_tokens
        union = q_tokens | k_tokens
        jaccard = len(overlap) / max(len(union), 1)

        sub_boost = 0.0
        for qt in q_tokens:
            if len(qt) >= 2 and qt in key:
                sub_boost = max(sub_boost, 0.30)
        for kt in k_tokens:
            if len(kt) >= 2 and kt in query:
                sub_boost = max(sub_boost, 0.25)
        # Dampen substring boost when Jaccard overlap is low
        sub_boost *= min(1.0, jaccard * 5.0)

        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', key))
        cn_bonus = min(0.15, cn_chars * 0.02) if jaccard > 0 else 0.0
        len_bonus = min(0.10, len(key) * 0.003) if jaccard > 0 else 0.0

        score = jaccard + sub_boost + cn_bonus + len_bonus
        scored.append((score, key))

    if not scored:
        return (0.0, None, None)

    scored.sort(reverse=True)
    best_score, best_key = scored[0]

    # Confidence check: top N scores too close -> return all top candidates
    top = [t for t in scored if t[0] >= best_score - 0.06]
    if len(top) > 1:
        return (best_score, best_key, top)

    return (best_score, best_key, None)


def get_playbook_selector(page_name: str, element_description: str) -> str:
    """
    Fuzzy-search the playbook for a CSS selector matching the LLM's natural-language description.
    When fuzzy match fails, returns the full key list so the LLM can pick with its superior semantics.
    """
    playbook = load_playbook()
    page_data = playbook.get(page_name, {})
    if not page_data:
        return "手册中暂无此页面记录，请使用浏览器默认工具进行探索。"

    # 1) Exact match
    if element_description in page_data:
        selector = page_data[element_description]
        return f"精确命中: {selector}。请使用 page.locator('{selector}') 进行操作。"

    # 2) Fuzzy match
    score, matched_key, top_candidates = _fuzzy_match(element_description, page_data)
    if score >= 0.20 and matched_key:
        # Confident single match
        if top_candidates is None:
            selector = page_data[matched_key]
            return (
                f"模糊匹配 (得分 {score:.0%}): key='{matched_key}' -> {selector}。"
                f"请使用 page.locator('{selector}') 进行操作，如失败请降级为视觉探索。"
            )
        # Multiple close candidates -> let LLM decide
        else:
            top_list = ", ".join(
                f"'{k}' ({s:.0%})" for s, k in top_candidates[:5]
            )
            return (
                f"模糊匹配有 {len(top_candidates)} 个相近候选: {top_list}。"
                f"请从以上 key 中选择最匹配的，然后调用 execute_playwright_action 执行。"
            )

    # 3) No fuzzy match -> show candidate list for LLM to decide
    if page_data:
        keys_preview = list(page_data.keys())[:15]
        return (
            f"手册中暂无精确匹配。当前页面 '{page_name}' 已收录 {len(page_data)} 个元素："
            f"{json.dumps(keys_preview, ensure_ascii=False)}"
            f"\n请根据元素描述自行选择最合适的 key，然后调用 execute_playwright_action 执行。"
        )
    return "手册中暂无此页面元素记录，请使用浏览器默认工具进行探索。"


async def execute_playwright_action(
    browser_session: BrowserSession,
    selector: str,
    action: str,
    text: Optional[str] = None
) -> ActionResult:
    """直接通过Playwright执行点击或输入操作（用于命中操作手册后的极速执行）。"""
    page = await browser_session.get_current_page()
    try:
        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=5000)
        if action == "click":
            await locator.click()
        elif action == "fill" and text:
            await locator.fill(text)
        elif action == "type" and text:
            await locator.type(text)
        return ActionResult(extracted_content=f"成功通过手册执行: {action} on {selector}")
    except Exception as e:
        return ActionResult(error=f"手册执行失败(可能页面改版): {str(e)}。请降级为视觉/DOM探索。")
