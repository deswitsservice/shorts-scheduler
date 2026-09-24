"""Owned, headed Chrome processes. CDP never leaves the server's loopback interface."""
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request


class BrowserWorker:
    def __init__(self, profile, capacity, on_ready):
        self.profile = Path(profile)
        self.capacity = capacity
        self.on_ready = on_ready
        self.lock = threading.RLock()
        self.process = self.display = self.profile_lock = None
        self.endpoint = None
        self.port = None
        self.slot = False
        self.last_used = time.monotonic()

    def running(self):
        process = self.process
        return self.endpoint is not None and process is not None and process.poll() is None

    def touch(self):
        self.last_used = time.monotonic()

    def start(self, *args, **kwargs):
        with self.lock:
            self.touch()
            if self.running():
                return
            self.stop()
            if not self.capacity.acquire(blocking=False):
                raise RuntimeError('All browser slots are in use. Please try again shortly.')
            self.slot = True
            try:
                self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.profile_lock = open(self.profile.parent / 'browser.lock', 'a')
                fcntl.flock(self.profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                marker = self.profile / 'DevToolsActivePort'
                # A previous unclean server shutdown may leave Chrome alive. Never adopt it
                # by port number alone, or delete its SingletonLock/profile files.
                if marker.exists():
                    try:
                        old_port = int(marker.read_text().splitlines()[0])
                        with urllib.request.urlopen(f'http://127.0.0.1:{old_port}/json/version', timeout=1):
                            raise RuntimeError('A previous browser is still running. Ask the administrator to stop it before reconnecting.')
                    except (OSError, ValueError, IndexError):
                        pass
                    marker.unlink(missing_ok=True)
                chrome = os.environ.get('SHORTS_CHROME_BINARY')
                if not chrome:
                    chrome = ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' if sys.platform == 'darwin'
                              else shutil.which('google-chrome') or shutil.which('chromium'))
                if not chrome or not Path(chrome).is_file():
                    raise RuntimeError('Chrome is not installed on the server.')
                env = os.environ.copy()
                if sys.platform.startswith('linux'):
                    xvfb = shutil.which('Xvfb')
                    if not xvfb:
                        raise RuntimeError('Install Xvfb on the server to run regular Chrome.')
                    read_fd, write_fd = os.pipe()
                    try:
                        self.display = subprocess.Popen([xvfb, '-displayfd', str(write_fd), '-screen', '0',
                                                         '1280x900x24', '-nolisten', 'tcp'],
                                                        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        os.close(write_fd)
                        write_fd = None
                        if not select.select([read_fd], [], [], 10)[0]:
                            raise RuntimeError('Could not start the private browser display.')
                        number = os.read(read_fd, 32).decode().strip()
                        if not number.isdigit():
                            raise RuntimeError('Could not start the private browser display.')
                        env['DISPLAY'] = ':' + number
                    finally:
                        os.close(read_fd)
                        if write_fd is not None:
                            os.close(write_fd)
                flags = [f'--user-data-dir={self.profile}', '--remote-debugging-address=127.0.0.1',
                         '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check',
                         '--disable-session-crashed-bubble', '--window-size=1200,850',
                         '--window-position=-2400,-2400', 'about:blank']
                command = [chrome, *flags]
                if sys.platform == 'darwin':
                    # -W keeps a process handle for the lifetime of this separate app instance.
                    command = ['open', '-n', '-g', '-j', '-W', '-a', str(Path(chrome).parents[2]), '--args', *flags]
                self.process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        break
                    if marker.exists():
                        try:
                            lines = marker.read_text().splitlines()
                            port = int(lines[0])
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=1) as response:
                                data = json.load(response)
                            if data['webSocketDebuggerUrl'].endswith(lines[1]):
                                self.port = port
                                self.endpoint = f'http://127.0.0.1:{port}'
                                self.on_ready(port)
                                return
                        except (OSError, ValueError, KeyError, IndexError):
                            pass
                    time.sleep(.15)
                raise RuntimeError('The private browser did not start. Check the server browser setup.')
            except BaseException:
                self.stop()
                raise

    def stop(self):
        with self.lock:
            if self.endpoint and self.process and self.process.poll() is None:
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        browser = p.chromium.connect_over_cdp(self.endpoint, no_defaults=True, timeout=3000)
                        session = browser.new_browser_cdp_session()
                        session.send('Browser.close')
                except Exception:
                    pass
            for process in (self.process, self.display):
                if process and process.poll() is None:
                    try:
                        process.wait(timeout=5) if process is self.process and self.endpoint else process.terminate()
                    except subprocess.TimeoutExpired:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            self.process = self.display = None
            self.endpoint = self.port = None
            if self.profile_lock:
                self.profile_lock.close()
                self.profile_lock = None
            if self.slot:
                self.capacity.release()
                self.slot = False
