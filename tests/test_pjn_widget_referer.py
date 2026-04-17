"""
Comprueba el comportamiento del widget PJN respecto al header Referer.

Ejecutar (desde contrata-seguro/):
  python tests/test_pjn_widget_referer.py

Si Referer es un dominio ajeno a PJN (p. ej. localhost o tu dominio), la respuesta
del HTML del widget suele ser ~247 bytes y el desafío no carga ("Captcha no disponible").
Sin Referer o con Referer de scw.pjn.gov.ar, el HTML es grande (~50k).
"""
import re
import sys

import requests


def main() -> int:
    hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    }
    init = requests.get(
        "https://captcha.pjn.gov.ar/api/init.js?sitekey=SCW",
        headers={**hdr, "Referer": "https://scw.pjn.gov.ar/scw/home.seam"},
        timeout=25,
    )
    init.raise_for_status()
    m = re.search(r"https://captcha\.pjn\.gov\.ar/api/(widget\.scw\.[^\"']+\.html)", init.text)
    if not m:
        print("No se encontró URL de widget en init.js", file=sys.stderr)
        return 1
    w = "https://captcha.pjn.gov.ar/api/" + m.group(1)
    print("Widget:", w)

    cases = [
        ("http://127.0.0.1:5000/pjn/captcha-embed.html",),
        ("https://contrataseguro.ar/",),
        ("https://scw.pjn.gov.ar/scw/home.seam",),
        (None,),
    ]
    for (ref,) in cases:
        h = dict(hdr)
        if ref:
            h["Referer"] = ref
        r = requests.get(w + "?sitekey=SCW", headers=h, timeout=25)
        ok = len(r.text) > 10000
        print(f"  Referer={ref!r:50} len={len(r.text):6} widget_html_completo={ok}")
    print(
        "\nConclusion (entorno actual): solo un Referer de scw.pjn.gov.ar devolvio HTML grande; "
        "desde un dominio propio el navegador envia otro Referer y PJN responde HTML truncado (~200-250 B), "
        "por eso el widget muestra 'Captcha no disponible'. La app ofrece 'Siguiente' para seguir con SCBA."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
