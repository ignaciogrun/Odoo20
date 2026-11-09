import re

from markupsafe import Markup

# Only Coachable's shared plays can be embedded: this address ends up as the source
# of a frame on the page, so it must not be able to point anywhere else.
COACHABLE_URL = re.compile(r"https://(www\.)?coachableplays\.com/shared/[A-Za-z0-9_-]+/?")


def is_coachable_url(url):
    return bool(url and COACHABLE_URL.fullmatch(url.strip()))


def coachable_frame(url, small=False):
    """Return the cropped frame for a play, or False if the address is not a valid one."""
    if not is_coachable_url(url):
        return False
    size_class = " o_wslides_coachable_frame_small" if small else ""
    return Markup(
        '<div class="o_wslides_coachable_frame{}">'
        '<iframe src="{}" title="Play animation" tabindex="-1" scrolling="no" '
        'loading="lazy" referrerpolicy="no-referrer" '
        'sandbox="allow-scripts allow-same-origin"></iframe></div>'
    ).format(Markup(size_class), url.strip())
