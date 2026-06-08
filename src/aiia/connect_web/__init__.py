"""per-user 連携(OAuth callback) の Web 受け口。

callback.py = FastAPI 非依存の純粋ロジック（テスト可能）、app.py = 薄い FastAPI ラッパ。
本番は ALB/API Gateway 背後で HTTPS 公開し、Google の redirect を受ける。
"""
