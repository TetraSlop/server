"""Ordered encrypted delivery adapter for configured original event matches."""

import asyncio
import uuid
from collections import deque

from tmserver.economy.claim_transactions import event_transaction
from tmserver.events.models import EventDelivery
from tmserver.events.matchmaking import EventMatchmaking
from tmserver.events.spectators import EventSpectators
from tmserver.protocol.identities import GAME_KEY, service_nick
from tmserver.game.packets import decode
from tmserver.rooms.routing import validate_room_entry


class EventAdapter:
    def __init__(
        self,
        server,
        configs=(),
        *,
        hand_provider=None,
        progress_policy="none",
        allow_observers=False,
    ):
        if progress_policy not in ("none", "wins", "rounds"):
            raise ValueError("invalid event step policy")
        self.server = server
        self.progress_policy = progress_policy
        self.instance = uuid.uuid4().hex
        self.coordinator = EventMatchmaking(
            server.events,
            server.event_rooms,
            configs,
            hand_provider=hand_provider,
            allow_observers=allow_observers,
            transactions=server.commerce,
        )
        self.routes = {
            service_nick(identity ^ GAME_KEY): (event, identity, channel)
            for event, identity, channel in self.coordinator.routes()
        }
        self.channels = {channel.upper() for _, _, channel in self.coordinator.routes()}
        self.lock = asyncio.Lock()
        self.task = None
        self.sender = None
        self.outbox = deque()
        self.spectators = EventSpectators(self)
        self.failure = None

    def _settle(self):
        # Results are journaled before the visible EventResult packet. The
        # coordinator retains each participant until the mapped Ready exit.
        results = self.coordinator.results()
        if results:
            participants = {key for _, _, _, keys in results for key in keys if key is not None}
            with event_transaction(self.server, participants) as state:
                for event, epoch, outcome, keys in results:
                    state.results.record(
                        state.events,
                        event,
                        f"{self.instance}:{epoch}",
                        outcome,
                        keys,
                        self.progress_policy,
                    )
        for event, epoch, _, _ in results:
            self.coordinator.acknowledge_result(event, epoch)
        out = []
        for event, epoch, _ in self.coordinator.completed():
            out += self.coordinator.finish(event, epoch)
        return out

    async def dispatch(self, peer, target, payload):
        async with self.lock:
            if peer.closed:
                return True
            if self.server.storage_failed is not None:
                raise OSError("event state storage failed")
            if self.failure is not None:
                raise ValueError("event driver stopped; restart is required")
            key = self.server.ensure_account(peer.nick)
            message = decode(payload)
            if message is not None and message.primary in (1, 0x98):
                binding = self.server.event_rooms.room_for_service(target)
                if binding is not None:
                    validate_room_entry(self.server, peer, binding.room)
            watching = self.spectators.admissions.get(peer)
            if self.spectators.handle(peer, key, target, payload):
                out = (
                    self._leave_chat(peer, watching[0])
                    if watching is not None and peer not in self.spectators.admissions
                    else []
                )
            else:
                out = self.coordinator.handle(
                    peer, key, target, payload, joined_channels=peer.channels
                )
                if out is None:
                    return False
                out += self._settle()
            done = self._queue(out, self.coordinator.take_closures())
        await self._wait_delivery(done)
        return True

    def _leave_chat(self, peer, table_id):
        source = service_nick(table_id ^ GAME_KEY)
        return [
            EventDelivery(d.peer, table_id, d.payload) for d in self.server.chat.leave(peer, source)
        ]

    def _queue(self, deliveries, closures=()):
        # Every release source reaches this synchronous barrier under the lock.
        # Detach before notifications allow a peer/table to be admitted again.
        chat = []
        for peer, table_id in self.coordinator.take_releases():
            chat.extend(self._leave_chat(peer, table_id))
        self.spectators.publish(deliveries)
        deliveries = (*deliveries, *chat)
        if not deliveries and not closures:
            return None
        done = asyncio.get_running_loop().create_future()
        self.outbox.append((tuple(deliveries), tuple(closures), done))
        if self.sender is None:
            self.sender = asyncio.create_task(self._drain())
        return done

    async def _wait_delivery(self, done, *, wait=True):
        # Room delivery can close a failed event peer while event delivery is
        # already closing another peer and awaiting that same room queue.
        # Queue cleanup in order, but never make the two writers await each other.
        current = asyncio.current_task()
        if done is None:
            return
        if wait and current not in (self.sender, self.server.room_task):
            await done
        else:

            def completed(result):
                if not result.cancelled() and (error := result.exception()) is not None:
                    self.server.log("event_delivery_error", reason=str(error))

            done.add_done_callback(completed)

    async def _drain(self):
        try:
            while self.outbox:
                deliveries, closures, done = self.outbox.popleft()
                try:
                    for d in deliveries:
                        if d.peer.closed:
                            continue
                        source = service_nick(d.source ^ GAME_KEY)
                        await self.server.deliver(
                            d.peer,
                            b":" + source + b"!p@local NOTICE " + d.peer.nick + b" :" + d.payload,
                        )
                    for peer in closures:
                        await self.close_released_peer(peer)
                except Exception as exc:
                    if not done.done():
                        done.set_exception(exc)
                else:
                    if not done.done():
                        done.set_result(None)
        finally:
            self.sender = None

    async def close_released_peer(self, peer):
        """Close a retired event peer only while no newer service owns it."""
        server = self.server
        if server.nicks.get(peer.nick) is not peer:
            return
        if server.storage_failed is None and (
            server.table_for_peer(peer) is not None
            or self.coordinator.participant(peer)
            or peer in self.spectators.admissions
            or server.economy.is_busy(peer.nick.decode("ascii"))
        ):
            return
        await peer.close()

    async def disconnect(self, peer, *, wait_delivery=True):
        async with self.lock:
            self.spectators.disconnect(peer)
            out = self.coordinator.disconnect(peer)
            if self.server.storage_failed is None and self.coordinator.results():
                out += self._settle()
            done = self._queue(out, self.coordinator.take_closures())
        await self._wait_delivery(done, wait=wait_delivery)

    async def _run(self):
        try:
            while True:
                async with self.lock:
                    if self.server.storage_failed is not None:
                        return
                    out = self.coordinator.poll()
                    for event, reason in self.coordinator.take_errors():
                        self.server.log("event_configuration_error", event=event, reason=reason)
                    for event, epoch in self.coordinator.pending():
                        out += self.coordinator.advance(event, epoch)
                    if out or self.coordinator.results():
                        out += self._settle()
                    done = self._queue(out, self.coordinator.take_closures())
                await self._wait_delivery(done)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except (ValueError, OSError) as exc:
            self.failure = exc
            self.server.log("event_driver_error", reason=str(exc))
            # Event-local failure must not discard an unrelated ordinary round.
            # Shared storage failure is global: every further mutation is unsafe.
            affected = [
                peer
                for peer in self.server.sessions
                if self.server.storage_failed is not None
                or self.coordinator.participant(peer)
                or peer in self.spectators.admissions
            ]
            for peer in affected:
                if self.coordinator.participant(peer) or peer in self.spectators.admissions:
                    await peer.close()
                else:
                    await self.close_released_peer(peer)

    def start(self):
        if self.coordinator.configs and self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        await self.spectators.stop()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.sender is not None:
            await self.sender
