"""
LLM 设置服务: JSON 文件持久化, 支持运行时读写。
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).parent.parent / "settings.json"

DEFAULTS = {
    "api_key": "",
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "chapter_max_chars": 20000,
}


def load_settings() -> dict:
    """读取 settings.json, 不存在或损坏则返回默认值。"""
    if not SETTINGS_PATH.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def save_settings(data: dict) -> None:
    """合并写入 settings.json, 仅保留已知字段。"""
    current = load_settings()
    for key in DEFAULTS:
        if key in data:
            current[key] = data[key]
    SETTINGS_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_llm_config() -> dict:
    """返回完整的 LLM 配置, 供 translator 使用。"""
    s = load_settings()
    api_base = s.get("api_base", DEFAULTS["api_base"]).rstrip("/")
    return {
        "api_key": s.get("api_key", DEFAULTS["api_key"]),
        "api_base": api_base,
        "model": s.get("model", DEFAULTS["model"]),
        "chapter_max_chars": int(s.get("chapter_max_chars", DEFAULTS["chapter_max_chars"])),
        "is_deepseek": "deepseek" in api_base.lower(),
    }


def mask_api_key(key: str) -> str:
    """脱敏 API Key, 仅显示后 4 位。"""
    if not key or len(key) <= 8:
        return key
    return f"{key[:3]}***{key[-4:]}"
