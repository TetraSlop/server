"""Portable wire regressions backed by original D1/D7 consumer captures.

The fixture records execution of the hash-checked original image at the four
addresses in its provenance. These tests bind our builders to those accepted
bytes; they do not emulate the consumer or prescribe a notification scheduler.
The archived research tests remain the executable original-consumer evidence.
"""

import hashlib
import json
from pathlib import Path
import unittest

from tmserver.economy.service import EconomyService
from tmserver.events.packets import announcement, time_reply
from tmserver.events.service import EventHost

from tests.events.test_event_rooms import config


class EventNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "fixtures" / "event_notifications.json"
        cls.vectors = json.loads(path.read_text(encoding="utf8"))

    def setUp(self):
        self.now = 200
        self.host = EventHost(EconomyService(), (config(),), clock=lambda: self.now)

    def test_update_and_query_keep_raw_now_on_wire_but_distinct_client_outputs(self):
        update, reply = self.vectors["time_packets"][:2]
        self.assertEqual(self.host.time_update("festival"), update["packet"].encode("ascii"))
        self.assertEqual(time_reply(**reply["arguments"]), reply["packet"].encode("ascii"))
        # The original query consumer subtracts Now itself. Sending 300 in the
        # query's Now field would incorrectly turn 300 seconds remaining into 200.
        self.assertTrue(update["packet"].endswith("/Now=200"))
        self.assertTrue(reply["packet"].endswith("/Now=200"))
        self.assertEqual(update["consumer_u32"], [100, 500, 200])
        self.assertEqual(reply["consumer_u32"], [100, 500, 300])

    def test_query_preserves_deadline_and_signed_timestamp_boundaries(self):
        replies = [case for case in self.vectors["time_packets"] if case["factory"] == "reply"]
        for case in replies:
            with self.subTest(arguments=case["arguments"]):
                self.assertEqual(time_reply(**case["arguments"]), case["packet"].encode("ascii"))
                self.assertEqual(case["consumer_return"], 1)
        # At End the remaining time is zero; one second later it is signed -1.
        self.assertEqual([case["consumer_u32"][2] for case in replies], [300, 0, 0xFFFFFFFF, 300])
        self.assertEqual(replies[-1]["consumer_u32"][0], 0xFFFFFFFF)

    def test_updates_capture_each_clock_value_in_latest_update_sequence(self):
        case = self.vectors["latest_update"]
        updates = []
        for self.now in (200, 250, 300):
            updates.append(self.host.time_update("festival").decode("ascii"))
        self.assertEqual(updates, case["packets"])
        # Recorded consumer evidence: it drains queued D1/03 packets and uses
        # the newest raw timestamp. This does not imply a server send interval.
        self.assertEqual(case["consumer_u32"], [100, 500, 300])
        self.assertEqual(case["queue_remaining"], 0)

    def test_start_end_and_close_notices_preserve_optional_ecm(self):
        notices = [case for case in self.vectors["time_packets"] if case["factory"] == "notice"]
        for case in notices:
            with self.subTest(arguments=case["arguments"]):
                wire = self.host.time_notice("festival", **case["arguments"])
                self.assertEqual(wire, case["packet"].encode("ascii"))
                self.assertEqual(case["consumer_return"], 1)
        self.assertEqual(
            [case["consumer_u32"] for case in notices],
            [[100, 0], [500, 0], [510, 2], [700, 0xFFFFFFFF], [0xFFFFFFFF, 0xFFFFFFFE]],
        )
        self.assertNotIn("/ECM=", notices[3]["packet"])

    def test_announcements_match_original_accepted_ordered_page_groups(self):
        pattern = self.vectors["announcement_pattern"].encode("ascii")
        for case in self.vectors["announcement_groups"]:
            with self.subTest(length=case["length"], identifier=case["identifier"]):
                contents = (pattern * 100)[: case["length"]]
                pages = announcement(case["identifier"], contents, case["loops"])
                self.assertEqual(
                    [hashlib.sha256(page).hexdigest() for page in pages], case["packet_sha256"]
                )
                decoded = []
                for index, page in enumerate(pages):
                    prefix = (
                        f"GTM0GD7000000@ETelop=/ID={case['identifier']}/Lp={case['loops']}"
                        f"/Pg={index}|{len(pages)}/St="
                    ).encode("ascii")
                    self.assertTrue(page.startswith(prefix))
                    text = bytes.fromhex(page[len(prefix) :].decode("ascii"))
                    self.assertEqual(page[len(prefix) :], text.hex().upper().encode("ascii"))
                    self.assertLessEqual(len(page) - 13, 447)
                    self.assertGreaterEqual(len(text), 1)
                    self.assertLessEqual(len(text), 199)
                    decoded.append(text)
                self.assertEqual(b"".join(decoded), contents)
                self.assertEqual([len(text) for text in decoded], case["decoded_lengths"])
                self.assertLessEqual(len(pages), 10)
                # Completion returns the page count, not a boolean; Lp belongs
                # to the presentation and does not request repeated packets.
                self.assertEqual(case["consumer_returns"], [0] * (len(pages) - 1) + [len(pages)])
                self.assertEqual(case["consumer_loops"], case["loops"])

    def test_ten_full_pages_fit_even_with_longest_signed_decimal_fields(self):
        case = self.vectors["announcement_groups"][-1]
        self.assertEqual(
            (case["length"], case["identifier"], case["loops"]), (1990, 0x7FFFFFFF, 0x7FFFFFFF)
        )
        self.assertEqual(case["decoded_lengths"], [199] * 10)
        with self.assertRaises(ValueError):
            announcement(case["identifier"], b"x" * 1991, case["loops"])

    def test_announcement_hex_encoding_preserves_non_ascii_and_named_field_delimiters(self):
        case = self.vectors["opaque_announcement"]
        contents = bytes.fromhex(case["contents_hex"])
        self.assertEqual(announcement(0, contents), (case["packet"].encode("ascii"),))
        self.assertEqual(case["consumer_return"], 1)
        self.assertEqual(case["consumer_contents_hex"], case["contents_hex"])

    def test_duplicate_and_reordered_page_sequences_remain_reproducible(self):
        case = self.vectors["announcement_replay"]
        group = next(
            group
            for group in self.vectors["announcement_groups"]
            if group["length"] == case["group_length"]
        )
        contents = (self.vectors["announcement_pattern"].encode("ascii") * 100)[: group["length"]]
        pages = announcement(group["identifier"], contents, group["loops"])
        for key in ("out_of_order", "duplicated_order"):
            with self.subTest(sequence=key):
                self.assertEqual(
                    [hashlib.sha256(pages[index]).hexdigest() for index in case[key]],
                    [group["packet_sha256"][index] for index in case[key]],
                )
        # The original counts arrivals, including duplicate pages. An extra
        # arrival poisons completion for that ID; do not infer retry safety.
        self.assertEqual(case["out_of_order_returns"], [0, 2])
        self.assertEqual(case["duplicated_returns"], [0, 0, 0])
        self.assertEqual(case["duplicated_arrival_count"], 3)

    def test_newer_id_resets_poisoned_group_and_older_id_keeps_current_text(self):
        case = self.vectors["announcement_replay"]
        packets = (
            announcement(1234566, b"Old", 2)
            + announcement(1234568, b"New", 2)
            + announcement(1234567, b"Old", 2)
        )
        self.assertEqual([packet.decode("ascii") for packet in packets], case["generation_packets"])
        # This sequence follows the duplicate-page group in the captured run.
        # A stale ID may re-report completion but cannot replace the new text.
        self.assertEqual(case["generation_returns"], [0, 1, 1])
        self.assertEqual(case["final_text"], "New")
        self.assertEqual(case["final_arrival_count"], 1)


if __name__ == "__main__":
    unittest.main()
