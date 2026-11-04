import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
import pytest_asyncio

# NOTE on approach: these tests spin up a real `uvicorn` process and hit it
# over a real TCP socket, rather than using httpx's in-process ASGITransport.
# Reason: ASGITransport awaits a route handler's entire call — including its
# attached BackgroundTasks — before returning control to the test. That
# makes it impossible to honestly verify this service's core contract
# (respond immediately, process afterwards): a test against ASGITransport
# would see the transaction already PROCESSED the instant the POST call
# returns, even though that's not what happens with a real client talking
# to a real socket. A subprocess server reproduces the real timing.


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server_base_url():
    port = _free_port()
    db_path = os.path.abspath("test_session.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env["PROCESSING_DELAY_SECONDS"] = "0.5"
    env["RECOVER_STUCK_ON_STARTUP"] = "false"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 15
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(base_url + "/", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.2)

    if not ready:
        proc.terminate()
        out, _ = proc.communicate(timeout=5)
        raise RuntimeError(f"server did not start in time. Output:\n{out}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture
async def client(server_base_url):
    async with httpx.AsyncClient(base_url=server_base_url, timeout=10.0) as ac:
        yield ac
