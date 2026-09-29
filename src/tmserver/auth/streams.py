"""Bounded byte-stream adapter; WebSocket messages are chunks, not protocol packets."""

import asyncio

from starlette.websockets import WebSocketDisconnect


CHUNK_BYTES = 65536
BUFFER_BYTES = 2 * 1024 * 1024


class FlowControl:
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()

    def pause_reading(self):
        self.ready.clear()

    def resume_reading(self):
        self.ready.set()


class StreamWriter:
    def __init__(self, websocket, reader):
        self.websocket, self.reader = websocket, reader
        self.buffer = bytearray()
        self.closed = False
        self.finished = asyncio.Event()

    def get_extra_info(self, name):
        # The legacy challenge includes IPv4/port bytes, not authorization facts.
        return ("127.0.0.1", 0) if name in ("peername", "sockname") else None

    def write(self, data):
        if self.closed or len(self.buffer) + len(data) > BUFFER_BYTES:
            raise OSError("closed or overfull authenticated stream")
        self.buffer.extend(data)

    async def drain(self):
        if self.closed:
            raise OSError("authenticated stream is closed")
        while self.buffer:
            data = bytes(self.buffer[:CHUNK_BYTES])
            del self.buffer[:CHUNK_BYTES]
            try:
                await self.websocket.send_bytes(data)
            except (WebSocketDisconnect, RuntimeError) as exc:
                raise OSError("authenticated stream disconnected") from exc

    def close(self):
        self.closed = True
        self.buffer.clear()
        self.reader.feed_eof()
        self.finished.set()

    async def wait_closed(self):
        await self.finished.wait()


async def wait_expiry(access):
    while not access.revoked.is_set():
        remaining = access.deadline - access.clock()
        if remaining <= 0:
            access.revoke()
            return
        try:
            await asyncio.wait_for(access.revoked.wait(), remaining)
        except TimeoutError:
            pass  # A successful renewal may have extended the deadline.


async def relay(websocket, handler, access):
    flow = FlowControl()
    reader = asyncio.StreamReader(limit=BUFFER_BYTES // 2)
    reader.set_transport(flow)
    writer = StreamWriter(websocket, reader)
    close_code = 1000

    async def receive():
        nonlocal close_code
        while True:
            await flow.ready.wait()
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is None:
                close_code = 1003
                return
            if not data or len(data) > CHUNK_BYTES:
                close_code = 1009
                return
            access.check()
            reader.feed_data(data)

    tasks = [asyncio.create_task(receive()),
             asyncio.create_task(handler(reader, writer, access=access)),
             asyncio.create_task(wait_expiry(access))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (ValueError, OSError, WebSocketDisconnect):
        close_code = 4401 if access.revoked.is_set() else 1011
    finally:
        if access.revoked.is_set() or access.clock() >= access.deadline:
            close_code = 4401
        writer.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close(code=close_code)
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
