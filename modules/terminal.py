"""Web-Terminal: echte Bash-Shell über WebSocket + PTY."""
import os
import asyncio
import fcntl
import termios
import struct
import signal

try:
    import ptyprocess
    HAS_PTY = True
except ImportError:
    HAS_PTY = False


class TerminalSession:
    """Eine PTY-Shell-Session, an einen WebSocket gebunden."""

    def __init__(self, shell="/bin/bash", cwd="/root", argv=None):
        self.shell = shell
        self.cwd = cwd if os.path.isdir(cwd) else "/"
        self.argv = argv
        self.proc = None

    def start(self):
        if not HAS_PTY:
            raise RuntimeError("ptyprocess nicht installiert")
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        self.proc = ptyprocess.PtyProcess.spawn(
            self.argv or [self.shell], cwd=self.cwd, env=env, dimensions=(24, 80)
        )

    def write(self, data: str):
        if self.proc and self.proc.isalive():
            self.proc.write(data.encode())

    def read(self, size=1024):
        try:
            return self.proc.read(size).decode(errors="replace")
        except EOFError:
            return None

    def resize(self, rows: int, cols: int):
        if self.proc and self.proc.isalive():
            try:
                fcntl.ioctl(self.proc.fd, termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0))
            except OSError:
                pass

    def alive(self):
        return self.proc is not None and self.proc.isalive()

    def kill(self):
        if self.proc and self.proc.isalive():
            try:
                self.proc.kill(signal.SIGTERM)
            except Exception:
                pass


async def pty_to_ws(session: TerminalSession, websocket):
    """Liest fortlaufend aus der PTY und sendet an den WebSocket."""
    loop = asyncio.get_event_loop()
    while session.alive():
        data = await loop.run_in_executor(None, session.read, 1024)
        if data is None:
            break
        try:
            await websocket.send_text(data)
        except Exception:
            break
