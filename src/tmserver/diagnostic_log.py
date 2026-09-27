"""JSONL diagnostics that never replace protocol or persistence errors."""

import json
import logging


def write_record(stream, record):
    stream.write(json.dumps(record, ensure_ascii=True) + "\n")
    stream.flush()


def report_failure(service, error):
    try:
        logging.getLogger(__name__).error(
            "%s diagnostic logging disabled after I/O failure: %s", service, error
        )
    except OSError:
        # A failed stderr/custom logging sink must not interrupt cleanup either.
        pass
