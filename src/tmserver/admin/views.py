"""Read-only framework views over request-local service projections."""

from datetime import datetime, timezone
from types import SimpleNamespace

from starlette.exceptions import HTTPException
from starlette_admin import (
    BaseModelView,
    BooleanField,
    ColumnWidget,
    CustomView,
    IntegerField,
    StringField,
    TableWidget,
)
from starlette_admin.actions import link_row_action

from . import queries


def capture(request, key, callback):
    """Capture on the host event loop before a framework renderer can await."""
    if not hasattr(request.state, "tm_snapshots"):
        request.state.tm_snapshots = {}
        request.state.tm_capture_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshots = request.state.tm_snapshots
    if key not in snapshots:
        try:
            snapshots[key] = callback()
        except Exception as exc:
            raise HTTPException(
                503, "This server view is temporarily unavailable. Refresh to retry."
            ) from exc
    return snapshots[key]


class ReadOnlyView(BaseModelView):
    pk_attr = "id"
    actions = []
    row_actions = ["view"]
    page_size = 25
    page_size_options = [10, 25, 50, 100]
    list_template = "readonly_list.html"
    detail_template = "readonly_detail.html"

    def __init__(self, host):
        self.host = host
        super().__init__()

    def can_create(self, request):
        return False

    can_edit = can_delete = can_import = can_export = can_create

    def _rows(self, request, q=None, filters=None):
        if filters is not None and not filters.is_empty():
            raise HTTPException(400, "Use the search box to narrow this view.")
        if q is not None and len(q) > 200:
            raise HTTPException(400, "Search text must contain at most 200 characters.")
        rows = capture(request, self.key, lambda: self.load_rows(self.host))
        if q:
            text = q.casefold()
            rows = [
                row
                for row in rows
                if row["id"] == q
                or any(
                    text in str(row.get(field, "")).casefold()
                    for field in self.searchable_fields
                    if field != "id"
                )
            ]
        return rows

    async def find_all(self, request, skip=0, limit=100, q=None, sorts=None, filters=None):
        rows = list(self._rows(request, q, filters))
        for field, direction in reversed(sorts or [("id", "asc")]):
            if field not in self.sortable_fields or direction not in ("asc", "desc"):
                raise HTTPException(400, "Unsupported sort order.")
            rows.sort(
                key=lambda row: (row.get(field) is None, row.get(field)),
                reverse=direction == "desc",
            )
        if skip < 0 or not 0 <= limit <= 100:
            raise HTTPException(400, "Unsupported page size.")
        return [SimpleNamespace(**row) for row in rows[skip : skip + limit]]

    async def count(self, request, q=None, filters=None):
        return len(self._rows(request, q, filters))

    async def find_by_pk(self, request, pk):
        return next(
            (SimpleNamespace(**row) for row in self._rows(request) if row["id"] == pk), None
        )

    async def find_by_pks(self, request, pks):
        selected = set(pks)
        return [SimpleNamespace(**row) for row in self._rows(request) if row["id"] in selected]


class AccountsView(ReadOnlyView):
    key = "accounts"
    display_name = "Account"
    menu_label = "Accounts"
    searchable_fields = ["id", "name"]
    sortable_fields = ["id", "name", "balance", "card_count", "rating", "connected", "busy"]
    fields = [
        StringField("id", label="Account key", filters=[]),
        StringField("name", label="Name", filters=[]),
        IntegerField("player_id", label="Player ID", filters=[]),
        IntegerField("balance", label="Spendable money", filters=[]),
        IntegerField("card_count", label="Owned cards", filters=[]),
        IntegerField("rating", label="Rating", filters=[]),
        BooleanField("connected", label="Connected", filters=[]),
        BooleanField("busy", label="In an activity", filters=[]),
        StringField("rating_mode", label="Rating policy", filters=[]),
    ]
    exclude_fields_from_list = ["player_id", "rating_mode"]
    detail_template = "account_detail.html"

    @staticmethod
    def load_rows(host):
        return queries.account_rows(host)

    async def find_by_pk(self, request, pk):
        if not isinstance(pk, str) or not pk or len(pk) > 128 or any(ord(c) < 32 for c in pk):
            return None

        def load_detail():
            try:
                return queries.account_detail(self.host, pk)
            except KeyError:
                return None

        row = capture(request, (self.key, pk), load_detail)
        return None if row is None else SimpleNamespace(**row)

    async def serialize(self, obj, request, include_relationships=True, include_select2=False):
        data = await super().serialize(obj, request, include_relationships, include_select2)
        data["inventory"] = getattr(obj, "inventory", [])
        data["pending_claims"] = getattr(obj, "pending_claims", [])
        return data


class ClaimsView(ReadOnlyView):
    key = "claims"
    display_name = "Pending claim"
    menu_label = "Pending claims"
    searchable_fields = ["id", "account_key", "account_name", "kind", "receipt"]
    sortable_fields = [
        "id",
        "account_key",
        "account_name",
        "kind",
        "money",
        "cards",
        "prize_points",
    ]
    fields = [
        StringField("id", label="Claim ID", filters=[]),
        StringField("account_key", label="Account key", filters=[]),
        StringField("account_name", label="Name", filters=[]),
        StringField("kind", label="Claim type", filters=[]),
        StringField("receipt", label="Receipt", filters=[]),
        IntegerField("money", label="Pending money", filters=[]),
        IntegerField("cards", label="Pending cards", filters=[]),
        IntegerField("prize_points", label="Prize points", filters=[]),
    ]
    exclude_fields_from_list = ["id"]

    @staticmethod
    def load_rows(host):
        return queries.claim_rows(host)


class RoomsView(ReadOnlyView):
    key = "rooms"
    display_name = "Room"
    menu_label = "Rooms and events"
    searchable_fields = ["id", "lobby", "name", "event_id"]
    sortable_fields = [
        "id",
        "lobby",
        "name",
        "members",
        "tables",
        "occupied_tables",
        "computer_enabled",
    ]
    fields = [
        StringField("id", label="Room ID", filters=[]),
        StringField("lobby", label="Lobby", filters=[]),
        StringField("name", label="Room", filters=[]),
        IntegerField("members", label="Players present", filters=[]),
        IntegerField("tables", label="Tables", filters=[]),
        IntegerField("occupied_tables", label="Occupied tables", filters=[]),
        BooleanField("computer_enabled", label="Computer available", filters=[]),
        StringField("event_id", label="Event", filters=[]),
        StringField("rules", label="Rules", filters=[]),
    ]
    exclude_fields_from_list = ["id", "event_id", "rules"]

    @staticmethod
    def load_rows(host):
        return queries.room_rows(host)


class ReceiptHistoryView(ReadOnlyView):
    key = "receipts"
    display_name = "Operator receipt"
    menu_label = "Receipt history"
    searchable_fields = ["id", "action", "account_key", "operator", "source", "reason"]
    sortable_fields = ["id", "action", "account_key", "operator", "source", "committed_at"]
    fields_default_sort = [("committed_at", True), "id"]
    fields = [
        StringField("id", label="Request ID", filters=[]),
        StringField("action", label="Action", filters=[]),
        StringField("account_key", label="Account key", filters=[]),
        StringField("operator", label="Operator", filters=[]),
        StringField("source", label="Source", filters=[]),
        StringField("reason", label="Reason", filters=[]),
        StringField("committed_at", label="Saved at (UTC)", filters=[]),
    ]

    def __init__(self, host, *, actions_enabled=False):
        self.row_actions = ["view", "receipt"] if actions_enabled else ["view"]
        super().__init__(host)

    def _rows(self, request, q=None, filters=None):
        if self.host.storage_failed is not None:
            raise HTTPException(503, queries.RECEIPTS_UNAVAILABLE)
        return super()._rows(request, q, filters)

    @staticmethod
    def load_rows(host):
        return queries.receipt_rows(host)

    @link_row_action(name="receipt", text="Open receipt", icon_class="default_actions.view")
    def receipt_link(self, request, pk):
        row = next((row for row in self._rows(request) if row["id"] == pk), None)
        if row is None:
            raise HTTPException(404, "This operator receipt was not found.")
        route = {
            "Event grant": "event-grant-receipt",
            "Event eligibility": "event-member-receipt",
            "Event progress": "event-member-receipt",
        }.get(row["action"], "prize-award-receipt")
        return str(request.url_for("admin:" + route).include_query_params(id=pk))


def overview_view(host, started_at):
    async def build(request):
        data = capture(
            request, "overview", lambda: queries.server_overview(host, started_at=started_at)
        )
        request.state.tm_capture_time = data["captured_at"]
        rows = [
            ["Captured at", data["captured_at"]],
            ["Server version", data["version"]],
            ["Uptime", f"{int(data['uptime_seconds']) // 60} minutes"],
            ["Saved state", data["storage"]],
            ["Diagnostics", data["diagnostics"]],
            ["Connected accounts", data["connected_accounts"]],
            ["Accounts", data["accounts"]],
            ["Rooms", data["rooms"]],
            ["Tables", data["tables"]],
            ["Occupied tables", data["occupied_tables"]],
        ]

        async def values(_request):
            return rows

        return ColumnWidget(
            children=[
                TableWidget(
                    title="Live server overview — refresh this page for current values",
                    columns=["Status", "Value"],
                    rows_callback=values,
                )
            ]
        )

    return CustomView(menu_label="Overview", widget=build)
