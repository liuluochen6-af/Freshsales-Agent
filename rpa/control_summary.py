from __future__ import annotations

import argparse
import json

from pywinauto import Desktop


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("handle", type=int)
    parser.add_argument(
        "--types",
        default="Button,Edit,List,ListItem,Text,MenuItem",
        help="Comma-separated UI Automation control types",
    )
    parser.add_argument("--include-empty", action="store_true")
    args = parser.parse_args()
    allowed = {item.strip() for item in args.types.split(",") if item.strip()}
    window = Desktop(backend="uia").window(handle=args.handle)
    output = []
    for item in window.descendants():
        info = item.element_info
        name = item.window_text()
        automation_id = info.automation_id
        if info.control_type not in allowed or (not args.include_empty and not (name or automation_id)):
            continue
        rect = item.rectangle()
        output.append(
            {
                "type": info.control_type,
                "name": name,
                "automation_id": automation_id,
                "enabled": item.is_enabled(),
                "visible": item.is_visible(),
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            }
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
