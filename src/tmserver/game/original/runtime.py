"""Process-owned native rules for direct API users; CLI contexts remain explicit."""

import atexit
import os
from threading import Lock

from tmserver.game.original.image import OriginalCodeError

_engine = None
_lock = Lock()


def configured_rules(image=None, bridge=None):
    """Create an owned context from explicit paths or the operator's environment."""
    from tmserver.game.original.native import NativeRules

    image = image or os.environ.get("TM_ORIGINAL_IMAGE")
    bridge = bridge or os.environ.get("TM_NATIVE_BRIDGE")
    if not image or not bridge:
        raise OriginalCodeError(
            "Native rules require --rules-image and --rules-bridge "
            "(or TM_ORIGINAL_IMAGE and TM_NATIVE_BRIDGE)"
        )
    return NativeRules(image, bridge)


def default_rules():
    """Load the required original image once. Never substitute Python algorithms."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = configured_rules()
            atexit.register(_engine.close)
        return _engine


class NativeRuntime:
    """Defer loading for import/inspection tools; all calculations require native code."""

    def calculate_battle(self, *args, **kwargs):
        return default_rules().calculate_battle(*args, **kwargs)

    def grow_card(self, *args, **kwargs):
        return default_rules().grow_card(*args, **kwargs)

    def choose_move(self, *args, **kwargs):
        return default_rules().choose_move(*args, **kwargs)

    def choose_special_move(self, *args, **kwargs):
        return default_rules().choose_special_move(*args, **kwargs)

    def choose_target(self, *args, **kwargs):
        return default_rules().choose_target(*args, **kwargs)
