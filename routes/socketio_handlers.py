"""
WebSocket event handlers for real-time communication with the browser.
"""
from flask import request
from flask_socketio import emit
from services.recording_service import save_recordings
from config.settings import G3_HOSTNAME, DEFAULT_GAZE_DECIMATION, DEFAULT_IMU_DECIMATION

acquisition_service = None
webrtc_service = None


def init_socketio_handlers(socketio, acq_service, webrtc_svc=None):
    global acquisition_service, webrtc_service
    acquisition_service = acq_service
    webrtc_service = webrtc_svc

    @socketio.on('connect')
    def handle_connect():
        print('Client connected')
        emit('status_update', acquisition_service.get_status())

    @socketio.on('disconnect')
    def handle_disconnect():
        print('Client disconnected')

    @socketio.on('connect_device')
    def handle_connect_device(data=None):
        try:
            hostname = G3_HOSTNAME
            if data and isinstance(data, dict):
                hostname = data.get('hostname', hostname)
            elif data and isinstance(data, str):
                hostname = data

            acquisition_service.connect(hostname)
            emit('status_update', acquisition_service.get_status())

        except Exception as e:
            error_msg = str(e) or repr(e)
            print(f"Error connecting: {error_msg}")
            emit('error', {'message': f'Connection failed: {error_msg}'})

    @socketio.on('disconnect_device')
    def handle_disconnect_device():
        try:
            acquisition_service.disconnect()
            emit('status_update', acquisition_service.get_status())
        except Exception as e:
            print(f"Error disconnecting: {e}")
            emit('error', {'message': f'Disconnect failed: {str(e)}'})

    @socketio.on('start_streaming')
    def handle_start_streaming(data=None):
        try:
            gaze_dec = DEFAULT_GAZE_DECIMATION
            imu_dec = DEFAULT_IMU_DECIMATION
            if data and isinstance(data, dict):
                gaze_dec = data.get('gaze_decimation', gaze_dec)
                imu_dec = data.get('imu_decimation', imu_dec)

            # Start glasses hardware recording FIRST, before data streams open.
            # Starting recorder.start() after the high-frequency gaze/IMU stream
            # is active can overwhelm the shared WebSocket and drop the connection.
            acquisition_service.start_glasses_recording()

            # Then open data streams (gaze/IMU subscriptions + keepalive)
            acquisition_service.start_streaming(gaze_dec, imu_dec)

            emit('status_update', acquisition_service.get_status())

        except Exception as e:
            print(f"Error starting recording: {e}")
            emit('error', {'message': f'Failed to start recording: {str(e)}'})

    @socketio.on('stop_streaming')
    def handle_stop_streaming():
        try:
            # Stop data streams first so the WebSocket quiets down,
            # then stop the glasses hardware recording.
            acquisition_service.stop_streaming()

            try:
                acquisition_service.stop_glasses_recording()
            except Exception as e:
                print(f"Warning: failed to stop glasses recording: {e}")
                emit('error', {'message': f'Glasses recording stop failed: {str(e)}'})

            # Save local CSV recordings
            save_recordings(
                acquisition_service.gaze_data,
                acquisition_service.imu_data,
                acquisition_service.recording_metadata,
                socketio,
            )

            emit('status_update', acquisition_service.get_status())

        except Exception as e:
            print(f"Error stopping recording: {e}")
            emit('error', {'message': f'Failed to stop recording: {str(e)}'})

    @socketio.on('cancel_recording')
    def handle_cancel_recording():
        try:
            acquisition_service.cancel_glasses_recording()
            emit('status_update', acquisition_service.get_status())
        except Exception as e:
            print(f"Error cancelling recording: {e}")
            emit('error', {'message': f'Failed to cancel recording: {str(e)}'})

    @socketio.on('update_decimation')
    def handle_update_decimation(data):
        try:
            gaze_dec = data.get('gaze_decimation')
            imu_dec = data.get('imu_decimation')
            acquisition_service.update_decimation(gaze_dec, imu_dec)
        except Exception as e:
            emit('error', {'message': str(e)})

    @socketio.on('run_calibration')
    def handle_run_calibration():
        try:
            success = acquisition_service.run_calibration()
            emit('calibration_result', {'success': success})
            emit('status_update', acquisition_service.get_status())
        except Exception as e:
            print(f"Calibration error: {e}")
            emit('calibration_result', {'success': False})
            emit('error', {'message': f'Calibration failed: {str(e)}'})

    # WebRTC signaling handlers

    @socketio.on('webrtc_start')
    def handle_webrtc_start(data=None):
        if webrtc_service is None:
            emit('error', {'message': 'WebRTC service not available'})
            return
        webrtc_service.start(request.sid)

    @socketio.on('webrtc_playback')
    def handle_webrtc_playback(data):
        if webrtc_service is None:
            emit('error', {'message': 'WebRTC service not available'})
            return
        uuid = data.get('uuid') if data else None
        if not uuid:
            emit('error', {'message': 'Recording UUID required for playback'})
            return
        webrtc_service.start(request.sid, recording_uuid=uuid)

    @socketio.on('webrtc_answer')
    def handle_webrtc_answer(data):
        if webrtc_service is None:
            return
        webrtc_service.send_answer(data.get('sdp', ''))

    @socketio.on('webrtc_ice_candidate')
    def handle_webrtc_ice_candidate(data):
        if webrtc_service is None:
            return
        webrtc_service.add_ice_candidate(
            data.get('sdpMLineIndex', 0),
            data.get('candidate', ''),
        )

    @socketio.on('webrtc_stop')
    def handle_webrtc_stop():
        if webrtc_service is None:
            return
        webrtc_service.stop()

    @socketio.on('set_gaze_overlay')
    def handle_set_gaze_overlay(data):
        try:
            enabled = bool(data.get('enabled', False))
            acquisition_service.set_gaze_overlay(enabled)
        except Exception as e:
            emit('error', {'message': f'Failed to set gaze overlay: {str(e)}'})
