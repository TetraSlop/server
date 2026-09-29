"""Framework forms for previewing and queuing an audited Prize Center award."""

import re
from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.responses import RedirectResponse
from starlette_admin import CustomView
from starlette_admin.routing import route

from .actions import (
    AdminActionConflict,
    AdminActionError,
    PrizeAwardRequest,
    apply_prize_award,
    preview_prize_award,
)
from .queries import account_rows
from .audit import PrizeAwardReceipt


def _fields(form, allowed):
    if set(form) - (set(allowed) | {"csrftoken"}) or any(
        len(form.getlist(key)) != 1 for key in form
    ):
        raise AdminActionError("The form contains unexpected or repeated fields.")


def _number(form, name, default=0):
    value = form.get(name, str(default))
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,4}", value):
        raise AdminActionError("Award amounts and card identifiers must be whole numbers.")
    return int(value)


class PrizeAwardsView(CustomView):
    menu_label = "Queue prize award"
    path = "/awards"
    route_name = "prize-awards"

    def __init__(self, host):
        super().__init__()
        self.host = host

    def render(self, request, *, status_code=200, **context):
        return self.templates.TemplateResponse(
            request=request, name="prize_award.html", status_code=status_code,
            context=dict(title="Queue prize award", cards=self.host.economy.catalog.cards, **context),
        )

    def draft(self, request, *, values=None, error=None, status_code=200):
        identifier = str(uuid4())
        defaults = dict(
            request_id=identifier, award_id="admin:" + identifier,
            account_key=request.query_params.get("account", ""), reason="",
            top30=0, lucky=0, rookies=0, lucky1=65535, lucky2=65535, lucky3=65535,
        )
        return self.render(
            request, status_code=status_code, values=defaults | (values or {}), error=error,
            accounts=account_rows(self.host),
        )

    @route("")
    async def index(self, request):
        return self.draft(request)

    @route("/preview", methods=["POST"], name="prize-award-preview")
    async def preview(self, request):
        form = await request.form()
        try:
            _fields(form, (
                "request_id", "award_id", "account_key", "reason", "top30", "lucky",
                "rookies", "lucky1", "lucky2", "lucky3",
            ))
            command = PrizeAwardRequest(
                request_id=form.get("request_id"), award_id=form.get("award_id"),
                account_key=form.get("account_key"), reason=form.get("reason"),
                operator=request.state.admin_user.username, source="web",
                top30=_number(form, "top30"), lucky=_number(form, "lucky"),
                rookies=_number(form, "rookies"),
                lucky_cards=tuple(_number(form, f"lucky{i}", 65535) for i in (1, 2, 3)),
            )
            preview = preview_prize_award(self.host, command)
        except (ValueError, KeyError) as error:
            return self.draft(request, values=dict(form), error=str(error), status_code=400)
        except OSError:
            return self.draft(
                request, values=dict(form), status_code=503,
                error="Storage is unavailable. Wait for the server to recover before previewing "
                "or confirming an award.",
            )
        if preview.already_committed is not None:
            return self.receipt_redirect(request, command.request_id)
        return self.render(request, preview=preview, command_json=command.model_dump_json())

    @route("/apply", methods=["POST"], name="prize-award-apply")
    async def apply(self, request):
        form = await request.form()
        try:
            _fields(form, ("command", "digest"))
            document = form.get("command")
            if not isinstance(document, str) or len(document) > 8192:
                raise AdminActionError("The confirmed award is missing or too large.")
            command = PrizeAwardRequest.model_validate_json(document)
            if command.operator != request.state.admin_user.username or command.source != "web":
                raise AdminActionError("The confirmed award belongs to another operator.")
            receipt = apply_prize_award(self.host, command, form.get("digest"))
        except AdminActionConflict as error:
            return self.render(request, error=str(error), status_code=409)
        except (ValueError, KeyError) as error:
            return self.render(request, error=str(error), status_code=400)
        except OSError:
            return self.render(
                request, status_code=503, failed_request_id=command.request_id,
                receipt_url=request.url_for("admin:prize-award-receipt").include_query_params(
                    id=command.request_id
                ),
                error="Storage did not confirm this request. Keep its request ID and retry the "
                "same confirmed award after the server has recovered; do not create a new award.",
            )
        return self.receipt_redirect(request, receipt.request.request_id)

    def receipt_redirect(self, request, request_id):
        return RedirectResponse(
            request.url_for("admin:prize-award-receipt").include_query_params(id=request_id),
            status_code=303,
        )

    @route("/receipt", name="prize-award-receipt")
    async def receipt(self, request):
        if self.host.storage_failed is not None:
            raise HTTPException(
                503, "Storage is unavailable. Restart the server to reload saved receipts before "
                "checking this request; its award may already have been saved.",
            )
        receipt = self.host.admin_audit.get(request.query_params.get("id", ""))
        if receipt is None:
            raise HTTPException(404, "This operator request was not found.")
        if not isinstance(receipt, PrizeAwardReceipt):
            raise HTTPException(404, "This request is not a Prize Center award receipt.")
        return self.render(request, receipt=receipt)
