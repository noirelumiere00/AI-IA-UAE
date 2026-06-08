"""設計図(Markdown)を全文HTMLに変換する。

使い方: python scripts/build_plan_html.py
出力 : docs/PLAN_FULL.html
依存 : markdown (pip install markdown)
"""
from __future__ import annotations

import pathlib

import markdown

SRC = pathlib.Path("/root/.claude/plans/shimmying-foraging-ullman.md")
OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "PLAN_FULL.html"

HEAD = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-IA-UAE 設計図（全文）</title>
<style>
  :root{--ink:#1d1c1d;--line:#e6e6e6;--bg:#f6f7f9;--card:#fff;--purple:#3f0e40;--blue:#1264a3}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",Meiryo,sans-serif;line-height:1.75}
  .wrap{max-width:960px;margin:20px auto;padding:28px 24px 80px;background:var(--card);
        border:1px solid var(--line);border-radius:14px}
  h1{font-size:25px;border-bottom:3px solid var(--purple);padding-bottom:10px}
  h2{font-size:20px;margin-top:34px;border-left:6px solid var(--purple);padding-left:12px}
  h3{font-size:16px;margin-top:22px;color:#333}
  a{color:var(--blue)}
  hr{border:0;border-top:1px solid var(--line);margin:26px 0}
  code{background:#f0f0f3;border-radius:4px;padding:1px 5px;font-size:12.5px;font-family:ui-monospace,Menlo,Consolas,monospace}
  pre{background:#0f1117;color:#e6e6e6;border-radius:10px;padding:14px 16px;overflow:auto;font-size:12.5px;line-height:1.55}
  pre code{background:transparent;color:inherit;padding:0}
  table{border-collapse:collapse;width:100%;font-size:13.5px;margin:10px 0;display:block;overflow:auto}
  th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
  th{background:#f3f3f5}
  blockquote{margin:10px 0;padding:8px 14px;background:#fff8e6;border-left:4px solid #f0c75e;color:#5b4b1f}
  ul,ol{padding-left:22px}
  li{margin:3px 0}
  .mermaid{background:#fafafa;border:1px solid var(--line);border-radius:10px;padding:12px;margin:12px 0;text-align:center}
  strong{color:#111}
</style>
</head>
<body>
<div class="wrap">
"""

TAIL = """
</div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
  document.querySelectorAll("pre > code.language-mermaid").forEach(function(c){
    var d=document.createElement("div"); d.className="mermaid"; d.textContent=c.textContent;
    c.parentElement.replaceWith(d);
  });
  try{ mermaid.initialize({startOnLoad:true, securityLevel:"loose"}); }catch(e){}
</script>
</body>
</html>
"""


def main() -> None:
    src = SRC.read_text(encoding="utf-8")
    body = markdown.markdown(
        src,
        extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list", "nl2br"],
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(HEAD + body + TAIL, encoding="utf-8")
    print(f"written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
