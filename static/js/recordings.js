/*
 * recordings.js - Device and local CSV recordings, playback modal
 *
 * Globals exposed: loadDeviceRecordings, loadRecordings,
 *                  openPlaybackModal, closePlayback, handleBackdropClick,
 *                  replayPlayback
 * Calls into: webrtc.js (playRecording, stopWebRTC)
 * Depends on globals from app.js: showSuccess, showError
 */

// Playback modal

let currentPlaybackUuid = null;

function openPlaybackModal(title, uuid) {
  if (uuid !== undefined) currentPlaybackUuid = uuid;
  document.getElementById("playbackTitle").textContent =
    title || "Recording Playback";
  document.getElementById("playbackModal").style.display = "block";
}

function closePlayback() {
  document.getElementById("playbackModal").style.display = "none";
  document.getElementById("playbackVideo").srcObject = null;
  if (typeof stopWebRTC === "function") stopWebRTC();
}

function handleBackdropClick(event) {
  if (event.target === event.currentTarget) closePlayback();
}

function replayPlayback() {
  if (!currentPlaybackUuid) return;
  const title = document.getElementById("playbackTitle").textContent;
  if (typeof playRecording === "function")
    playRecording(currentPlaybackUuid, title);
}

// Device recordings (glasses SD card)

function loadDeviceRecordings() {
  const btn = document.getElementById("btnRefreshDeviceRec");
  if (btn) btn.disabled = true;
  fetch("/api/device-recordings")
    .then((r) => r.json())
    .then((data) => {
      if (data.error) {
        showError("Failed to load device recordings: " + data.error);
        return;
      }
      renderDeviceRecordings(data);
    })
    .catch(() => showError("Error loading device recordings"))
    .finally(() => {
      if (btn) btn.disabled = false;
    });
}

function renderDeviceRecordings(recordings) {
  const list = document.getElementById("deviceRecordingsList");
  if (!recordings.length) {
    list.innerHTML =
      '<p style="color:#999;font-style:italic">No recordings on device</p>';
    return;
  }
  let html = "";
  recordings.forEach((rec) => {
    const name = rec.visible_name || rec.folder || rec.uuid;
    const date = rec.created
      ? new Date(rec.created).toLocaleString()
      : "Unknown date";
    const dur = rec.duration != null ? formatDuration(rec.duration) : "--";
    const gazeS =
      rec.gaze_samples != null
        ? rec.gaze_samples.toLocaleString() + " gaze samples"
        : "";
    const info = [date, dur, gazeS].filter(Boolean).join(" | ");
    const safeName = escapeHtml(name).replace(/'/g, "\\'");
    html += `
      <div class="recording-item">
        <div class="recording-info">
          <h3>${escapeHtml(name)}</h3>
          <p>${info}</p>
        </div>
        <div class="recording-actions">
          <button class="btn-start btn-sm" onclick="playRecording('${rec.uuid}', '${safeName}')">Play</button>
          <button class="btn-stop btn-sm" onclick="deleteDeviceRecording('${rec.uuid}')">Delete</button>
        </div>
      </div>`;
  });
  list.innerHTML = html;
}

function deleteDeviceRecording(uuid) {
  if (!confirm("Delete this recording from the device? This cannot be undone."))
    return;
  fetch(`/api/device-recordings/${uuid}`, { method: "DELETE" })
    .then((r) => r.json())
    .then((data) => {
      if (data.success) {
        showSuccess("Recording deleted from device");
        loadDeviceRecordings();
      } else {
        showError("Failed to delete: " + (data.error || "unknown error"));
      }
    })
    .catch(() => showError("Error deleting recording"));
}

// Local CSV recordings

function loadRecordings() {
  fetch("/api/recordings")
    .then((r) => r.json())
    .then(renderLocalRecordings)
    .catch((err) => console.error("Error loading recordings:", err));
}

function renderLocalRecordings(recordings) {
  const list = document.getElementById("recordingsList");
  if (!recordings.length) {
    list.innerHTML =
      '<p style="color:#999;font-style:italic">No recordings available</p>';
    return;
  }

  // Group gaze + IMU files that share the same session timestamp
  const groups = {};
  const ungrouped = [];
  recordings.forEach((rec) => {
    const m = rec.filename.match(/tobii_(?:gaze|imu)_(\d{8}_\d{6})\.csv/);
    if (m) {
      const key = m[1];
      if (!groups[key]) groups[key] = { key, files: [], created: rec.created };
      groups[key].files.push(rec);
    } else {
      ungrouped.push(rec);
    }
  });

  let html = "";

  Object.values(groups)
    .sort((a, b) => b.created - a.created)
    .forEach((group) => {
      const date =
        group.files[0].metadata.start_time ||
        new Date(group.created * 1000).toLocaleString();
      html += `<div class="recording-item" style="flex-direction:column;align-items:stretch;gap:8px">
        <div style="font-size:13px;font-weight:600;color:#333">${date}</div>`;
      group.files.forEach((rec) => {
        const sizeKB = (rec.size / 1024).toFixed(1);
        const samples = rec.metadata.samples || "N/A";
        const type = rec.type.toUpperCase();
        html += `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span style="font-size:12px;color:#666">
              <strong style="color:#2c5364">[${type}]</strong>
              ${rec.filename} &mdash; ${samples} samples &mdash; ${sizeKB} KB
            </span>
            <a href="/api/recordings/${rec.filename}" class="btn-download btn-sm" download>Download</a>
          </div>`;
      });
      html += `</div>`;
    });

  ungrouped.forEach((rec) => {
    const sizeKB = (rec.size / 1024).toFixed(1);
    const date =
      rec.metadata.start_time || new Date(rec.created * 1000).toLocaleString();
    const samples = rec.metadata.samples || "N/A";
    const type = rec.type.toUpperCase();
    html += `
      <div class="recording-item">
        <div class="recording-info">
          <h3>[${type}] ${rec.filename}</h3>
          <p>${date} | ${samples} samples | ${sizeKB} KB</p>
        </div>
        <a href="/api/recordings/${rec.filename}" class="btn-download" download>Download</a>
      </div>`;
  });

  list.innerHTML = html;
}

// Helpers

function formatDuration(seconds) {
  if (seconds == null) return "--";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Initial load
loadRecordings();

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
