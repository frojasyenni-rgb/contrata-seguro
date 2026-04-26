"""Comprueba si www sirve rutas PJN (mismo origen que el front)."""
import re
import ssl
import sys
import urllib.request

ctx = ssl._create_unverified_context()
base = "https://www.contrataseguro.ar"
for path in ("/", "/pjn/captcha-init.js", "/pjn/captcha-embed.html", "/health", "/api/info"):
    url = base + path
    try:
        r = urllib.request.urlopen(url, context=ctx, timeout=25)
        b = r.read()
        print(path, r.status, len(b))
    except Exception as e:
        print(path, "ERR", e)


if __name__ == "__main__":
    sys.exit(0)
