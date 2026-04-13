"""
Arranque de Gunicorn leyendo PORT desde el entorno (sin $PORT en la línea de comando).
Evita PaaS que no expanden variables o validan el comando antes del shell.
"""
import os
import sys


def _port() -> str:
    raw = (os.environ.get("PORT") or "8080").strip()
    return raw if raw.isdigit() else "8080"


def main() -> None:
    bind = f"0.0.0.0:{_port()}"
    argv = [
        sys.executable,
        "-m",
        "gunicorn",
        "api:app",
        "--bind",
        bind,
        "--timeout",
        "300",
        "--workers",
        "1",
    ]
    os.execvp(sys.executable, argv)


if __name__ == "__main__":
    main()
