"""Fresh preferences, original guild/reload branches, and authenticated Init80."""
import asyncio
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from economy_service import EconomyError, EconomyService, PROFILE_SIZE, named
from identities import ROOM_RAW, TABLE_RAW, SHOP_RAW, player_id, service_nick
from profile_service import ProfileServer
from result_stats import PracticeStats
from server import Server
from test_server import Client
from test_profile_accounts import file_request, transact

sys.path.insert(0, str(Path(__file__).parents[1] / 'protocol_map'))
from startup_persistence_vectors import VM as StartupVM
from combat_sequence_vectors import CombatVM
from result_reward_vectors import VM as ProfileVM
from unicorn.x86_const import UC_X86_REG_ESP

F = 0x10294BD0
OPTIONS = 0x102B5E8C


def request(service, key='a', guild=0, subtype=0, **changes):
    account = service.account_view(key)
    fields = dict(NN=f'{account.nn:016X}', HID='0', Dm='0', Vol='0',
                  CN=account.name.hex(), GID=f'{account.player_id:016X}', L='1', GLD=str(guild))
    fields.update(changes)
    return named(0x80, subtype, 0, '@Init=' + ''.join(f'/{k}={v}' for k, v in fields.items()))


def imported(profile):
    vm = ProfileVM()
    vm.w32(0x102BB634, 0x20170000)  # Title table; presentation is not exercised.
    vm.u.mem_write(F, profile)
    vm.call(0x101011F0, this=vm.player)
    for address in (0x100A7F10, 0x100A7F70, 0x100A7B60):
        vm.hooks[address] = lambda sp: vm.ret(sp)
    vm.call(0x1014D9F0)
    return vm


class NativeVM(StartupVM):
    block = CombatVM.block

    def __init__(self):
        self.file_children = []
        super().__init__()

    def hook(self, u, address, size, data):
        if address == 0x10182230 and self.r32(u.reg_read(UC_X86_REG_ESP)+4) == 0x14C:
            self.file_children.append((self.heap+15)&~15)
        super().hook(u, address, size, data)


class NewProfileTests(unittest.TestCase):
    def test_fresh_preferences_match_original_bootstrap_and_profile_import(self):
        vm = NativeVM()
        vm.block(0x100A7BB7, 0x100A7D03, eax=0)
        bootstrap = [vm.r32(OPTIONS + 4*i) for i in range(12)]
        self.assertEqual(bootstrap, [0, 0, 2, 2, 1, 0, 0, 0, 0, 5, 60, 0])
        economy = EconomyService(seed=1)
        economy.provision('a')
        profile = economy.profile_bytes('a')
        loaded = imported(profile)
        self.assertEqual([loaded.r32(OPTIONS + 4*i) for i in range(12)], bootstrap)
        self.assertEqual(struct.unpack_from('<I', profile)[0], 300)
        # Header300 is not rating. Preserve the existing explicit local policy.
        self.assertEqual((loaded.r32(loaded.player+4), loaded.r16(loaded.player+0x86)), (0, 0))
        self.assertEqual(PracticeStats.from_profile(profile).rating, 0)
        self.assertEqual((loaded.r8(loaded.player+0xAF), loaded.r32(0x102B5F6C)), (1, 1))
        self.assertEqual(EconomyService.restore(economy.snapshot()).profile_bytes('a'), profile)

    def test_supplied_templates_and_old_saves_are_not_migrated(self):
        for rating, guild in ((0, 0), (200, 2), (300, 1), (777, 3)):
            with self.subTest(rating=rating, guild=guild):
                template = bytearray(PROFILE_SIZE)
                struct.pack_into('<I', template, 0, 0xDEADBEEF)
                struct.pack_into('<I', template, 0x30, rating)
                struct.pack_into('<H', template, 0x44, 65535)
                template[0xB3:0xB6] = bytes((0, 3, 0))
                template[0x102:0x104] = bytes((8, 70))
                template[0x140:0x148] = b'opaque!!'
                economy = EconomyService(seed=1)
                economy.provision('a', guild=guild, cards=[], profile_template=template)
                profile = economy.profile_bytes('a')
                self.assertEqual(profile[0x30:0x34], template[0x30:0x34])
                self.assertEqual(profile[0x40:0x104], template[0x40:0x104])
                self.assertEqual(profile[0x140:0x148], b'opaque!!')
                self.assertEqual(EconomyService.restore(economy.snapshot()).profile_bytes('a'), profile)
                loaded = imported(profile)
                self.assertEqual((loaded.r32(loaded.player+4), loaded.r16(loaded.player+0x86)), (rating, 65535))

    def test_original_online_guild_zero_opens_picker_nonzero_bypasses(self):
        for guild in range(4):
            vm = NativeVM()
            vm.w32(0x102B624C, 1)
            vm.w8(F+0x3B, guild)
            constructors = []
            def create(sp):
                constructors.append([vm.r32(sp+4+i*4) for i in range(3)])
                vm.ret(sp, 0x20400000, 12)
            vm.hooks[0x1014CBE0] = create
            vm.block(0x1014C866, 0x1014C90A, ebp=vm.obj, esi=0xffffffff)
            self.assertEqual(vm.r32(0x102B5F6C), guild)
            self.assertEqual(vm.r32(vm.obj+0x194), 0x13 if guild == 0 else 0x15)
            self.assertEqual(constructors, [[vm.obj, 1, 1]] if guild == 0 else [])

    def test_original_picker_choices_and_confirmation_route(self):
        for guild, event in enumerate((0x03001388, 0x03001389, 0x0300138A), 1):
            vm = NativeVM()
            vm.w32(0x102FF510, 0x20180000)
            vm.w32(vm.obj+0x64, event)
            vm.hooks[0x1019AFD0] = lambda sp: vm.ret(sp)
            vm.hooks[0x1019C7B0] = lambda sp: vm.ret(sp, 0, 24)
            vm.block(0x101500CC, 0x10150214, esi=vm.obj, edi=1, ebp=3)
            self.assertEqual(vm.r32(0x102B5F6C), guild)
            vm.w32(vm.obj+0x64, 0x03000019)
            vm.w32(vm.obj+0x160, 1)
            vm.block(0x101500CC, 0x1015061D, esi=vm.obj, edi=1, ebp=3)
            self.assertEqual(vm.posts, [[0x0300115D, 0]])

    def test_registration_only_changes_guild_and_replays_strict_reload(self):
        for guild in (1, 2, 3):
            economy = EconomyService(seed=1)
            economy.provision('a', guild=0)
            original = economy.profile_bytes('a')
            self.assertEqual(economy.initialize_profile('a', request(economy)), named(0x81, 0, 0, '@InitAns=/Ans=1'))
            self.assertEqual(economy.profile_bytes('a'), original)
            response = economy.initialize_profile('a', request(economy, guild=guild, subtype=1))
            self.assertEqual(response, named(0x81, 1, 0, '@InitAns=/Ans=2'))
            expected = bytearray(original)
            expected[0x3B] = guild
            self.assertEqual(economy.profile_bytes('a'), expected)
            self.assertEqual(economy.initialize_profile('a', request(economy, guild=guild, subtype=1)), response)
            restored = EconomyService.restore(economy.snapshot())
            self.assertEqual(restored.initialize_profile('a', request(restored, guild=guild, subtype=1)), response)
            self.assertEqual(restored.profile_bytes('a'), expected)
            # Execute original ack and reread decision for both startup paths.
            for function, state in ((0x10090A40, 2), (0x100910B0, 1)):
                vm = NativeVM()
                vm.w32(vm.obj+0x148, state)
                vm.queue(response[5:].decode())
                vm.call(function, this=vm.obj)
                self.assertEqual(vm.r32(vm.obj+0x140), 2)
                self.assertEqual(vm.r32(vm.obj+0x148), state+1)
                vm.w32(vm.obj+0x144, 1)
                vm.call(function, this=vm.obj)
                self.assertEqual(len(vm.file_children), 1)
                child = vm.file_children[0]
                self.assertEqual(vm.r32(child), 0x10205F60)
                self.assertEqual(vm.r32(child+0x148), 0)  # Strict child, optional0.
                vm.call(0x10090740, this=child)
                vm.call(0x10090740, this=child)
                self.assertEqual(vm.requests, [dict(path='U/g/TM0DataFile', buffer='10294bd0', capacity=PROFILE_SIZE)])

    def test_identity_schema_conflicts_and_busy_registration_fail_atomically(self):
        economy = EconomyService(seed=1)
        economy.provision('a', nn=123, player_id=7, guild=0)
        for fields in ({'NN': '000000000000007C'}, {'GID': '0000000000000008'},
                       {'CN': '466F72676564'}, {'HID': '1'}, {'Dm': '256'},
                       {'Vol': '65536'}, {'L': '2147483648'}, {'GLD': '4'},
                       {'GLD': '-1'}, {'NN': '123'}, {'CN': '00'}, {'Extra': '1'}):
            before = economy.snapshot()
            with self.assertRaises(EconomyError):
                economy.initialize_profile('a', request(economy, guild=2, **fields))
            self.assertEqual(economy.snapshot(), before)
        token = economy.reserve_match('a')
        before = economy.snapshot()
        with self.assertRaises(EconomyError):
            economy.initialize_profile('a', request(economy, guild=2))
        self.assertEqual(economy.snapshot(), before)
        economy.release_match(token)
        economy.initialize_profile('a', request(economy, guild=2))
        before = economy.snapshot()
        with self.assertRaises(EconomyError):
            economy.initialize_profile('a', request(economy, guild=1))
        self.assertEqual(economy.snapshot(), before)
        # Native initial send commonly carries0 before its file read finishes.
        self.assertEqual(economy.initialize_profile('a', request(economy)), named(0x81, 0, 0, '@InitAns=/Ans=1'))


class RegistrationTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temp.name)/'state.json'
        self.server = Server(state_path=self.path)
        self.key = 'UGITW5T4U'
        self.server.economy.provision(self.key, nn=player_id(self.key.encode()), guild=0)
        self.server.account_keys.add(self.key)
        self.server.persist()
        self.port = await self.server.start(0)
        self.profile = ProfileServer(Path(self.temp.name)/'profile.jsonl', self.server)
        self.profile_port = await self.profile.start(0)
        self.client = Client()
        await self.client.open(self.port)
        self.assertIn(b' 422 ', await self.client.read())

    async def asyncTearDown(self):
        await self.client.close()
        await self.profile.stop()
        await self.server.stop()
        self.temp.cleanup()

    async def init(self, guild, target=ROOM_RAW, **changes):
        payload = request(self.server.economy, self.key, guild, **changes)
        await self.client.send(b'NOTICE '+service_nick(target)+b' :'+payload)
        return await self.client.read()

    async def read_profile(self):
        error, payload = await transact(self.profile_port, self.key, 3, 0, file_request(),
                                       context=(self.client.cipher, self.client.iv))
        self.assertEqual(error, 0)
        return payload[:-4]

    async def test_encrypted_registration_profile_reread_restart_and_replay(self):
        before = await self.read_profile()
        self.assertEqual(before[0x3B], 0)
        self.assertTrue((await self.init(0)).endswith(b'@InitAns=/Ans=1'))
        self.assertTrue((await self.init(3)).endswith(b'@InitAns=/Ans=2'))
        after = await self.read_profile()
        expected = bytearray(before)
        expected[0x3B] = 3
        self.assertEqual(after, expected)
        # Registration is durably available before the positive response.
        restored = Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.profile_bytes(self.key), after)
            self.assertEqual(restored.economy.initialize_profile(self.key,
                request(restored.economy, self.key, 3)), named(0x81, 0, 0, '@InitAns=/Ans=2'))
        finally:
            await restored.stop()
        self.assertTrue((await self.init(3, target=TABLE_RAW)).endswith(b'@InitAns=/Ans=2'))
        self.assertTrue((await self.init(0, target=SHOP_RAW)).endswith(b'@InitAns=/Ans=1'))
        self.assertEqual(await self.read_profile(), after)

    async def test_encrypted_forged_identity_disconnects_without_mutation(self):
        before = self.path.read_bytes()
        self.assertEqual(await self.init(2, GID='0000000000000002'), b'')
        self.assertEqual(self.server.economy.account_view(self.key).guild, 0)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_failed_registration_save_has_no_success_and_preserves_last_save(self):
        before = self.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('injected registration save failure')):
            self.assertEqual(await self.init(2), b'')
        self.assertIsNotNone(self.server.storage_failed)
        self.assertEqual(self.path.read_bytes(), before)
        restored = Server(state_path=self.path)
        try:
            self.assertEqual(restored.economy.account_view(self.key).guild, 0)
        finally:
            await restored.stop()


if __name__ == '__main__':
    unittest.main()
