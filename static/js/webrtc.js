/**
 * WebRTC Live View — Tobii Pro Glasses 3
 *
 * Flask is the full WebRTC signaling server. This file contains only the
 * browser-side WebRTC logic: it has no knowledge of the g3api protocol.
 *
 * Flow:
 *   1. User clicks Start → emit webrtc_start to Flask (with hostname)
 *   2. Flask opens dedicated WS to glasses, runs g3api signaling, emits
 *      webrtc_offer with the glasses' SDP offer.
 *   3. Browser: SdpDesc parsing/stream suspension → setRemoteDescription →
 *      createAnswer → setLocalDescription → emit webrtc_answer.
 *   4. Browser ICE candidates → emit webrtc_ice_candidate to Flask.
 *      Flask replaces .local addresses and forwards to glasses.
 *   5. Glasses ICE candidates → Flask emits webrtc_glasses_ice_candidate →
 *      browser addIceCandidate.
 *
 * Depends on globals defined in index.html: socket, showError
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let webrtcPeer = null;
let pendingGlassesCandidates = [];
let mlineIndexToMid = {};          // sdpMLineIndex → sdpMid (built from offer SDP)

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
// Socket.IO listeners (registered at load time)
// ---------------------------------------------------------------------------

socket.on("webrtc_offer", async (data) => {
  await _handleOffer(data.sdp);
});

socket.on("webrtc_glasses_ice_candidate", (data) => {
  const iceInit = {
    sdpMLineIndex: data.sdpMLineIndex,
    sdpMid: mlineIndexToMid[data.sdpMLineIndex] ?? null,
    candidate: data.candidate,
  };
  console.log(`[WebRTC] Glasses ICE [mline=${iceInit.sdpMLineIndex}]:`, iceInit.candidate);
  if (!webrtcPeer || !webrtcPeer.remoteDescription) {
    pendingGlassesCandidates.push(iceInit);
  } else {
    webrtcPeer
      .addIceCandidate(new RTCIceCandidate(iceInit))
      .catch((e) => console.warn("[WebRTC] addIceCandidate failed:", e, iceInit));
  }
});

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
    pendingGlassesCandidates = [];
    mlineIndexToMid = {};

    webrtcPeer = new RTCPeerConnection();

    webrtcPeer.oniceconnectionstatechange = () =>
      console.log("[WebRTC] ICE state:", webrtcPeer.iceConnectionState);
    webrtcPeer.onconnectionstatechange = () =>
      console.log("[WebRTC] Connection state:", webrtcPeer.connectionState);
    webrtcPeer.onicecandidateerror = (ev) =>
      console.warn("[WebRTC] ICE error:", ev.errorCode, ev.errorText, ev.url);

    webrtcPeer.onicecandidate = (ev) => {
      if (ev.candidate) {
        console.log(
          `[WebRTC] Local candidate [mline=${ev.candidate.sdpMLineIndex}]:`,
          ev.candidate.candidate
        );
        // Flask handles .local → real IP replacement before forwarding to glasses
        socket.emit("webrtc_ice_candidate", {
          sdpMLineIndex: ev.candidate.sdpMLineIndex,
          candidate: ev.candidate.candidate,
        });
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

    // Ask Flask to start signaling with the glasses
    socket.emit("webrtc_start", { hostname });
    console.log("[WebRTC] Signaling request sent to Flask, waiting for offer...");

    // Remainder of setup continues in _handleOffer() when Flask emits webrtc_offer

  } catch (err) {
    console.error("[WebRTC] Setup failed:", err);
    showError("WebRTC start failed: " + err.message);
    _teardownWebRTC();
    document.getElementById("btnWebrtcStart").disabled = false;
  }
}

async function _handleOffer(offerSdp) {
  try {
    console.log("[WebRTC] Received SDP offer from Flask");

    // Parse SDP, build mlineIndex→mid map, suspend unwanted video streams
    const desc = new SdpDesc(offerSdp);
    let mlineIdx = 0;
    for (const mid in desc.streams) {
      mlineIndexToMid[mlineIdx] = mid;
      const isVideo = desc.streams[mid].some((l) => l.startsWith("m=video"));
      if (isVideo && !WANTED_MIDS.has(mid)) {
        desc.suspend(mid);
        console.log(`[WebRTC] Suspended stream: ${mid}`);
      }
      mlineIdx++;
    }

    // Set remote description (glasses' offer)
    await webrtcPeer.setRemoteDescription(
      new RTCSessionDescription({ type: "offer", sdp: desc.to_sdp() })
    );

    // Flush candidates that arrived before remote description was set
    console.log(`[WebRTC] Flushing ${pendingGlassesCandidates.length} queued candidates`);
    for (const c of pendingGlassesCandidates) {
      await webrtcPeer
        .addIceCandidate(new RTCIceCandidate(c))
        .catch((e) => console.warn("[WebRTC] Flush ICE failed:", e, c));
    }
    pendingGlassesCandidates = [];

    // Create answer and send to Flask (which forwards to glasses via !start)
    const answer = await webrtcPeer.createAnswer();
    await webrtcPeer.setLocalDescription(answer);
    console.log("[WebRTC] Sending answer to Flask");
    socket.emit("webrtc_answer", { sdp: answer.sdp });

    document.getElementById("btnWebrtcStop").disabled = false;

  } catch (err) {
    console.error("[WebRTC] handleOffer failed:", err);
    showError("WebRTC offer handling failed: " + err.message);
    _teardownWebRTC();
    document.getElementById("btnWebrtcStart").disabled = false;
  }
}

function stopWebRTC() {
  socket.emit("webrtc_stop");
  _teardownWebRTC();
  document.getElementById("webrtcPlaceholder").style.display = "flex";
  document.getElementById("btnWebrtcStart").disabled = false;
  document.getElementById("btnWebrtcStop").disabled = true;
}

function _teardownWebRTC() {
  if (webrtcPeer) { webrtcPeer.close(); webrtcPeer = null; }
  pendingGlassesCandidates = [];
  mlineIndexToMid = {};
  document.getElementById("webrtcVideo").srcObject = null;
}
