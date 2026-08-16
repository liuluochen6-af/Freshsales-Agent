from __future__ import annotations

import ctypes
import argparse
import json
from ctypes import wintypes


user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def text_for(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def class_for(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def enumerate_windows(*, all_visible: bool = False) -> list[dict]:
    results: list[dict] = []

    @EnumWindowsProc
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
            if not process_handle:
                return True
            path_buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path_buffer))
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                process_handle, 0, path_buffer, ctypes.byref(size)
            )
            ctypes.windll.kernel32.CloseHandle(process_handle)
            is_weixin = bool(ok and path_buffer.value.lower().endswith("\\weixin.exe"))
            if not is_weixin and not all_visible:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append(
                {
                    "handle": int(hwnd),
                    "pid": pid.value,
                    "process_path": path_buffer.value if ok else "",
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "enabled": bool(user32.IsWindowEnabled(hwnd)),
                    "class_name": class_for(hwnd),
                    "title": text_for(hwnd),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                }
            )
        except OSError:
            pass
        return True

    user32.EnumWindows(callback, 0)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-visible", action="store_true")
    args = parser.parse_args()
    windows = enumerate_windows(all_visible=args.all_visible)
    if args.all_visible:
        windows = [item for item in windows if item["visible"] and item["rect"][2] > item["rect"][0]]
    print(json.dumps(windows, ensure_ascii=False, indent=2))
