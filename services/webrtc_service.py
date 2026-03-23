"""
Minimal WebRTC service — lifecycle hooks only.

All WebRTC signaling (create/setup/start/ICE/delete) is handled by the
browser directly through G3ProxyService. This class only exists so that
AcquisitionService can notify it when the glasses connect/disconnect.
"""
import logging

logger = logging.getLogger(__name__)


class WebRTCService:
    def __init__(self, socketio):
        self.socketio = socketio
        self._g3 = None

    def set_glasses(self, g3):
        self._g3 = g3

    def clear_glasses(self):
        self._g3 = None
