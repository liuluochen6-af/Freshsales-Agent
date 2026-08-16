from __future__ import annotations

import argparse
import json

from pywinauto import Desktop


def inspect(handle: int) -> dict:
    window = Desktop(backend="uia").window(handle=handle)
    controls = []
    for item in window.descendants():
        info = item.element_info
        rect = item.rectangle()
        controls.append(
            {
                "control_type": info.control_type,
                "name": item.window_text(),
                "automation_id": info.automation_id,
                "enabled": item.is_enabled(),
                "visible": item.is_visible(),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            }
        )
    return {
        "handle": handle,
        "title": window.window_text(),
        "rect": [
            window.rectangle().left,
            window.rectangle().top,
            window.rectangle().right,
            window.rectangle().bottom,
        ],
        "controls": controls,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("handles", type=int, nargs="*")
    parser.add_argument("--list-windows", action="store_true")
    args = parser.parse_args()
    if args.list_windows:
        windows = []
        for item in Desktop(backend="uia").windows():
            rect = item.rectangle()
            windows.append(
                {
                    "handle": item.handle,
                    "title": item.window_text(),
                    "control_type": item.element_info.control_type,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                }
            )
        print(json.dumps(windows, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    handles = args.handles
    if not handles:
        handles = [
            item.handle
            for item in Desktop(backend="uia").windows()
            if item.window_text() in ("WeChat", "Weixin")
        ]
    results = []
    for handle in handles:
        try:
            results.append(inspect(handle))
        except Exception as exc:
            results.append({"handle": handle, "error": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))
