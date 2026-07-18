"""Run the wall: python -m bird_painter [port]

Port resolution (first wins): CLI arg → BP_PORT env → default 8537.
8537 is an uncommon high port chosen to avoid colliding with other local
dev servers; override it if it's taken.
"""

import sys

import uvicorn

from .config import load_config


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else load_config().port
    uvicorn.run("bird_painter.web:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
