"""Optional ASGI listener ownership for the administration panel."""

import asyncio
import socket
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass

import uvicorn

class EmbeddedServer(uvicorn.Server):
    def capture_signals(self):
        # The CLI's asyncio runner owns interruption and the order of shutdown.
        return nullcontext()


@dataclass(frozen=True)
class HttpListener:
    port: int
    task: asyncio.Task

    async def wait(self, seconds=0):
        """Surface a failed listener even during an otherwise unbounded run."""
        deadline = asyncio.create_task(
            asyncio.sleep(seconds) if seconds else asyncio.Event().wait()
        )
        try:
            await asyncio.wait((deadline, self.task), return_when=asyncio.FIRST_COMPLETED)
            if self.task.done():
                self.task.result()
                raise RuntimeError("The HTTP listener stopped unexpectedly")
        finally:
            deadline.cancel()
            await asyncio.gather(deadline, return_exceptions=True)


async def _stop(server, task):
    server.should_exit = True
    try:
        async with asyncio.timeout(5):
            await asyncio.shield(task)
    finally:
        # Uvicorn normally drains these itself. Also cover startup failure,
        # cancellation and an exception escaping its main loop.
        listeners = getattr(server, "servers", ())
        for listener in listeners:
            listener.close()
        for connection in tuple(server.server_state.connections):
            connection.shutdown()
        pending = [task, *server.server_state.tasks]
        for item in pending:
            if not item.done():
                item.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for listener in listeners:
            await listener.wait_closed()


@asynccontextmanager
async def serving_http(app_factory, *, port, bind="127.0.0.1"):
    """Borrow the live host; drain HTTP before its protocol listeners or store."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Bind ourselves so an occupied port raises OSError, not Uvicorn's
        # process-level SystemExit. The actual port also fixes the browser origin.
        listener.bind((bind, port))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        app = app_factory(port)
        server = EmbeddedServer(uvicorn.Config(
            app, host=bind, port=port, workers=1, reload=False,
            loop="asyncio", http="h11", ws="none",
            ws_max_size=65536, ws_max_queue=4, ws_per_message_deflate=False, lifespan="off",
            proxy_headers=False, access_log=False, log_config=None,
            timeout_graceful_shutdown=3,
        ))
        task = asyncio.create_task(server.serve(sockets=[listener]), name="tm-http")
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        task.result()
                        raise RuntimeError("The HTTP listener did not start")
                    await asyncio.sleep(0.01)
            yield HttpListener(port, task)
        finally:
            await _stop(server, task)
