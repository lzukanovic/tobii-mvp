"""
Tobii Pro Glasses 3 MVP Flask Application
Main application entry point with SocketIO for real-time data streaming.
"""
import logging
import threading
import time
from queue import Queue

from flask import Flask, render_template
from flask_socketio import SocketIO

from config.settings import (
    SECRET_KEY, HOST, PORT, DEBUG,
    SOCKETIO_CORS_ALLOWED_ORIGINS, SOCKETIO_ASYNC_MODE,
    DATA_QUEUE_MAX_SIZE,
)
from services.async_bridge import start_async_loop
from services.acquisition_service import AcquisitionService
from routes.api_routes import api_bp, init_routes
from routes.socketio_handlers import init_socketio_handlers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)


def create_app():
    """Create and configure Flask application."""
    app = Flask(__name__)
    app.config['SECRET_KEY'] = SECRET_KEY

    socketio = SocketIO(
        app,
        cors_allowed_origins=SOCKETIO_CORS_ALLOWED_ORIGINS,
        async_mode=SOCKETIO_ASYNC_MODE,
    )

    # Start the background asyncio event loop for g3pylib
    start_async_loop()

    # Create data queue for real-time streaming to browser
    data_queue = Queue(maxsize=DATA_QUEUE_MAX_SIZE)

    # Initialize services
    acquisition_service = AcquisitionService(data_queue, socketio)

    # Initialize routes
    init_routes(acquisition_service)
    init_socketio_handlers(socketio, acquisition_service)
    app.register_blueprint(api_bp)

    @app.route('/')
    def index():
        return render_template('index.html')

    # Background worker to broadcast queued data to all connected browser clients
    def data_broadcast_worker():
        while True:
            if not data_queue.empty():
                data = data_queue.get()
                socketio.emit('new_data', data)
            else:
                time.sleep(0.001)

    broadcast_thread = threading.Thread(target=data_broadcast_worker, daemon=True)
    broadcast_thread.start()

    return app, socketio


if __name__ == '__main__':
    print("Starting Tobii Pro Glasses 3 MVP Server...")
    print(f"Open http://localhost:{PORT} in your browser")

    app, socketio = create_app()
    socketio.run(
        app,
        host=HOST,
        port=PORT,
        debug=DEBUG,
        use_reloader=False,
    )
