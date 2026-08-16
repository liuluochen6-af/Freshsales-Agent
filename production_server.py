from __future__ import annotations

import os

from waitress import serve

from app import app, init_db


def main() -> None:
    host = os.environ.get("AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_PORT", "8015"))
    threads = max(4, min(64, int(os.environ.get("AGENT_THREADS", "12"))))
    if host not in {"127.0.0.1", "localhost"}:
        if not os.environ.get("AGENT_BOOTSTRAP_TOKEN", "").strip():
            raise RuntimeError("远程部署必须设置AGENT_BOOTSTRAP_TOKEN")
        if not os.environ.get("AGENT_ADMIN_TOKEN", "").strip():
            raise RuntimeError("远程部署必须设置AGENT_ADMIN_TOKEN")
    init_db()
    print(f"中央控制台正在监听 http://{host}:{port}，工作线程={threads}", flush=True)
    serve(app, host=host, port=port, threads=threads, channel_timeout=60)


if __name__ == "__main__":
    main()
