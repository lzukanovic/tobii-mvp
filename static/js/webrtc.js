/**
 * WebRTC Live View — Tobii Pro Glasses 3
 *
 * Signaling via a server-side g3api WebSocket proxy (Flask/Socket.IO).
 * The glasses reject direct browser WebSocket connections (CORS), so Flask
 * opens the connection and relays raw g3api JSON messages.
 *
 * Flow:
 *   1. Browser emits g3_proxy_connect → Flask opens WS to glasses
 *   2. Browser calls !remote-host → gets real local IP for .local replacement
 *   3. Browser drives full WebRTC signaling: !create → subscribe ICE →
 *      !setup (offer) → setRemoteDescription → createAnswer → !start (answer)
 *   4. Local ICE candidates have .local replaced with real IP before forwarding
 *
 * Depends on globals defined in index.html: socket, showError
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let webrtcPeer = null;
let g3proxyOpen = false;
let g3msgId = 0;
let g3pending = {};       // id → {resolve, reject}
let g3signals = {};       // signalId → callback
let webrtcSessionId = null;
let webrtcKeepalive = null;
let pendingGlassesCandidates = [];
let mlineIndexToMid = {};

// ---------------------------------------------------------------------------
// g3api protocol client (Socket.IO transport)
// ---------------------------------------------------------------------------

function g3post(path, body = []) {
  return new Promise((resolve, reject) => {
    if (!g3proxyOpen) { reject(new Error("g3 proxy not open")); return; }
    const id = ++g3msgId;
    g3pending[id] = { resolve, reject };
    socket.emit("g3_proxy_send", { data: JSON.stringify({ path, method: "POST", body, id }) });
  });
}

function g3subscribe(path, callback) {
  return new Promise((resolve, reject) => {
    if (!g3proxyOpen) { reject(new Error("g3 proxy not open")); return; }
    const id = ++g3msgId;
    g3pending[id] = {
      resolve: (sigId) => { g3signals[sigId] = callback; resolve(sigId); },
      reject,
    };
    socket.emit("g3_proxy_send", { data: JSON.stringify({ path, method: "POST", body: [], id }) });
  });
}

function g3handleMessage(data) {
  const msg = JSON.parse(data);
  if ("id" in msg) {
    const h = g3pending[msg.id];
    if (h) { delete g3pending[msg.id]; h.resolve(msg.body); }
  } else if ("signal" in msg) {
    const cb = g3signals[msg.signal];
    if (cb) cb(msg.body);
  }
}

socket.on("g3_proxy_msg", (d) => g3handleMessage(d.data));
socket.on("g3_proxy_error", (d) => console.error("[G3Proxy] error:", d.error));
socket.on("g3_proxy_close", () => { g3proxyOpen = false; console.log("[G3Proxy] closed"); });

// ---------------------------------------------------------------------------
// SDP parsing (mirrors Tobii reference client)
// ---------------------------------------------------------------------------

class SdpDesc {
  constructor(sdp) {
    this.sess = [];
    this.streams = {};
    let stream = this.sess;
    let mid = null;
    sdp.split("\r\n").forEach((line) => {
      line = line.trim();
      if (line.startsWith("m=")) {
        if (mid != null) this.streams[mid] = stream;
        stream = [];
        mid = line.substr(2);
      } else if (line.startsWith("a=mid:")) {
        mid = line.substr(6);
      }
      if (line !== "") stream.push(line);
    });
    if (stream != null) this.streams[mid] = stream;
  }

  to_sdp() {
    let sdp = this.sess.join("\r\n");
    for (const mid in this.streams)
      sdp += "\r\n" + this.streams[mid].join("\r\n");
    return sdp + "\r\n";
  }

  /** Zero out the port for a stream mid to signal we won't receive it. */
  suspend(mid) {
    if (!(mid in this.streams)) return false;
    for (let i in this.streams[mid]) {
      const l = this.streams[mid][i];
      if (l.startsWith("m=")) {
        const parts = l.split(" ", 4);
        this.streams[mid][i] = parts[0] + " 0 " + parts[2] + " " + parts[3];
        return true;
      }
    }
    return false;
  }
}

const WANTED_MIDS = new Set(["scenevideo", "sceneaudio", "eyesvideo"]);

// ---------------------------------------------------------------------------
// WebRTC lifecycle
// ---------------------------------------------------------------------------

async function startWebRTC() {
  document.getElementById("btnWebrtcStart").disabled = true;

  const hostname = document.getElementById("hostname").value.trim();
  if (!hostname) {
    showError("Enter a hostname before starting live view");
    document.getElementById("btnWebrtcStart").disabled = false;
    return;
  }

  try {
    // 1. Open server-side proxy WebSocket to glasses
    g3msgId = 0; g3pending = {}; g3signals = {}; g3proxyOpen = false;

    await new Promise((resolve, reject) => {
      const onOpen = () => { socket.off("g3_proxy_open", onOpen); socket.off("g3_proxy_error", onErr); resolve(); };
      const onErr = (d) => { socket.off("g3_proxy_open", onOpen); socket.off("g3_proxy_error", onErr); reject(new Error(d.error || "Proxy connect failed")); };
      socket.once("g3_proxy_open", onOpen);
      socket.once("g3_proxy_error", onErr);
      socket.emit("g3_proxy_connect", { hostname });
      setTimeout(() => reject(new Error("Proxy connection timeout")), 10000);
    });
    g3proxyOpen = true;
    console.log("[WebRTC] Proxy connected to glasses");

    // 2. Get real local IP via !remote-host (for .local candidate replacement)
    const localIP = await g3post("/!remote-host", []);
    console.log("[WebRTC] Local IP for mDNS replacement:", localIP);

    // 3. Create RTCPeerConnection
    webrtcPeer = new RTCPeerConnection();
    pendingGlassesCandidates = [];
    mlineIndexToMid = {};

    webrtcPeer.oniceconnectionstatechange = () =>
      console.log("[WebRTC] ICE state:", webrtcPeer.iceConnectionState);
    webrtcPeer.onconnectionstatechange = () =>
      console.log("[WebRTC] Connection state:", webrtcPeer.connectionState);
    webrtcPeer.onicecandidateerror = (ev) =>
      console.warn("[WebRTC] ICE error:", ev.errorCode, ev.errorText, ev.url);

    webrtcPeer.onicecandidate = (ev) => {
      if (ev.candidate) {
        let cand = ev.candidate.candidate;
        if (localIP) cand = cand.replace(/[\w-]+\.local\b/gi, localIP);
        console.log(`[WebRTC] Local candidate [mline=${ev.candidate.sdpMLineIndex}]:`, cand);
        g3post(`//webrtc/${webrtcSessionId}!add-ice-candidate`, [
          ev.candidate.sdpMLineIndex, cand,
        ]).catch((e) => console.warn("[WebRTC] Failed to send ICE candidate:", e));
      } else {
        console.log("[WebRTC] ICE gathering complete");
      }
    };

    webrtcPeer.ontrack = (ev) => {
      const mid = ev.transceiver && ev.transceiver.mid;
      console.log("[WebRTC] Track received — kind:", ev.track.kind, "mid:", mid);
      if (ev.track.kind === "video" && (!mid || mid === "scenevideo")) {
        const video = document.getElementById("webrtcVideo");
        video.srcObject = ev.streams[0] || new MediaStream([ev.track]);
        document.getElementById("webrtcPlaceholder").style.display = "none";
      }
    };

    // 4. Create WebRTC session on glasses
    const uuid = await g3post("//webrtc!create", []);
    webrtcSessionId = String(uuid);
    console.log("[WebRTC] Session created:", webrtcSessionId);

    // 5. Subscribe to ICE candidates BEFORE !setup (critical ordering)
    await g3subscribe(`//webrtc/${webrtcSessionId}:new-ice-candidate`, (body) => {
      const iceInit = {
        sdpMLineIndex: body[0],
        sdpMid: mlineIndexToMid[body[0]] ?? null,
        candidate: body[1],
      };
      console.log(`[WebRTC] Glasses ICE [mline=${iceInit.sdpMLineIndex}]:`, iceInit.candidate);
      if (!webrtcPeer || !webrtcPeer.remoteDescription) {
        pendingGlassesCandidates.push(iceInit);
      } else {
        webrtcPeer.addIceCandidate(new RTCIceCandidate(iceInit))
          .catch((e) => console.warn("[WebRTC] addIceCandidate failed:", e, iceInit));
      }
    });

    // 6. Request SDP offer
    const offerSdp = await g3post(`//webrtc/${webrtcSessionId}!setup`, []);
    console.log("[WebRTC] Received SDP offer");

    // 7. Start keepalive (must call every ≤5s; glasses drop after 20s)
    webrtcKeepalive = setInterval(() => {
      g3post(`//webrtc/${webrtcSessionId}!keepalive`, [])
        .catch((e) => console.warn("[WebRTC] Keepalive failed:", e));
    }, 4000);

    // 8. Parse SDP, build mline→mid map, suspend unwanted streams
    const desc = new SdpDesc(offerSdp);
    let mlineIdx = 0;
    for (const mid in desc.streams) {
      mlineIndexToMid[mlineIdx] = mid;
      const isVideo = desc.streams[mid].some((l) => l.startsWith("m=video"));
      if (isVideo && !WANTED_MIDS.has(mid)) {
        desc.suspend(mid);
        console.log(`[WebRTC] Suspended: ${mid}`);
      }
      mlineIdx++;
    }

    // 9. Set remote description
    await webrtcPeer.setRemoteDescription(
      new RTCSessionDescription({ type: "offer", sdp: desc.to_sdp() })
    );

    // 10. Flush glasses candidates queued before remoteDescription
    console.log(`[WebRTC] Flushing ${pendingGlassesCandidates.length} queued candidates`);
    for (const c of pendingGlassesCandidates) {
      await webrtcPeer.addIceCandidate(new RTCIceCandidate(c))
        .catch((e) => console.warn("[WebRTC] Flush ICE failed:", e, c));
    }
    pendingGlassesCandidates = [];

    // 11. Create answer and send to glasses
    const answer = await webrtcPeer.createAnswer();
    await webrtcPeer.setLocalDescription(answer);
    console.log("[WebRTC] Sending answer to glasses");
    const startResult = await g3post(`//webrtc/${webrtcSessionId}!start`, [answer.sdp]);
    if (!startResult) throw new Error("Glasses rejected the SDP answer");
    console.log("[WebRTC] Session started");
    document.getElementById("btnWebrtcStop").disabled = false;

  } catch (err) {
    console.error("[WebRTC] Setup failed:", err);
    showError("WebRTC start failed: " + err.message);
    _teardownWebRTC();
    document.getElementById("btnWebrtcStart").disabled = false;
  }
}

function stopWebRTC() {
  _teardownWebRTC();
  document.getElementById("webrtcPlaceholder").style.display = "flex";
  document.getElementById("btnWebrtcStart").disabled = false;
  document.getElementById("btnWebrtcStop").disabled = true;
}

function _teardownWebRTC() {
  if (webrtcKeepalive) { clearInterval(webrtcKeepalive); webrtcKeepalive = null; }
  if (webrtcSessionId && g3proxyOpen) {
    g3post("//webrtc!delete", [webrtcSessionId]).catch(() => {});
  }
  webrtcSessionId = null;
  g3proxyOpen = false;
  socket.emit("g3_proxy_stop");
  if (webrtcPeer) { webrtcPeer.close(); webrtcPeer = null; }
  g3pending = {}; g3signals = {};
  pendingGlassesCandidates = []; mlineIndexToMid = {};
  document.getElementById("webrtcVideo").srcObject = null;
}
