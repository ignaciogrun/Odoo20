from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

# Relative import, so the module keeps working if its folder is renamed again.
from ..models.res_partner import HANDLE, SOCIAL_FIELDS, normalize_handle


class SocialTaggingPortal(CustomerPortal):

    def _create_or_update_address(self, partner_sudo, **form_data):
        # The question is only asked on Account Details, about the person themselves:
        # ignore these fields when they come with another address (a delivery
        # address, a colleague's contact...).
        if partner_sudo != request.env.user.partner_id:
            for name in SOCIAL_FIELDS:
                form_data.pop(name, None)
        return super()._create_or_update_address(partner_sudo, **form_data)

    def _validate_address_values(self, address_values, partner_sudo, address_type, *args, **kwargs):
        invalid_fields, missing_fields, error_messages = super()._validate_address_values(
            address_values, partner_sudo, address_type, *args, **kwargs
        )
        if address_values.get("social_tagging") == "yes":
            handle = normalize_handle(address_values.get("social_handle"))
            if not handle:
                missing_fields.add("social_handle")
                error_messages.append(request.env._(
                    "You said we can tag you: please add your social media handle, for example @jane.doe."
                ))
            elif not HANDLE.match(handle.lstrip("@")):
                invalid_fields.add("social_handle")
                error_messages.append(request.env._(
                    "Your social media handle can only use letters, numbers, periods and underscores "
                    "(30 at most), for example @jane.doe."
                ))
        return invalid_fields, missing_fields, error_messages
