"""Bounded trade transactions and actual original-x86 receiver integration.

No sockets, native process launches or file writes. Run with python -B.
"""
from collections import Counter
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest

from economy_service import Card, Catalog, EconomyError, EconomyService, ExchangeReservation, named, parse_named
from trade_service import TradeError, TradeService

PM = Path(__file__).resolve().parents[1] / 'protocol_map'


def card(catalog, model, deck=255):
    return Card(model, 20, 0, 30, 40, catalog.points(model, 20, 30, 40), 129, deck)


def offer(side, cards, *, total=None, start=0):
    if not cards and not total:
        return f'@Card=/P={side}/E=0'
    return (f'@Card=/P={side}/E={len(cards) if total is None else total}/S={start}/C={len(cards)}' +
            ''.join(f'@N{i}=/D=' + '|'.join(map(str, c.reduced())) for i, c in enumerate(cards)))


def data(side, mode, selected=()):
    bits = [0] * 30
    for index in selected:
        bits[index] = 1
    return f'@Data=/P={side}/M={mode}/D=' + '|'.join(map(str, bits))


def edits(side, token, cards=(), *, mode=None, name=None):
    head = f'@{token}=/P={side}' + (f'/Mode={mode}' if mode is not None else '')
    if mode != 2:
        head += f'/C={len(cards)}' + ''.join(f'@{i}=/D=' + '|'.join(map(str,
            c.reduced() + ((c.deck,) if token == 'Decks' else ()))) for i, c in enumerate(cards))
    if name:
        head += f'@DN{name[0]}=/S={name[1].hex()}'
    return head


class TradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def setUp(self):
        self.economy = EconomyService(seed=1, catalog=self.catalog)
        self.economy.provision('left', nn=101, name=b'Left', cards=[card(self.catalog, i) for i in (1, 2, 6, 7)])
        self.economy.provision('right', nn=202, name=b'Right', cards=[card(self.catalog, i) for i in (3, 4, 8, 9)])
        self.trade = TradeService(self.economy, first_session=17)
        self.trade.bind('peer0', 'left'); self.trade.bind('peer1', 'right')
        self.session = 17
        self.initial = self.economy.snapshot()

    def send(self, side, subtype, body, session=None):
        return self.trade.handle(f'peer{side}', named(0xA2, subtype, self.session if session is None else session, body))

    def begin(self):
        return self.trade.begin('peer0', 'peer1')

    def offers(self):
        self.send(0, 0x1A, offer(0, self.economy.inventory('left')[2:]))
        self.send(1, 0x1A, offer(1, self.economy.inventory('right')[2:]))

    def confirm(self, left=(0,), right=(1,)):
        self.send(0, 0x1B, data(0, 1, left))
        self.send(1, 0x1B, data(1, 1, right))
        self.send(0, 0x1B, data(0, 2, left))
        return self.send(1, 0x1B, data(1, 2, right))

    def complete(self):
        self.send(0, 0x1C, '@Ans=/P=0/M=2')
        return self.send(1, 0x1C, '@Ans=/P=1/M=2')

    def settle(self):
        self.begin(); self.offers(); self.confirm(); return self.complete()

    def test_init_matches_mapped_fields_and_locks_both_accounts(self):
        deliveries = self.begin()
        self.assertEqual({d.peer for d in deliveries}, {'peer0', 'peer1'})
        for d in deliveries:
            family, subtype, session, sections = parse_named(d.payload)
            self.assertEqual((family, subtype, session), (0xA2, 0x18, 17))
            self.assertEqual(sections['Init'], {'ID': '17'})
            self.assertEqual(sections['No0']['S'], '4')
            self.assertEqual(sections['No1']['M'], '10000')
        self.assertTrue(self.economy.is_reserved('left'))
        self.assertFalse(self.economy.validate_selection('left', self.economy.inventory('left')))

    def test_entry_requires_authenticated_peer_consent(self):
        payload = named(0xA0, 0, 0, '@TrEnt=/NN=0000000000000065|00000000000000CA/HID=0|1/Dm=0|0/Vol=0|0')
        with self.assertRaises(TradeError):
            self.trade.handle('peer0', payload)
        self.trade.authorize_pair('peer0', 'peer1')
        deliveries = self.trade.handle('peer0', payload)
        self.assertEqual(len(deliveries), 4)
        self.assertEqual([parse_named(d.payload)[:2] for d in deliveries], [(0xA1, 0), (0xA1, 0), (0xA2, 0x18), (0xA2, 0x18)])
        self.assertIsNone(self.trade.handle('peer0', named(0xA0, 0, 0, '@TrID=/ID=0000000000000300')))

    def test_forged_registration_does_not_reserve(self):
        self.trade.authorize_pair('peer0', 'peer1')
        for identities in ('0000000000000066|00000000000000CA', '0000000000000065|00000000000000CB'):
            with self.subTest(identities=identities), self.assertRaises(TradeError):
                self.trade.handle('peer0', named(0xA0, 0, 0, f'@TrEnt=/NN={identities}/HID=0|1/Dm=0|0/Vol=0|0'))
        self.assertFalse(self.economy.is_reserved('left'))

    def test_unrelated_family_and_duplicate_account_binding(self):
        self.assertIsNone(self.trade.handle('peer0', b'GTM0LBELL'))
        with self.assertRaises(TradeError):
            self.trade.bind('impostor', 'left')
        with self.assertRaises(TradeError):
            self.trade.begin('peer0', 'peer0')

    def test_paired_flow_matches_recorded_original_fixture_and_commits_together(self):
        self.settle()
        self.assertEqual(self.economy.snapshot(), self.initial)
        self.assertEqual(self.send(0, 0x1D, '@Get=/P=0'), [])
        self.assertEqual(self.economy.snapshot(), self.initial)
        responses = self.send(1, 0x1D, '@Get=/P=1')
        expected = json.loads((PM / 'trade_transaction_vectors.json').read_text())['groups']['paired_flow'][0]
        for i, key in enumerate(('left', 'right')):
            self.assertEqual([c.model for c in self.economy.inventory(key)], expected['clients'][i]['final_ids'])
            self.assertEqual(self.economy.balance(key), 10000)
            self.assertFalse(self.economy.is_reserved(key))
            self.assertEqual(parse_named(responses[i].payload)[3], {'Quit': {'P': str(i), 'B': '0'}})
        combined = self.economy.inventory('left') + self.economy.inventory('right')
        self.assertEqual(Counter(c.model for c in combined), Counter(expected['combined_models_before']))

    def test_duplicate_ans_and_get_cannot_duplicate_transfer(self):
        self.begin(); self.offers(); self.confirm()
        self.send(0, 0x1C, '@Ans=/P=0/M=2')
        self.assertEqual(self.send(0, 0x1C, '@Ans=/P=0/M=2'), [])
        completed = self.send(1, 0x1C, '@Ans=/P=1/M=2')
        self.assertEqual(len(completed), 2)
        self.assertEqual(self.send(1, 0x1C, '@Ans=/P=1/M=2'), [])
        self.send(0, 0x1D, '@Get=/P=0')
        self.assertEqual(self.send(0, 0x1D, '@Get=/P=0'), [])
        self.send(1, 0x1D, '@Get=/P=1')
        committed = self.economy.snapshot()
        self.assertEqual(len(self.send(0, 0x1D, '@Get=/P=0')), 1)
        self.assertEqual(self.economy.snapshot(), committed)

    def test_first_get_disconnect_rolls_back_both_and_requires_peer_reload(self):
        self.settle(); self.send(0, 0x1D, '@Get=/P=0')
        replies = self.trade.disconnect('peer1')
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].peer, 'peer0')
        self.assertTrue(replies[0].reload_profile)
        self.assertEqual(parse_named(replies[0].payload)[1], 0x27)
        self.assertEqual(self.economy.snapshot(), self.initial)
        self.assertFalse(self.economy.is_reserved('left'))
        self.assertFalse(self.economy.is_reserved('right'))

    def test_cancel_before_or_after_m4_rolls_back(self):
        self.begin(); self.offers()
        replies = self.send(1, 0x19, '@Quit=/P=1')
        self.assertTrue(all(r.reload_profile for r in replies))
        self.assertEqual(self.economy.snapshot(), self.initial)
        self.session = 18
        self.settle()
        replies = self.send(0, 0x19, '@Quit=/P=0')
        self.assertTrue(all(r.reload_profile for r in replies))
        self.assertEqual(self.economy.snapshot(), self.initial)

    def test_disconnect_after_both_get_does_not_undo_commit(self):
        self.settle(); self.send(0, 0x1D, '@Get=/P=0'); self.send(1, 0x1D, '@Get=/P=1')
        committed = self.economy.snapshot()
        self.assertEqual(self.trade.disconnect('peer1'), [])
        self.assertEqual(self.economy.snapshot(), committed)

    def test_offer_forgery_duplicate_multiplicity_and_page_bounds(self):
        self.begin()
        owned = self.economy.inventory('left')[2]
        bad = [offer(0, [replace(owned, attack=255)]), offer(0, [owned, owned]),
               '@Card=/P=0/E=31/S=0/C=0', offer(0, [owned], total=2, start=1),
               '@Card=/P=0/E=0/S=0/C=0', '@Card=/P=0/E=1/S=0/C=0']
        before = self.trade.status('peer0')
        for body in bad:
            with self.subTest(body=body), self.assertRaises(TradeError):
                self.send(0, 0x1A, body)
            self.assertEqual(self.trade.status('peer0'), before)
        self.assertEqual(self.economy.snapshot(), self.initial)

    def test_thirty_card_pages_and_exact_partial_retry(self):
        self.economy.provision('many', nn=303, cards=[card(self.catalog, i) for i in range(30)])
        self.trade.bind('peer2', 'many')
        self.trade.begin('peer2', 'peer1')
        cards = self.economy.inventory('many')
        page = named(0xA2, 0x1A, 17, offer(0, cards[:15], total=30))
        self.assertEqual(self.trade.handle('peer2', page), [])
        self.assertEqual(self.trade.handle('peer2', page), [])
        with self.assertRaises(TradeError):
            self.trade.handle('peer2', named(0xA2, 0x1B, 17, data(0, 2)))
        responses = self.trade.handle('peer2', named(0xA2, 0x1A, 17, offer(0, cards[15:], total=30, start=15)))
        cards_out = [r for r in responses if parse_named(r.payload)[1] == 0x1A]
        self.assertEqual(len(cards_out), 2)
        self.assertEqual([parse_named(r.payload)[3]['Card']['S'] for r in cards_out], ['0', '15'])
        self.assertTrue(all(len(r.payload) <= 460 for r in responses))

    def test_maximum_width_cards_are_repaginated_under_447_body_bytes(self):
        c = Card(249, 255, 3, 255, 255, self.catalog.points(249, 255, 255, 255), 255)
        self.economy.provision('maximum', nn=303, cards=[c]*30)
        self.trade.bind('peer2', 'maximum'); self.trade.begin('peer2', 'peer1')
        responses = []
        for start, count in ((0, 14), (14, 14), (28, 2)):
            responses += self.trade.handle('peer2', named(0xA2, 0x1A, 17, offer(0, [c]*count, total=30, start=start)))
        card_messages = [parse_named(r.payload)[3]['Card'] for r in responses if parse_named(r.payload)[1] == 0x1A]
        self.assertEqual([(int(f['S']), int(f['C'])) for f in card_messages], [(0, 14), (14, 14), (28, 2)])
        self.assertTrue(all(len(r.payload)-13 <= 447 for r in responses))

    def test_empty_offer_clears_peer_selection_and_confirmation(self):
        self.begin(); self.offers(); self.confirm()
        responses = self.send(0, 0x1A, offer(0, []))
        self.assertEqual(parse_named(responses[0].payload)[3], {'Card': {'P': '0', 'E': '0'}})
        self.assertEqual(self.trade.status('peer0')['modes'], [0, 1])
        self.assertEqual(self.send(0, 0x1C, '@Ans=/P=0/M=2'), [])
        self.assertEqual(self.economy.snapshot(), self.initial)

    def test_changed_selection_restarts_confirmation(self):
        self.begin(); self.offers(); self.confirm()
        self.send(0, 0x1C, '@Ans=/P=0/M=2')
        self.send(1, 0x1B, data(1, 1, (0,)))
        self.assertEqual(self.trade.status('peer0')['answered'], [])
        self.assertEqual(self.send(1, 0x1C, '@Ans=/P=1/M=2'), [])
        self.send(0, 0x1B, data(0, 2, (0,)))
        self.send(1, 0x1B, data(1, 2, (0,)))
        self.assertEqual(len(self.complete()), 2)

    def test_cannot_reconfirm_changed_selection_under_peers_confirmation(self):
        self.begin(); self.offers(); self.confirm()
        before = self.trade.status('peer0')
        with self.assertRaises(TradeError):
            self.send(0, 0x1B, data(0, 2, (1,)))
        self.assertEqual(self.trade.status('peer0'), before)

    def test_unknown_selection_bits_side_and_session_rejected(self):
        self.begin(); self.offers()
        for body in (data(0, 1, (2,)), data(0, 1).replace('/D=0', '/D=2', 1), data(1, 1)):
            with self.subTest(body=body), self.assertRaises(TradeError):
                self.send(0, 0x1B, body)
        with self.assertRaises(TradeError):
            self.send(0, 0x1B, data(0, 1), session=18)
        with self.assertRaises(TradeError):
            self.send(0, 0x1D, '@Get=/P=0')

    def test_locked_offer_and_selection_after_m4(self):
        self.settle()
        for subtype, body in ((0x1A, offer(0, [])), (0x1B, data(0, 1))):
            with self.assertRaises(TradeError):
                self.send(0, subtype, body)

    def test_trade_decks_erase_no_money_and_delayed_rename_after_commit(self):
        self.settle()
        self.send(0, 0x22, edits(0, 'Decks', [card(self.catalog, 8, 4)], name=(1, b'New')))
        self.send(0, 0x10, edits(0, 'Erase', [card(self.catalog, 1)], mode=1))
        self.send(0, 0x10, edits(0, 'Erase', mode=2))
        self.send(0, 0x1D, '@Get=/P=0')
        self.send(1, 0x1D, '@Get=/P=1')
        self.assertEqual([c.model for c in self.economy.inventory('left')], [2, 6, 8])
        self.assertEqual(self.economy.inventory('left')[-1].deck, 4)
        self.assertEqual(self.economy.balance('left'), 10000)
        self.send(0, 0x22, edits(0, 'Decks', name=(2, b'Delayed')))
        self.assertEqual(dict(self.economy.account_view('left').deck_names)[2], b'Delayed')
        with self.assertRaises(TradeError):
            self.send(0, 0x10, edits(0, 'Erase', [card(self.catalog, 2)], mode=1))

    def test_unselected_offered_deck_preserved_transferred_deck_cleared(self):
        self.economy.apply_deck_changes('left', [card(self.catalog, 6, 3), card(self.catalog, 7, 4)])
        self.settle(); self.send(0, 0x1D, '@Get=/P=0'); self.send(1, 0x1D, '@Get=/P=1')
        self.assertEqual([(c.model, c.deck) for c in self.economy.inventory('left')][-2:], [(6, 3), (8, 255)])
        self.assertEqual(self.economy.inventory('right')[-1].deck, 255)

    def test_offered_card_cannot_be_erased_before_reconciliation(self):
        self.begin(); self.offers()
        with self.assertRaises(TradeError):
            self.send(0, 0x10, edits(0, 'Erase', [card(self.catalog, 7)], mode=1))
        self.assertEqual(self.economy.snapshot(), self.initial)

    def test_bad_rename_or_missing_card_edit_is_atomic(self):
        self.settle()
        before = self.trade.status('peer0')
        body = edits(0, 'Erase', [card(self.catalog, 1), card(self.catalog, 249)], mode=1)
        with self.assertRaises(TradeError):
            self.send(0, 0x10, body)
        with self.assertRaises(TradeError):
            self.send(0, 0x22, edits(0, 'Decks', [card(self.catalog, 8, 3)]) + '@DN1=/S=ABC')
        self.assertEqual(self.trade.status('peer0'), before)
        self.assertEqual(self.economy.snapshot(), self.initial)

    def test_overflow_requires_owned_discards_before_both_get(self):
        self.economy.provision('full', nn=303, cards=[card(self.catalog, 1)]*1000)
        self.trade.bind('peer2', 'full')
        self.trade.begin('peer2', 'peer1')
        self.trade.handle('peer2', named(0xA2, 0x1A, 17, offer(0, [])))
        self.send(1, 0x1A, offer(1, [card(self.catalog, 8)]))
        self.trade.handle('peer2', named(0xA2, 0x1B, 17, data(0, 1, (0,))))
        self.send(1, 0x1B, data(1, 1))
        self.trade.handle('peer2', named(0xA2, 0x1B, 17, data(0, 2, (0,))))
        self.send(1, 0x1B, data(1, 2))
        self.trade.handle('peer2', named(0xA2, 0x1C, 17, '@Ans=/P=0/M=2'))
        self.send(1, 0x1C, '@Ans=/P=1/M=2')
        with self.assertRaises(TradeError):
            self.trade.handle('peer2', named(0xA2, 0x1D, 17, '@Get=/P=0'))
        self.trade.handle('peer2', named(0xA2, 0x10, 17, edits(0, 'Erase', [card(self.catalog, 8)], mode=1)))
        self.trade.handle('peer2', named(0xA2, 0x1D, 17, '@Get=/P=0'))
        self.send(1, 0x1D, '@Get=/P=1')
        self.assertEqual(len(self.economy.inventory('full')), 1000)
        self.assertEqual([c.model for c in self.economy.inventory('right')], [3, 4, 9])

    def test_persisted_snapshot_reload_matches_both_committed_inventories(self):
        self.settle(); self.send(0, 0x1D, '@Get=/P=0'); self.send(1, 0x1D, '@Get=/P=1')
        restored = EconomyService.restore(json.loads(json.dumps(self.economy.snapshot())), catalog=self.catalog)
        for key in ('left', 'right'):
            self.assertEqual(restored.profile_bytes(key), self.economy.profile_bytes(key))
            self.assertEqual(restored.inventory(key), self.economy.inventory(key))


@unittest.skipUnless(importlib.util.find_spec('unicorn'), 'optional original-x86 receiver verification requires Unicorn')
class OriginalReceiverTests(TradeTests):
    """Own test method drives two original clients, not just our own decoder."""
    # Suppress inherited cases: they run once in TradeTests.
    __unittest_skip__ = False

    def test_original_ptl_d_record_export_decoder_and_trade_discovery(self):
        sys.path.insert(0, str(PM))
        from trade_control_vectors import TradeVM, VIEW
        from room_contract_vectors import CTX, PTL, make_ptl, table
        service = 0x400 ^ 0xB62DC8D0E4BCBB90
        record = bytearray(0x68)
        struct.pack_into('<Q', record, 0, service)
        record[0x18:0x24] = b'#LOCALTRADE1'
        record[0x28:0x59] = b'D' + b'A'*48
        ptl = bytearray(make_ptl(100, table()))
        struct.pack_into('<I', ptl, 0x48, 2)
        ptl[0x58B8:0x5920] = record
        m = TradeVM(); m.initialize()
        m.u.mem_write(PTL, bytes(ptl)); m.call(0x101A0C90, CTX, PTL)
        projected = m.refresh()
        self.assertEqual(projected['raw_count'], 2)
        self.assertEqual(projected['derived_count'], 1)  # D is service-only, not an A/B play table.
        self.assertEqual(bytes(m.u.mem_read(0x1028BB70+0x68, 0x68)), bytes(record))
        self.assertEqual(m.d(0x10297FF0+4), 0xFFFFFFFF)  # D decodes to signed -1.
        m.block(0x100156E5, 0x10015722, esi=VIEW, ebp=0)
        self.assertEqual(m.q(VIEW+0x1A8), service)

    def test_original_two_client_init_offer_confirmation_reconcile_get_quit(self):
        sys.path.insert(0, str(PM))
        from trade_transaction_vectors import Trade as OriginalTrade
        clients = [OriginalTrade(0, (1, 2), (), ()), OriginalTrade(1, (3, 4), (), ())]
        queues = []

        def deliver(deliveries):
            for delivery in deliveries:
                side = int(delivery.peer[-1])
                m = clients[side]
                family, subtype, seq, sections = parse_named(delivery.payload)
                self.assertEqual(family, 0xA2)
                before = len(m.sends)
                m.queue(delivery.payload[5:].decode('ascii'))
                returned = m.receive(subtype)
                self.assertIn(returned, (0, 1))
                if subtype == 0x1C and sections['Data']['M'] == '3':
                    self.assertEqual(len(m.sends), before+1)
                    queues.append((side, b'GTM0G' + m.sends[-1]['payload'].encode('ascii')))
                if subtype == 0x1C and sections['Data']['M'] == '4':
                    self.assertEqual(returned, 1)
                    m.reconcile(); m.merge_trade()

        deliver(self.begin())
        for side, models in enumerate(((6, 7), (8, 9))):
            m = clients[side]
            # Actual selection has already removed the offered cards locally.
            m.w16(m.player+0xEC, 2)
            m.w16(m.player+0xF0, 2)
            for base, ids in ((m.player+0xB30, (1, 2) if side == 0 else (3, 4)), (m.player+0x4B90, models)):
                for i, model in enumerate(ids):
                    c = card(self.catalog, model)
                    raw = struct.pack('<6BHiHBB', c.attack, c.kind, c.physical, c.magical, c.points, c.arrows,
                                      c.model, self.catalog.sale_value(c), i, c.deck, 0)
                    m.u.mem_write(base+16*i, raw)
            before = len(m.sends)
            m.call(0x10106E30, [0x1A, 0, 0])
            for sent in m.sends[before:]:
                deliver(self.trade.handle(f'peer{side}', b'GTM0G' + sent['payload'].encode('ascii')))
        for mode in (1, 2):
            for side, selection in enumerate(((0,), (1,))):
                m = clients[side]
                bits = [0]*30
                for index in selection:
                    bits[index] = 1
                m.u.mem_write(m.player+0x38, bytes(bits))
                m.call(0x10106E30, [0x1B, mode, 0])
                deliver(self.trade.handle(f'peer{side}', b'GTM0G' + m.sends[-1]['payload'].encode('ascii')))
        self.assertEqual(len(queues), 2)
        for side, answer in queues:
            deliver(self.trade.handle(f'peer{side}', answer))
        self.assertEqual(self.economy.snapshot(), self.initial)
        for side, m in enumerate(clients):
            m.call(0x10106E30, [0x1D, 0, 0])
            deliver(self.trade.handle(f'peer{side}', b'GTM0G' + m.sends[-1]['payload'].encode('ascii')))
        for side, key in enumerate(('left', 'right')):
            m = clients[side]
            actual = []
            for i in range(m.r16(m.player+0xEC)):
                address = m.player+0xB30+16*i
                actual.append((m.r16(address+6), *m.u.mem_read(address, 6), m.r8(address+14)))
            # Native compaction may reorder equal-rank records; ownership and
            # every saved card field must agree, not their incidental indices.
            self.assertEqual(Counter(actual), Counter(c.full() for c in self.economy.inventory(key)))
            self.assertEqual(clients[side].r8(clients[side].player+0xEA), 0)
            self.assertEqual(clients[side].r32(clients[side].player+0xC8), self.economy.balance(key))


# Only inherit shared setup/helpers in the original receiver class, not test cases.
for _name in tuple(vars(TradeTests)):
    if _name.startswith('test_'):
        setattr(OriginalReceiverTests, _name, None)


if __name__ == '__main__':
    unittest.main()
