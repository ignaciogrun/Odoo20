from markupsafe import Markup

from odoo import api, fields, models
from odoo.http import request

from .coachable import coachable_frame, is_coachable_url


class SlideSlide(models.Model):
    _inherit = "slide.slide"

    video_url = fields.Char(
        help="Link of the video (we support YouTube, Google Drive, Vimeo and Coachable as sources)",
    )
    video_source_type = fields.Selection(selection_add=[("coachable", "Coachable")])
    coachable_preview = fields.Html(
        string="Play Preview", compute="_compute_coachable_preview", sanitize=False,
    )

    @api.depends("slide_category", "video_url")
    def _compute_coachable_preview(self):
        for slide in self:
            slide.coachable_preview = (
                coachable_frame(slide.video_url, small=True)
                if slide.slide_category == "video" else False
            )

    @api.depends("video_url")
    def _compute_video_source_type(self):
        super()._compute_video_source_type()
        for slide in self:
            if is_coachable_url(slide.video_url):
                slide.video_source_type = "coachable"

    @api.depends("slide_type", "video_source_type")
    def _compute_slide_icon_class(self):
        super()._compute_slide_icon_class()
        for slide in self:
            if slide.slide_category == "video" and slide.video_source_type == "coachable":
                slide.slide_icon_class = "play_circle"

    @api.depends("slide_category", "google_drive_id", "video_source_type", "youtube_id")
    def _compute_embed_code(self):
        super()._compute_embed_code()
        base_url = request.httprequest.url_root.rstrip("/") if request else False
        for slide in self:
            if (
                slide.slide_category == "video"
                and slide.video_source_type == "coachable"
                and slide._origin.id
            ):
                # An Odoo page holding the cropped frame, rather than Coachable's address
                # itself: every place that shows a video expects one plain frame it can size,
                # and only a page of our own can scale the crop to whatever size it is given.
                player_url = (
                    f"{base_url or slide.get_base_url()}"
                    f"/website_slides_coachable/player/{slide._origin.id}"
                )
                slide.embed_code = Markup(
                    '<iframe src="{}" title="Play animation" allowfullscreen="true" '
                    'frameborder="0"></iframe>'
                ).format(player_url)
                slide.embed_code_external = slide.embed_code
