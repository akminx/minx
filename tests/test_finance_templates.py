from string import Template

from minx_mcp.finance.report_builders import _render


def test_render_allows_literal_dollar_text_in_rendered_content():
    template = Template("${body}")

    content = _render(template, body="Spent at $ave Drug and ${not_a_placeholder} Market")

    assert content == "Spent at $ave Drug and ${not_a_placeholder} Market"
