"""
WebSocket event handlers for real-time communication with the browser.
"""
from flask_socketio import emit
from services.recording_service import save_recordings
from config.settings import G3_HOSTNAME, DEFAULT_GAZE_DECIMATION, DEFAULT_IMU_DECIMATION

acquisition_service = None


def init_socketio_handlers(socketio, acq_service):
    global acquisition_service
    acquisition_service = acq_service

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
            print(f"Error connecting: {e}")
            emit('error', {'message': f'Connection failed: {str(e)}'})

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

            acquisition_service.start_streaming(gaze_dec, imu_dec)
            emit('status_update', acquisition_service.get_status())

        except Exception as e:
            print(f"Error starting streaming: {e}")
            emit('error', {'message': f'Failed to start streaming: {str(e)}'})

    @socketio.on('stop_streaming')
    def handle_stop_streaming():
        try:
            acquisition_service.stop_streaming()

            # Save recordings
            files = save_recordings(
                acquisition_service.gaze_data,
                acquisition_service.imu_data,
                acquisition_service.recording_metadata,
                socketio,
            )

            emit('status_update', acquisition_service.get_status())

        except Exception as e:
            print(f"Error stopping streaming: {e}")
            emit('error', {'message': f'Failed to stop streaming: {str(e)}'})

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
