from __future__ import annotations

from app.websearch import parse_duckduckgo

PAGE = """
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">花样年华 &amp; 解读</a>
<a class="result__snippet" href="x">关于<b>克制</b>与错过</a></div>
<div class="result"><a class="result__a" href="https://example.org/b">第二篇</a>
<a class="result__snippet" href="y">摘要二</a></div>
"""


def test_parse_duckduckgo_unwraps_redirects_and_cleans_html():
    results = parse_duckduckgo(PAGE, 5)
    assert results[0] == {"title": "花样年华 & 解读", "url": "https://example.com/a", "snippet": "关于克制与错过"}
    assert results[1]["url"] == "https://example.org/b"


def test_parse_duckduckgo_respects_limit_and_empty_page():
    assert len(parse_duckduckgo(PAGE, 1)) == 1
    assert parse_duckduckgo("<html></html>", 5) == []
