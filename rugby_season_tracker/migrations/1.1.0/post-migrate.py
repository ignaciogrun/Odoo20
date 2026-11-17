import json

from lxml import html as lxml_html

from odoo import SUPERUSER_ID, api

# Odoo's standard text block, so loose text gets the page's usual margins.
TEXT_BLOCK = ('<section class="s_text_block o_colored_level pt16 pb16" data-snippet="s_text_block" '
              'data-name="Text"><div class="s_allow_columns container">{}</div></section>')


def wrap_loose_content(value):
    """Wrap top-level text that isn't inside a block; leave blocks as they are."""
    if not value or not value.strip():
        return value
    pieces, loose = [], []

    def flush():
        inner = "".join(p if isinstance(p, str) else lxml_html.tostring(p, encoding="unicode") for p in loose)
        if inner.strip():
            pieces.append(TEXT_BLOCK.format(inner))
        loose.clear()

    for part in lxml_html.fragments_fromstring(value):
        if not isinstance(part, str) and part.tag == "section":
            flush()
            pieces.append(lxml_html.tostring(part, encoding="unicode"))
        else:
            loose.append(part)
    flush()
    return "".join(pieces)


def migrate(cr, version):
    """Runs once, when upgrading from a version before 1.1.0."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    # "Show on Website" used to publish the match's event, and that decided what
    # the team page listed. Results now have their own switch: turn it on for the
    # matches that were on the website, so the results table stays as it was.
    env["rugby.match"].with_context(active_test=False).search(
        [("event_id.is_published", "=", True)]
    ).write({"show_result": True})

    # The team introduction became a block area. Text typed there before sat in
    # a padded container; wrap it in a text block so it keeps its margins.
    cr.execute("SELECT id, website_description FROM rugby_team WHERE website_description IS NOT NULL")
    for team_id, translations in cr.fetchall():
        wrapped = {lang: wrap_loose_content(value) for lang, value in translations.items()}
        if wrapped != translations:
            cr.execute("UPDATE rugby_team SET website_description = %s WHERE id = %s", (json.dumps(wrapped), team_id))
