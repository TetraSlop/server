"""Experimental drop-in calculations using original TM instructions.

The Python modules supply result types and input validation only. No Python
battle calculator, hand chooser, scoring or placement policy executes here.
One instance handles one operation at a time; different instances are isolated.
"""

from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from tmserver.game import ai, rules
from tmserver.game.original.image import OriginalCodeError, read_image


class OriginalRules:
    def __init__(self, image: str | Path, *, instruction_limit: int = 200_000):
        if type(instruction_limit) is not int or not 1 <= instruction_limit <= 0xFFFFFFFF:
            raise ValueError("Original-code instruction limit must be a positive uint32 integer")
        data = read_image(image)
        try:
            from tmserver.game.original.machine import Machine
        except ModuleNotFoundError as exc:
            if exc.name == "unicorn":
                raise OriginalCodeError("Install tmserver[original] to run original code") from exc
            raise
        self._machine = Machine(data, instruction_limit)
        self._lock = Lock()

    @property
    def last_draws(self) -> tuple[int, ...]:
        """Draws consumed by the last operation, including an interrupted one."""
        return tuple(self._machine.draws)

    @contextmanager
    def _operation(self, draw, maximum_draw, draw_error):
        if not self._lock.acquire(blocking=False):
            raise OriginalCodeError("This original-code instance is already executing")
        try:
            self._machine.reset(draw, maximum_draw, draw_error)
            yield self._machine
        finally:
            self._machine.draw = None
            self._lock.release()

    def calculate_battle(
        self,
        attacker,
        defender,
        draw,
        *,
        attacker_modifier=0,
        defender_modifier=0,
        attacker_property=0,
        defender_property=0,
    ) -> rules.BattleResult:
        from tmserver.game.original.machine import CARDS

        inputs = []
        for card, modifier, prop in (
            (attacker, attacker_modifier, attacker_property),
            (defender, defender_modifier, defender_property),
        ):
            attack, physical, magical = rules._battle_stats(card)
            kind = rules._integer(card.kind, "card kind", 0, 3)
            rules._integer(modifier, "modifier", -128, 127)
            rules._integer(prop, "property", 0, 255)
            inputs.append((bytes((attack, kind, physical, magical)), modifier, prop))
        with self._operation(draw, 0x7FFFFFFF, rules.RulesError) as machine:
            addresses = (CARDS, CARDS + 0x100)
            for address, (stats, modifier, prop) in zip(addresses, inputs, strict=True):
                machine.u.mem_write(address + 8, stats)
                machine.w8(address + 0x26, modifier)
                machine.w8(address + 0x24, prop)
            machine.call(0x100D31D0, addresses)
            sides = tuple(
                rules.BattleSide(
                    machine.r16(address + 0x8E),
                    machine.r16(address + 0x8C),
                    machine.r8(address + 0x29),
                    machine.r8(address + 0x2A),
                )
                for address in addresses
            )
            return rules.BattleResult(*sides, tuple(machine.draws))

    def choose_move(
        self, hand, board, owner, raw_rng, personality=ai.Personality()
    ) -> ai.Move | None:
        from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ESI, UC_X86_REG_ESP

        from tmserver.game.original.machine import GAME, PLAYER, STACK

        hand = tuple(ai._card(card) for card in hand)
        snapshot = ai._board(board, owner)
        if len(hand) > 5:
            raise ai.AIError("native hand has at most five cards")
        if not isinstance(personality, ai.Personality):
            raise ai.AIError("personality must be a Personality")
        with self._operation(raw_rng, 32767, ai.AIError) as machine:
            if not hand or all(cell is not None for cell in snapshot):
                return None  # Original downstream divisions assume legal candidates.
            for offset, value in (
                (0x178, owner),
                (0x171, 1),
                (0x16F, 0x22),
                (0x17A, 255),
                (0x179, 255),
            ):
                machine.w8(GAME + offset, value)
            machine.w8(PLAYER + 0x19F + owner * 0x30, len(hand))
            machine.w8(PLAYER + 0x19A + owner * 0x30, 1)
            for offset, value in (
                (0x184, personality.score_chance),
                (0x185, personality.preference),
                (0x186, personality.preference_chance),
            ):
                machine.w8(PLAYER + offset + owner * 0x30, value)
            for index, cell in enumerate(snapshot):
                address = GAME + 0x344 + index * 0x9C
                if cell is None or cell is ai.BLOCKED:
                    model, arrows, seat = (0xFFFF, 0, 8) if cell is None else (0x8000, 0, 9)
                else:
                    (model, arrows), seat = cell
                machine.w16(address + 14, model)
                machine.w8(address + 13, arrows)
                for offset in (0x1C, 0x1D, 0x1E):
                    machine.w8(address + offset, seat)
            for index, (model, arrows) in enumerate(hand):
                address = PLAYER + 0x20C + (owner * 5 + index) * 0x9C
                machine.w16(address + 14, model)
                machine.w8(address + 13, arrows)
            machine.u.reg_write(UC_X86_REG_EAX, owner)
            machine.u.reg_write(UC_X86_REG_ESI, GAME)
            machine.u.reg_write(UC_X86_REG_ESP, STACK)
            machine.run(0x100C42DE, 0x100C434F)
            index = machine.r8(GAME + 0x17A)
            ready = machine.call(0x100D68D0, this=GAME)
            field = machine.r8(GAME + 0x179)
            if (
                not ready
                or not 0 <= index < len(hand)
                or not 0 <= field < 16
                or snapshot[field] is not None
            ):
                raise OriginalCodeError("Original AI returned an invalid immediate move")
            return ai.Move(index, field)
