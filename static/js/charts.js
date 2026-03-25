/*
 * charts.js - Chart.js initialisation, gaze canvas, data handlers
 *
 * Globals exposed: handleGaze, handleIMU, clearCharts
 * Depends on globals from app.js: MAX_POINTS, MAX_LINE_POINTS
 */

// Gaze 2D scatter chart

const gazeChart = new Chart(
  document.getElementById("gazeChart").getContext("2d"),
  {
    type: "scatter",
    data: {
      datasets: [
        {
          label: "Gaze Position",
          data: [],
          backgroundColor: "rgba(44,83,100,0.6)",
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          title: { display: true, text: "X (normalized)" },
          min: 0,
          max: 1,
        },
        y: {
          title: { display: true, text: "Y (normalized)" },
          min: 0,
          max: 1,
          reverse: true,
        },
      },
      plugins: { legend: { display: false } },
    },
  },
);

// Gaze heatmap (matrix chart)

const HEATMAP_BINS = 20;
const heatmapGrid = Array.from({ length: HEATMAP_BINS * HEATMAP_BINS }, () => 0);
let heatmapMax = 1;
let heatmapCounter = 0;
const HEATMAP_REFRESH_EVERY = 10;

function heatmapData() {
  const data = [];
  for (let row = 0; row < HEATMAP_BINS; row++) {
    for (let col = 0; col < HEATMAP_BINS; col++) {
      const v = heatmapGrid[row * HEATMAP_BINS + col];
      if (v > 0) data.push({ x: col, y: row, v });
    }
  }
  return data;
}

const heatmapChart = new Chart(
  document.getElementById("heatmapChart").getContext("2d"),
  {
    type: "matrix",
    data: {
      datasets: [
        {
          label: "Gaze Density",
          data: [],
          width: ({ chart }) =>
            (chart.chartArea || {}).width / HEATMAP_BINS - 1,
          height: ({ chart }) =>
            (chart.chartArea || {}).height / HEATMAP_BINS - 1,
          backgroundColor: (ctx) => {
            const v = ctx.dataset.data[ctx.dataIndex]?.v || 0;
            const t = Math.min(v / Math.max(heatmapMax, 1), 1);
            if (t < 0.25)
              return `rgba(0, ${Math.round(t * 4 * 200)}, 255, 0.8)`;
            if (t < 0.5)
              return `rgba(0, 200, ${Math.round((1 - (t - 0.25) * 4) * 255)}, 0.8)`;
            if (t < 0.75)
              return `rgba(${Math.round((t - 0.5) * 4 * 255)}, 200, 0, 0.8)`;
            return `rgba(255, ${Math.round((1 - (t - 0.75) * 4) * 200)}, 0, 0.9)`;
          },
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          type: "linear",
          min: -0.5,
          max: HEATMAP_BINS - 0.5,
          offset: false,
          title: { display: true, text: "X" },
          ticks: {
            stepSize: 5,
            callback: (v) => (v / HEATMAP_BINS).toFixed(1),
          },
          grid: { display: false },
        },
        y: {
          type: "linear",
          min: -0.5,
          max: HEATMAP_BINS - 0.5,
          offset: false,
          reverse: true,
          title: { display: true, text: "Y" },
          ticks: {
            stepSize: 5,
            callback: (v) => (v / HEATMAP_BINS).toFixed(1),
          },
          grid: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
    },
  },
);

function updateHeatmap(gazeX, gazeY) {
  const col = Math.min(Math.floor(gazeX * HEATMAP_BINS), HEATMAP_BINS - 1);
  const row = Math.min(Math.floor(gazeY * HEATMAP_BINS), HEATMAP_BINS - 1);
  if (col < 0 || row < 0) return;
  const idx = row * HEATMAP_BINS + col;
  heatmapGrid[idx]++;
  if (heatmapGrid[idx] > heatmapMax) heatmapMax = heatmapGrid[idx];
  heatmapCounter++;
  if (heatmapCounter % HEATMAP_REFRESH_EVERY === 0) {
    heatmapChart.data.datasets[0].data = heatmapData();
    heatmapChart.update("none");
  }
}

// Pupil diameter chart

const pupilChart = new Chart(
  document.getElementById("pupilChart").getContext("2d"),
  {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "Left",
          data: [],
          borderColor: "#3b82f6",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Right",
          data: [],
          borderColor: "#ef4444",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          display: true,
          title: { display: true, text: "Time" },
          ticks: { maxTicksLimit: 5, display: false },
        },
        y: {
          title: { display: true, text: "Diameter (mm)" },
          beginAtZero: false,
        },
      },
      plugins: { legend: { position: "top" } },
    },
  },
);

// Accelerometer chart

const accelChart = new Chart(
  document.getElementById("accelChart").getContext("2d"),
  {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "X",
          data: [],
          borderColor: "#ef4444",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Y",
          data: [],
          borderColor: "#10b981",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Z",
          data: [],
          borderColor: "#3b82f6",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          display: true,
          title: { display: true, text: "Time" },
          ticks: { maxTicksLimit: 5, display: false },
        },
        y: { title: { display: true, text: "m/s\u00B2" } },
      },
      plugins: { legend: { position: "top" } },
    },
  },
);

// Gyroscope chart

const gyroChart = new Chart(
  document.getElementById("gyroChart").getContext("2d"),
  {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "X",
          data: [],
          borderColor: "#ef4444",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Y",
          data: [],
          borderColor: "#10b981",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: "Z",
          data: [],
          borderColor: "#3b82f6",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          display: true,
          title: { display: true, text: "Time" },
          ticks: { maxTicksLimit: 5, display: false },
        },
        y: { title: { display: true, text: "deg/s" } },
      },
      plugins: { legend: { position: "top" } },
    },
  },
);

// Live gaze canvas

const gazeCanvas = document.getElementById("gazeCanvas");
const gazeCtx = gazeCanvas.getContext("2d");
const gazeTrail = [];
const TRAIL_LENGTH = 30;

function resizeGazeCanvas() {
  gazeCanvas.width = gazeCanvas.clientWidth;
  gazeCanvas.height = gazeCanvas.clientHeight;
}
resizeGazeCanvas();
window.addEventListener("resize", resizeGazeCanvas);

function drawGazeCanvas(x, y) {
  const w = gazeCanvas.width;
  const h = gazeCanvas.height;
  const px = x * w;
  const py = y * h;

  gazeTrail.push({ x: px, y: py });
  if (gazeTrail.length > TRAIL_LENGTH) gazeTrail.shift();

  gazeCtx.clearRect(0, 0, w, h);

  // Grid lines
  gazeCtx.strokeStyle = "rgba(255,255,255,0.06)";
  gazeCtx.lineWidth = 1;
  for (let i = 1; i < 10; i++) {
    gazeCtx.beginPath();
    gazeCtx.moveTo((w / 10) * i, 0);
    gazeCtx.lineTo((w / 10) * i, h);
    gazeCtx.stroke();
    gazeCtx.beginPath();
    gazeCtx.moveTo(0, (h / 10) * i);
    gazeCtx.lineTo(w, (h / 10) * i);
    gazeCtx.stroke();
  }

  // Fading trail
  for (let i = 0; i < gazeTrail.length - 1; i++) {
    const alpha = ((i + 1) / gazeTrail.length) * 0.4;
    const radius = 2 + (i / gazeTrail.length) * 4;
    gazeCtx.beginPath();
    gazeCtx.arc(gazeTrail[i].x, gazeTrail[i].y, radius, 0, Math.PI * 2);
    gazeCtx.fillStyle = `rgba(99, 202, 255, ${alpha})`;
    gazeCtx.fill();
  }

  // Crosshair
  gazeCtx.strokeStyle = "rgba(99, 202, 255, 0.4)";
  gazeCtx.lineWidth = 1;
  gazeCtx.setLineDash([4, 4]);
  gazeCtx.beginPath();
  gazeCtx.moveTo(px, 0);
  gazeCtx.lineTo(px, h);
  gazeCtx.moveTo(0, py);
  gazeCtx.lineTo(w, py);
  gazeCtx.stroke();
  gazeCtx.setLineDash([]);

  // Gaze point
  gazeCtx.beginPath();
  gazeCtx.arc(px, py, 10, 0, Math.PI * 2);
  gazeCtx.fillStyle = "rgba(99, 202, 255, 0.25)";
  gazeCtx.fill();
  gazeCtx.beginPath();
  gazeCtx.arc(px, py, 5, 0, Math.PI * 2);
  gazeCtx.fillStyle = "#63caff";
  gazeCtx.fill();

  // Coordinate label
  gazeCtx.fillStyle = "rgba(255,255,255,0.7)";
  gazeCtx.font = "12px monospace";
  gazeCtx.fillText(`(${x.toFixed(3)}, ${y.toFixed(3)})`, px + 14, py - 10);
}

// Data handlers

function handleGaze(d) {
  if (d.gaze2d_x != null && d.gaze2d_y != null) {
    drawGazeCanvas(d.gaze2d_x, d.gaze2d_y);

    const ds = gazeChart.data.datasets[0].data;
    ds.push({ x: d.gaze2d_x, y: d.gaze2d_y });
    if (ds.length > MAX_POINTS) ds.shift();
    gazeChart.update("none");

    updateHeatmap(d.gaze2d_x, d.gaze2d_y);
  }

  const labels = pupilChart.data.labels;
  const ts = new Date(d.ts * 1000).toLocaleTimeString();
  labels.push(ts);
  pupilChart.data.datasets[0].data.push(d.left_pupil);
  pupilChart.data.datasets[1].data.push(d.right_pupil);
  if (labels.length > MAX_LINE_POINTS) {
    labels.shift();
    pupilChart.data.datasets[0].data.shift();
    pupilChart.data.datasets[1].data.shift();
  }
  pupilChart.update("none");
}

function handleIMU(d) {
  const ts = new Date(d.ts * 1000).toLocaleTimeString();

  const labels = accelChart.data.labels;
  labels.push(ts);
  accelChart.data.datasets[0].data.push(d.accel_x);
  accelChart.data.datasets[1].data.push(d.accel_y);
  accelChart.data.datasets[2].data.push(d.accel_z);
  if (labels.length > MAX_LINE_POINTS) {
    labels.shift();
    accelChart.data.datasets.forEach((ds) => ds.data.shift());
  }
  accelChart.update("none");

  const gLabels = gyroChart.data.labels;
  gLabels.push(ts);
  gyroChart.data.datasets[0].data.push(d.gyro_x);
  gyroChart.data.datasets[1].data.push(d.gyro_y);
  gyroChart.data.datasets[2].data.push(d.gyro_z);
  if (gLabels.length > MAX_LINE_POINTS) {
    gLabels.shift();
    gyroChart.data.datasets.forEach((ds) => ds.data.shift());
  }
  gyroChart.update("none");
}

function clearCharts() {
  gazeTrail.length = 0;
  gazeCtx.clearRect(0, 0, gazeCanvas.width, gazeCanvas.height);

  gazeChart.data.datasets[0].data = [];
  gazeChart.update();

  heatmapGrid.fill(0);
  heatmapMax = 1;
  heatmapCounter = 0;
  heatmapChart.data.datasets[0].data = [];
  heatmapChart.update();

  pupilChart.data.labels = [];
  pupilChart.data.datasets.forEach((ds) => (ds.data = []));
  pupilChart.update();

  accelChart.data.labels = [];
  accelChart.data.datasets.forEach((ds) => (ds.data = []));
  accelChart.update();

  gyroChart.data.labels = [];
  gyroChart.data.datasets.forEach((ds) => (ds.data = []));
  gyroChart.update();
}
