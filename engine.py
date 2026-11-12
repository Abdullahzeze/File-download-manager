"""Download engine (no GUI): segmented, resumable downloads using only the stdlib."""
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

UA = "Mozilla/5.0 (Macintosh) PyDownloadManager/1.0"


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward cookies/auth to a different host after a redirect."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            for h in ("Cookie", "Authorization"):
                new.headers.pop(h, None)
                new.unredirected_hdrs.pop(h, None)
        return new


_opener = urllib.request.build_opener(_SafeRedirect)
CHUNK = 64 * 1024
MAX_ACTIVE = 3          # downloads running at once
CONNECTIONS = 4         # parallel segments per download (when server supports Range)
MIN_SPLIT = 1 << 20     # don't split files smaller than 1 MB


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(s):
    if s is None or s < 0 or s > 86400 * 7:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


@dataclass
class Segment:
    start: int
    end: int            # inclusive; -1 = unknown length
    pos: int = 0
    finished: bool = False

    @property
    def done(self):
        return self.finished or (self.end >= 0 and self.pos > self.end)


class Download:
    def __init__(self, url, dest_dir, headers=None):
        self.url, self.dest_dir = url, dest_dir
        self.extra = dict(headers or {})   # e.g. Cookie, Referer, User-Agent from the browser
        self.name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
        self.path = self.part = None
        self.total = 0
        self.resumable = False
        self.segments = []
        self.status = "Queued"
        self.intent = "Paused"
        self.error = ""
        self.stop = threading.Event()
        self.speed = 0.0
        self._last = (time.time(), 0)

    # ---------- public controls ----------
    @property
    def downloaded(self):
        return sum(s.pos - s.start for s in self.segments)

    @property
    def active(self):
        return self.status in ("Connecting", "Downloading", "Pausing")

    def start(self):
        if self.status not in ("Queued", "Paused", "Error"):
            return
        self.stop.clear()
        self.error = ""
        self.status = "Connecting"
        threading.Thread(target=self._run, daemon=True).start()

    def pause(self):
        if self.status in ("Connecting", "Downloading"):
            self.intent = "Paused"
            self.status = "Pausing"
            self.stop.set()
        elif self.status == "Queued":
            self.status = "Paused"

    def cancel(self):
        if self.active:
            self.intent = "Cancelled"
            self.status = "Pausing"
            self.stop.set()
        elif self.status != "Done":
            self._cleanup()
            self.status = "Cancelled"

    def update_speed(self):
        now, cur = time.time(), self.downloaded
        t0, b0 = self._last
        if now - t0 >= 0.5:
            inst = max(0, cur - b0) / (now - t0)
            self.speed = inst if self.speed == 0 else 0.6 * self.speed + 0.4 * inst
            self._last = (now, cur)
        if self.status != "Downloading":
            self.speed = 0

    # ---------- internals ----------
    def _cleanup(self):
        if self.part and os.path.exists(self.part):
            try:
                os.remove(self.part)
            except OSError:
                pass

    def _base_headers(self):
        return {"User-Agent": UA, **self.extra}

    def _probe(self):
        req = urllib.request.Request(self.url, headers={**self._base_headers(), "Range": "bytes=0-0"})
        with _opener.open(req, timeout=20) as r:
            if urllib.parse.urlparse(r.geturl()).netloc != urllib.parse.urlparse(self.url).netloc:
                self.extra = {k: v for k, v in self.extra.items() if k.lower() not in ("cookie", "authorization")}
            self.url = r.geturl()
            cr = r.headers.get("Content-Range")
            if r.status == 206 and cr and "/" in cr:
                try:
                    self.total = int(cr.rsplit("/", 1)[1])
                    self.resumable = True
                except ValueError:
                    pass
            if not self.resumable:
                self.total = int(r.headers.get("Content-Length") or 0)
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
            if m:
                self.name = urllib.parse.unquote(m.group(1))
            else:
                self.name = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(self.url).path)) or self.name
            self.name = os.path.basename(self.name) or "download"

    def _plan(self):
        base, ext = os.path.splitext(self.name)
        path, i = os.path.join(self.dest_dir, self.name), 1
        while os.path.exists(path) or os.path.exists(path + ".part"):
            path = os.path.join(self.dest_dir, f"{base} ({i}){ext}")
            i += 1
        self.path, self.part = path, path + ".part"
        self.name = os.path.basename(path)
        with open(self.part, "wb") as f:
            if self.total:
                f.truncate(self.total)
        if self.resumable and self.total > MIN_SPLIT:
            n = CONNECTIONS
            size = self.total // n
            self.segments = [
                Segment(i * size, self.total - 1 if i == n - 1 else (i + 1) * size - 1, i * size)
                for i in range(n)
            ]
        else:
            end = self.total - 1 if self.total else -1
            self.segments = [Segment(0, end, 0)]

    def _fail(self, msg):
        self.error = msg or "Connection lost"
        self.stop.set()

    def _worker(self, seg):
        retries = 0
        while not self.stop.is_set() and not seg.done:
            before, err = seg.pos, None
            try:
                headers = self._base_headers()
                if self.resumable:
                    headers["Range"] = f"bytes={seg.pos}-{seg.end if seg.end >= 0 else ''}"
                req = urllib.request.Request(self.url, headers=headers)
                with _opener.open(req, timeout=30) as r, open(self.part, "r+b") as f:
                    f.seek(seg.pos)
                    while not self.stop.is_set():
                        want = CHUNK if seg.end < 0 else min(CHUNK, seg.end + 1 - seg.pos)
                        if want <= 0:
                            break
                        buf = r.read(want)
                        if not buf:
                            if seg.end < 0:
                                seg.finished = True
                            break
                        f.write(buf)
                        seg.pos += len(buf)
            except Exception as e:  # network error, HTTP error, etc.
                err = e
            if self.stop.is_set() or seg.done:
                return
            if not self.resumable:
                return self._fail("Connection lost (server doesn't support resume)")
            retries = 0 if seg.pos > before else retries + 1
            if retries > 3:
                return self._fail(str(err) if err else "")
            self.stop.wait(1)

    def _run(self):
        try:
            if not self.segments:
                self._probe()
                if self.stop.is_set():
                    self.status = self.intent
                    return
                self._plan()
            elif not self.resumable:
                for s in self.segments:
                    s.pos, s.finished = s.start, False
                with open(self.part, "wb") as f:
                    if self.total:
                        f.truncate(self.total)
            self.status = "Downloading"
            threads = [threading.Thread(target=self._worker, args=(s,), daemon=True)
                       for s in self.segments if not s.done]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            if self.error:
                self.status = "Error"
            elif self.stop.is_set():
                if self.intent == "Cancelled":
                    self._cleanup()
                self.status = self.intent
            else:
                if not self.total:
                    self.total = self.downloaded
                os.replace(self.part, self.path)
                self.status = "Done"
        except Exception as e:
            self.error = str(e)
            self.status = "Error"
