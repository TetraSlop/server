"""Authenticated form for trusted event eligibility and progress corrections."""

from uuid import uuid4

from pydantic import TypeAdapter
from starlette_admin.routing import route

from .actions import apply_event_member
from .audit import EventMemberReceipt, EventMemberRequest
from .forms import AuditedFormView, exact_fields, whole_number
from .queries import account_rows


REQUEST = TypeAdapter(EventMemberRequest)
COMMON_FIELDS = {"action", "request_id", "account_key", "event_id", "reason"}
ACTION_FIELDS = {
    "event-eligibility": {"value"},
    "event-progress": {"update_id", "steps_delta", "completed_missions"},
}


class EventMembersView(AuditedFormView):
    menu_label = "Correct event membership"
    path = "/event-members"
    route_name = "event-members"
    template = "event_member.html"
    action = staticmethod(apply_event_member)
    receipt_type = EventMemberReceipt
    receipt_route = "event-member-receipt"
    storage_error = (
        "Storage did not confirm this request. After recovery, check its receipt "
        "or retry this exact correction; keep the same request ID."
    )
    receipt_unavailable = "Restart to reload receipts; the correction may already be saved."
    receipt_missing = "This event correction receipt was not found."

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

    def parse(self, request, form, *, operator):
        fields = COMMON_FIELDS | ACTION_FIELDS[form["action"]]
        exact_fields(form, fields)
        parsed = {name: form[name] for name in fields}
        for name in fields & {"value", "steps_delta", "completed_missions"}:
            parsed[name] = whole_number(parsed[name], signed=True)
        return REQUEST.validate_python(dict(**parsed, source="web", operator=operator))

    @route("/apply", methods=["POST"], name="event-member-apply")
    async def apply(self, request):
        return await self.submit(request)

    @route("/receipt", name="event-member-receipt")
    async def receipt(self, request):
        return self.show_receipt(request)
