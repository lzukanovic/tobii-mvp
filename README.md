# Tobii Pro Glasses 3 - MVP

Real-time gaze and IMU data acquisition from Tobii Pro Glasses 3 via WebSocket API.

## Setup

```bash
cd Tobii/tobii-mvp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Set your glasses hostname in `.env`:

```
G3_HOSTNAME=tg03b-000000000000
```

## Run

```bash
python app.py
```

Open http://localhost:5002 in your browser.

## Usage

1. **Connect** - Enter hostname, click Connect. Status panel shows serial, firmware, battery.
2. **Calibrate** - Click Run Calibration (glasses must be worn by participant looking at calibration target).
3. **Start Streaming** - Select decimation rates, click Start Streaming. Charts update in real-time:
   - Gaze 2D scatter plot (normalized 0-1)
   - Pupil diameter (left/right)
   - Accelerometer XYZ
   - Gyroscope XYZ
4. **Stop Streaming** - Click Stop. CSV files are saved to `recordings/` and appear in the recordings panel.

## Data Export

Two CSV files per session:

- `tobii_gaze_YYYYMMDD_HHMMSS.csv` - 21 columns (gaze2d, gaze3d, eye data, pupil diameter)
- `tobii_imu_YYYYMMDD_HHMMSS.csv` - 11 columns (accelerometer, gyroscope, magnetometer)
