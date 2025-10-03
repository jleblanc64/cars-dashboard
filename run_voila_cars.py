import tornado.web
import voila.voila_kernel_manager as vkm
from jupyter_server.services.kernels.kernelmanager import MappingKernelManager
from tornado.ioloop import IOLoop
from tornado.web import HTTPError
import logging
import sys
import threading
import time
import asyncio
from collections import defaultdict

# --- Configuration ---
NOTEBOOK = "misc/demo_cars.ipynb"
POOL_SIZE = 2
MAX_KERNELS = 6
KERNEL_CLEANUP_TIMEOUT_SEC = 60

# voila logger
voila_logger = logging.getLogger("voila")
voila_logger.setLevel(logging.CRITICAL + 1)
for handler in voila_logger.handlers[:]:
    voila_logger.removeHandler(handler)

# --- Global State ---
_kernel_lock = threading.Lock()
_original_start_kernel = MappingKernelManager.start_kernel
_original_shutdown_kernel = MappingKernelManager.shutdown_kernel
global_kernel_manager = None

# Kernels that watchdog has decided to forcibly shutdown
_forced_shutdowns = set()

# Tracks when a kernel first hit connections=0
kernel_connection_tracker = defaultdict(lambda: {"zero_connection_start": None})

# --- Capture Tornado IOLoop for scheduling from threads ---
MAIN_LOOP = IOLoop.current()

# --- Shutdown Scheduler ---
def _schedule_kernel_shutdown(km_instance, kernel_id):
    async def do_shutdown():
        try:
            await _original_shutdown_kernel(km_instance, kernel_id)
        except Exception:
            pass

    # Schedule on the main Tornado IOLoop
    MAIN_LOOP.add_callback(lambda: asyncio.ensure_future(do_shutdown()))

# --- Monkey-patch shutdown_kernel to block external calls ---
def controlled_shutdown_kernel(self, kernel_id, **kwargs):
    return asyncio.ensure_future(asyncio.sleep(0))  # Dummy completed awaitable

MappingKernelManager.shutdown_kernel = controlled_shutdown_kernel

# limited_factory
_original_factory = vkm.voila_kernel_manager_factory

def limited_factory(base_class, preheat_kernel, default_pool_size, page_config_hook=None):
    cls = _original_factory(base_class, preheat_kernel, default_pool_size, page_config_hook)

    # Patch the class method
    orig = cls.get_rendered_notebook
    async def limited_get_rendered_notebook(self, *args, **kwargs):
        running = self.list_kernel_ids()
        if len(running) >= MAX_KERNELS:
            raise HTTPError(503)
        return await orig(self, *args, **kwargs)

    cls.get_rendered_notebook = limited_get_rendered_notebook
    return cls

vkm.voila_kernel_manager_factory = limited_factory


# --- EXCEPTION SWALLOWING PATCH for static_url ---
_original_static_url = tornado.web.RequestHandler.static_url
def patched_static_url(self, path, include_host=None, **kwargs):
    try:
        return _original_static_url(self, path, include_host=include_host, **kwargs)
    except Exception as e:
        if "You must define the 'static_path'" in str(e):
            return f"/voila/static/{path}"
        raise
tornado.web.RequestHandler.static_url = patched_static_url

# --- Monkey-patch start_kernel to enforce MAX_KERNELS ---
def limited_start_kernel(self, **kwargs):
    global global_kernel_manager
    if global_kernel_manager is None:
        global_kernel_manager = self

    return _original_start_kernel(self, **kwargs)

MappingKernelManager.start_kernel = limited_start_kernel

# --- Custom write_error just for VoilaHandler ---
from voila.handler import VoilaHandler

def custom_voila_write_error(self, status_code, **kwargs):
    if status_code == 503:
        html = """
        <html>
          <head>
            <title>App Limit Reached</title>
            <style>
              body {
                font-family: Arial, sans-serif;
                background: #fafafa;
                color: #333;
                text-align: center;
                padding-top: 10%;
              }
              .box {
                display: inline-block;
                background: white;
                border: 1px solid #ccc;
                border-radius: 12px;
                padding: 30px 50px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
              }
              h1 {
                color: #c0392b;
              }
            </style>
          </head>
          <body>
            <div class="box">
              <h1>App Already Open</h1>
              <p>You have duplicated pages of the app opened.<br>
                 Please re-use an existing tab or close extra ones.</p>
            </div>
          </body>
        </html>
        """
        self.set_status(status_code)
        self.set_header("Content-Type", "text/html")
        self.finish(html)
    else:
        super(VoilaHandler, self).write_error(status_code, **kwargs)

VoilaHandler.write_error = custom_voila_write_error

# --- Watchdog helpers ---
def get_pool_kernel_ids(km):
    pool_ids = set()
    for tasks in km._pools.values():
        for task in tasks:
            if task.done():
                result = task.result()
                kernel_id = result.get("kernel_id")
                if kernel_id:
                    pool_ids.add(kernel_id)

    return pool_ids

def cleanup_dead_kernels():
    if global_kernel_manager is None:
        return

    pool_ids = get_pool_kernel_ids(global_kernel_manager)

    with _kernel_lock:
        now = time.time()
        running_kernels = global_kernel_manager.list_kernel_ids()

        # Remove trackers for kernels that no longer exist
        active_ids = set(running_kernels)
        dead_tracked_ids = list(kernel_connection_tracker.keys() - active_ids)
        for dead_id in dead_tracked_ids:
            del kernel_connection_tracker[dead_id]

        for kernel_id in running_kernels:
            if kernel_id in _forced_shutdowns:
                continue

            in_pool = kernel_id in pool_ids
            if in_pool:
                continue

            try:
                km_info = global_kernel_manager.kernel_model(kernel_id)
                connections = km_info["connections"]
                tracker = kernel_connection_tracker[kernel_id]

                # Only mark kernels for deletion when len(running_kernels) >= MAX_KERNELS / 2
                if connections > 0 or km_info["execution_state"] != "idle" or \
                        (len(running_kernels) < MAX_KERNELS / 2 and tracker["zero_connection_start"] is None):
                    tracker["zero_connection_start"] = None
                else:
                    if tracker["zero_connection_start"] is None:
                        tracker["zero_connection_start"] = now
                    else:
                        zero_duration = now - tracker["zero_connection_start"]
                        if zero_duration >= KERNEL_CLEANUP_TIMEOUT_SEC:
                            _forced_shutdowns.add(kernel_id)
                            _schedule_kernel_shutdown(global_kernel_manager, kernel_id)

            except Exception:
                pass

def kernel_watchdog_thread():
    while True:
        try:
            cleanup_dead_kernels()
        except Exception:
            pass
        time.sleep(2)

# Voila args
extra_args = [
    "--port=8866",
    "--no-browser",
    "--Voila.ip=0.0.0.0",
    "--base_url=/",
    "--ServerApp.log_level=ERROR",
    "--show_tracebacks=True",
    "--preheat_kernel=True",
    f"--pool_size={POOL_SIZE}",
]
sys.argv = ["voila", NOTEBOOK] + extra_args

# --- Start the watchdog thread ---
watchdog = threading.Thread(target=kernel_watchdog_thread, daemon=True)
watchdog.start()

# start Voila
from voila.app import Voila
voila_app = Voila()
voila_app.initialize()
print("VOILA: http://localhost:8866")
logging.disable(logging.CRITICAL)
voila_app.start()