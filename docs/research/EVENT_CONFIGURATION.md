# Explicit local event configuration

The standalone runner accepts `--events PATH.json`. This is trusted host input,
not a client request or a recovered historical event schedule. Keep the same
configuration when reopening a save: durable event records validate its
fingerprint. With no configuration, event directories remain empty.

The file is an object with an `events` array, optional `tables` array and optional
`progress_policy` (`none`, `wins` or `rounds`; default `none`). Each event requires:

- `id`: stable local identifier.
- `listing`: `key_region`, `key_event`, `description` strings. Optional
  `scalar`, four-byte `flags`, `start` and `end` preserve the original listing
  fields. Region/name are exact directory lookup keys, limited to26/30 bytes.
- `data`: original display fields. Supported keys are `start`/`end` (five
  raw integer components each), `classes`, `steps_per_class`, `prizes` and
  `missions`. The raw five-component date labels are not all established;
  do not infer a calendar ordering from the API names.
- `start` and `end`: bounded host control times in Unix seconds.
- `room_raw`, `init_raw`, `card_raw`: distinct routable41-bit raw POL IDs,
  excluding all existing account/service IDs.

Optional `init_channel`/`card_channel` strings configure the E/F channels.
Use distinct channels for multiple events. Room channels are generated stably
from each room ID. Text uses the current PC client's Latin1 fixture encoding.

Set `card_service_enabled` to true to advertise the proven F collection
service. `match_enabled` with `entry_answer=1` requires at least one configured
playable table. With `entry_answer=2`, it enables original mode8 observer-room
entry and requires no playable table. Mode8 skips owned-card selection and has
no CPU matchmaking. `--spectator-preview` supports observers of configured
playable event tables; separate mode8 broadcast-room routing remains unproved.
See `EVENT_MODE8_NOTES.md` and `EVENT_SPECTATOR_NOTES.md`.

Each table requires `event_id`, `table_raw` (distinct raw41-bit route) and
`channel`. Optional fields: `player_count` (2/3), `turn_limit` (0..5),
`departure_rule` (0..2), `seed` (unsigned32), `start_fields` (16/25 non-CBC
original tiles) and `cpu_cards` (exactly five full8 integer records for an
explicit synthetic second seat). Default borrowed hands are the five owned
cards selected through the native E-session selection scene. They return to
the owned collection locally before EQuit and remain unchanged by event play.
See `events.example.json` for a two-human fixture. Set `--accounts 3` when a
configured event needs three humans.

Explicit boards must leave five placement cells per seat: at most six occupied
tiles on a 16-cell board or ten on a 25-cell board. F3/4/5 effect tiles remain
placeable and do not consume this allowance. F18/CBC remains unsupported for
event play. Board and seed checks run when constructing `EventTableConfig`,
including JSON loading, before the table can be advertised or players reserved.
The validated board is frozen so later changes to a caller's list cannot make
an accepted table unplayable. This enforces the existing round engine's limits;
it introduces no historical board distribution or event reward policy.

Multiple table entries may name the same event_id. Selected players are grouped
into free configured tables in configuration order. Proposals carry each actual
table number and identity; rounds, deadlines, consent, observers, result journals
and cleanup remain independent. A room with both E and F services supports126
playable tables within the client's128-record limit. Four configured local
accounts allow two simultaneous two-human matches. Historical matchmaking
preferences and scheduling remain distinct from this explicit local policy.
Use --events events.multi.example.json --accounts 4 for the two-table example.
The room publishes state1 while a group is preparing, state2 after all GameOK
messages, and state0 after that group's release; other tables keep their state.

A prize display row can contain `cards` (at most3 model IDs), `money`, and
`description`. `missions` maps original bit strings `"0"` through `"7"`
to positive target bytes, with at most3 displayed missions. These are UI data:
neither display records nor client-supplied progress create awards.

After authenticated room admission, trusted host policy may call:

    server.events.update_progress(account_key, event_id, stable_update_id,
                                  steps_delta=..., completed_missions=...)
    server.event_cards.grant(account_key, event_id, stable_grant_id,
                             cards=..., money=...)
    server.persist()

IDs are durable and replay-safe. Grant cards must be validated full cards with
deck255. The client claims at most9 per visit, commits cards at Get, and can use
the original overflow editor before accepting them. Money commits before its
unacknowledged animation. Membership, progress, grant receipts and account data
share one atomic save. Historical automatic reward policy remains unclaimed.
