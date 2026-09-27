"""Independent opt-in observer delivery for configured event tables."""

import asyncio
from collections import deque

from tmserver.admission_identity import validate_admission
from tmserver.identities import GAME_KEY, service_nick
from tmserver.match_service import decode, packet
from tmserver.spectator_service import SpectatorError, SpectatorService
from tmserver.table_control import channel_exit, viewer_state


class EventSpectators:
    def __init__(self, adapter):
        self.adapter = adapter
        self.contexts = {}
        self.admissions = {}
        self.viewers = set()
        self.queues = {}
        self.tasks = {}
        self.senders = set()
        self.closers = set()
        self.stopping = False

    def _service(self, target, context):
        event, epoch, config, engine = context
        old = self.contexts.get(target)
        if old is not None and old[0] is engine:
            return old
        if old is not None:
            for peer, (bound, _) in tuple(self.admissions.items()):
                if bound == target:
                    self.disconnect(peer)
                    self._close_later(peer)
        template = self.adapter.server.default_table.spectators
        service = SpectatorService(
            enabled=template.enabled,
            clock=template.clock,
            bootstrap_delay=template.bootstrap_delay,
            turn_delay=template.turn_delay,
            action_delay=template.action_delay,
            packet_delay=template.packet_delay,
            max_backlog=template.max_backlog,
            max_observers=template.max_observers,
        )
        self.contexts[target] = (engine, service)
        return engine, service

    def _close_later(self, peer):
        # The close path reacquires the event lock; never await it here.
        task = asyncio.create_task(self._close(peer))
        self.closers.add(task)
        task.add_done_callback(self.closers.discard)

    async def _close(self, peer):
        try:
            await peer.close()
        except Exception as exc:
            self.adapter.server.log("event_observer_close_error", reason=str(exc))

    def handle(self, peer, key, target, payload):
        server = self.adapter.server
        coordinator = self.adapter.coordinator
        if self.stopping or not server.default_table.spectators.enabled:
            return False
        msg = decode(payload)
        if msg is None:
            return False
        active = self.admissions.get(peer)
        if active is not None and target != active[0] and msg.primary in (1, 0x41, 0x43, 0x98):
            raise SpectatorError("leave the watched table before another event admission")
        if coordinator.participant(peer):
            return False
        viewing = viewer_state(msg)
        exiting = channel_exit(msg)
        if active is not None and active[0] == target:
            if viewing is not None:
                if viewing:
                    self.viewers.add(peer)
                else:
                    self.viewers.discard(peer)
                return True
            if exiting:
                self.disconnect(peer)
                return True
        context = coordinator.observer_context(target)
        if context is None:
            if active is not None and msg.primary in (1, 0x41, 0x43):
                raise SpectatorError("watched event round has ended")
            return False
        event, epoch, config, engine = context
        binding = server.event_rooms.room_for_service(target)
        eligible = (
            binding is not None
            and peer in binding.room.members
            and not server.economy.is_busy(key)
            and engine.phase in ("turn", "battle")
        )
        if msg.primary == 0x2D:
            if msg.subtype or msg.argument or msg.sections != {"CheckJoinTable": {}}:
                raise SpectatorError("invalid watch table discovery")
            self._queue(
                peer,
                target,
                engine,
                None,
                packet(0x2E, body=f"@CheckJoinTable_Ans=/Join={int(eligible)}/Mem=0"),
            )
            return True
        if msg.primary == 1:
            if not eligible or config.channel.upper() not in {c.upper() for c in peer.channels}:
                raise SpectatorError(
                    "event observer requires active table and authenticated room/channel membership"
                )
            if msg.subtype or msg.argument or set(msg.sections) != {"Req"}:
                raise SpectatorError("invalid observer request")
            f = msg.sections["Req"]
            view = server.economy.account_view(key)
            try:
                validate_admission(f, "Req", nn=view.nn, name=view.name, require_nn=True)
            except ValueError as exc:
                raise SpectatorError(str(exc)) from exc
            if active is not None and active != (target, engine):
                raise SpectatorError("stale event observer admission")
            self._service(target, context)
            self.admissions[peer] = (target, engine)
            self._queue(peer, target, engine, None, engine.playack(engine.players[0].peer))
            return True
        if msg.primary == 0x43 and (msg.subtype == 0x25 or active is not None):
            if active != (target, engine) or config.channel.upper() not in {
                c.upper() for c in peer.channels
            }:
                raise SpectatorError("event watch requires table admission")
            _, service = self._service(target, context)
            out = service.handle(peer, payload, engine.observer_snapshot(), epoch=engine)
            if out is None:
                raise SpectatorError("observer cannot submit event participant actions")
            self._deliveries(target, engine, service, out)
            return True
        if active is not None and msg.primary == 0x41:
            raise SpectatorError("observer cannot claim event seat")
        return False

    def _deliveries(self, target, engine, service, deliveries):
        for d in deliveries:
            self._queue(d.peer, target, engine, service, d.payload)
        for peer, reason in service.take_evicted():
            self.adapter.server.log("event_observer_evicted", reason=reason)
            self.disconnect(peer)
            self._close_later(peer)

    def publish(self, deliveries):
        if self.stopping:
            return
        for target, (engine, service) in tuple(self.contexts.items()):
            if engine.outcome is not None and engine.outcome.aborted:
                for peer, (bound, _) in tuple(self.admissions.items()):
                    if bound == target:
                        self.disconnect(peer)
                        self._close_later(peer)
                del self.contexts[target]
                continue
            current = self.adapter.coordinator.observer_context(target)
            if current is not None and current[3] is not engine:
                self._service(target, current)
                continue
            related = [d for d in deliveries if d.source == target]
            out = service.publish(related, engine.observer_snapshot(), epoch=engine)
            self._deliveries(target, engine, service, out)

    def _queue(self, peer, target, engine, service, payload):
        if peer.closed or self.stopping:
            return
        queue = self.queues.setdefault(peer, deque())
        if len(queue) >= self.adapter.server.default_table.spectators.max_backlog + 3:
            self.disconnect(peer)
            self._close_later(peer)
            return
        queue.append((target, engine, service, payload))
        if peer not in self.tasks:
            task = self.tasks[peer] = asyncio.create_task(self._send(peer))
            self.senders.add(task)
            task.add_done_callback(self.senders.discard)

    async def _send(self, peer):
        task = asyncio.current_task()
        try:
            while self.tasks.get(peer) is task and not peer.closed and self.queues.get(peer):
                target, engine, service, payload = self.queues[peer].popleft()
                source = service_nick(target ^ GAME_KEY)
                # Handle failures here: a retired write cannot close a new admission.
                await peer.send(b":" + source + b"!p@local NOTICE " + peer.nick + b" :" + payload)
                if peer.closed:
                    break
                if service is not None:
                    async with self.adapter.lock:
                        if self.tasks.get(peer) is task:
                            service.record_sent(peer, payload, epoch=engine)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A failed observer writer must neither escape as an unobserved
            # task exception nor interrupt the participant event driver.
            self.adapter.server.log("event_observer_send_error", reason=str(exc))
            async with self.adapter.lock:
                if self.tasks.get(peer) is not task:
                    return
                self.disconnect(peer)
            self._close_later(peer)
        finally:
            if self.tasks.get(peer) is task:
                self.tasks.pop(peer, None)

    def disconnect(self, peer):
        self.admissions.pop(peer, None)
        self.queues.pop(peer, None)
        self.viewers.discard(peer)
        for _, service in self.contexts.values():
            service.disconnect(peer)
        # A new admission may reuse this peer and engine before old I/O retires.
        # Task ownership identifies the admission for callbacks and final cleanup.
        task = self.tasks.pop(peer, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def stop(self):
        self.stopping = True
        tasks = list(self.senders)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.closers:
            await asyncio.gather(*tuple(self.closers), return_exceptions=True)
        self.queues.clear()
        self.admissions.clear()
        self.contexts.clear()
