import threading
import time
import ipywidgets as widgets
from IPython.display import display, HTML

KERNEL_DEAD_TIMEOUT_SEC = 10
CHECK_INTERVAL_MS = 1000

# A thread-safe way to stop the heartbeat if needed (optional)
_stop_heartbeat = threading.Event()

def update_heartbeat(heartbeat_widget):
    counter = 0
    while not _stop_heartbeat.is_set():
        heartbeat_widget.value = f'<div id="kernel-heartbeat-value" style="display: none;">{counter}</div>'
        counter += 1
        time.sleep(1)

def setup_heartbeat_checker():
    # 1. Create the hidden widget for heartbeat
    heartbeat_widget = widgets.HTML(
        value='<div id="kernel-heartbeat-value" style="display: none;">-1</div>'
    )
    display(heartbeat_widget)

    # 2. Start the background thread to update the widget
    heartbeat_thread = threading.Thread(target=update_heartbeat, args=(heartbeat_widget,))
    heartbeat_thread.daemon = True
    heartbeat_thread.start()

    # 3. Inject JavaScript monitor and error banner
    error_message_div_id = "kernel-disconnected-error-heartbeat"

    MAX_FAILED_CHECKS = KERNEL_DEAD_TIMEOUT_SEC * 1000 / CHECK_INTERVAL_MS
    js_and_html_to_inject = f"""
    <style>
        #{error_message_div_id} {{
            display: none; position: fixed; top: 0; left: 0; width: 100%;
            padding: 15px; z-index: 10000; color: #a94442;
            background-color: #f2dede; border-bottom: 1px solid #ebccd1;
            font-family: sans-serif; text-align: center; box-sizing: border-box;
        }}
    </style>

    <div id="{error_message_div_id}">
        <b>Connection Error!</b> The connection to the application server has been lost. Please check your internet connection and refresh the page to reconnect.
    </div>

    <script type="text/javascript">
    (function() {{

        const HEARTBEAT_VALUE_ID = 'kernel-heartbeat-value';
        const ERROR_BANNER_ID = '{error_message_div_id}';
        const MAX_FAILED_CHECKS = {MAX_FAILED_CHECKS};

        let lastSeenValue = -1;
        let failedChecks = 0;

        const monitorInterval = setInterval(() => {{
            const heartbeatDiv = document.getElementById(HEARTBEAT_VALUE_ID);
            const errorBanner = document.getElementById(ERROR_BANNER_ID);

            if (!heartbeatDiv || !errorBanner) {{
                clearInterval(monitorInterval);
                return;
            }}

            const currentValue = parseInt(heartbeatDiv.innerText, 10);

            if (currentValue !== lastSeenValue) {{
                failedChecks = 0; 
                lastSeenValue = currentValue;
                errorBanner.style.display = 'none';
            }} else {{
                failedChecks++;
            }}

            if (failedChecks >= MAX_FAILED_CHECKS) {{
                errorBanner.style.display = 'block';
            }}

        }}, {CHECK_INTERVAL_MS});
    }})();
    </script>
    """

    display(HTML(js_and_html_to_inject))
