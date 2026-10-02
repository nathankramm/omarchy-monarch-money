#!/usr/bin/env python3
"""A virtual pointer for gesture tests on Hyprland: real pointer motion and clicks through
zwlr_virtual_pointer_v1, spoken directly on the Wayland socket (stdlib only, no root, no
uinput, nothing installed).

Why it exists: warping the cursor with a compositor dispatcher raises no hover on a layer
surface, so a bar widget's hover card could only be tested by hand. A virtual pointer device
goes through the compositor's input path, so enter/leave/motion reach the surfaces like a
mouse. The protocol is wlr-virtual-pointer-unstable-v1 (zwlr_virtual_pointer_manager_v1).

    vpointer.py OP [OP ...]
      move:X,Y            jump to X,Y (logical pixels on the focused monitor)
      glide:X,Y[,MS]      move there in a straight line over MS milliseconds (default 200)
      sleep:S             wait S seconds (the device stays alive)
      click[:left|right|middle]
      where               print `hyprctl cursorpos`

Coordinates are LOGICAL pixels of the monitor (the extent sent is the monitor's logical
size, read from `hyprctl monitors -j`). It moves the real cursor: do not run it while
someone is using the pointer.
"""
import json
import os
import socket
import struct
import subprocess
import sys
import time

BTN = {"left": 0x110, "right": 0x111, "middle": 0x112}


class Wl:
    def __init__(self):
        path = os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.next_id = 2
        self.buf = b""

    def new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    def send(self, obj, opcode, payload=b""):
        size = 8 + len(payload)
        self.sock.sendall(struct.pack("<II", obj, (size << 16) | opcode) + payload)

    @staticmethod
    def string(s):
        raw = s.encode() + b"\0"
        pad = (-len(raw)) % 4
        return struct.pack("<I", len(raw)) + raw + b"\0" * pad

    def events(self, until_callback):
        """Yield (object, opcode, payload) until wl_callback `until_callback` fires."""
        while True:
            while len(self.buf) < 8:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise RuntimeError("compositor closed the connection")
                self.buf += chunk
            obj, word = struct.unpack("<II", self.buf[:8])
            size, opcode = word >> 16, word & 0xFFFF
            while len(self.buf) < size:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise RuntimeError("compositor closed the connection")
                self.buf += chunk
            payload, self.buf = self.buf[8:size], self.buf[size:]
            if obj == 1 and opcode == 0:      # wl_display.error
                oid, code = struct.unpack("<II", payload[:8])
                raise RuntimeError("wl_display.error object=%d code=%d" % (oid, code))
            if obj == until_callback:
                return
            yield obj, opcode, payload

    def roundtrip(self):
        cb = self.new_id()
        self.send(1, 0, struct.pack("<I", cb))            # wl_display.sync
        return list(self.events(cb))


def monitor_extent():
    mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout)
    m = next((x for x in mons if x.get("focused")), mons[0])
    return int(round(m["width"] / m["scale"])), int(round(m["height"] / m["scale"]))


def main(ops):
    if not ops:
        sys.stderr.write(__doc__)
        return 2
    wl = Wl()
    registry = wl.new_id()
    wl.send(1, 1, struct.pack("<I", registry))            # wl_display.get_registry
    manager_name = manager_version = None
    for obj, opcode, payload in wl.roundtrip():
        if obj == registry and opcode == 0:               # wl_registry.global
            name, slen = struct.unpack("<II", payload[:8])
            iface = payload[8:8 + slen - 1].decode()
            version = struct.unpack("<I", payload[8 + ((slen + 3) & ~3):][:4])[0]
            if iface == "zwlr_virtual_pointer_manager_v1":
                manager_name, manager_version = name, version
    if manager_name is None:
        sys.stderr.write("compositor does not offer zwlr_virtual_pointer_manager_v1\n")
        return 1
    manager = wl.new_id()
    wl.send(registry, 0, struct.pack("<I", manager_name) + Wl.string("zwlr_virtual_pointer_manager_v1")
            + struct.pack("<II", 1, manager))             # wl_registry.bind, version 1
    pointer = wl.new_id()
    wl.send(manager, 0, struct.pack("<II", 0, pointer))   # create_virtual_pointer(seat=null)
    wl.roundtrip()

    w, h = monitor_extent()
    pos = [None, None]

    def now_ms():
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    def move(x, y):
        x = max(0, min(w - 1, int(round(x))))
        y = max(0, min(h - 1, int(round(y))))
        wl.send(pointer, 1, struct.pack("<IIIII", now_ms(), x, y, w, h))   # motion_absolute
        wl.send(pointer, 4)                                                  # frame
        pos[0], pos[1] = x, y

    for op in ops:
        name, _, arg = op.partition(":")
        if name == "move":
            x, y = (float(v) for v in arg.split(","))
            move(x, y)
            wl.roundtrip()
        elif name == "glide":
            parts = [float(v) for v in arg.split(",")]
            x, y = parts[0], parts[1]
            ms = parts[2] if len(parts) > 2 else 200.0
            x0, y0 = (pos[0], pos[1]) if pos[0] is not None else (x, y)
            steps = max(2, int(ms / 8))
            for i in range(1, steps + 1):
                t = i / steps
                move(x0 + (x - x0) * t, y0 + (y - y0) * t)
                time.sleep(ms / 1000.0 / steps)
            wl.roundtrip()
        elif name == "sleep":
            wl.roundtrip()
            time.sleep(float(arg))
        elif name == "click":
            b = BTN[arg or "left"]
            wl.send(pointer, 2, struct.pack("<III", now_ms(), b, 1))
            wl.send(pointer, 4)
            wl.roundtrip()
            time.sleep(0.04)
            wl.send(pointer, 2, struct.pack("<III", now_ms(), b, 0))
            wl.send(pointer, 4)
            wl.roundtrip()
        elif name == "where":
            wl.roundtrip()
            sys.stdout.write(subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True).stdout)
        else:
            sys.stderr.write("unknown op %r\n" % op)
            return 2
    wl.roundtrip()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
