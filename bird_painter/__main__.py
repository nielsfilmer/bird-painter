"""Run the wall: python -m bird_painter [port]"""

import sys

import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8321
    uvicorn.run("bird_painter.web:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
