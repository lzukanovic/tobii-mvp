"""
WebRTC signaling service for Tobii Pro Glasses 3.

Handles the full g3api WebRTC signaling exchange using g3pylib's existing
connection (the same one used for gaze/IMU streaming). g3pylib's receiver
task dispatches both RPC responses and signal events on that connection,
so no separate WebSocket or receive loop is needed here.

AcquisitionService calls set_connection(g3) after connecting to the glasses
and clear_connection() before disconnecting.
"""
import asyncio
import logging
import re

from services.async_bridge import get_loop, run_coroutine

logger = logging.getLogger(__name__)


class WebRTCSignalingService:
    def __init__(self, socketio):
        self.socketio = socketio
        self._g3ws = None           # G3WebSocketClientProtocol, set by set_connection()
        self._sid = None
        self._local_ip = None
        self._session_uuid = None
        self._webrtc_task = None

    # Connection lifecycle

    def set_connection(self, g3):
        """Store g3pylib's WebSocket after glasses connect."""
        self._g3ws = g3._connection if g3 else None

    def clear_connection(self):
        """Drop connection reference when glasses disconnect."""
        self.stop()
        self._g3ws = None

    # Public API

    def start(self, sid, recording_uuid=None):
        """Begin WebRTC signaling. Pass recording_uuid for playback, omit for live view."""
        if self._g3ws is None:
            self.socketio.emit("error", {"message": "Not connected to glasses"}, to=sid)
            return
        self._sid = sid
        self._webrtc_task = run_coroutine(self._run(sid, recording_uuid))

    def send_answer(self, answer_sdp):
        """Forward the browser's SDP answer to glasses via !start."""
        if self._g3ws is None or self._session_uuid is None:
            logger.warning("[WebRTC] send_answer called but session not ready")
            return
        asyncio.run_coroutine_threadsafe(
            self._g3ws.require_post(
                f"//webrtc/{self._session_uuid}!start", [answer_sdp]
            ),
            get_loop(),
        )

    def add_ice_candidate(self, mline_index, candidate_str):
        """Forward a browser ICE candidate to glasses (.local replaced)."""
        if self._g3ws is None or self._session_uuid is None:
            return
        if self._local_ip:
            candidate_str = re.sub(
                r"[\w-]+\.local\b", self._local_ip, candidate_str, flags=re.IGNORECASE
            )
        asyncio.run_coroutine_threadsafe(
            self._g3ws.require_post(
                f"//webrtc/{self._session_uuid}!add-ice-candidate",
                [mline_index, candidate_str],
            ),
            get_loop(),
        )

    def stop(self):
        """Cancel the WebRTC task; cleanup runs in _run's finally block."""
        if self._webrtc_task:
            self._webrtc_task.cancel()
            self._webrtc_task = None

    # Async internals

    async def _run(self, sid, recording_uuid=None):
        unsubscribe = None
        keepalive_task = None
        try:
            ws = self._g3ws

            # 1. Real local IP for Chrome mDNS .local candidate replacement
            self._local_ip = await ws.require_post("/!remote-host", [])
            logger.info(f"[WebRTC] Local IP: {self._local_ip}")

            # 2. Create WebRTC session — live view or recording playback
            if recording_uuid:
                session_uuid_raw = await ws.require_post("//webrtc!play", [recording_uuid])
                if session_uuid_raw is None:
                    raise RuntimeError(f"//webrtc!play returned null for UUID {recording_uuid}")
                self._session_uuid = str(session_uuid_raw)
                logger.info(f"[WebRTC] Playback session UUID: {self._session_uuid} (recording: {recording_uuid})")
            else:
                self._session_uuid = str(await ws.require_post("//webrtc!create", []))
                logger.info(f"[WebRTC] Live view session UUID: {self._session_uuid}")

            # 3. Subscribe to ICE candidates BEFORE !setup (critical ordering)
            #    g3pylib's receiver task fills ice_queue automatically
            ice_queue, unsubscribe = await ws.subscribe_to_signal(
                f"//webrtc/{self._session_uuid}:new-ice-candidate"
            )
            logger.info("[WebRTC] Subscribed to ICE signal")

            # 4. Request SDP offer (glasses are the offerer)
            offer_sdp = await ws.require_post(
                f"//webrtc/{self._session_uuid}!setup", []
            )
            logger.info("[WebRTC] Got SDP offer from glasses")

            # 5. Start keepalive (glasses drop the session after ~20 s without it)
            keepalive_task = asyncio.create_task(self._keepalive_loop())

            # 6. Send offer to browser
            self.socketio.emit("webrtc_offer", {"sdp": offer_sdp}, to=sid)
            logger.info("[WebRTC] Offer forwarded to browser")

            # 7. Forward glasses ICE candidates to browser as they arrive
            while True:
                body = await ice_queue.get()    # [sdpMLineIndex, candidateStr]
                if len(body) >= 2:
                    self.socketio.emit(
                        "webrtc_glasses_ice_candidate",
                        {"sdpMLineIndex": body[0], "candidate": body[1]},
                        to=sid,
                    )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[WebRTC] Signaling error: {e}", exc_info=True)
            self.socketio.emit("error", {"message": f"WebRTC error: {str(e)}"}, to=sid)
        finally:
            if keepalive_task:
                keepalive_task.cancel()
            if unsubscribe:
                try:
                    await unsubscribe
                except Exception:
                    pass
            if self._session_uuid and self._g3ws:
                try:
                    await self._g3ws.require_post("//webrtc!delete", [self._session_uuid])
                    logger.info(f"[WebRTC] Session {self._session_uuid} deleted")
                except Exception as e:
                    logger.warning(f"[WebRTC] Delete session failed: {e}")
            self._session_uuid = None
            self._local_ip = None
            self._sid = None

    async def _keepalive_loop(self):
        """Send !keepalive every 4 s to keep the glasses WebRTC session alive."""
        while True:
            await asyncio.sleep(4)
            if self._g3ws is None or self._session_uuid is None:
                break
            try:
                await self._g3ws.require_post(
                    f"//webrtc/{self._session_uuid}!keepalive", []
                )
            except Exception as e:
                logger.warning(f"[WebRTC] Keepalive failed: {e}")
                break
