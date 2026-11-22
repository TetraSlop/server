"""JSONL diagnostics that never replace protocol or persistence errors."""

import json
import logging
import time
from pathlib import Path


class JsonlLog:
    """Append JSONL records; the first I/O failure is reported once and stops the log.

    A kept-open log opens on open() or its first record and stays open until
    close(), which is final. Otherwise each record opens and closes the file.
    """

    def __init__(self, service, path=None, *, keep_open=True):
        self.service, self.path, self.keep_open = service, path, keep_open
        self.stream, self.error = None, None

    def open(self):
        if self.stream is None and self.path is not None:
            self.stream = Path(self.path).open("a", encoding="utf-8")

    def __call__(self, event, **fields):
        if self.error is not None or (self.stream is None and self.path is None):
            return
        try:
            self.open()
            record = {"time": time.time(), "event": event, **fields}
            self.stream.write(json.dumps(record, ensure_ascii=True) + "\n")
            self.stream.flush()
        except OSError as error:
            self.fail(error)
        finally:
            if not self.keep_open:
                self._release()

    def close(self):
        self.path = None
        self._release()

    def _release(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError as error:
                self.fail(error)

    def fail(self, error):
        if self.error is not None:
            return
        self.error = error
        try:
            logging.getLogger(__name__).error(
                "%s diagnostic logging disabled after I/O failure: %s", self.service, error
            )
        except OSError:
            # A failed stderr/custom logging sink must not interrupt cleanup either.
            pass
