"""微信操作 Playbook：缓存搜索和按钮定位结果，加速同类任务。

与知乎/WPS playbook 类比：
- 知乎瓶颈 = DOM 探索（截图→LLM→找选择器，10s+/步）→ 缓存 CSS 选择器
- WPS 瓶颈   = LLM 生成（从零写标题+正文+排版参数）→ 缓存排版参数+内容骨架
- 微信瓶颈   = 视觉定位（截图→颜色检测→OCR 验证，2-3s/步）→ 缓存相对坐标

工作方式：
  1. 首次运行某服务号：完整视觉流程（截图→OCR→颜色→点击）
  2. 成功后自动缓存搜索命中位置和按钮位置（以客户区百分比存储）
  3. 后续运行同一服务号：从缓存读取位置→直接点击，跳过截图+OCR
  4. 缓存按 keyword 分组，过期时间 30 天，窗口尺寸变更自动校验

存储：memory/wechat_playbook.json
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

PLAYBOOK_DIR = Path(__file__).parent.parent / "memory"
PLAYBOOK_PATH = PLAYBOOK_DIR / "wechat_playbook.json"

# 缓存有效期（天）
STALE_DAYS = 30

# 窗口尺寸容差（百分比）：当前窗口与缓存时窗口尺寸偏差超过此值则视为无效
SIZE_TOLERANCE_PCT = 0.15


def _load() -> dict:
    if not PLAYBOOK_PATH.exists():
        return {}
    try:
        with open(PLAYBOOK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)
    with open(PLAYBOOK_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _is_stale(entry: dict) -> bool:
    """检查缓存条目是否过期（> STALE_DAYS 天）。"""
    updated = entry.get("updated", "")
    if not updated:
        return True
    try:
        dt = datetime.fromisoformat(updated)
        return datetime.now() - dt > timedelta(days=STALE_DAYS)
    except Exception:
        return True


def _size_matches(entry: dict, current_w: int, current_h: int) -> bool:
    """检查当前窗口尺寸是否与缓存时接近（容差内）。"""
    cached_w = entry.get("client_w", 0)
    cached_h = entry.get("client_h", 0)
    if cached_w <= 0 or cached_h <= 0:
        return False
    dw = abs(current_w - cached_w) / max(cached_w, 1)
    dh = abs(current_h - cached_h) / max(cached_h, 1)
    return dw <= SIZE_TOLERANCE_PCT and dh <= SIZE_TOLERANCE_PCT


# ── 搜索命中位置 ──

def lookup_search_result(keyword: str, client_w: int, client_h: int) -> tuple[int, int] | None:
    """查找缓存的搜索命中位置，返回客户区绝对坐标。

    Returns:
        (client_x, client_y) 或 None（未命中/过期/尺寸不匹配）
    """
    data = _load()
    entry = data.get(keyword)
    if not entry:
        return None

    if _is_stale(entry):
        print(f"[WECHAT-PLAYBOOK] 缓存过期: keyword={keyword}")
        return None

    if not _size_matches(entry, client_w, client_h):
        print(f"[WECHAT-PLAYBOOK] 窗口尺寸变更，缓存失效: "
              f"cached={entry.get('client_w')}x{entry.get('client_h')} "
              f"vs current={client_w}x{client_h}")
        return None

    sr = entry.get("search_result")
    if not sr:
        return None

    x_pct = sr.get("x_pct")
    y_pct = sr.get("y_pct")
    if x_pct is None or y_pct is None:
        return None

    cx = int(client_w * x_pct)
    cy = int(client_h * y_pct)
    print(f"[WECHAT-PLAYBOOK] HIT search_result: keyword={keyword}, "
          f"pct=({x_pct:.3f},{y_pct:.3f}), client=({cx},{cy})")
    return (cx, cy)


def save_search_result(keyword: str, client_x: int, client_y: int, client_w: int, client_h: int) -> None:
    """缓存搜索命中位置（百分比）。"""
    data = _load()
    if keyword not in data:
        data[keyword] = {}

    data[keyword]["search_result"] = {
        "x_pct": round(client_x / max(client_w, 1), 4),
        "y_pct": round(client_y / max(client_h, 1), 4),
    }
    data[keyword]["client_w"] = client_w
    data[keyword]["client_h"] = client_h
    data[keyword]["updated"] = _now_iso()

    _save(data)
    print(f"[WECHAT-PLAYBOOK] SAVED search_result: keyword={keyword}, "
          f"client=({client_x},{client_y}), win={client_w}x{client_h}")


def save_search_miss(keyword: str) -> None:
    """记录 OCR 搜索失败（避免反复走视觉流程却无果）。"""
    data = _load()
    if keyword not in data:
        data[keyword] = {}
    data[keyword]["search_miss_count"] = data[keyword].get("search_miss_count", 0) + 1
    data[keyword]["updated"] = _now_iso()
    _save(data)


def get_search_miss_count(keyword: str) -> int:
    """获取该关键词的 OCR 搜索失败次数。"""
    data = _load()
    return data.get(keyword, {}).get("search_miss_count", 0)


# ── 按钮位置 ──

def lookup_button(keyword: str, button_type: str, client_w: int, client_h: int) -> tuple[int, int] | None:
    """查找缓存的按钮位置。

    Args:
        keyword: 服务号关键词
        button_type: "follow" | "send_msg" | "keyboard_toggle" | "input_box"
        client_w, client_h: 当前窗口客户区尺寸

    Returns:
        (client_x, client_y) 或 None
    """
    data = _load()
    entry = data.get(keyword)
    if not entry:
        return None

    if _is_stale(entry):
        return None

    if not _size_matches(entry, client_w, client_h):
        return None

    btn = entry.get(button_type)
    if not btn:
        return None

    x_pct = btn.get("x_pct")
    y_pct = btn.get("y_pct")
    if x_pct is None or y_pct is None:
        return None

    cx = int(client_w * x_pct)
    cy = int(client_h * y_pct)
    print(f"[WECHAT-PLAYBOOK] HIT {button_type}: keyword={keyword}, "
          f"pct=({x_pct:.3f},{y_pct:.3f}), client=({cx},{cy})")
    return (cx, cy)


def save_button(keyword: str, button_type: str, client_x: int, client_y: int,
                client_w: int, client_h: int) -> None:
    """缓存按钮位置（百分比）。"""
    data = _load()
    if keyword not in data:
        data[keyword] = {}

    data[keyword][button_type] = {
        "x_pct": round(client_x / max(client_w, 1), 4),
        "y_pct": round(client_y / max(client_h, 1), 4),
    }
    data[keyword]["client_w"] = client_w
    data[keyword]["client_h"] = client_h
    data[keyword]["updated"] = _now_iso()

    _save(data)
    print(f"[WECHAT-PLAYBOOK] SAVED {button_type}: keyword={keyword}, "
          f"client=({client_x},{client_y})")


# ── 详情窗口特征 ──

def lookup_detail_window(keyword: str) -> dict | None:
    """查找缓存的详情窗口特征（标题关键词、最小宽高等）。"""
    data = _load()
    entry = data.get(keyword)
    if not entry or _is_stale(entry):
        return None
    dw = entry.get("detail_window")
    if not dw:
        return None
    print(f"[WECHAT-PLAYBOOK] HIT detail_window: keyword={keyword}, "
          f"title={dw.get('title_keyword')}, min={dw.get('min_w')}x{dw.get('min_h')}")
    return dw


def save_detail_window(keyword: str, title_keyword: str, min_w: int, min_h: int) -> None:
    """缓存详情窗口特征。"""
    data = _load()
    if keyword not in data:
        data[keyword] = {}
    data[keyword]["detail_window"] = {
        "title_keyword": title_keyword,
        "min_w": min_w,
        "min_h": min_h,
    }
    data[keyword]["updated"] = _now_iso()
    _save(data)


# ── 清空 / 工具 ──

def clear_keyword(keyword: str) -> None:
    """清除指定关键词的缓存。"""
    data = _load()
    if keyword in data:
        del data[keyword]
        _save(data)
        print(f"[WECHAT-PLAYBOOK] CLEARED: keyword={keyword}")


def clear_all() -> None:
    """清空所有缓存。"""
    _save({})
    print("[WECHAT-PLAYBOOK] CLEARED all")


def list_entries() -> list[str]:
    """列出所有缓存的关键词。"""
    data = _load()
    return sorted(data.keys())
