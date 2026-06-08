"""mcp 層: 外部ツール（Gmail/Slack）への差込口（Protocol）と実装（Fake等）。

循環 import を避けるため、ここでは重い再 export はしない（各 .py から直接 import する）。
"""
