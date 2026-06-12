"""§Q aiia-mcp：AiLaのメール機能を streamable-http MCP サーバとして公開（OpenClaw配下バックエンド）。

OpenClaw(自律外殻)はここを越えて Gmail/トークン/KMS に直接触れない。per-user 本人解決
（slack_user_id→email をサーバ側で確定・外殻申告は不信）と要約生成は本境界の内側で死守する。
"""
