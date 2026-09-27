"""Opt-in connection adapter for original active practice departure rules.

Requests are synchronous: a failed send may call Session.close while the match
lock is held. A separate worker applies departures after that publication, so
cleanup never waits recursively for the same lock. Account capabilities remain
owned by the table until its result/abort path releases them.
"""

import asyncio
from collections import deque


class MatchDepartures:
    def __init__(self, table, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError("allow_departures must be a boolean")
        self.table, self.enabled = table, enabled
        self.queue = deque()
        self.pending = set()
        self.task = None
        self.stopping = False
        self.aborting = None

    def abort(self, match):
        """Timeouts and processing failures always abort, never substitute."""
        self.aborting = match

    def request(self, peer):
        table, match = self.table, self.table.match
        if (
            not self.enabled
            or self.stopping
            or self.aborting is match
            or table.host.storage_failed is not None
        ):
            return False
        player = next((p for p in match.players if p.peer is peer), None)
        if player is None or peer not in table.match_reservations:
            return False
        if not player.departed and not match.departure_supported(peer):
            return False
        key = (match, peer)
        if key not in self.pending:
            self.pending.add(key)
            self.queue.append(key)
        if self.task is None:
            self.task = asyncio.create_task(self._run())
        return True

    def retains_account(self, nick):
        """Prevent a second session from binding a still-owned departed account."""
        match = self.table.match
        return any(
            p.peer is not None
            and p.peer.nick == nick
            and p.peer in self.table.match_reservations
            and (p.departed or p.peer.closed or (match, p.peer) in self.pending)
            for p in match.players
        )

    async def _run(self):
        table = self.table
        match = None
        try:
            while self.queue and not self.stopping:
                match, peer = self.queue.popleft()
                async with table.match_lock:
                    if match is not table.match or self.aborting is match:
                        self.pending.discard((match, peer))
                        continue
                    # Queued PART/Quit and disconnected sockets cannot keep an
                    # otherwise abandoned table running against itself.
                    remaining = [
                        p
                        for p in match.players
                        if p.peer is not None
                        and not p.departed
                        and not p.peer.closed
                        and (match, p.peer) not in self.pending
                    ]
                    if not remaining:
                        table.reset_match()
                        await table.publish_room(
                            table.room.set_table(0, 0, 0) + table.room.clear_npcs()
                        )
                    else:
                        player = next(p for p in match.players if p.peer is peer)
                        replies = [] if player.departed else match.depart(peer)
                        await table.publish_match(match, replies)
                        table.schedule_match_work(match)
                    self.pending.discard((match, peer))
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            table.host.log("departure_error", reason=str(exc))
            self.abort(match)
            # close retains its established fail-closed recovery for unsupported
            # or failed transitions; invoke it after leaving the match lock.
            peers = [p.peer for p in match.players if p.peer is not None]
            for peer in peers:
                if not peer.closed:
                    await peer.close()
            if table.match is match:
                table.reset_match()
        finally:
            self.task = None
            self.pending = {key for key in self.pending if key in self.queue}

    async def stop(self):
        self.stopping = True
        self.queue.clear()
        self.pending.clear()
        task = self.task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.task = None
