"""Framework form for queuing an audited Prize Center award."""

from uuid import uuid4

from starlette_admin.routing import route

from .actions import apply_prize_award
from .audit import PrizeAwardReceipt, PrizeAwardRequest
from .forms import AuditedFormView, exact_fields, whole_number
from .queries import account_rows

FIELDS = (
    "request_id", "award_id", "account_key", "reason", "top30", "lucky", "rookies",
    "lucky1", "lucky2", "lucky3",
)


class PrizeAwardsView(AuditedFormView):
    menu_label = "Queue prize award"
    path = "/awards"
    route_name = "prize-awards"
    template = "prize_award.html"
    action = staticmethod(apply_prize_award)
    receipt_type = PrizeAwardReceipt
    receipt_route = "prize-award-receipt"
    storage_error = (
        "Storage did not confirm this request. Keep its request ID and retry the "
        "same award after the server has recovered; do not create a new award."
    )
    receipt_unavailable = (
        "Storage is unavailable. Restart the server to reload saved receipts before "
        "checking this request; its award may already have been saved."
    )
    receipt_missing = "This Prize Center award receipt was not found."

    def context(self):
        return dict(cards=self.host.economy.catalog.cards)

    def draft(self, request, *, values=None, status_code=200, **context):
        identifier = str(uuid4())
        defaults = dict(
            request_id=identifier, award_id="admin:" + identifier,
            account_key=request.query_params.get("account", ""), reason="",
            top30=0, lucky=0, rookies=0, lucky1=65535, lucky2=65535, lucky3=65535,
        )
        return self.render(
            request, status_code=status_code, values=defaults | (values or {}),
            accounts=account_rows(self.host), **context,
        )

    def parse(self, request, form, *, operator):
        exact_fields(form, FIELDS, optional=True)

        def number(name, default=0):
            return whole_number(form.get(name, str(default)))

        return PrizeAwardRequest(
            request_id=form.get("request_id"), award_id=form.get("award_id"),
            account_key=form.get("account_key"), reason=form.get("reason"),
            operator=operator, source="web",
            top30=number("top30"), lucky=number("lucky"), rookies=number("rookies"),
            lucky_cards=tuple(number(f"lucky{i}", 65535) for i in (1, 2, 3)),
        )

    @route("/apply", methods=["POST"], name="prize-award-apply")
    async def apply(self, request):
        return await self.submit(request)

    @route("/receipt", name="prize-award-receipt")
    async def receipt(self, request):
        return self.show_receipt(request)
