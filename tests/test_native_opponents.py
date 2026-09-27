"""Original asset, NPC setup and local generated-hand comparisons; no UI."""

import hashlib
import struct
import sys
import unittest
from pathlib import Path

from tmserver.economy_service import Card, Catalog
from tmserver.native_opponents import (
    DECODED_SHA256,
    OPPONENTS,
    ROWS,
    SOURCE_SHA256,
    OpponentError,
    opponent,
    prepare_opponent,
)

BASE = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    unittest.main()
