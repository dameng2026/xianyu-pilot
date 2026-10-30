"""Immediate cross-thread pause; clearing one cause never clears another."""
from threading import Lock


class DeliveryGate:
    def __init__(self):
        self._lock=Lock()
        self._reasons=set()
        self._revisions={}

    def block(self,reason):
        with self._lock:
            self._reasons.add(reason)
            self._revisions[reason]=self._revisions.get(reason,0)+1

    def clear(self,reason,*,expected_revision=None):
        with self._lock:
            if expected_revision is not None and self._revisions.get(reason,0)!=expected_revision:
                return False
            self._reasons.discard(reason)
            self._revisions[reason]=self._revisions.get(reason,0)+1
            return True

    def revision(self,reason):
        with self._lock:
            return self._revisions.get(reason,0)

    @property
    def reasons(self):
        with self._lock:
            return tuple(sorted(self._reasons))

    @property
    def blocked(self):
        return bool(self.reasons)
