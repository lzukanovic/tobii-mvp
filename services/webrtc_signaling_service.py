"""
WebRTC signaling service for Tobii Pro Glasses 3.

Uses g3pylib's existing G3WebSocketClientProtocol connection (the same one
used for gaze/IMU streaming) instead of opening a separate WebSocket.

g3pylib's receiver task is always running while connected, so it dispatches
both RPC responses (via futures) and signal events (via asyncio.Queue) on
the shared connection — no separate receive loop needed here.

Flow:
  AcquisitionService calls set_connection(g3) after connecting and
  clear_connection() before disconnecting.

  WebRTC signaling (triggered by browser via Socket.IO):
    require_post /!remote-host          → real local IP (.local replacement)
    require_post //webrtc!create        → session UUID
    subscribe_to_signal :new-ice-candidate → asyncio.Queue of ICE bodies
    require_post //webrtc/<uuid>!setup  → SDP offer  →  emit webrtc_offer
    keepalive every 4 s
    ice_queue.get() loop               → emit webrtc_glasses_ice_candidate
    [browser answer arrives]
    require_post //webrtc/<uuid>!start  (called from send_answer)
    [browser ICE candidates arrive]
    require_post //webrtc/<uuid>!add-ice-candidate (called from add_ice_candidate)
    [stop() called]
    cancel keepalive, unsubscribe ICE, require_post //webrtc!delete
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

    # ------------------------------------------------------------------
    # Connection lifecycle — called by AcquisitionService
    # ------------------------------------------------------------------

    def set_connection(self, g3):
        """Store g3pylib's WebSocket after glasses connect."""
        self._g3ws = g3._connection if g3 else None

    def clear_connection(self):
        """Drop connection reference when glasses disconnect."""
        self.stop()
        self._g3ws = None

    # ------------------------------------------------------------------
    # Public API — called from sync Flask/Socket.IO handlers
    # ------------------------------------------------------------------

    def start(self, sid):
        """Begin WebRTC signaling using the existing g3pylib connection."""
        if self._g3ws is None:
            self.socketio.emit("error", {"message": "Not connected to glasses"}, to=sid)
            return
        self._sid = sid
        self._webrtc_task = run_coroutine(self._run(sid))

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

    # ------------------------------------------------------------------
    # Async internals — run on the shared background event loop
    # ------------------------------------------------------------------

    async def _run(self, sid):
        unsubscribe = None
        keepalive_task = None
        try:
            ws = self._g3ws

            # 1. Real local IP for Chrome mDNS .local candidate replacement
            self._local_ip = await ws.require_post("/!remote-host", [])
            logger.info(f"[WebRTC] Local IP: {self._local_ip}")

            # 2. Create WebRTC session on glasses
            self._session_uuid = str(await ws.require_post("//webrtc!create", []))
            logger.info(f"[WebRTC] Session UUID: {self._session_uuid}")

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

            # 5. Start keepalive — glasses drop the session after ~20 s
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
