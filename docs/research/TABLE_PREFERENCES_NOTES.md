# Durable table and game defaults

`table_preferences.py` implements account defaults saved by primary24 `@Save=/Tbl=0|1/Game=0|1` with complete `@Tet` and `@Tab` sections. Only the flagged groups change. Primary14 still controls current table settings; saving defaults does not activate them or alter gameplay.

`TablePreferences(economy).handle(account_key,payload)` returns [] after a valid save, or None for another family. The root adapter supplies authenticated-account authorization, existing table/phase checks and durable persistence. No distinct original save acknowledgement was found. Persist before completing handling. `parse_save` exposes the selected game/table dictionaries for adapters needing validation before another mutation.

`EconomyService.save_table_preferences(key, game=None, table=None)` uses pure `apply_preferences(profile, game=None, table=None)` and atomically installs the resulting template. The public Economy method was added by the economy owner. It preserves money, inventory, other settings, sessions and reservations. Existing account snapshots already include the template, so restore requires no new storage format.

## Original file and editor reload

The original importer `1014D9F0` reads the following relative offsets from the canonical0x3028-byte TM0DataFile at10294BD0. Only these consumed bytes are changed; surrounding opaque bytes remain intact.

| Group | Tag | File offset | Width | Saved global |
|---|---|---:|---:|---|
| Tet | bm | B8 | 4 | 102B5ED4 |
| Tet | du | BC | 1 | 102B5ED8 |
| Tet | st | C0 | 1 | 102B5ED9 |
| Tet | cb | C4 | 1 | 102B5EDA |
| Tet | ca | C8 | 1 | 102B5EDB |
| Tet | gs | CC | 1 | 102B5EDC |
| Tet | tl | D0 | 1 | 102B5EDD |
| Tab | in | D8 | 1 | 102B5EE0 |
| Tab | lu | DC | 4 | 102B5EE4 |
| Tab | ll | E0 | 4 | 102B5EE8 |
| Tab | au | E4 | 4 | 102B5EEC |
| Tab | al | E8 | 4 | 102B5EF0 |
| Tab | co | EC | 1 | 102B5EF4 |
| Tab | pa | F0 | 1 | 102B5EF5 |
| Tab | pw | F4 | 10 copied bytes | 102B5EF7 |

All multibyte integers are little-endian. Importer evidence is `1014DAE3..1014DBA3`. Game editor Reload `1004ECA0` consumes these globals and caps a reloaded wager at current balance. Table editor Reload `100515C0` consumes the saved comment/password/observe/bounds and copies the password into both working strings.

Original save callers `100512CD..100512ED` and `10053653..10053674` send their saved global structures to the selected service. Serializer `10087410` chooses24 when either save flag equals1; otherwise it selects14. Executed sender cases confirm separate Tbl-only, Game-only, and combined flags with complete Tet/Tab.

Validation follows the existing local settings adapter: bm0..2000, Boolean du/st/cb/ca, gs0..2, tl0..5; observe0..2, ordered card/rank bounds0..99999, comment0..8, Boolean password flag. These are supported local/UI domains, not proof of historical server limits. Saved au/al are preserved exactly; only applying current settings fixes them to300/100 elsewhere.

## Password reload limit

The wire editor can send12 bytes, and the saved in-memory password field can hold them. However `1014DB7E..1014DB9B` calls strncpy with length10. It does not clear destination bytes10/11. Original tests load `1234567890AB` over an existing `xxxxxxxxxxxx` and observe `1234567890xx`.

Durable saves therefore accept at most9 password bytes, leaving the NUL terminator inside the ten copied bytes. Selected table saves with10..12 bytes fail explicitly and atomically. A Game-only save still accepts an unselected12-byte table password because that group is unchanged. Disabled passwords write the original local padding `********` plus NULs. This persistence limit is separate from PTL/TD metadata's8-byte password field.

## Computer-menu advertisement

The computer action uses an ordinary empty **A** table. There is no distinct C metadata layout in this route:

- Raw table identity/channel retain their standard PTL fields; the49-byte info is the existing `table_info(number,0,0,game_rules,table_rules)`. Raw state0, count0 and owner0 produce client low action flags0.
- Exporter `1008608F..10086097` accepts A/B only. C gets number-1 and is omitted by derived-table construction.
- Computer-button constructor `10045D70..10045DC2` requires derived type+8A8=A and room type102B54FC=A. It additionally requires at least five owned cards via original helper100B8B30. B therefore decodes as a table but disables this computer action.
- Button event03001158 at `1004838B` requires modal flag0 and low action flags+8AC&15==0. With no previous selected-table identity it posts03001159; an existing table triggers a confirmation dialog first.
- Dispatcher03001159 lands at1004D8FB and constructs10095610 at1004D99A. That child's state0 sends41/GameENC at1009574D. This is separate from choosing an NPC later in the computer menu.

`../protocol_map/computer_table_advertisement_vectors.py/json` contains17 passing original cases: three metadata prefixes, eight button gate combinations, five action-flag cases and the dispatcher branch. Widget calls, inventory-count results and event transport are substituted; no GUI ran.

## Validation

`python -B -m unittest test_table_preferences -v` passes13 methods. Twenty-nine original cases execute complete Save serialization, profile import and editor reload, including24 rule/password combinations and two stale-password-tail demonstrations. Pure/account tests cover exact byte preservation, independent groups, invalid inputs, immutable failure, snapshot/restore, account isolation, saving during a pending purchase, and saving while an auction reservation remains live.

No game launch, GUI input, original-file mutation or native end-to-end saved-preference test was performed.
