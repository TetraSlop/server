"""Original ranking score/date rendering against synthetic production records.

Read-only original images; no native application, sockets, assets or save files.
Requires the existing external protocol_map Unicorn harness and global Python.
Only output JSON is written. By default production metadata must omit dates;
the old week-start timestamp is a labeled, in-memory comparison. The optional
legacy baseline mode verifies the pre-fix implementation and zero-date candidate.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


TM_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"
CORE_SHA256 = "f39e232820e072189becc179bcec292a5b73563dfef94f7a9b6446299166adfb"
STACK = 0x200FF000
MONDAY = 1_704_067_200  # 2024-01-01 00:00 UTC.
WEEK = 7 * 86400


def run(research_dir, repo, *, legacy_baseline=False):
    image = research_dir / "TM_emulated_mapped.dll"
    assert hashlib.sha256(image.read_bytes()).hexdigest() == TM_SHA256
    sys.path[:0] = [str(repo / "src"), str(research_dir / "protocol_map")]
    from auction_rank_vectors import Machine, SCENE
    from profile_semantics_vectors import CoreVM
    from probe import PATHS
    from tmserver.economy.service import EconomyService
    from tmserver.economy.models import PROFILE_SIZE
    from tmserver.rankings.models import RankingHistory
    from tmserver.rankings.service import RankingService
    from tmserver.rankings.packets import RECORD_SIZE, SCORE_OFFSETS, UI_CATEGORIES
    from tmserver.rankings.policy import RatingPolicy
    from tmserver.rankings.stats import PracticeStats

    assert hashlib.sha256(Path(PATHS["core"]).read_bytes()).hexdigest() == CORE_SHA256

    def project(vm, data, rank_id):
        category = UI_CATEGORIES[rank_id]
        count = len(data) // RECORD_SIZE
        vm.w32(SCENE + 0x63AC, category)
        vm.w32(0x102F46F0 + 4 * category, count)
        vm.w32(0x102B52A0, 1)
        vm.w32(0x102B52A4, 0)
        vm.w32(0x102B529C, 1)
        vm.u.mem_write(SCENE + 0x120, data)
        for index in range(count):
            vm.w32(0x102F4748 + (category * 100 + index) * 0x2C + 0x28, index + 1)
        vm.block(0x101752A8, 0x101754F8, esi=SCENE, ebp=0)
        projected = []
        for index in range(count):
            address = 0x102F4748 + (category * 100 + index) * 0x2C
            score = vm.r32(address + 0x20)
            assert (
                score
                == struct.unpack_from("<I", data, index * RECORD_SIZE + SCORE_OFFSETS[rank_id])[0]
            )
            projected.append(
                {
                    "name": vm.text(address),
                    "score": score,
                    "previous_rank": vm.r32(address + 0x24),
                    "rank": vm.r32(address + 0x28),
                }
            )
        return projected

    score_cases = []
    for value in (0, 99, 100, 9_999_999, 999_999_999, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
        economy = EconomyService(seed=1)
        profile = bytearray(PROFILE_SIZE)
        struct.pack_into("<I", profile, 0x30, value)
        struct.pack_into("<I", profile, 0x50, value)
        economy.provision("test", nn=1, player_id=1, name=b"Test", profile_template=bytes(profile))
        service = RankingService(
            economy,
            historical_stats={
                "test": RankingHistory(weekly_prize=value, last_week_rating=value, rookie=True),
            },
        )
        for rank_id, category in UI_CATEGORIES.items():
            vm = Machine()
            data = service.records(rank_id)
            assert len(data) == RECORD_SIZE
            projected = project(vm, data, rank_id)
            vm.w32(STACK + 0x14, 0)  # Current display row.
            vm.block(0x1017663A, 0x1017676C, esi=SCENE, ebp=0, ebx=0, eax=category)
            display = vm.text(STACK + 0x90)
            assert display
            if value >= 0x80000000:
                signed = value - (1 << 32)
                assert display == (str(signed) if rank_id in (0, 1) else f"0.{signed}")
            score_cases.append(
                {
                    "rank_id": rank_id,
                    "ui_category": category,
                    "wire_score": value,
                    "projected_score": projected[0]["score"],
                    "display": display,
                }
            )

    # Original Common692 and its original CRT conversion execute in a separate
    # image. Only TLS storage and timezone acquisition are seeded. No Python
    # datetime conversion supplies the calendar or replaces original arithmetic.
    core = CoreVM()
    assert core.r32(0x1006FBE8 + 692 * 4) == 0x1004CEF0
    core.hooks[0x10060A68] = lambda sp: core.ret(sp, 0x20210000)
    core.hooks[0x1005527A] = lambda sp: core.ret(sp)

    def utc_timezone(sp):
        core.w32(core.r32(sp + 4), 0)
        core.ret(sp)

    for address in (0x10054AB2, 0x10054AE6, 0x10054B1A):
        core.hooks[address] = utc_timezone

    economy = EconomyService(seed=1)
    for number, key in enumerate(("first", "second"), 1):
        economy.provision(key, nn=number, player_id=number, name=key.encode())
    now = [MONDAY]
    policy = RatingPolicy(economy, mode="local-history", weekly=True, clock=lambda: now[0])

    def commit(match_id, reward, tokens=None):
        keys = ("first", "second")
        if tokens is None:
            tokens = {key: economy.reserve_match(key) for key in keys}
        quoted = policy.quote(match_id, dict(enumerate(keys)), (7, 3))
        changes = {key: quoted[seat] for seat, key in enumerate(keys)}
        rounds, stats = [], {}
        rewards = {"first": reward, "second": 0}
        for key in keys:
            change = changes[key]
            previous = PracticeStats.from_profile(economy.profile_bytes(key))
            stats[key] = previous.next_result(
                (change.result_class < 2) - (change.result_class > 2),
                rewards[key],
                rating_change=change,
                vs_player_game=True,
            )
            cards = economy.inventory(key)[:5]
            rounds.append((tokens[key], cards, cards))
        return policy.commit_growth(
            rounds,
            stats,
            rewards,
            rating_changes_by_key=changes,
            vs_player_game=True,
        )

    tokens = commit("week:1", 25)
    now[0] += WEEK
    policy.advance_week()
    commit("week:2", 99, tokens)
    ranking = RankingService(
        economy,
        rating_policy=policy,
        historical_stats={
            "first": RankingHistory(rookie=True),
        },
    )
    before = economy.snapshot(), policy.snapshot()
    metadata = ranking.metadata()
    timestamp = policy.weekly_history()[1]
    assert timestamp == MONDAY + WEEK
    old = bytearray(metadata)
    struct.pack_into("<I", old, 12, timestamp)
    old = bytes(old)
    omitted = bytearray(metadata)
    struct.pack_into("<I", omitted, 12, 0)
    omitted = bytes(omitted)
    assert omitted[:12] == old[:12] and omitted[16:] == old[16:]
    if legacy_baseline:
        assert metadata == old, "legacy mode requires the original week-start projection"
        hidden_mode = "zero_date_candidate"
    else:
        assert metadata == omitted, "production weekly metadata must omit misleading dates"
        hidden_mode = "production_zero_date"

    date_cases = []
    expected_labels = {
        1: (
            "Tally Period: 2023/12/25 - 2023/12/31",
            "Tally Period: 12/25/2023 - 12/31/2023",
            "Tally Period: 25/12/2023 - 31/12/2023",
        ),
        3: (
            "Next update: 2024/01/08  0:00 PST",
            "Next update: 01/08/2024  0:00 PST",
            "Next update: 08/01/2024  0:00 PST",
        ),
    }
    expected_labels[4] = expected_labels[3]
    for rank_id in (1, 3, 4):
        data = ranking.records(rank_id)
        for language in (0, 1, 2):
            reference = None
            for mode, candidate in (("explicit_old_week_start", old), (hidden_mode, omitted)):
                vm = Machine()
                category = UI_CATEGORIES[rank_id]
                vm.u.mem_write(0x102923AC, candidate)
                vm.block(0x101756E3, 0x10175724, esi=SCENE)
                assert vm.r32(0x102F46CC) == struct.unpack_from("<I", candidate, 12)[0]
                counts = [vm.r32(0x102F46D4 + 4 * index) for index in range(6)]
                projected = project(vm, data, rank_id)
                projection_address = 0x102F4748 + category * 100 * 0x2C
                projected_bytes = bytes(vm.u.mem_read(projection_address, len(projected) * 0x2C))
                if reference is None:
                    reference = counts, projected, projected_bytes
                else:
                    assert (counts, projected, projected_bytes) == reference
                vm.w32(0x102FF4A0, 0x20350000)
                vm.w32(0x1024DFE8, 0x20500000)
                vm.w32(0x20500000 + 0xAD0, 0x20501000)
                vm.w32(0x20500000 + 0xAE4, 0x20501010)
                calls, labels = [], []

                def calendar(sp):
                    destination, stamp = vm.r32(sp + 4), vm.r32(sp + 8)
                    core.call(0x1004CEF0, [0x20200000, stamp])
                    raw = bytes(core.u.mem_read(0x20200000, 36))
                    calls.append({"input": stamp, "calendar": list(struct.unpack("<9i", raw))})
                    vm.u.mem_write(destination, raw)
                    vm.ret(sp)

                def draw(sp):
                    descriptor = vm.r32(sp + 4)
                    pointer = vm.r32(descriptor)
                    labels.append(bytes(vm.u.mem_read(pointer, 200)).split(b"\0")[0])
                    vm.ret(sp, cleanup=20)

                vm.hooks[0x20501000] = calendar
                vm.hooks[0x20501010] = lambda sp: vm.ret(sp, language)
                vm.hooks[0x1019D8D0] = lambda sp: vm.ret(sp)
                vm.hooks[0x1019B010] = draw
                vm.block(0x10176A13, 0x10176CFA, esi=SCENE, ebx=0)
                if mode == hidden_mode:
                    assert not calls and not labels
                else:
                    expected = (
                        [timestamp - 14 * 86400, timestamp - 8 * 86400]
                        if rank_id == 1
                        else [timestamp]
                    )
                    assert [call["input"] for call in calls] == expected
                    assert len(labels) == 1 and labels[0]
                    assert labels[0].decode("ascii") == expected_labels[rank_id][language]
                # The consumer does not change the imported denominators or
                # production rows while drawing/suppressing the date label.
                assert counts == [vm.r32(0x102F46D4 + 4 * index) for index in range(6)]
                assert (
                    bytes(vm.u.mem_read(projection_address, len(projected_bytes)))
                    == projected_bytes
                )
                assert bytes(vm.u.mem_read(SCENE + 0x120, len(data))) == data
                assert ranking.records(rank_id) == data
                date_cases.append(
                    {
                        "mode": mode,
                        "rank_id": rank_id,
                        "ui_category": category,
                        "locale_selector": language,
                        "metadata_hex": candidate.hex(),
                        "row_sha256": hashlib.sha256(data).hexdigest(),
                        "row_count": len(data) // RECORD_SIZE,
                        "denominators": counts,
                        "projected_rows": projected,
                        "calendar_calls": calls,
                        "labels": [value.decode("cp932", "backslashreplace") for value in labels],
                        "labels_hex": [value.hex() for value in labels],
                    }
                )
    assert (economy.snapshot(), policy.snapshot()) == before
    assert ranking.metadata() == metadata
    return {
        "tm_image_sha256": TM_SHA256,
        "core_sha256": CORE_SHA256,
        "legacy_baseline": legacy_baseline,
        "scope": {
            "score_projection": "Original 101752A8..101754F8",
            "score_formatting": "Original 1017663A..1017676C including original CRT sprintf",
            "metadata_import": "Original 101756E3..10175724",
            "date_rendering": "Original 10176A13..10176CFA including original CRT sprintf",
            "calendar": "Original Core Common692 1004CEF0, 1004CF50 and CRT localtime",
            "substitutions": [
                "Existing external harness runtime/heap boundaries",
                "Core per-thread tm storage and timezone initialization/getters fixed to UTC",
                "Locale selector 0/1/2 seeded; final drawing intercepted",
                "Old week-start metadata is an explicitly labeled in-memory comparison",
                "Production zero-date metadata is required unless --legacy-baseline is selected",
            ],
        },
        "local_policy": {
            "active_week_start": timestamp,
            "current_week_first_prize": 99,
            "previous_week_first_prize": 25,
        },
        "score_cases": score_cases,
        "date_cases": date_cases,
        "case_count": len(score_cases) + len(date_cases),
        "limits": [
            "UTC is a synthetic fixture configuration, not the installed timezone or historical host schedule.",
            "The original date format appends literal PST even with the fixture UTC calendar.",
            "No change to saved unsigned 32-bit scores, ranking order, period journal, awards or original images.",
            "No full native application, desktop, sockets or live save was used.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path, default=Path("results/ranking-display-zero-date-original.json")
    )
    parser.add_argument(
        "--legacy-baseline",
        action="store_true",
        help="verify the frozen pre-fix week-start metadata and zero-date candidate",
    )
    args = parser.parse_args()
    report = run(
        args.research_dir.resolve(), args.repo.resolve(), legacy_baseline=args.legacy_baseline
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    hidden = "candidate" if args.legacy_baseline else "production"
    print(
        f"PASS {report['case_count']} cases: 40 score boundaries, 9 old date labels, 9 {hidden} zero-date omissions"
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
