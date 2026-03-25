/*
 * app.js - Socket connection, device controls, status updates, toast helpers
 *
 * Globals exposed: socket, MAX_POINTS, MAX_LINE_POINTS, showSuccess, showError
 * Calls into: charts.js (clearCharts, handleGaze, handleIMU),
 *             recordings.js (loadRecordings, loadDeviceRecordings),
 *             webrtc.js (stopWebRTC, webrtcPeer)
 */

const socket = io();
const MAX_POINTS = 100;
const MAX_LINE_POINTS = 200;

// Socket events

socket.on("connect", () => {
  showSuccess("Connected to server");
});

socket.on("disconnect", () => {
  showError("Disconnected from server");
});

let _wasConnected = false;
socket.on("status_update", (s) => {
  updateStatus(s);
  if (s.connected && !_wasConnected) loadDeviceRecordings();
  _wasConnected = s.connected;
});

socket.on("error", (e) => showError(e.message));

socket.on("calibration_result", (r) => {
  if (r.success) {
    document.getElementById("calIndicator").className = "indicator on";
    document.getElementById("calText").textContent = "Calibrated";
    showSuccess("Calibration successful");
  } else {
    document.getElementById("calIndicator").className = "indicator off";
    document.getElementById("calText").textContent = "Calibration failed";
    showError("Calibration failed");
  }
  document.getElementById("btnCalibrate").disabled = false;
});

socket.on("new_data", (d) => {
  if (d.type === "gaze") handleGaze(d);
  else if (d.type === "imu") handleIMU(d);
});

socket.on("new_recording", (r) => {
  showSuccess(
    `Data saved: ${r.files.join(", ")} (${r.gaze_samples} gaze, ${r.imu_samples} IMU samples)`,
  );
  loadRecordings();
  loadDeviceRecordings();
});

// Status

function updateStatus(s) {
  const connected = s.connected;

  document.getElementById("connIndicator").className = connected
    ? "indicator on"
    : "indicator off";
  document.getElementById("connText").textContent = connected
    ? "Connected"
    : "Disconnected";
  document.getElementById("statusConnection").className = connected
    ? "status-item ok"
    : "status-item off";

  document.getElementById("statusSerial").textContent = s.serial || "--";
  document.getElementById("statusFirmware").textContent = s.firmware || "--";
  document.getElementById("statusBattery").textContent =
    s.battery == null ? "--%" : s.battery + "%";
  document.getElementById("statusCharging").textContent =
    s.charging == null ? "--" : s.charging ? "Yes" : "No";
  document.getElementById("statusGazeFreq").textContent = s.gaze_freq
    ? s.gaze_freq + " Hz"
    : "-- Hz";

  document.getElementById("gazeSamples").textContent = (
    s.gaze_samples || 0
  ).toLocaleString();
  document.getElementById("imuSamples").textContent = (
    s.imu_samples || 0
  ).toLocaleString();

  if (s.calibrated) {
    document.getElementById("calIndicator").className = "indicator on";
    document.getElementById("calText").textContent = "Calibrated";
  }

  const recInd = document.getElementById("recIndicator");
  const recText = document.getElementById("recText");
  const recStatus = document.getElementById("statusRecording");
  if (s.recording) {
    recInd.className = "indicator on";
    recText.textContent = "Recording";
    recStatus.className = "status-item ok";
  } else {
    recInd.className = "indicator off";
    recText.textContent = "Idle";
    recStatus.className = "status-item off";
  }

  document.getElementById("btnConnect").disabled = connected;
  document.getElementById("btnDisconnect").disabled = !connected;
  document.getElementById("btnCalibrate").disabled = !connected || s.streaming;
  document.getElementById("btnStart").disabled = !connected || s.streaming;
  document.getElementById("btnStop").disabled = !s.streaming;
  document.getElementById("btnCancelRec").disabled = !s.recording;
  document.getElementById("btnRefreshDeviceRec").disabled = !connected;
  if (!webrtcPeer) {
    document.getElementById("btnWebrtcStart").disabled = !connected;
  }
}

// Device actions

function connectDevice() {
  const hostname = document.getElementById("hostname").value.trim();
  if (!hostname) {
    showError("Please enter a hostname");
    return;
  }
  document.getElementById("btnConnect").disabled = true;
  showSuccess("Connecting...");
  socket.emit("connect_device", { hostname });
}

function disconnectDevice() {
  if (webrtcPeer) stopWebRTC();
  socket.emit("disconnect_device");
}

function startStreaming() {
  clearCharts();
  const gazeDec = parseInt(document.getElementById("gazeDecimation").value);
  const imuDec = parseInt(document.getElementById("imuDecimation").value);
  socket.emit("start_streaming", {
    gaze_decimation: gazeDec,
    imu_decimation: imuDec,
  });
}

function stopStreaming() {
  socket.emit("stop_streaming");
}

function cancelRecording() {
  if (
    !confirm(
      "Cancel video recording on device? The video will be deleted (local data recording continues).",
    )
  )
    return;
  socket.emit("cancel_recording");
}

function runCalibration() {
  document.getElementById("btnCalibrate").disabled = true;
  document.getElementById("calIndicator").className = "indicator warn";
  document.getElementById("calText").textContent = "Calibrating...";
  socket.emit("run_calibration");
}

function updateDecimation() {
  const gazeDec = parseInt(document.getElementById("gazeDecimation").value);
  const imuDec = parseInt(document.getElementById("imuDecimation").value);
  socket.emit("update_decimation", {
    gaze_decimation: gazeDec,
    imu_decimation: imuDec,
  });
}

// Toast helpers

function toast(message, { background, duration = 3500, close = false } = {}) {
  Toastify({
    text: message,
    duration,
    close,
    gravity: "top",
    position: "right",
    stopOnFocus: true,
    style: { background },
  }).showToast();
}

const showSuccess = (msg) => toast(msg, { background: "#10b981" });
const showError = (msg) =>
  toast(msg, { background: "#ef4444", duration: 6000 });

// Initial load

fetch("/api/status")
  .then((r) => r.json())
  .then((s) => {
    updateStatus(s);
    if (s.connected) loadDeviceRecordings();
  })
  .catch(() => {});

// Periodic sample count refresh
setInterval(() => {
  fetch("/api/status")
    .then((r) => r.json())
    .then((s) => {
      document.getElementById("gazeSamples").textContent = (
        s.gaze_samples || 0
      ).toLocaleString();
      document.getElementById("imuSamples").textContent = (
        s.imu_samples || 0
      ).toLocaleString();
    })
    .catch(() => {});
}, 2000);
