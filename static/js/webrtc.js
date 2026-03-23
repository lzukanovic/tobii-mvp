/*
 * WebRTC Live View - Tobii Pro Glasses 3
 *
 * Browser-side only. Flask handles all g3api signaling using the existing
 * glasses connection.
 *
 * Signaling flow:
 *   1. User clicks Start - browser emits webrtc_start to Flask
 *   2. Flask runs g3api signaling and emits webrtc_offer with the glasses SDP offer
 *   3. Browser parses and filters the SDP, sets remote description,
 *      creates an answer, and emits webrtc_answer to Flask
 *   4. Browser ICE candidates are emitted as webrtc_ice_candidate to Flask,
 *      which handles .local replacement and forwards them to the glasses
 *   5. Glasses ICE candidates arrive as webrtc_glasses_ice_candidate from Flask,
 *      browser calls addIceCandidate for each one
 *
 * Depends on globals from index.html: socket, showError
 */

// State
let webrtcPeer = null;
let pendingGlassesCandidates = [];
let mlineIndexToMid = {}; // built from offer SDP, used when adding glasses candidates

// SDP helper - mirrors Tobii reference client implementation
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

  // Zero out the port for a stream to signal we won't receive it
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

// Socket.IO listeners

socket.on("webrtc_offer", async (data) => {
  await handleOffer(data.sdp);
});

socket.on("webrtc_glasses_ice_candidate", (data) => {
  const iceInit = {
    sdpMLineIndex: data.sdpMLineIndex,
    sdpMid: mlineIndexToMid[data.sdpMLineIndex] ?? null,
    candidate: data.candidate,
  };
  console.log(
    `[WebRTC] Glasses ICE [mline=${iceInit.sdpMLineIndex}]:`,
    iceInit.candidate,
  );
  if (!webrtcPeer || !webrtcPeer.remoteDescription) {
    pendingGlassesCandidates.push(iceInit);
  } else {
    webrtcPeer
      .addIceCandidate(new RTCIceCandidate(iceInit))
      .catch((e) =>
        console.warn("[WebRTC] addIceCandidate failed:", e, iceInit),
      );
  }
});

// WebRTC lifecycle

async function startWebRTC() {
  document.getElementById("btnWebrtcStart").disabled = true;

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
          ev.candidate.candidate,
        );
        // Flask handles .local to real IP replacement before forwarding to glasses
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
      console.log("[WebRTC] Track received - kind:", ev.track.kind, "mid:", mid);
      if (ev.track.kind === "video" && (!mid || mid === "scenevideo")) {
        const video = document.getElementById("webrtcVideo");
        video.srcObject = new MediaStream([ev.track]);
        document.getElementById("webrtcPlaceholder").style.display = "none";
      } else if (ev.track.kind === "video" && mid === "eyesvideo") {
        const video = document.getElementById("eyesVideo");
        video.srcObject = new MediaStream([ev.track]);
        document.getElementById("eyesPlaceholder").style.display = "none";
      }
    };

    socket.emit("webrtc_start");
    console.log("[WebRTC] Waiting for offer from Flask...");
  } catch (err) {
    console.error("[WebRTC] Setup failed:", err);
    showError("WebRTC start failed: " + err.message);
    teardownWebRTC();
    document.getElementById("btnWebrtcStart").disabled = false;
  }
}

async function handleOffer(offerSdp) {
  try {
    console.log("[WebRTC] Received SDP offer from Flask");

    // Parse SDP, build mlineIndex to mid map, suspend unwanted video streams
    const desc = new SdpDesc(offerSdp);
    let mlineIdx = 0;
    for (const mid in desc.streams) {
      mlineIndexToMid[mlineIdx] = mid;
      const isVideo = desc.streams[mid].some((l) => l.startsWith("m=video"));
      if (isVideo && !WANTED_MIDS.has(mid)) {
        desc.suspend(mid);
        console.log("[WebRTC] Suspended stream:", mid);
      }
      mlineIdx++;
    }

    await webrtcPeer.setRemoteDescription(
      new RTCSessionDescription({ type: "offer", sdp: desc.to_sdp() }),
    );

    // Flush candidates that arrived before remote description was set
    console.log(
      `[WebRTC] Flushing ${pendingGlassesCandidates.length} queued candidates`,
    );
    for (const c of pendingGlassesCandidates) {
      await webrtcPeer
        .addIceCandidate(new RTCIceCandidate(c))
        .catch((e) => console.warn("[WebRTC] Flush ICE failed:", e, c));
    }
    pendingGlassesCandidates = [];

    const answer = await webrtcPeer.createAnswer();
    await webrtcPeer.setLocalDescription(answer);
    console.log("[WebRTC] Sending answer to Flask");
    socket.emit("webrtc_answer", { sdp: answer.sdp });

    document.getElementById("btnWebrtcStop").disabled = false;
    document.getElementById("btnWebrtcMute").disabled = false;
  } catch (err) {
    console.error("[WebRTC] handleOffer failed:", err);
    showError("WebRTC offer handling failed: " + err.message);
    teardownWebRTC();
    document.getElementById("btnWebrtcStart").disabled = false;
  }
}

function stopWebRTC() {
  socket.emit("webrtc_stop");
  teardownWebRTC();
  document.getElementById("webrtcPlaceholder").style.display = "flex";
  document.getElementById("btnWebrtcStart").disabled = false;
  document.getElementById("btnWebrtcStop").disabled = true;
  document.getElementById("btnWebrtcMute").disabled = true;
  document.getElementById("btnWebrtcMute").textContent = "Unmute";
}

function toggleMute() {
  const video = document.getElementById("webrtcVideo");
  const btn = document.getElementById("btnWebrtcMute");
  video.muted = !video.muted;
  btn.textContent = video.muted ? "Unmute" : "Mute";
}

function teardownWebRTC() {
  if (webrtcPeer) {
    webrtcPeer.close();
    webrtcPeer = null;
  }
  pendingGlassesCandidates = [];
  mlineIndexToMid = {};
  const video = document.getElementById("webrtcVideo");
  video.srcObject = null;
  video.muted = true;
  const eyesVideo = document.getElementById("eyesVideo");
  eyesVideo.srcObject = null;
  document.getElementById("eyesPlaceholder").style.display = "flex";
}
