"""
WebRTC signaling service for Tobii Pro Glasses 3.

Flask acts as the full WebRTC signaling server:
- Opens a dedicated WebSocket to the glasses (g3api subprotocol), separate
  from the g3pylib connection used for acquisition/recording.
- Drives the full g3api WebRTC exchange on that connection.
- Relays the glasses' SDP offer and ICE candidates to the browser via Socket.IO.
- Receives the browser's SDP answer and ICE candidates via Socket.IO and
  forwards them to the glasses (with Chrome mDNS .local replacement).

Flow:
  Flask                        Glasses (g3api WS)         Browser (Socket.IO)
  ─────                        ──────────────────         ───────────────────
  !remote-host             →   get local IP
  //webrtc!create          →   session UUID
  //webrtc/<uuid>:new-ice-candidate → subscribe (signal id)
  //webrtc/<uuid>!setup    →   SDP offer           →   emit webrtc_offer
  keepalive every 4 s
                           ←   ICE signal          →   emit webrtc_glasses_ice_candidate
                           ←                       ←   webrtc_answer (SDP)
  //webrtc/<uuid>!start (answer SDP)
                           ←                       ←   webrtc_ice_candidate
  //webrtc/<uuid>!add-ice-candidate (.local replaced)
"""
import asyncio
import json
import logging
import re

import websockets

from services.async_bridge import get_loop, run_coroutine

logger = logging.getLogger(__name__)


class WebRTCSignalingService:
    def __init__(self, socketio):
        self.socketio = socketio
        self._ws = None
        self._sid = None
        self._local_ip = None
        self._session_uuid = None
        self._msg_id = 0
        self._pending = {}          # msg id → asyncio.Future
        self._ice_signal_id = None
        self._main_task = None

    # ------------------------------------------------------------------
    # Public API — called from sync Flask/Socket.IO handlers
    # ------------------------------------------------------------------

    def start(self, hostname, sid):
        """Open a dedicated WS to the glasses and begin WebRTC signaling."""
        self._sid = sid
        self._main_task = run_coroutine(self._run(hostname, sid))

    def send_answer(self, answer_sdp):
        """Forward the browser's SDP answer to the glasses via !start."""
        if self._ws is None or self._session_uuid is None:
            logger.warning("[WebRTC] send_answer called but session not ready")
            return
        asyncio.run_coroutine_threadsafe(
            self._post(f"//webrtc/{self._session_uuid}!start", [answer_sdp]),
            get_loop(),
        )

    def add_ice_candidate(self, mline_index, candidate_str):
        """Forward a browser ICE candidate to the glasses (.local replaced)."""
        if self._ws is None or self._session_uuid is None:
            return
        if self._local_ip:
            candidate_str = re.sub(
                r"[\w-]+\.local\b", self._local_ip, candidate_str, flags=re.IGNORECASE
            )
        asyncio.run_coroutine_threadsafe(
            self._post(
                f"//webrtc/{self._session_uuid}!add-ice-candidate",
                [mline_index, candidate_str],
            ),
            get_loop(),
        )

    def stop(self):
        """Tear down the WebRTC session and dedicated WS connection."""
        uuid = self._session_uuid
        ws = self._ws          # capture before clearing
        if uuid and ws:
            asyncio.run_coroutine_threadsafe(self._delete_session(uuid, ws), get_loop())
        if self._main_task:
            self._main_task.cancel()
            self._main_task = None
        self._ws = None
        self._session_uuid = None
        self._sid = None
        self._local_ip = None
        self._pending.clear()

    # ------------------------------------------------------------------
    # Async internals — run on the shared background event loop
    # ------------------------------------------------------------------

    async def _run(self, hostname, sid):
        ws_url = f"ws://{hostname}/websocket"
        try:
            async with websockets.connect(ws_url, subprotocols=["g3api"]) as ws:
                self._ws = ws

                # Receive loop runs concurrently; it resolves _post futures and
                # forwards ICE signal events to the browser.
                recv_task = asyncio.create_task(self._recv_loop(ws, sid))

                # 1. Real local IP — used to replace Chrome mDNS .local candidates
                self._local_ip = await self._post("/!remote-host", [])
                logger.info(f"[WebRTC] Local IP: {self._local_ip}")

                # 2. Create WebRTC session on glasses
                self._session_uuid = str(await self._post("//webrtc!create", []))
                logger.info(f"[WebRTC] Session UUID: {self._session_uuid}")

                # 3. Subscribe to ICE candidates BEFORE !setup (critical ordering)
                self._ice_signal_id = await self._post(
                    f"//webrtc/{self._session_uuid}:new-ice-candidate", []
                )
                logger.info(f"[WebRTC] ICE signal id: {self._ice_signal_id}")

                # 4. Request SDP offer from glasses (glasses are the offerer)
                offer_sdp = await self._post(
                    f"//webrtc/{self._session_uuid}!setup", []
                )
                logger.info("[WebRTC] Got SDP offer from glasses")

                # 5. Start keepalive — glasses drop the session after ~20 s
                keepalive_task = asyncio.create_task(
                    self._keepalive_loop(self._session_uuid)
                )

                # 6. Send offer to browser
                self.socketio.emit(
                    "webrtc_offer", {"sdp": offer_sdp}, to=sid
                )
                logger.info("[WebRTC] Offer forwarded to browser")

                await recv_task
                keepalive_task.cancel()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[WebRTC] Signaling error: {e}", exc_info=True)
            if sid:
                self.socketio.emit(
                    "error", {"message": f"WebRTC error: {str(e)}"}, to=sid
                )
        finally:
            self._ws = None

    async def _recv_loop(self, ws, sid):
        """Dispatch incoming g3api messages: RPC responses and ICE signals."""
        async for raw in ws:
            msg = json.loads(raw)
            if "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    fut.set_result(msg.get("body"))
            elif "signal" in msg and msg["signal"] == self._ice_signal_id:
                body = msg.get("body", [])
                # body = [sdpMLineIndex, candidateString]
                if len(body) >= 2:
                    self.socketio.emit(
                        "webrtc_glasses_ice_candidate",
                        {"sdpMLineIndex": body[0], "candidate": body[1]},
                        to=sid,
                    )

    async def _post(self, path, body):
        """Send a g3api POST request and await its response."""
        self._msg_id += 1
        msg_id = self._msg_id
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut
        await self._ws.send(
            json.dumps({"path": path, "method": "POST", "body": body, "id": msg_id})
        )
        return await fut

    async def _keepalive_loop(self, uuid):
        """Send !keepalive every 4 s to keep the glasses session alive."""
        while True:
            await asyncio.sleep(4)
            if self._ws is None:
                break
            try:
                await self._post(f"//webrtc/{uuid}!keepalive", [])
            except Exception as e:
                logger.warning(f"[WebRTC] Keepalive failed: {e}")
                break

    async def _delete_session(self, uuid, ws):
        """Best-effort delete of the WebRTC session on the glasses.

        Uses the captured ws reference directly (self._ws is already cleared
        by the time this coroutine runs). Just sends — no response awaited,
        since we're tearing down anyway.
        """
        try:
            self._msg_id += 1
            await ws.send(json.dumps({
                "path": "//webrtc!delete",
                "method": "POST",
                "body": [uuid],
                "id": self._msg_id,
            }))
            logger.info(f"[WebRTC] Session {uuid} deleted")
        except Exception as e:
            logger.warning(f"[WebRTC] Delete session failed: {e}")
