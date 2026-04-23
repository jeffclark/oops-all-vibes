"""Post-hoc inject the GoatCounter tracking script and the prompt-link footer.

Called from write_outputs AFTER validation (so Georgia's raw output is what
validate_output sees). The GoatCounter script is client-side: it fires on
page load and reports to GoatCounter. fetch_feedback.py reads the other side
of that loop.
"""
from __future__ import annotations

from bs4 import BeautifulSoup


FOOTER_STYLE = (
    "position:fixed;bottom:4px;right:8px;font-size:10px;opacity:0.5;"
    "font-family:sans-serif;z-index:9999;"
)


def inject_tech(html: str, date_str: str, goatcounter_code: str | None) -> str:
    soup = BeautifulSoup(html, "html.parser")

    html_tag = soup.find("html")
    head = soup.find("head")
    body = soup.find("body")

    if head is None:
        head = soup.new_tag("head")
        if html_tag is not None:
            html_tag.insert(0, head)
        else:
            soup.insert(0, head)

    if goatcounter_code:
        script = soup.new_tag(
            "script",
            attrs={
                "data-goatcounter": f"https://{goatcounter_code}.goatcounter.com/count",
                "async": "",
                "src": "//gc.zgo.at/count.js",
            },
        )
        head.append(script)

    # Always append the transparency footer: links to today's log and today's prompt.
    footer = soup.new_tag("footer", style=FOOTER_STYLE)
    log_link = soup.new_tag("a", href=f"/log/{date_str}.md", style="color:inherit;")
    log_link.string = "today's log"
    sep = soup.new_tag("span")
    sep.string = " · "
    prompt_link = soup.new_tag("a", href=f"/prompts/{date_str}.md", style="color:inherit;")
    prompt_link.string = "today's prompt"
    footer.append(log_link)
    footer.append(sep)
    footer.append(prompt_link)

    if body is not None:
        body.append(footer)
    else:
        # No body — unusual; append at document root so at least the footer exists
        soup.append(footer)

    return str(soup)
