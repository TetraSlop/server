"""Shared behaviour of the audited operator forms: submit once, then show the receipt.

Subclasses parse their own fields and keep their own named ``apply`` and
``receipt`` routes. The operator always comes from the authenticated session.
"""

import re

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse, Response
from starlette_admin import CustomView
from starlette_admin.routing import route

from .actions import AdminActionConflict, AdminActionError


def exact_fields(form, expected, *, optional=False):
    """Reject unknown or repeated fields, and missing ones unless ``optional``."""
    names = set(form) - {"csrftoken"}
    if names - set(expected) or any(len(form.getlist(key)) != 1 for key in form):
        raise AdminActionError("The form contains unexpected or repeated fields.")
    if not optional and names != set(expected):
        raise AdminActionError("The form is incomplete. Review the fields and try again.")


def whole_number(value, *, signed=False):
    """Parse a decimal integer without signs or leading zeros; models check the range."""
    pattern = r"0|-?[1-9][0-9]{0,9}" if signed else r"0|[1-9][0-9]{0,9}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise AdminActionError("Use whole numbers without leading zeros.")
    return int(value)


class AuditedFormView(CustomView):
    """Subclasses set template, action, receipt_type, receipt_route and the messages."""

    template: str
    receipt_route: str
    storage_error: str
    receipt_unavailable: str
    receipt_missing: str

    def __init__(self, host):
        super().__init__()
        self.host = host

    def context(self):
        return {}

    def render(self, request, *, status_code=200, **context):
        return self.templates.TemplateResponse(
            request=request, name=self.template, status_code=status_code,
            context=dict(title=self.menu_label, **self.context(), **context),
        )

    @route("")
    async def index(self, request):
        return self.draft(request)

    async def submit(self, request):
        """Apply the parsed request once and redirect to its receipt."""
        form = await request.form()
        values = dict(form)
        try:
            command = self.parse(request, form, operator=request.state.admin_user.username)
            if isinstance(command, Response):
                return command
            receipt = self.action(self.host, command)
        except AdminActionConflict as error:
            return self.draft(request, values=values, error=str(error), status_code=409)
        except (ValueError, KeyError) as error:
            return self.draft(request, values=values, error=str(error), status_code=400)
        except OSError:
            return self.draft(
                request, values=values, status_code=503, error=self.storage_error,
                failed_request_id=command.request_id,
                receipt_url=self.receipt_url(request, command.request_id),
            )
        return RedirectResponse(
            self.receipt_url(request, receipt.request.request_id), status_code=303
        )

    def receipt_url(self, request, request_id):
        return request.url_for("admin:" + self.receipt_route).include_query_params(id=request_id)

    def show_receipt(self, request):
        if self.host.storage_failed is not None:
            raise HTTPException(503, self.receipt_unavailable)
        receipt = self.host.admin_audit.get(request.query_params.get("id", ""))
        if not isinstance(receipt, self.receipt_type):
            raise HTTPException(404, self.receipt_missing)
        return self.render(request, receipt=receipt)
