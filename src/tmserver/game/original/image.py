"""Load and check the operator's TM memory image."""

from hashlib import sha256
from pathlib import Path

IMAGE_BASE = 0x10000000
IMAGE_SIZE = 0x35B000
IMAGE_SHA256 = "422e46ab2ad6fd02db570a2384424dd1540b197e1ccd7e3da2645ca2b40b1879"


class OriginalCodeError(RuntimeError):
    """Unsupported code image or an original-code execution failure."""


def read_image(path: str | Path) -> bytes:
    """The image is RVA-indexed, not an on-disk PE file."""
    path = Path(path)
    if path.stat().st_size != IMAGE_SIZE:
        raise OriginalCodeError(
            "Expected the TM_emulated_mapped.dll memory image; "
            "the loader's TM_native.dll cannot be used directly"
        )
    data = path.read_bytes()
    if sha256(data).hexdigest() != IMAGE_SHA256:
        raise OriginalCodeError("Unsupported TM code image: SHA-256 mismatch")
    return data
