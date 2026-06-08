"""pydantic モデル → Bedrock/Anthropic の強制 tool_use 用 tool spec 変換（純粋関数・SDK非依存）。

Bedrock で構造化出力を確実に得るには「出力スキーマをツールの input_schema として渡し、
そのツールを必ず呼ばせる(tool_choice)」のが堅い。pydantic の model_json_schema() は
`$ref`/`$defs`・`minimum`/`maxLength` 等を含むため、以下に整形する:
  - `$ref`/`$defs` をインライン展開（self-contained に）
  - 単一要素 `allOf`（pydantic が description 付きフィールドで使う）をマージ
  - 全 object に `additionalProperties: false`（構造化出力の要件）
  - 構造化出力で非対応 or 不要なキー(minimum/maximum/pattern/format/title/default 等)を除去
list[Model] は tool input が object 必須のため {items: [...]} でラップする。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# 構造化出力スキーマから落とすキー（pydantic の検証はクライアント側 model_validate で担保）。
_STRIP_KEYS = frozenset(
    {
        "$defs", "title", "default", "examples",
        "minLength", "maxLength", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "pattern", "format",
    }
)


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """$ref をインライン展開し、単一 allOf をマージする。"""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            merged = dict(_resolve(defs[ref], defs))
            for k, v in node.items():
                if k != "$ref":
                    merged[k] = _resolve(v, defs)
            return merged
        if "allOf" in node and len(node["allOf"]) == 1:
            base = dict(_resolve(node["allOf"][0], defs))
            for k, v in node.items():
                if k != "allOf":
                    base[k] = _resolve(v, defs)
            return base
        return {k: _resolve(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(x, defs) for x in node]
    return node


def _clean(node: Any) -> Any:
    """非対応キーを除去し、全 object に additionalProperties:false を付与。"""
    if isinstance(node, dict):
        out = {k: _clean(v) for k, v in node.items() if k not in _STRIP_KEYS}
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_clean(x) for x in node]
    return node


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """pydantic モデル → self-contained な JSON Schema（tool の input_schema 用）。"""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    return _clean(_resolve(raw, defs))


def tool_from(model: type[BaseModel], *, name: str, description: str) -> dict[str, Any]:
    """単一モデル用の tool spec。"""
    return {"name": name, "description": description, "input_schema": inline_schema(model)}


def list_tool_from(
    item: type[BaseModel], *, name: str, description: str, items_key: str = "items"
) -> dict[str, Any]:
    """list[Model] 用の tool spec（input は {items_key: [...]} の object でラップ）。"""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {items_key: {"type": "array", "items": inline_schema(item)}},
            "required": [items_key],
            "additionalProperties": False,
        },
    }
