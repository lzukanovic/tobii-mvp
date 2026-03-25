"""
REST API routes for device status and recording management.
"""
from flask import Blueprint, jsonify, send_file
from services.recording_service import list_recordings, get_recording_path

api_bp = Blueprint('api', __name__, url_prefix='/api')

acquisition_service = None

def init_routes(acq_service):
    global acquisition_service
    acquisition_service = acq_service


@api_bp.route('/status')
def get_status():
    return jsonify(acquisition_service.get_status())


# Local CSV recordings

@api_bp.route('/recordings')
def get_recordings():
    return jsonify(list_recordings())


@api_bp.route('/recordings/<filename>')
def download_recording(filename):
    filepath = get_recording_path(filename)
    if not filepath:
        return jsonify({'error': 'Recording not found'}), 404
    return send_file(filepath, as_attachment=True, download_name=filename)


# Device recordings (glasses SD card)

@api_bp.route('/device-recordings')
def get_device_recordings():
    if not acquisition_service.status.connected:
        return jsonify([])
    try:
        return jsonify(acquisition_service.list_device_recordings())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/device-recordings/<uuid>', methods=['DELETE'])
def delete_device_recording(uuid):
    if not acquisition_service.status.connected:
        return jsonify({'error': 'Not connected'}), 400
    try:
        from services.async_bridge import run_coroutine_sync
        success = run_coroutine_sync(acquisition_service._g3.recordings.delete(uuid))
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
