"""
Service for exporting Tobii recording data to CSV files.

Produces two CSV files per session (different schemas and sample rates):
- tobii_gaze_YYYYMMDD_HHMMSS.csv
- tobii_imu_YYYYMMDD_HHMMSS.csv
"""
import csv
import os
from datetime import datetime
from config.settings import RECORDINGS_DIR


GAZE_COLUMNS = [
    'DeviceTS', 'LocalTS',
    'Gaze2D_X', 'Gaze2D_Y',
    'Gaze3D_X', 'Gaze3D_Y', 'Gaze3D_Z',
    'EyeLeft_OriginX', 'EyeLeft_OriginY', 'EyeLeft_OriginZ',
    'EyeLeft_DirX', 'EyeLeft_DirY', 'EyeLeft_DirZ',
    'EyeLeft_PupilDiameter',
    'EyeRight_OriginX', 'EyeRight_OriginY', 'EyeRight_OriginZ',
    'EyeRight_DirX', 'EyeRight_DirY', 'EyeRight_DirZ',
    'EyeRight_PupilDiameter',
]

GAZE_KEYS = [
    'device_ts', 'local_ts',
    'gaze2d_x', 'gaze2d_y',
    'gaze3d_x', 'gaze3d_y', 'gaze3d_z',
    'left_origin_x', 'left_origin_y', 'left_origin_z',
    'left_dir_x', 'left_dir_y', 'left_dir_z',
    'left_pupil',
    'right_origin_x', 'right_origin_y', 'right_origin_z',
    'right_dir_x', 'right_dir_y', 'right_dir_z',
    'right_pupil',
]

IMU_COLUMNS = [
    'DeviceTS', 'LocalTS',
    'Accel_X', 'Accel_Y', 'Accel_Z',
    'Gyro_X', 'Gyro_Y', 'Gyro_Z',
    'Mag_X', 'Mag_Y', 'Mag_Z',
]

IMU_KEYS = [
    'device_ts', 'local_ts',
    'accel_x', 'accel_y', 'accel_z',
    'gyro_x', 'gyro_y', 'gyro_z',
    'mag_x', 'mag_y', 'mag_z',
]


def save_recordings(gaze_data, imu_data, metadata, socketio=None):
    """
    Save gaze and IMU data to separate CSV files.

    Args:
        gaze_data: List of gaze sample dicts
        imu_data: List of IMU sample dicts
        metadata: Recording metadata dict
        socketio: Optional SocketIO instance for emitting events

    Returns:
        list: Filenames of saved files
    """
    now = datetime.now()
    ts_str = now.strftime('%Y%m%d_%H%M%S')
    files = []

    if gaze_data:
        fname = f"tobii_gaze_{ts_str}.csv"
        _write_csv(fname, GAZE_COLUMNS, GAZE_KEYS, gaze_data, metadata, "Tobii Gaze Recording")
        files.append(fname)

    if imu_data:
        fname = f"tobii_imu_{ts_str}.csv"
        _write_csv(fname, IMU_COLUMNS, IMU_KEYS, imu_data, metadata, "Tobii IMU Recording")
        files.append(fname)

    if socketio and files:
        socketio.emit('new_recording', {
            'files': files,
            'gaze_samples': len(gaze_data),
            'imu_samples': len(imu_data),
            'start_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        })

    return files


def _write_csv(filename, columns, keys, data, metadata, title):
    """Write a single CSV file with metadata header."""
    filepath = os.path.join(RECORDINGS_DIR, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)

        # Metadata header
        writer.writerow([f'# {title}'])
        writer.writerow(['# Timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow(['# Serial', metadata.get('serial', 'N/A')])
        writer.writerow(['# Firmware', metadata.get('firmware', 'N/A')])
        writer.writerow(['# Battery (%)', metadata.get('battery', 'N/A')])
        writer.writerow(['# Gaze Frequency (Hz)', metadata.get('gaze_freq', 'N/A')])
        writer.writerow(['# Total Samples', len(data)])
        writer.writerow([])

        # Column header
        writer.writerow(columns)

        # Data rows
        for row in data:
            writer.writerow([row.get(k) for k in keys])

    print(f"Recording saved: {filepath} ({len(data)} samples)")


def list_recordings():
    """List all available recording files with metadata."""
    recordings = []

    if not os.path.exists(RECORDINGS_DIR):
        return recordings

    for filename in os.listdir(RECORDINGS_DIR):
        if not filename.endswith('.csv'):
            continue

        filepath = os.path.join(RECORDINGS_DIR, filename)
        file_stats = os.stat(filepath)

        metadata = {}
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if not line.startswith('#'):
                        break
                    if '# Serial' in line:
                        metadata['serial'] = line.split(',', 1)[1].strip()
                    elif '# Total Samples' in line:
                        metadata['samples'] = line.split(',', 1)[1].strip()
                    elif '# Timestamp' in line:
                        metadata['start_time'] = line.split(',', 1)[1].strip()
        except Exception:
            pass

        # Determine type from filename
        rec_type = 'gaze' if 'gaze' in filename else 'imu' if 'imu' in filename else 'unknown'

        recordings.append({
            'filename': filename,
            'type': rec_type,
            'size': file_stats.st_size,
            'created': file_stats.st_ctime,
            'metadata': metadata,
        })

    recordings.sort(key=lambda x: x['created'], reverse=True)
    return recordings


def get_recording_path(filename):
    """Get full path for a recording file (with traversal protection)."""
    filename = os.path.basename(filename)
    filepath = os.path.join(RECORDINGS_DIR, filename)
    if os.path.exists(filepath):
        return filepath
    return None
