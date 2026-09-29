"""Bounded x86 execution with explicit memory and a single substituted CRT call.

No PE startup, operating-system emulation, graphics, file or network callbacks.
Only rand() is intercepted; an unexpected dependency faults instead of being
silently stubbed. All writable memory and CPU state reset before each operation.
"""

import struct

from unicorn import (
    UC_ARCH_X86,
    UC_HOOK_CODE,
    UC_MODE_32,
    UC_PROT_EXEC,
    UC_PROT_READ,
    UC_PROT_WRITE,
    Uc,
    UcError,
)
from unicorn.x86_const import (
    UC_X86_REG_EAX,
    UC_X86_REG_ECX,
    UC_X86_REG_EIP,
    UC_X86_REG_ESP,
    UC_X86_REG_FPCW,
)

from tmserver.data.resources import read_json
from tmserver.game.original.image import IMAGE_BASE, IMAGE_SIZE, OriginalCodeError

PLAYER = 0x102B63E0
GAME = 0x20200000
CARDS = 0x20130000
MODE = 0x20300000
CATALOG = 0x20370000
RETURN = 0x20000000
STACK = 0x200FF000
RAND = 0x101E2E44
DATA_OFFSET = 0x204000
SCRATCH = ((0x200F0000, 0x10000), (CARDS, 0x1000), (GAME, 0x4000), (MODE, 0x1000))


class Machine:
    def __init__(self, image: bytes, instruction_limit: int):
        self.u = Uc(UC_ARCH_X86, UC_MODE_32)
        self.instruction_limit = instruction_limit
        self.draw = None
        self.draws = []
        self.maximum_draw = 0
        self.draw_error = OriginalCodeError
        self.data = image[DATA_OFFSET:]
        self.u.mem_map(IMAGE_BASE, IMAGE_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.u.mem_write(IMAGE_BASE, image)
        self.u.mem_protect(IMAGE_BASE, 0x8000, UC_PROT_READ)
        self.u.mem_protect(IMAGE_BASE + 0x8000, 0x1FC000, UC_PROT_READ | UC_PROT_EXEC)
        for address, size in SCRATCH:
            self.u.mem_map(address, size, UC_PROT_READ | UC_PROT_WRITE)
        self.u.mem_map(RETURN, 0x1000, UC_PROT_READ | UC_PROT_EXEC)
        self.u.mem_map(CATALOG, 0x1000, UC_PROT_READ)
        cards = read_json("economy_catalog.json")["cards"]
        self.u.mem_write(CATALOG, b"".join(bytes.fromhex(card["raw_hex"]) for card in cards))
        self.u.reg_write(UC_X86_REG_FPCW, 0x37F)
        self.initial_cpu = self.u.context_save()
        self.u.hook_add(UC_HOOK_CODE, self._random, begin=RAND, end=RAND)

    def reset(self, draw, maximum_draw: int, draw_error) -> None:
        self.u.context_restore(self.initial_cpu)
        self.u.mem_write(IMAGE_BASE + DATA_OFFSET, self.data)
        for address, size in SCRATCH:
            self.u.mem_write(address, bytes(size))
        self.draw, self.draws, self.maximum_draw = draw, [], maximum_draw
        self.draw_error = draw_error
        self.w32(0x102B63C4, GAME)
        self.w32(0x102BB62C, MODE)
        self.w32(0x102B6364, CATALOG)
        self.w32(0x102FF4FC, 250)
        self.w32(0x102FF568, 1)  # Immediate decisions; retain the original gate draws.
        self.w8(PLAYER + 0xE5, 2)

    def _random(self, machine, address, size, data) -> None:
        value = self.draw()
        if type(value) is not int or not 0 <= value <= self.maximum_draw:
            raise self.draw_error(f"Random draw must be an integer in 0..{self.maximum_draw}")
        self.draws.append(value)
        stack = machine.reg_read(UC_X86_REG_ESP)
        machine.reg_write(UC_X86_REG_EAX, value)
        machine.reg_write(UC_X86_REG_EIP, self.r32(stack))
        machine.reg_write(UC_X86_REG_ESP, stack + 4)

    def run(self, start: int, end: int) -> int:
        try:
            # Count bounds guest execution without a timer thread per call.
            # Trusted host RNG callbacks are outside this instruction budget.
            self.u.emu_start(start, end, count=self.instruction_limit)
        except UcError as exc:
            address = self.u.reg_read(UC_X86_REG_EIP)
            raise OriginalCodeError(f"Original x86 fault at {address:08X}: {exc}") from exc
        address = self.u.reg_read(UC_X86_REG_EIP)
        if address != end:
            raise OriginalCodeError(f"Original x86 execution budget exhausted at {address:08X}")
        return self.u.reg_read(UC_X86_REG_EAX)

    def call(self, address: int, args=(), *, this=0) -> int:
        self.u.reg_write(UC_X86_REG_ESP, STACK)
        self.u.reg_write(UC_X86_REG_ECX, this)
        for index, value in enumerate((RETURN, *args)):
            self.w32(STACK + index * 4, value)
        return self.run(address, RETURN)

    def w8(self, address: int, value: int) -> None:
        self.u.mem_write(address, bytes((value & 255,)))

    def w16(self, address: int, value: int) -> None:
        self.u.mem_write(address, struct.pack("<H", value))

    def w32(self, address: int, value: int) -> None:
        self.u.mem_write(address, struct.pack("<I", value))

    def r8(self, address: int) -> int:
        return self.u.mem_read(address, 1)[0]

    def r16(self, address: int) -> int:
        return struct.unpack("<H", self.u.mem_read(address, 2))[0]

    def r32(self, address: int) -> int:
        return struct.unpack("<I", self.u.mem_read(address, 4))[0]
