"""
Service for managing Tobii Pro Glasses 3 data acquisition.

Handles connection lifecycle, gaze/IMU streaming via g3pylib's rudimentary API,
and calibration. All g3pylib calls go through the async bridge.
"""
import asyncio
import time
import logging

from g3pylib import connect_to_glasses
from models.device import DeviceStatus
from services.async_bridge import run_coroutine_sync, run_coroutine
from config.settings import DESIRED_GAZE_SAMPLING_RATE, DEFAULT_GAZE_DECIMATION, DEFAULT_IMU_DECIMATION

logger = logging.getLogger(__name__)


class AcquisitionService:
    """Manages Tobii glasses connection, streaming, and calibration."""

    def __init__(self, data_queue, socketio):
        self.data_queue = data_queue
        self.socketio = socketio
        self.status = DeviceStatus()

        # g3pylib objects (managed on the async loop)
        self._g3 = None
        self._g3_context = None

        # Stream subscriptions
        self._gaze_unsub = None
        self._imu_unsub = None
        self._event_unsub = None
        self._sync_unsub = None

        # Receiver task futures
        self._gaze_future = None
        self._imu_future = None
        self._event_future = None
        self._sync_future = None

        # Streaming control
        self._streaming = False

        # Decimation settings
        self.gaze_decimation = DEFAULT_GAZE_DECIMATION
        self.imu_decimation = DEFAULT_IMU_DECIMATION

        # Recording buffers
        self.gaze_data = []
        self.imu_data = []
        self.events_data = []
        self.sync_data = []
        self.recording_metadata = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, hostname):
        """Connect to glasses. Blocks until connected or raises."""
        if self.status.connected:
            raise RuntimeError("Already connected")

        try:
            run_coroutine_sync(self._async_connect(hostname))
        except Exception as e:
            self.status.error = str(e)
            self.socketio.emit('status_update', self.status.to_dict())
            raise

    async def _async_connect(self, hostname):
        """Async connection logic."""
        self._g3_context = connect_to_glasses.with_hostname(hostname)
        self._g3 = await self._g3_context.__aenter__()

        # Fetch device info
        self.status.serial = await self._g3.system.get_head_unit_serial()
        self.status.firmware = await self._g3.system.get_version()

        battery_level = await self._g3.system.battery.get_level()
        self.status.battery = round(battery_level * 100, 1)

        self.status.charging = await self._g3.system.battery.get_charging()

        freqs = await self._g3.system.available_gaze_frequencies()
        if freqs:
            # try to set desired frequency if available, otherwise use max available
            if DESIRED_GAZE_SAMPLING_RATE in freqs:
                await self._g3.settings.set_gaze_frequency(DESIRED_GAZE_SAMPLING_RATE)
                self.status.gaze_freq = DESIRED_GAZE_SAMPLING_RATE
            else:
                max_freq = max(freqs)
                await self._g3.settings.set_gaze_frequency(max_freq)
                self.status.gaze_freq = max_freq
                logger.warning(
                    "Desired gaze frequency %d Hz not available, set to max available %d Hz",
                    DESIRED_GAZE_SAMPLING_RATE, max_freq
                )

        self.status.connected = True
        self.status.error = None

        logger.info(
            "Connected to %s (serial=%s, fw=%s, battery=%.1f%%, charging=%s)",
            hostname, self.status.serial, self.status.firmware, self.status.battery, self.status.charging
        )

    def disconnect(self):
        """Disconnect from glasses."""
        if not self.status.connected:
            return

        if self._streaming:
            self.stop_streaming()

        try:
            run_coroutine_sync(self._async_disconnect())
        except Exception as e:
            logger.error("Error during disconnect: %s", e)
        finally:
            self.status.reset()

    async def _async_disconnect(self):
        if self._g3_context is not None:
            await self._g3_context.__aexit__(None, None, None)
            self._g3 = None
            self._g3_context = None

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def start_streaming(self, gaze_decimation=None, imu_decimation=None):
        """Subscribe to gaze/IMU/event/sync and start receiver tasks."""
        if not self.status.connected:
            raise RuntimeError("Not connected")
        if self._streaming:
            raise RuntimeError("Already streaming")

        if gaze_decimation is not None:
            self.gaze_decimation = gaze_decimation
        if imu_decimation is not None:
            self.imu_decimation = imu_decimation

        # Clear buffers
        self.gaze_data.clear()
        self.imu_data.clear()
        self.events_data.clear()
        self.sync_data.clear()
        while not self.data_queue.empty():
            self.data_queue.get()

        self.recording_metadata = {
            'serial': self.status.serial,
            'firmware': self.status.firmware,
            'battery': self.status.battery,
            'charging': self.status.charging,
            'gaze_freq': self.status.gaze_freq,
        }

        # Set streaming flag BEFORE launching receivers, otherwise the
        # receiver coroutines see _streaming=False and exit immediately.
        self._streaming = True
        self.status.streaming = True

        try:
            run_coroutine_sync(self._async_start_streaming())
        except Exception:
            self._streaming = False
            self.status.streaming = False
            raise

    async def _async_start_streaming(self):
        """Subscribe to all signals and launch receiver coroutines."""
        # Subscribe
        gaze_queue, self._gaze_unsub = await self._g3.rudimentary.subscribe_to_gaze()
        imu_queue, self._imu_unsub = await self._g3.rudimentary.subscribe_to_imu()
        event_queue, self._event_unsub = await self._g3.rudimentary.subscribe_to_event()
        sync_queue, self._sync_unsub = await self._g3.rudimentary.subscribe_to_sync_port()

        # Start keepalive / streams
        await self._g3.rudimentary.start_streams()

        # Launch receiver tasks (self._streaming is already True)
        self._gaze_future = run_coroutine(self._gaze_receiver(gaze_queue))
        self._imu_future = run_coroutine(self._imu_receiver(imu_queue))
        self._event_future = run_coroutine(self._event_receiver(event_queue))
        self._sync_future = run_coroutine(self._sync_receiver(sync_queue))

    def stop_streaming(self):
        """Stop streaming and unsubscribe from all signals."""
        if not self._streaming:
            return

        self._streaming = False
        self.status.streaming = False

        # Cancel receiver tasks
        for fut in (self._gaze_future, self._imu_future, self._event_future, self._sync_future):
            if fut is not None:
                fut.cancel()

        try:
            run_coroutine_sync(self._async_stop_streaming())
        except Exception as e:
            logger.error("Error stopping streams: %s", e)

        self.status.gaze_samples = len(self.gaze_data)
        self.status.imu_samples = len(self.imu_data)

    async def _async_stop_streaming(self):
        await self._g3.rudimentary.stop_streams()

        for unsub in (self._gaze_unsub, self._imu_unsub, self._event_unsub, self._sync_unsub):
            if unsub is not None:
                try:
                    await unsub
                except Exception:
                    pass

        self._gaze_unsub = None
        self._imu_unsub = None
        self._event_unsub = None
        self._sync_unsub = None

    # ------------------------------------------------------------------
    # Receiver coroutines
    # ------------------------------------------------------------------

    async def _gaze_receiver(self, queue):
        """Receive gaze samples, store all, decimate for browser."""
        counter = 0
        try:
            while self._streaming:
                try:
                    sample = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                local_ts = time.time()

                # Parse sample - may be [timestamp, data_dict] or just data_dict
                device_ts, data = self._parse_sample(sample)

                gaze2d_x = None
                gaze2d_y = None
                gaze3d_x = None
                gaze3d_y = None
                gaze3d_z = None
                left_origin = [None, None, None]
                left_dir = [None, None, None]
                left_pupil = None
                right_origin = [None, None, None]
                right_dir = [None, None, None]
                right_pupil = None

                if data:
                    if "gaze2d" in data and data["gaze2d"] is not None:
                        g2d = data["gaze2d"]
                        gaze2d_x = g2d[0] if len(g2d) > 0 else None
                        gaze2d_y = g2d[1] if len(g2d) > 1 else None

                    if "gaze3d" in data and data["gaze3d"] is not None:
                        g3d = data["gaze3d"]
                        gaze3d_x = g3d[0] if len(g3d) > 0 else None
                        gaze3d_y = g3d[1] if len(g3d) > 1 else None
                        gaze3d_z = g3d[2] if len(g3d) > 2 else None

                    if "eyeleft" in data and data["eyeleft"] is not None:
                        eye = data["eyeleft"]
                        if "gazeorigin" in eye and eye["gazeorigin"] is not None:
                            left_origin = eye["gazeorigin"]
                        if "gazedirection" in eye and eye["gazedirection"] is not None:
                            left_dir = eye["gazedirection"]
                        if "pupildiameter" in eye:
                            left_pupil = eye["pupildiameter"]

                    if "eyeright" in data and data["eyeright"] is not None:
                        eye = data["eyeright"]
                        if "gazeorigin" in eye and eye["gazeorigin"] is not None:
                            right_origin = eye["gazeorigin"]
                        if "gazedirection" in eye and eye["gazedirection"] is not None:
                            right_dir = eye["gazedirection"]
                        if "pupildiameter" in eye:
                            right_pupil = eye["pupildiameter"]

                record = {
                    'device_ts': device_ts,
                    'local_ts': local_ts,
                    'gaze2d_x': gaze2d_x,
                    'gaze2d_y': gaze2d_y,
                    'gaze3d_x': gaze3d_x,
                    'gaze3d_y': gaze3d_y,
                    'gaze3d_z': gaze3d_z,
                    'left_origin_x': left_origin[0] if len(left_origin) > 0 else None,
                    'left_origin_y': left_origin[1] if len(left_origin) > 1 else None,
                    'left_origin_z': left_origin[2] if len(left_origin) > 2 else None,
                    'left_dir_x': left_dir[0] if len(left_dir) > 0 else None,
                    'left_dir_y': left_dir[1] if len(left_dir) > 1 else None,
                    'left_dir_z': left_dir[2] if len(left_dir) > 2 else None,
                    'left_pupil': left_pupil,
                    'right_origin_x': right_origin[0] if len(right_origin) > 0 else None,
                    'right_origin_y': right_origin[1] if len(right_origin) > 1 else None,
                    'right_origin_z': right_origin[2] if len(right_origin) > 2 else None,
                    'right_dir_x': right_dir[0] if len(right_dir) > 0 else None,
                    'right_dir_y': right_dir[1] if len(right_dir) > 1 else None,
                    'right_dir_z': right_dir[2] if len(right_dir) > 2 else None,
                    'right_pupil': right_pupil,
                }

                self.gaze_data.append(record)
                self.status.gaze_samples = len(self.gaze_data)

                # Decimate for browser
                counter += 1
                if counter % self.gaze_decimation == 0:
                    if not self.data_queue.full():
                        self.data_queue.put({
                            'type': 'gaze',
                            'gaze2d_x': gaze2d_x,
                            'gaze2d_y': gaze2d_y,
                            'left_pupil': left_pupil,
                            'right_pupil': right_pupil,
                            'ts': local_ts,
                        })

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Gaze receiver error: %s", e)

    async def _imu_receiver(self, queue):
        """Receive IMU samples, store all, decimate for browser."""
        counter = 0
        try:
            while self._streaming:
                try:
                    sample = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                local_ts = time.time()
                device_ts, data = self._parse_sample(sample)

                accel = [None, None, None]
                gyro = [None, None, None]
                mag = [None, None, None]

                if data:
                    if "accelerometer" in data and data["accelerometer"] is not None:
                        accel = data["accelerometer"]
                    if "gyroscope" in data and data["gyroscope"] is not None:
                        gyro = data["gyroscope"]
                    if "magnetometer" in data and data["magnetometer"] is not None:
                        mag = data["magnetometer"]

                record = {
                    'device_ts': device_ts,
                    'local_ts': local_ts,
                    'accel_x': accel[0] if len(accel) > 0 else None,
                    'accel_y': accel[1] if len(accel) > 1 else None,
                    'accel_z': accel[2] if len(accel) > 2 else None,
                    'gyro_x': gyro[0] if len(gyro) > 0 else None,
                    'gyro_y': gyro[1] if len(gyro) > 1 else None,
                    'gyro_z': gyro[2] if len(gyro) > 2 else None,
                    'mag_x': mag[0] if len(mag) > 0 else None,
                    'mag_y': mag[1] if len(mag) > 1 else None,
                    'mag_z': mag[2] if len(mag) > 2 else None,
                }

                self.imu_data.append(record)
                self.status.imu_samples = len(self.imu_data)

                counter += 1
                if counter % self.imu_decimation == 0:
                    if not self.data_queue.full():
                        self.data_queue.put({
                            'type': 'imu',
                            'accel_x': record['accel_x'],
                            'accel_y': record['accel_y'],
                            'accel_z': record['accel_z'],
                            'gyro_x': record['gyro_x'],
                            'gyro_y': record['gyro_y'],
                            'gyro_z': record['gyro_z'],
                            'ts': local_ts,
                        })

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("IMU receiver error: %s", e)

    async def _event_receiver(self, queue):
        """Receive event signals and store them."""
        try:
            while self._streaming:
                try:
                    sample = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                local_ts = time.time()
                device_ts, data = self._parse_sample(sample)
                self.events_data.append({
                    'device_ts': device_ts,
                    'local_ts': local_ts,
                    'data': data,
                })
                logger.info("Event received: %s", data)

                if not self.data_queue.full():
                    self.data_queue.put({
                        'type': 'event',
                        'data': str(data),
                        'ts': local_ts,
                    })

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Event receiver error: %s", e)

    async def _sync_receiver(self, queue):
        """Receive sync port signals and store them."""
        try:
            while self._streaming:
                try:
                    sample = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                local_ts = time.time()
                device_ts, data = self._parse_sample(sample)
                self.sync_data.append({
                    'device_ts': device_ts,
                    'local_ts': local_ts,
                    'data': data,
                })
                logger.info("SyncPort received: %s", data)

                if not self.data_queue.full():
                    self.data_queue.put({
                        'type': 'sync',
                        'data': str(data),
                        'ts': local_ts,
                    })

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("SyncPort receiver error: %s", e)

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def run_calibration(self):
        """Run calibration procedure. Blocks until done."""
        if not self.status.connected:
            raise RuntimeError("Not connected")

        result = run_coroutine_sync(self._async_calibrate(), timeout=60)
        self.status.calibrated = result
        return result

    async def _async_calibrate(self):
        # Calibration requires an active keep-alive session.
        # If we're already streaming, streams provide keep-alive;
        # otherwise we need to open a keep-alive context ourselves.
        if self._streaming:
            success = await self._g3.rudimentary.calibrate()
        else:
            async with self._g3.rudimentary.keep_alive_in_context():
                success = await self._g3.rudimentary.calibrate()
        return bool(success)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sample(sample):
        """
        Parse a g3pylib queue item which may be:
        - A list/tuple: [timestamp, data_dict]
        - A dict with 'timestamp' key
        - Something else entirely

        Returns:
            (device_timestamp, data_dict)
        """
        if isinstance(sample, (list, tuple)) and len(sample) >= 2:
            return sample[0], sample[1] if isinstance(sample[1], dict) else {}
        if isinstance(sample, dict):
            ts = sample.get('timestamp', None)
            return ts, sample
        return None, {}

    def get_status(self):
        return self.status.to_dict()

    def update_decimation(self, gaze_decimation=None, imu_decimation=None):
        """Update decimation rates (can be changed while streaming)."""
        if gaze_decimation is not None and gaze_decimation >= 1:
            self.gaze_decimation = int(gaze_decimation)
        if imu_decimation is not None and imu_decimation >= 1:
            self.imu_decimation = int(imu_decimation)
