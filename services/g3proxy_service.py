"""
Transparent WebSocket proxy for the Tobii g3api protocol.

The glasses' WebSocket server rejects connections from browser origins (CORS).
This service opens a server-side WebSocket to the glasses and relays raw g3api
JSON messages between the browser (via Socket.IO events) and the glasses.

Browser  <--Socket.IO-->  Flask G3ProxyService  <--WebSocket (g3api)-->  Glasses
"""
import asyncio
import logging

import websockets

from services.async_bridge import run_coroutine, cancel_task, get_loop

logger = logging.getLogger(__name__)


class G3ProxyService:
    def __init__(self, socketio):
        self.socketio = socketio
        self._ws = None
        self._recv_task = None
        self._sid = None  # Socket.IO session ID of the connected browser client

    # ------------------------------------------------------------------
    # Public API (called from sync Flask/SocketIO handlers)
    # ------------------------------------------------------------------

    def connect(self, hostname, sid):
        """Open proxy WebSocket to glasses and start relaying messages."""
        if self._ws is not None or self._recv_task is not None:
            raise RuntimeError("Proxy already connected — call stop() first")
        self._sid = sid
        ws_url = f"ws://{hostname}/websocket"
        logger.info("G3 proxy connecting to %s for sid=%s", ws_url, sid)
        self._recv_task = run_coroutine(self._proxy_loop(ws_url, sid))

    def send(self, msg_str):
        """Forward a raw g3api JSON string from the browser to the glasses."""
        if self._ws is None:
            logger.warning("G3 proxy send: not connected, dropping message")
            return
        asyncio.run_coroutine_threadsafe(self._ws.send(msg_str), get_loop())

    def stop(self):
        """Close the proxy connection."""
        cancel_task(self._recv_task)
        self._recv_task = None
        self._ws = None
        self._sid = None

    # ------------------------------------------------------------------
    # Background async loop (runs on the shared asyncio event loop)
    # ------------------------------------------------------------------

    async def _proxy_loop(self, ws_url, sid):
        try:
            async with websockets.connect(ws_url, subprotocols=["g3api"]) as ws:
                self._ws = ws
                logger.info("G3 proxy WebSocket open: %s", ws_url)
                self.socketio.emit("g3_proxy_open", {}, to=sid)

                async for msg in ws:
                    self.socketio.emit("g3_proxy_msg", {"data": msg}, to=sid)

        except asyncio.CancelledError:
            logger.info("G3 proxy cancelled")
        except Exception as e:
            logger.error("G3 proxy error: %s", e)
            self.socketio.emit("g3_proxy_error", {"error": str(e)}, to=sid)
        finally:
            self._ws = None
            self.socketio.emit("g3_proxy_close", {}, to=sid)
            logger.info("G3 proxy closed")
