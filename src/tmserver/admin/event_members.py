"""Authenticated form for trusted event eligibility and progress corrections."""

import re
from uuid import uuid4

from pydantic import TypeAdapter
from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette_admin import CustomView
from starlette_admin.routing import route

from .actions import AdminActionConflict, AdminActionError, apply_event_member
from .audit import EventMemberReceipt, EventMemberRequest
from .event_grants import _exact_fields
from .queries import account_rows


REQUEST = TypeAdapter(EventMemberRequest)
COMMON_FIELDS = {"action", "request_id", "account_key", "event_id", "reason"}
ACTION_FIELDS = {
    "event-eligibility": {"value"},
    "event-progress": {"update_id", "steps_delta", "completed_missions"},
}


def _integer(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|-?[1-9][0-9]{0,9}", value):
        raise AdminActionError("Use whole numbers without leading zeros.")
    return int(value)


class EventMembersView(CustomView):
    menu_label = "Correct event membership"
    path = "/event-members"
    route_name = "event-members"

    def __init__(self, host):
        super().__init__()
        self.host = host

    def render(self, request, *, status_code=200, **context):
        return self.templates.TemplateResponse(
            request=request, name="event_member.html", status_code=status_code,
            context=dict(title=self.menu_label, **context),
        )

    def draft(self, request, *, values=None, status_code=200, **context):
        identifier = str(uuid4())
        values = dict(
            request_id=identifier, update_id="admin:" + identifier,
            action=request.query_params.get("action", "event-eligibility"),
            account_key=request.query_params.get("account", ""), event_id="", reason="",
            value="0", steps_delta="0", completed_missions="0",
        ) | (values or {})
        if values["action"] not in ACTION_FIELDS:
            values["action"] = "event-eligibility"
        return self.render(
            request, status_code=status_code, values=values,
            accounts=account_rows(self.host),
            events=[dict(id=c.id, name=c.listing.key_event.decode("latin1"))
                    for c in self.host.events.configs.values()],
            **context,
        )

    @route("")
    async def index(self, request):
        return self.draft(request)

    @route("/apply", methods=["POST"], name="event-member-apply")
    async def apply(self, request):
        form = await request.form()
        values = dict(form)
        try:
            fields = COMMON_FIELDS | ACTION_FIELDS[form["action"]]
            _exact_fields(form, fields)
            parsed = {name: form[name] for name in fields}
            for name in fields & {"value", "steps_delta", "completed_missions"}:
                parsed[name] = _integer(parsed[name])
            command = REQUEST.validate_python(dict(
                **parsed, source="web", operator=request.state.admin_user.username,
            ))
            receipt = apply_event_member(self.host, command)
        except AdminActionConflict as error:
            return self.draft(request, values=values, error=str(error), status_code=409)
        except (ValueError, KeyError) as error:
            return self.draft(request, values=values, error=str(error), status_code=400)
        except OSError:
            return self.draft(
                request, values=values, status_code=503,
                failed_request_id=command.request_id,
                receipt_url=self.receipt_url(request, command.request_id),
                error="Storage did not confirm this request. After recovery, check its receipt "
                      "or retry this exact correction; keep the same request ID.",
            )
        return RedirectResponse(
            self.receipt_url(request, receipt.request.request_id), status_code=303
        )

    def receipt_url(self, request, request_id):
        return request.url_for("admin:event-member-receipt").include_query_params(id=request_id)

    @route("/receipt", name="event-member-receipt")
    async def receipt(self, request):
        if self.host.storage_failed is not None:
            raise HTTPException(503, "Restart to reload receipts; the correction may already be saved.")
        receipt = self.host.admin_audit.get(request.query_params.get("id", ""))
        if not isinstance(receipt, EventMemberReceipt):
            raise HTTPException(404, "This event correction receipt was not found.")
        return self.render(request, receipt=receipt)
