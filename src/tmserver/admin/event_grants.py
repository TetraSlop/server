"""Operator form for queuing audited event claims."""

import re
from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette_admin import CustomView
from starlette_admin.routing import route

from .actions import AdminActionConflict, AdminActionError, apply_event_grant
from .audit import EventGrantCard, EventGrantReceipt, EventGrantRequest
from .awards import _fields
from .queries import account_rows


CARD_FIELDS = ("model", "attack", "kind", "physical", "magical", "arrows")
DRAFT_FIELDS = {"request_id", "grant_id", "account_key", "event_id", "reason", "money"}


def _number(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,7}", value):
        raise AdminActionError(
            "Amounts and card values must be whole numbers without leading zeros."
        )
    return int(value)


def _exact_fields(form, expected):
    _fields(form, expected)
    if set(form) - {"csrftoken"} != set(expected):
        raise AdminActionError("The form is incomplete. Review the fields and try again.")


class EventGrantsView(CustomView):
    menu_label = "Queue event grant"
    path = "/event-grants"
    route_name = "event-grants"

    def __init__(self, host):
        super().__init__()
        self.host = host

    def render(self, request, *, status_code=200, **context):
        return self.templates.TemplateResponse(
            request=request,
            name="event_grant.html",
            status_code=status_code,
            context=dict(
                title="Queue event grant", cards=self.host.economy.catalog.cards, **context
            ),
        )

    def draft(self, request, *, values=None, status_code=200, **context):
        identifier = str(uuid4())
        values = dict(
            request_id=identifier,
            grant_id="admin:" + identifier,
            account_key=request.query_params.get("account", ""),
            event_id="",
            reason="",
            money="0",
            row_count=1,
        ) | (values or {})
        try:
            count = _number(str(values["row_count"]))
        except ValueError:
            count = 1
        values["row_count"] = max(1, min(count, 30))
        events = [
            dict(
                id=event.id,
                name=event.listing.key_event.decode("latin1"),
                enabled=event.card_service_enabled,
            )
            for event in self.host.events.configs.values()
        ]
        return self.render(
            request,
            status_code=status_code,
            values=values,
            accounts=account_rows(self.host),
            events=events,
            **context,
        )

    @route("")
    async def index(self, request):
        return self.draft(request)

    @route("/apply", methods=["POST"], name="event-grant-apply")
    async def apply(self, request):
        form = await request.form()
        values = dict(form)
        try:
            count = _number(form.get("row_count"))
            if not 1 <= count <= 30:
                raise AdminActionError(
                    "Use between one and thirty card rows; blank rows are ignored."
                )
            card_fields = {f"card_{index}_{name}" for index in range(count) for name in CARD_FIELDS}
            _exact_fields(form, DRAFT_FIELDS | card_fields | {"intent", "row_count"})
            if form["intent"] == "add-card":
                if count == 30:
                    raise AdminActionError("A grant can contain at most thirty cards.")
                return self.draft(request, values=values | {"row_count": count + 1})
            if form["intent"] != "apply":
                raise AdminActionError("Unknown event grant form action.")
            cards = []
            for index in range(count):
                row = {name: form[f"card_{index}_{name}"] for name in CARD_FIELDS}
                if any(value != "" for value in row.values()):
                    cards.append(
                        EventGrantCard(**{name: _number(value) for name, value in row.items()})
                    )
            command = EventGrantRequest(
                **{name: form[name] for name in DRAFT_FIELDS - {"money"}},
                money=_number(form["money"]),
                cards=tuple(cards),
                operator=request.state.admin_user.username,
                source="web",
            )
            receipt = apply_event_grant(self.host, command)
        except AdminActionConflict as error:
            return self.draft(request, values=values, error=str(error), status_code=409)
        except (ValueError, KeyError) as error:
            return self.draft(request, values=values, error=str(error), status_code=400)
        except OSError:
            return self.draft(
                request,
                values=values,
                status_code=503,
                failed_request_id=command.request_id,
                receipt_url=self.receipt_url(request, command.request_id),
                error="Storage did not confirm this request. Keep its request ID and retry the "
                "same grant after recovery; do not create a new grant.",
            )
        return RedirectResponse(
            self.receipt_url(request, receipt.request.request_id), status_code=303
        )

    def receipt_url(self, request, request_id):
        return request.url_for("admin:event-grant-receipt").include_query_params(id=request_id)

    @route("/receipt", name="event-grant-receipt")
    async def receipt(self, request):
        if self.host.storage_failed is not None:
            raise HTTPException(
                503,
                "Storage is unavailable. Restart the server to reload saved receipts before "
                "checking this request; its grant may already have been saved.",
            )
        receipt = self.host.admin_audit.get(request.query_params.get("id", ""))
        if not isinstance(receipt, EventGrantReceipt):
            raise HTTPException(404, "This event grant receipt was not found.")
        return self.render(request, receipt=receipt)
