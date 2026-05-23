from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable


class _ProxyHandler(BaseHTTPRequestHandler):
    """Serves player HTML and streams the growing WebM file."""

    work_dir: str = ""
    _ffmpeg_proc: subprocess.Popen | None = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/player"):
            self._serve_player()
        elif self.path.startswith("/stream"):
            self._serve_stream()
        else:
            self.send_error(404)

    def _serve_player(self):
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<style>"
            "*{margin:0;padding:0}"
            "body{background:#000;overflow:hidden}"
            "video{width:100vw;height:100vh;object-fit:contain}"
            "#s{position:fixed;bottom:8px;left:8px;background:rgba(0,0,0,.7);color:#fff;padding:4px 10px;border-radius:4px;font-size:12px;z-index:10}"
            "#x{position:fixed;top:8px;right:8px;background:rgba(0,0,0,.7);color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:13px;z-index:10}"
            "</style></head><body>"
            "<video id=v autoplay muted playsinline></video>"
            "<div id=s>Loading...</div>"
            "<div id=x onclick=\"document.title='DMM_CLOSE'\">&#x2715; close</div>"
            "<script>"
            "(function(){"
            "var v=document.getElementById('v'),s=document.getElementById('s');"
            "function log(m){s.textContent=m;document.title='DMM:'+m}"
            "log('waiting for stream...');"
            "function load(){v.src='/stream.webm?t='+Date.now();v.load()}"
            "setTimeout(function(){log('connecting...');load()},1500);"
            "v.addEventListener('loadedmetadata',function(){log('meta '+v.videoWidth+'x'+v.videoHeight);v.play().catch(function(e){log('blocked:'+e.name)})});"
            "v.addEventListener('playing',function(){log('playing')});"
            "v.addEventListener('waiting',function(){log('buffering')});"
            "v.addEventListener('stalled',function(){log('stalled')});"
            "v.addEventListener('error',function(){var c=v.error?v.error.code:'?';log('err:'+c)});"
            "var r=0;v.addEventListener('error',function(){if(r<30){r++;log('retry '+r+'...');setTimeout(load,1000)}})"
            "})();"
            "</script></body></html>"
        )
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_stream(self):
        ts_path = os.path.join(self.work_dir, "stream.webm")

        # Wait for file to appear (FFmpeg may not have created it yet)
        deadline = time.time() + 15
        while not os.path.exists(ts_path) and time.time() < deadline:
            time.sleep(0.2)

        if not os.path.exists(ts_path):
            self.send_error(503, "Stream not ready")
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/webm")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            with open(ts_path, "rb") as f:
                idle = 0
                while True:
                    chunk = f.read(65536)
                    if chunk:
                        self.wfile.write(chunk)
                        idle = 0
                    else:
                        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is not None:
                            break
                        idle += 1
                        if idle > 200:
                            break
                        time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass


class FFmpegStreamProxy:
    """Transcode a live stream via FFmpeg and serve as growing WebM file."""

    def __init__(self, on_status: Callable[[str], None] | None = None) -> None:
        self._on_status = on_status or (lambda _: None)
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._work_dir: str = ""
        self._running = False
        self._log_file = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def local_url(self) -> str:
        return "http://127.0.0.1:18923/player.html"

    def find_ffmpeg(self) -> str | None:
        return shutil.which("ffmpeg")

    def start(self, stream_url: str, cookies: str = "") -> bool:
        ffmpeg = self.find_ffmpeg()
        if not ffmpeg:
            self._on_status("FFmpeg 未安装，请先安装 FFmpeg")
            return False

        self.stop()

        self._work_dir = tempfile.mkdtemp(prefix="dmm_stream_")
        self._running = True

        webm_path = os.path.join(self._work_dir, "stream.webm")

        # Build FFmpeg command — write WebM to file (natively streamable in Chromium)
        cmd = [
            ffmpeg,
            "-hide_banner", "-loglevel", "warning",
        ]
        if cookies:
            cmd += ["-headers", f"Cookie: {cookies}\r\nReferer: https://live.douyin.com/\r\n"]
        cmd += [
            "-user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "-i", stream_url,
            "-c:v", "libvpx",
            "-speed", "6",
            "-quality", "realtime",
            "-threads", "4",
            "-s", "360x640",
            "-b:v", "600k",
            "-minrate", "300k",
            "-maxrate", "800k",
            "-bufsize", "1200k",
            "-g", "25",
            "-c:a", "libopus",
            "-b:a", "96k",
            "-f", "webm",
            "-cluster_time_limit", "500",
            webm_path,
        ]

        # Start HTTP server first
        work_dir = self._work_dir
        ready = threading.Event()

        def _start_http():
            _ProxyHandler.work_dir = work_dir
            try:
                self._http_server = HTTPServer(("127.0.0.1", 18923), _ProxyHandler)
            except OSError as exc:
                self._on_status(f"HTTP 服务启动失败: {exc}")
                ready.set()
                return
            ready.set()
            self._http_server.serve_forever()

        self._http_thread = threading.Thread(target=_start_http, daemon=True)
        self._http_thread.start()
        ready.wait(timeout=5)

        if not self._http_server:
            self.stop()
            return False

        # Start FFmpeg
        log_path = os.path.join(self._work_dir, "ffmpeg.log")
        try:
            self._log_file = open(log_path, "w")
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self._on_status(f"FFmpeg 启动失败: {exc}")
            self.stop()
            return False

        _ProxyHandler._ffmpeg_proc = self._ffmpeg_proc

        # Read stderr in background
        proc = self._ffmpeg_proc
        status_cb = self._on_status
        log_file = self._log_file

        def _read_stderr():
            try:
                for line in proc.stderr:
                    msg = line.decode(errors="replace").strip()
                    if msg:
                        log_file.write(msg + "\n")
                        log_file.flush()
                        try:
                            status_cb(f"FFmpeg: {msg}")
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                rc = proc.wait()
                log_file.write(f"[EXIT] code={rc}\n")
                log_file.flush()
                try:
                    status_cb(f"FFmpeg 退出，返回码={rc}")
                except Exception:
                    pass
            except Exception:
                pass

        threading.Thread(target=_read_stderr, daemon=True).start()

        # Check if FFmpeg is alive after 3 seconds
        def _check_alive():
            time.sleep(3)
            if proc.poll() is not None:
                try:
                    status_cb(f"FFmpeg 已退出，返回码={proc.returncode}，日志: {log_path}")
                except Exception:
                    pass

        threading.Thread(target=_check_alive, daemon=True).start()

        return True

    def stop(self) -> None:
        self._running = False

        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=5)
            except Exception:
                self._ffmpeg_proc.kill()
            self._ffmpeg_proc = None

        if self._http_server:
            self._http_server.shutdown()
            self._http_server = None

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        if self._work_dir:
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = ""
