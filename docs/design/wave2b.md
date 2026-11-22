# Wave 2b design: shared checks, edit parser, envelope codec, exceptions, staging

This document is based on main at 897d108, after the PostgreSQL-only cut. Wave 2a runs in parallel and will move some line numbers. Each item therefore names the function or class it touches, so the implementer can re-grep it.

Every 2b PR must leave these unchanged: the game wire bytes, the profile and loader protocols, the PostgreSQL rows and journal values, and the golden save (`tests/support/golden_save.py`, `tests/fixtures/storage/*.json`), byte for byte.

## PR order and owners

| # | PR | Owner | Depends on | Size (src) | Risk |
|---|---|---|---|---|---|
| 1 | `tmserver/checks.py` and its call sites | cross | 2a merged | about −100 | low |
| 2 | Config-check dedup (2d) | network | 2a merged | about −15 | low |
| 3 | Shared edit-section parser | economy | 1 | about −70 | medium |
| 4 | GTM0G envelope codec | game | 1 | about −35 | medium |
| 5 | `Staged` mixin and `transaction()` | cross | 1 | about −150 to −200 | medium |
| 6 | Exception consolidation, one PR per package | package owners | 1, 3, 4, 5 | about −60 in all | low |

- PRs 1 and 2 can open together.
- PRs 3, 4 and 5 can run in parallel once 1 merges. They touch different regions of the same economy and events files; whichever merges second rebases, and expects import-line conflicts only.
- PR 6 goes last, because it renames the error classes that PRs 1, 3 and 4 pass as `error=`.
- The owners follow the wave-1 areas. The coordinator may reassign.

## 1. `tmserver/checks.py`

The module has no domain imports. Every function takes the exception class to raise as `error=`, so each call site keeps its current exception type until PR 6. The messages become generic, for example `"invalid {what}"`. I found no `assertRaisesRegex` in tests that pins any of the replaced messages. The PR must re-check this with `grep -rn assertRaisesRegex tests`.

```python
STABLE_ID = r"[A-Za-z0-9_.:-]{1,128}"

def integer(value, maximum=0xFFFF_FFFF, minimum=0, *, error=ValueError, what="integer"):
    """type(value) is int (so bool is rejected) and minimum <= value <= maximum."""

def decimal(text, maximum=0xFFFF_FFFF, minimum=0, *, signed=False, error=ValueError, what="decimal"):
    """str or ASCII bytes matching -?[0-9]{1,10} when signed, else [0-9]{1,10}; then the bounds."""

def stable_id(value, *, error=ValueError, what="ID"):
    """str that fullmatches STABLE_ID."""

def exact_keys(mapping, names, *, error=ValueError, what="fields"):
    """set(mapping) == set(names)."""

def hex_bytes(text, maximum, *, error=ValueError):
    """Even-length hex of at most `maximum` bytes with no embedded NUL."""
```

Also add `Catalog.validate_batch(cards, *, limit=30, error=ValueError)` in `cards/catalog.py`. It validates each card and requires at most `limit` cards, all undecked (`deck == 255`).

`signed=True` exists only to keep today's acceptance of `-0`. That matters to the edit-parser dialects in PR 3.

### Call sites it replaces

The **Uses** column counts call sites at 897d108. Where the argument order already matches, the definition can become `functools.partial(...)` and the call sites stay as they are.

| Definition today | Uses | Replacement |
|---|---|---|
| `economy/auctions/ledger.py` `_uint(v, max, min)` | 23 | `partial(integer, error=AuctionError)` |
| `economy/match_ledger.py` `_int(x, lo, hi)` | 9 | `integer(x, hi, lo, error=…)`; call sites change because the argument order differs |
| `economy/match_ledger.py` `_id` | 3 | `stable_id` |
| `economy/packets.py` `_int(text, min, max)` (imported by `economy/service.py`, `economy/shop.py`) | — | `decimal(text, max, min, signed=True, error=EconomyError)` |
| `economy/packets.py` `_hex`, `economy/prize_center.py` `_hex` | — | `hex_bytes` |
| `economy/packets.py` `_keys`, `economy/prize_center.py` `_fields`, `economy/result_rewards.py` `_fields`, `economy/trade.py` `fields` (also imported by `economy/trade_router.py`), `events/packets.py` `_keys` (imported by `events/cards.py`) | 8 / 4 / 18 / — | `exact_keys` |
| `economy/prize_center.py` `_integer`, `economy/result_rewards.py` `_int`, `economy/trade.py` `integer` (also imported by `economy/trade_router.py`), `events/packets.py` `_number` (imported by `events/cards.py`) | 6 / 2 / 8 / — | `decimal(…, error=…)` |
| `economy/result_rewards.py` `_id`, `events/packets.py` `_id` (imported by `events/cards.py`), `rankings/history.py` `_identifier` | 3 / — / 7 | `stable_id` |
| `events/cards.py` `_integer(v, max)` | 4 | `integer` |
| `events/matches.py` `_uint(v, max, label)` | 12 | `integer(v, max, error=MatchError, what=label)` |
| `events/packets.py` `integer(v, bits, signed=)` | 15 | Keeps its bit-width signature as a two-line wrapper over `integer` |
| `game/rules.py` `_integer(v, name, min, max)` | 8 | `integer(v, max, min, error=RulesError, what=name)` |
| `profiles/characters.py` `decimal(bytes, max)` | 7 | `checks.decimal`, which accepts bytes |
| `rankings/history.py` `_integer(v, lo, hi, label)` | 7 | `integer(v, hi, lo, error=RankingError, what=label)` |
| `rankings/periods.py` `_uint` | 3 | `integer(…, 0xFFFF_FFFF, error=RankingError)` |
| `rankings/service.py` `_uint(v, bits, name)` | 3 | `integer(v, (1 << bits) - 1, …)` |
| Inline stable-ID regex: `economy/auctions/ledger.py` (external return IDs, in the method and in `restore`), `economy/prize_center.py` `_award` | 3 | `stable_id` |
| Pydantic `StableId` (`admin/audit.py`) and `_Identifier` (`events/results.py`) | — | `Field(pattern=f"^{STABLE_ID}$")` from the shared constant. The pattern is the same, so the save format does not change |
| `len(cards) > 30 or any(c.deck != 255 …)`: `economy/auctions/ledger.py` (external returns, and its `restore`), `economy/match_ledger.py` `_cards`, `economy/result_rewards.py` `_cards`, `economy/service.py` `commit_match_rewards`, `events/cards.py` `grant` | 6 | `catalog.validate_batch` |

These stay where they are, because each has a contract the shared functions should not absorb:
- `rooms/metadata.py` `_integer`: config errors that name the field and accept booleans on request.
- The admin form parsers in `admin/awards.py`, `admin/event_grants.py` and `admin/event_members.py`: canonical no-leading-zero text with operator-facing messages.
- `economy/auctions/packets.py` `_number`: wire sign wrapping. Only its parse step can call `decimal(signed=True)`.
- `economy/service.py` `_apply_deck_changes`: a 13-card batch that allows deck slots.

**Proof of unchanged behaviour:** the full portable and native lanes, `tests/cards/test_inventory_edit_compatibility.py`, and the golden save.

## 2. Config-check dedup (2d)

Today the same seat and board rules are checked at four layers. `game/match.py` `MatchService.__init__` is the owner: `OrdinaryTable.__init__` calls `new_match()`, so every `Server` builds a `MatchService` before startup recovery writes anything.

- **Delete** the two duplicate checks at the top of `network/server.py` `Server.__init__` (the "three-player tables require versus" and "automatic seats" blocks). Keep one check that `MatchService` cannot see, `match_player_count == 3 and account_count < 3`, worded as an account-count error. `MatchService` still covers every deleted check ("three-player mode requires humans…", "automatic seats require…", "board shape must match…", "CBC materialization requires…"), and it raises the same `ValueError` type. The commit message must name those checks.
- **Keep** the `cli/serve.py` `arguments()` checks. They fail before the PostgreSQL connection, the writer lock and the DLL load, and they name the flag.
- **Keep** `rooms/config.py` `_validate_seats`. It reports file errors at load time with the room-file wording.

Alternative, if the owner prefers one layer: also delete the CLI board checks and let `main()` print the `MatchService` error. The cost is that the error then arrives only after the database connection and the DLL load.

**Proof:** `tests/rooms/test_room_configuration.py`, `tests/game/test_flexible_seats.py`, the `tests/cli` tests and the portable lane. No test pins the deleted messages.

## 3. Shared edit-section parser

Today six services each parse the Erase/Decks header (Mode, C, the optional side `P`, and the Mode 2 completion marker), then call `cards/edits.py` `parse_edit_records`:
- `economy/shop.py` `_edit`
- `economy/prize_center.py` `_edit`
- `events/cards.py` `_edit`
- `economy/auctions/service.py` `_edit_collection`
- `economy/trade.py` `_edit_values`
- `economy/result_rewards.py` `handle`

Their differences are pinned by `tests/cards/test_inventory_edit_compatibility.py`:

| Input | shop | prize | event | auction | trade | result | Cause |
|---|---|---|---|---|---|---|---|
| `Erase Mode=01` | ok | ok | reject | reject | ok | ok | Mode parsed as a number vs compared as the text `"1"` |
| `Erase Mode=02` (no list) | reject | reject | reject | reject | ok | ok | Marker must be exactly `{"Erase": {"Mode": "2"}}` vs parsed as a number |
| `C=-0`, card field `-0` | ok | reject | reject | ok | reject | reject | Signed vs unsigned number parser |
| `P` field | forbidden | forbidden | forbidden | forbidden | required, ≤ 1 | required, ≤ 2 | Side-bound protocols |
| Deck name `\x01`, `\x7f` | ok | ok | ok | reject | ok | ok | Auction passes its printable `_hex_name` decoder |

### Design: one function plus a dialect value

Put both in `cards/edits.py`.

```python
@dataclass(frozen=True)
class EditDialect:
    number: Callable          # parser for C, Mode, P and card fields
    numeric_mode: bool        # Mode=1 is parsed by `number` (True) or must equal "1" (False)
    strict_marker: bool       # Mode 2 must be exactly {"Erase": {"Mode": "2"}} (True)
    side_limit: int | None = None   # None: P forbidden; n: P required, <= n
    name: Callable = deck_name
    error: type = ValueError

def parse_edit_section(sections, subtype, dialect, *, side=None, token=None):
    """Validate the header and return (values, renamed), or None for the Mode 2 marker.

    token defaults to Erase for 0x10 and Decks otherwise; the shop passes "Sell" for 0x16.
    """
```

The function checks the header keys (`{Mode, C}` or `{C}`, plus `P` when `side_limit` is set), the side value against `side`, and the Mode rules for the dialect. It then calls `parse_edit_records` with `dialect.number`, `dialect.name` and `dialect.error`. Each service keeps its own follow-up step:
- `apply_card_edits` with its own `discard=`;
- trade's model and kind check (`value[0] >= 250 or value[2] > 3`);
- the shop's Sell balance.

### Option A: keep the quirks

The owner has not decided; this is the default. Each service defines one module constant:

| Service | `number` | `numeric_mode` | `strict_marker` | `side_limit` | `name` |
|---|---|---|---|---|---|
| shop | `decimal(signed=True)` | True | True | None | `deck_name` |
| prize | `decimal` | True | True | None | `deck_name` |
| event | `decimal` | False | True | None | `deck_name` |
| auction | `decimal(signed=True)`, via auctions `_number` | False | True | None | `_hex_name(…, 17)` |
| trade | `decimal` | True | False | 1 | `deck_name` |
| result | `decimal` | True | False | 2 | `deck_name` |

The compatibility test stays exactly as it is. That is the proof.

### Option B: unify

Every service uses one dialect: `decimal` (unsigned), `numeric_mode=False`, `strict_marker=True`, and `name=deck_name`. Trade and result keep their `side_limit`, because `P` is part of those protocols.

The client sends only canonical text (`Mode=1`, `Mode=2`, unsigned numbers), so accepting `01`, `02` and `-0` is tolerance nothing needs. The auction deck-name rule should follow whatever the owner decides for names; I recommend `deck_name` everywhere.

The compatibility test becomes one expected value per row. Proof: `tests/protocol/test_native_capture_replay.py` and the native lane, which replay real client bytes.

Option B is a one-commit follow-up to Option A: change the constants and the test table. Build A first.

**Out of scope:** the Get/Quit barrier with closed-Quit replay is repeated in the shop, prize and event services. It can follow the same pattern later, but it is not part of 2b.

## 4. GTM0G envelope codec

There are two codecs for the same envelope, `GTM0G` + family, secondary, subtype and session (two hex digits each) + an ASCII body of at most 447 bytes:
- `economy/packets.py`: `named()` encodes and `parse_named()` decodes. It is used by 12 src modules and about 70 test modules.
- `game/packets.py`: `packet()` encodes and `decode()` decodes. It is used by 12 src modules and about 85 test modules.

The encoders produce the same bytes. The only difference is the body check: `named` requires 32..126 throughout, while `packet` forbids only 0, 10 and 13.

The decoders differ:

| Rule | `parse_named` (strict) | `decode` (lenient) |
|---|---|---|
| Not an envelope | raises | returns None (fullmatch) |
| Secondary byte | must be 00 | ignored |
| Body | required, starts with `@`, printable 32..126 | may be empty; no NUL, CR or LF |
| Section name | `[A-Za-z][A-Za-z0-9]*` or digits | any text before `=`; the header value must be empty |
| Field name / value | `[A-Za-z][A-Za-z0-9]*` / `[^/@]*` | non-empty / anything |

### Design: `protocol/envelope.py`

```python
class Envelope(NamedTuple):
    family: int
    subtype: int
    session: int
    sections: dict

def encode(family, subtype, session, body, *, printable=True, error=ValueError) -> bytes
def parse(payload, *, strict=True, error=ValueError) -> Envelope
```

`Envelope` is a NamedTuple, so `family, subtype, session, sections = parse_named(p)` keeps working.

`game/packets.Packet` uses attribute names `primary` and `argument` (about 180 attribute reads). Either rename those reads, or give `Envelope` two read-only property aliases until PR 6. I recommend renaming the reads in the same PR: it is mechanical, and the native lane covers it.

The old names stay as one-line dialect bindings, so no test import changes:
- `named = partial(encode, error=EconomyError)`
- `parse_named = partial(parse, error=EconomyError)`
- `packet = partial(encode, printable=False, error=MatchError)`
- `decode` keeps its fullmatch pre-check (it returns None for non-envelopes, and dispatch falls through on None), then calls `parse(strict=False, error=MatchError)`.

To unify later, the game path switches to `strict=True`. That is the same owner decision as Option B in section 3, with the same proof (capture replay plus the native lane). Do not do it in 2b.

**Proof:** `tests/fixtures/protocol/envelope_vectors.json` and `envelope_review_vectors.json` (through `tests/economy/test_economy_service.py`), the native lane, capture replay, and the golden save.

## 5. `Staged` mixin and one `transaction()` context

### Today

Twelve services hand-write a pair of methods:
- `stage_state` copies selected containers and rebinds the economy and other dependencies;
- `publish_state` copies a hand-written list back.

On top of them sit hand-written coordinators:
- `economy/claim_transactions.py`: `auction_transaction`, `prize_transaction`, `event_transaction`;
- `economy/match_transactions.py`: `MatchState`, `match_transaction`;
- `economy/commerce.py`: `Commerce`;
- `profiles/transactions.py`;
- `accounts/provisioning.py`;
- the three `apply_*` functions in `admin/actions.py`;
- `network/server.py`: `advance_rankings`, `ensure_account`.

### What the staging protects

The design keeps both properties below.

1. A request rejected part-way through mutates only copies. Nothing half-changed is published, so nothing half-changed is saved by the next commit (journals are saved whole).
2. Capability identity. Tests pin it with about 47 `assertIs` checks, in four forms:
   - **I1.** Accounts outside `keys` keep their object (`economy._accounts[other] is untouched`).
   - **I2.** Reservation objects are never copied (`economy._reservations[key] is token`), whether the transaction commits or fails.
   - **I3.** Editors of accounts outside `keys` keep their object (another account's auction collection row, result-reward editor, prize session, event card session).
   - **I4.** After a failed transaction, the editors of accounts inside `keys` keep their object, because nothing was published.

### The mixin (new `tmserver/staging.py`)

```python
class Staged:
    DEPENDENCIES = ()   # attribute names rebound to staged dependencies, never published

    def stage(self, keys=frozenset(), **dependencies):
        staged = copy(self)
        for name, value in vars(self).items():
            if name in self.DEPENDENCIES:
                continue
            if isinstance(value, (dict, list, set, random.Random)):
                setattr(staged, name, copy(value))   # shallow: values keep identity (I3)
        vars(staged).update(dependencies)
        self._stage_keys(staged, frozenset(keys))
        return staged

    def _stage_keys(self, staged, keys):
        """Copy the mutable editors that belong to `keys`. Default: nothing."""

    def publish(self, staged):
        vars(self).update(
            (name, value) for name, value in vars(staged).items() if name not in self.DEPENDENCIES
        )
```

Copying every container attribute by default, instead of keeping a list of fields, removes the risk of forgetting one. Publishing every attribute also publishes counters such as `_next_id` and `_next_pending`, which the hand-written lists have to remember today. `copy(random.Random)` gives an independent generator; I checked that it does not advance the original. That replaces the prize service's `deepcopy(self._rng)`.

`EconomyService.edit_accounts` stays as it is. It is the primitive that copies only the keyed accounts and passes reservations through by reference, which gives I1 and I2.

### Per-service table

| Host attribute | Class | `DEPENDENCIES` | `_stage_keys` (replaces today's hand copies) | Journal |
|---|---|---|---|---|
| `auction_ledger` | `AuctionEconomy` | `economy` | `_collections[k] = replace(row)` | `auction` |
| `auction` | `AuctionService` | `ledger`, `economy` | `_snapshots[k] = dict(…)`; `_collections[k] = replace(row, cards=list(row.cards), names=dict(row.names))` | — |
| `prize` | `PrizeService` | `economy` | `_known_awards[k] = set(…)`; `_active[k] = replace(row, pending=replace(pending, cards=list(…), names=dict(…)))` | `prize` |
| `match_economy` | `MatchEconomy` | `economy` | `deepcopy` of rounds whose seats include a key (as today) | `match_economy` |
| `result_rewards` | `ResultRewardService` | `economy`, `auction` | `_pending[k] = replace(row, cards=list or None, names=dict or None)` | `result_rewards` |
| `ratings` | `RatingPolicy` | `economy` | none: `history` and `periods` are replaced, never mutated | `ratings` |
| `events` | `EventHost` | `economy` | the inner dicts of `_members` and `_updates`, for every event (not keyed) | `events` |
| `event_cards` | `EventCardService` | `economy`, `host` | `_grants` rows with `row.key in keys` → `replace(row)`; `_active[k] = replace(row, cards=list(…), names=dict(…))` | `event_cards` |
| `event_results` | `EventResults` | — | none | `event_results` |
| `character_profiles` | `CharacterProfiles` | — | none. It keeps the live economy on purpose: profile reads cross accounts | `character_profiles` |
| `identities` | `IdentityRegistry` | — | none. `stage_links` becomes `staged.add_links(links)`, with the same reassignment check | `identities` |
| `admin_audit` | `AdminAudit` | — | none. `stage_receipt` becomes `staged.record(receipt)`, with the same duplicate check | `admin_audit` |

How the identity rules survive:
- **I3:** the shallow container copy keeps the values of non-key accounts.
- **I2:** `replace(row)` keeps a reservation field pointing at the same object.
- **I4:** `publish` runs only after the save.

Auction collections and match rounds never share a key: an active collection holds an `AuctionReservation`, and an account holds at most one reservation. So staging the ledger with match keys copies no collection rows, which keeps today's `stage_state(economy, ())` behaviour in `MatchState`.

### The context (new `tmserver/transactions.py`)

```python
SERVICES = {  # host attribute: (journal name or None, {dependency attribute: source})
    "auction_ledger": ("auction", {"economy": "economy"}),
    "auction": (None, {"ledger": "auction_ledger", "economy": "economy"}),
    "prize": ("prize", {"economy": "economy"}),
    "match_economy": ("match_economy", {"economy": "economy"}),
    "result_rewards": ("result_rewards", {"economy": "economy", "auction": "auction_ledger"}),
    "ratings": ("ratings", {"economy": "economy"}),
    "events": ("events", {"economy": "economy"}),
    "event_cards": ("event_cards", {"economy": "economy", "host": "events"}),
    "event_results": ("event_results", {}),
    "character_profiles": ("character_profiles", {}),
    "identities": ("identities", {}),
    "admin_audit": ("admin_audit", {}),
}

@contextmanager
def transaction(host, keys=(), *names):
    """Stage the named services and their dependencies; save, then publish.

    Never await inside. Pass every account the body reads through tx.economy
    (the edit_accounts rule). Set tx.save = False when the body proves nothing
    changed.
    """
```

How it works:
- Dependencies are added automatically. For example `"auction"` adds `"auction_ledger"` and `"event_cards"` adds `"events"`.
- Services are staged in `SERVICES` order, inside `host.economy.edit_accounts(keys)`.
- The body receives `tx`, with `tx.economy`, one attribute per staged service, and `tx.save = True`.
- After the body, the context calls `host.persist_changes(economy.snapshot()["accounts"], {journal: staged.snapshot()})` for every staged journal, then publishes each service.
- An exception in the body or in the save publishes nothing, which is today's behaviour.

Dependencies that were not asked for are still saved. The PostgreSQL writer skips unchanged journals (`save_changes` compares each journal with its baseline), so the save writes no extra rows. The golden PostgreSQL test confirms that the saved rows do not change.

### Call-site mapping

| Today | Becomes |
|---|---|
| `auction_transaction(host, keys)` | `transaction(host, keys, "auction")`; body uses `tx.auction` |
| `prize_transaction(host, key)` | `transaction(host, (key,), "prize")` |
| `event_transaction(host, keys)` → `EventState(events, cards, results)` | `transaction(host, keys, "events", "event_cards", "event_results")`; `.cards` → `.event_cards`, `.results` → `.event_results` |
| `match_transaction(host, keys)` → `MatchState` | `transaction(host, keys, "match_economy", "result_rewards", "ratings")`. The attribute names are unchanged, and `auction_ledger` is added as a dependency |
| `Commerce.edit_account`, `commit_exchange`, `apply_deck_changes`, `apply_match_deck_changes` | `Commerce(host)`: each method runs `transaction(host, keys)` and calls the same method on `tx.economy`. `Commerce` stays, because `TradeService` and `EventMatchmaking` take it as a duck-typed stand-in for `EconomyService` |
| `Commerce.initialize_profile`, `Commerce.shop` | The same, with `tx.save = guild changed` and `tx.save = replies is not None` respectively |
| `profiles/transactions.py` `character_profile_request` | `transaction(host, (), "character_profiles")`; `tx.save = records changed`. The module goes |
| `accounts/provisioning.py` `import_bindings`, `provision_identity` | `transaction(host, (), "identities")`; provisioning runs on `tx.economy`, and `add_links` on `tx.identities` |
| `admin/actions.py` `apply_prize_award` / `apply_event_grant` / `apply_event_member` | `transaction(host, (key,), "prize", "admin_audit")` / `(…, "event_cards", "admin_audit")` / `(…, "events", "admin_audit")`. `_commit` becomes `tx.admin_audit.record(receipt)`. The event grant now stages `key`; the unchanged account is not rewritten |
| `Server.advance_rankings` | `transaction(self, (), "ratings")`; `tx.save = tx.ratings.advance_week()` |
| `Server.advance_auctions` | `transaction(self, (), "auction")` |
| `Server.ensure_account` | Stays on `edit_accounts`. It deliberately saves the whole `journal_snapshot()` with a new account; changing that is a separate decision |
| `events/results.py` `EventResults.record` nested `host.stage_state` / `publish_state` | `host.stage(economy=host.economy)` / `host.publish(staged)`. This is the only nested staging, and it stays |

Removed:
- `economy/claim_transactions.py` and `economy/match_transactions.py`;
- `profiles/transactions.py`;
- every `stage_state` / `publish_state` pair, `stage_links` and `stage_receipt`.

`AdminState` (`cli/admin.py`) already has the host attributes the admin actions use, plus `persist_changes`, so it works with `transaction()` unchanged. It must define the services named in each call; today it lacks `ratings`, `identities` and `character_profiles`, and no admin action needs them.

The tests that call the coordinators must switch to `transaction(...)` in the same PR:
- `tests/economy/test_claim_transactions*.py`, `test_match_transactions*.py`, `test_commerce*.py`;
- `tests/support/golden_save.py`, through `ClaimFixture` and `TableFixture`;
- the admin and events tests that patch `persist_changes`.

**Proof:**
- the 47 `assertIs` identity checks;
- the claim, match and commerce rollback tests (portable lane, and the PostgreSQL lane for the lost-ack cases);
- `tests/storage/test_golden_save*.py`, byte for byte;
- the native lane for match commits.

## 6. Exception consolidation

Wave 1 already turned several classes into aliases: `MatchEconomyError`, `PrizeError` (economy), `TradeError`, `EventCardError`, `RatingHistoryError` and `ResultStatsError`.

### Classes that stay

Each one is caught by its own type, or carries data:

| Class | Why |
|---|---|
| `EconomyError` | Root of the account and economy rejections; caught in `admin/actions.py`, `economy/result_rewards.py`, `economy/service.py`, `events/cards.py` |
| `AuctionError(EconomyError)` | Carries `.code` for the wire error reply (`economy/auctions/service.py`) |
| `ResultRewardError(EconomyError)` | Caught on its own in `rooms/table.py` `refresh_result_contexts`; folding it would also swallow other `EconomyError`s |
| `MatchError` | Root of the gameplay rejections; caught in `rooms/spectators.py` |
| `SpectatorError(MatchError)` | Caught in `rooms/spectators.py` to evict a single observer |
| `RulesExecutionError` | Aborts the round without retrying (`events/matchmaking.py`, `rooms/table.py`) |
| `AdminActionError`, `AdminActionConflict` | The admin web forms map a conflict to its own message |
| `AccessError`, `InvalidToken`, `ProviderUnavailable`, `AccountCapacityError` | `auth/web.py` maps each one to its own HTTP response |
| `OriginalCodeError(RuntimeError)` | Operator setup failure (image or DLL); not a request rejection |

### Classes that go

Each is replaced by its parent, and each re-wrap that only changes the class is deleted. One PR per owning package, in parallel:

| Package (owner) | Goes | Becomes | Re-wraps deleted |
|---|---|---|---|
| economy | aliases `MatchEconomyError`, `PrizeError`, `TradeError` | `EconomyError` | — |
| events | `EventError`, alias `EventCardError` | `EconomyError` | `events/matchmaking.py` `_identity`: `except ValueError → EventError` stays as `→ EconomyError`, because it converts the admission `ValueError` |
| rankings | `RankingError`, aliases `RatingHistoryError`, `ResultStatsError` | `EconomyError` | the two `raise EconomyError(str(exc))` blocks in `economy/service.py` (`commit_match_growth`), which become no-ops |
| game | `AIError`, `BoardGenerationError`, `DepartureError`, `RulesError`, `SpecialRulesError`, `OpponentError`, `PrizeError` (`game/prizes.py`) | `MatchError` | `game/match.py`: the `except PrizeError` in the GetSelect handler and the `except OpponentError` in the computer-opponent check |
| rooms | `TableMetadataError` | `MatchError` | `game/match.py`: the `except TableMetadataError` around `table_info` |
| profiles | `PreferenceError` | `EconomyError` | `profiles/preferences.py`: the wrap of `parse_named`, which already raises `EconomyError` |

All of these are `ValueError` subclasses today, and so are their targets. Every `except ValueError` (24 sites) and every `except EconomyError` or `except MatchError` keeps catching what it caught before, except where a class moves to a new parent:
- `EventError` and `RankingError` were already `EconomyError` subclasses.
- The game and rooms classes move under `MatchError`. The game and rooms owners must check the one `except MatchError` site, `rooms/spectators.py` `_attach` around `bootstrap(snapshot)`. After the fold it also catches any of the moved classes raised inside `bootstrap`, which evicts that observer instead of failing the publish.

Tests: about 150 `assertRaises` references move to the parent class. The commit message names, for each class, the parent that still covers it.

## What 2b does not do

- the Get/Quit barrier (section 3);
- removing internal `type(x) is bool` / `type(x) is int` checks (wave 2a, per area);
- pydantic journals and journal pruning (2a);
- changing `Server.ensure_account`'s whole-journal save;
- unifying the parser dialects, which is the owner's decision (Option B);
- the strict envelope dialect for game messages.
