"""Copy-on-write service state for one synchronous transaction.

stage_copy() returns a copy whose listed containers are shallow copies, so the
transaction can change them without touching the live service. Values inside
the containers keep their identity: other accounts' rows and every reservation
object stay the same objects. _stage_keys() copies the mutable editors of the
accounts in the transaction. publish_staged() moves the staged state into the live
service after the save; a failed request never publishes, so a half-applied
change cannot reach the next save.
"""

from copy import copy


class Staged:
    _staged_fields = ()  # Containers the service changes; shallow-copied per stage.
    _dependencies = ()  # Services bound to the transaction's staged copies, never published.

    def stage_copy(self, keys=(), **dependencies):
        if set(dependencies) - set(self._dependencies):
            raise TypeError("unknown staged dependency")
        staged = copy(self)
        for name in self._staged_fields:
            setattr(staged, name, copy(getattr(self, name)))
        vars(staged).update(dependencies)
        self._stage_keys(staged, frozenset(keys))
        return staged

    def _stage_keys(self, staged, keys):
        """Copy the mutable rows that belong to the transaction's accounts."""

    def publish_staged(self, staged):
        vars(self).update(
            (name, value) for name, value in vars(staged).items() if name not in self._dependencies
        )
