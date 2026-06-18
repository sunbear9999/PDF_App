# core/utils/managed_signal_mixin.py
#
# Mixin for any QObject service that connects to signals on objects it does not
# own (runners, workers, external buses). Call _track_connection() instead of
# signal.connect(), then call _disconnect_all_tracked() to sever every tracked
# pair atomically — e.g. in a shutdown() method or before starting a new run.
#
# This mirrors the pattern already used in PapyrusAPI._subscriptions / _cleanup().
#
# Usage:
#   class MyService(QObject, _ManagedSignalMixin):
#       def __init__(self, bus):
#           super().__init__()
#           self._init_signal_tracking()
#           self._track_connection(bus.some_signal, self._handler)
#
#       def shutdown(self):
#           self._disconnect_all_tracked()


class _ManagedSignalMixin:
    def _init_signal_tracking(self):
        self.__tracked: list[tuple] = []

    def _track_connection(self, signal, slot):
        signal.connect(slot)
        self.__tracked.append((signal, slot))

    def _disconnect_all_tracked(self):
        for signal, slot in self.__tracked:
            try:
                signal.disconnect(slot)
            except RuntimeError:
                pass
        self.__tracked.clear()
