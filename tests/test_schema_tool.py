"""pydantic→tool spec 変換（強制 tool_use 用）。$ref展開・strict・非対応キー除去・listラップ。"""
from __future__ import annotations

from aiia.providers.schema_tool import inline_schema, list_tool_from, tool_from
from aiia.schemas import ActionItem, ClassificationResult, DraftReply, ThreadSummary


def _no_refs(node: object) -> bool:
    s = str(node)
    return "$ref" not in s and "$defs" not in s


def test_classification_tool_self_contained_and_strict() -> None:
    spec = tool_from(ClassificationResult, name="emit", description="分類を出力")
    sch = spec["input_schema"]
    assert spec["name"] == "emit"
    assert _no_refs(sch)
    assert sch["additionalProperties"] is False
    assert "category" in sch["required"]
    assert sch["properties"]["category"].get("enum")  # Enum はインライン展開
    conf = sch["properties"]["confidence"]
    assert "minimum" not in conf and "maximum" not in conf  # 非対応制約は除去


def test_threadsummary_inlines_actionitem() -> None:
    sch = inline_schema(ThreadSummary)
    assert _no_refs(sch)
    items = sch["properties"]["action_items"]
    assert items["type"] == "array"
    assert items["items"]["additionalProperties"] is False  # ネスト ActionItem も strict 化


def test_list_tool_wraps_items() -> None:
    spec = list_tool_from(ActionItem, name="emit_actions", description="抽出")
    sch = spec["input_schema"]
    assert sch["type"] == "object"
    assert sch["required"] == ["items"]
    assert sch["properties"]["items"]["type"] == "array"
    assert _no_refs(sch)


def test_draft_schema_strict() -> None:
    sch = inline_schema(DraftReply)
    assert sch["additionalProperties"] is False
    assert "title" not in sch  # title は除去
