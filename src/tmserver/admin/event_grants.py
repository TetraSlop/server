"""Operator form for queuing audited event claims."""

from uuid import uuid4

from starlette_admin.routing import route

from .actions import AdminActionError, apply_event_grant
from .audit import EventGrantCard, EventGrantReceipt, EventGrantRequest
from .forms import AuditedFormView, exact_fields, whole_number
from .queries import account_rows


CARD_FIELDS = ("model", "attack", "kind", "physical", "magical", "arrows")
DRAFT_FIELDS = {"request_id", "grant_id", "account_key", "event_id", "reason", "money"}


class EventGrantsView(AuditedFormView):
    menu_label = "Queue event grant"
    path = "/event-grants"
    route_name = "event-grants"
    template = "event_grant.html"
    action = staticmethod(apply_event_grant)
    receipt_type = EventGrantReceipt
    receipt_route = "event-grant-receipt"
    storage_error = (
        "Storage did not confirm this request. Keep its request ID and retry the "
        "same grant after recovery; do not create a new grant."
    )
    receipt_unavailable = (
        "Storage is unavailable. Restart the server to reload saved receipts before "
        "checking this request; its grant may already have been saved."
    )
    receipt_missing = "This event grant receipt was not found."

    def context(self):
        return dict(cards=self.host.economy.catalog.cards)

    def draft(self, request, *, values=None, status_code=200, **context):
        identifier = str(uuid4())
        values = dict(
            request_id=identifier, grant_id="admin:" + identifier,
            account_key=request.query_params.get("account", ""), event_id="", reason="",
            money="0", row_count=1,
        ) | (values or {})
        try:
            count = whole_number(str(values["row_count"]))
        except ValueError:
            count = 1
        values["row_count"] = max(1, min(count, 30))
        events = [
            dict(id=event.id, name=event.listing.key_event.decode("latin1"),
                 enabled=event.card_service_enabled)
            for event in self.host.events.configs.values()
        ]
        return self.render(
            request, status_code=status_code, values=values,
            accounts=account_rows(self.host), events=events, **context,
        )

    def parse(self, request, form, *, operator):
        count = whole_number(form.get("row_count"))
        if not 1 <= count <= 30:
            raise AdminActionError("Use between one and thirty card rows; blank rows are ignored.")
        card_fields = {f"card_{index}_{name}" for index in range(count) for name in CARD_FIELDS}
        exact_fields(form, DRAFT_FIELDS | card_fields | {"intent", "row_count"})
        if form["intent"] == "add-card":
            if count == 30:
                raise AdminActionError("A grant can contain at most thirty cards.")
            return self.draft(request, values=dict(form) | {"row_count": count + 1})
        if form["intent"] != "apply":
            raise AdminActionError("Unknown event grant form action.")
        cards = []
        for index in range(count):
            row = {name: form[f"card_{index}_{name}"] for name in CARD_FIELDS}
            if any(value != "" for value in row.values()):
                cards.append(EventGrantCard(**{name: whole_number(v) for name, v in row.items()}))
        return EventGrantRequest(
            **{name: form[name] for name in DRAFT_FIELDS - {"money"}},
            money=whole_number(form["money"]), cards=tuple(cards),
            operator=operator, source="web",
        )

    @route("/apply", methods=["POST"], name="event-grant-apply")
    async def apply(self, request):
        return await self.submit(request)

    @route("/receipt", name="event-grant-receipt")
    async def receipt(self, request):
        return self.show_receipt(request)
