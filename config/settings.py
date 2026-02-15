"""
Application configuration settings.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Flask configuration
SECRET_KEY = 'tobii-mvp-secret'
HOST = '0.0.0.0'
PORT = 5002
DEBUG = True

# SocketIO configuration
SOCKETIO_CORS_ALLOWED_ORIGINS = "*"
SOCKETIO_ASYNC_MODE = 'threading'

# Tobii device configuration
G3_HOSTNAME = os.getenv('G3_HOSTNAME')

# Data queue configuration
DATA_QUEUE_MAX_SIZE = 2000

# Decimation defaults (send every Nth sample to browser)
DEFAULT_GAZE_DECIMATION = 2   # 50Hz / 2 = 25Hz to browser
DEFAULT_IMU_DECIMATION = 5    # ~100Hz / 5 = 20Hz to browser

# Recording configuration
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'recordings')

# Ensure recordings directory exists
os.makedirs(RECORDINGS_DIR, exist_ok=True)
