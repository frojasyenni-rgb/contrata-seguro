"""
Pruebas del lookup CuitOnline: HTML sintético (sin red) e integración opcional con red.
Ejecutar: python tests/test_cuitonline_lookup.py
Integración: RUN_CUITONLINE_INTEGRATION=1 python tests/test_cuitonline_lookup.py
"""
from __future__ import annotations

import os
import random
import sys
import unittest

# Raíz del paquete (contrata-seguro/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cuitonline_lookup import (  # noqa: E402
    lookup_cuitonline,
    normalizar_cuit_cuil,
    parse_cuitonline_search_html,
)


class TestNormalizar(unittest.TestCase):
    def test_solo_digitos(self):
        self.assertEqual(normalizar_cuit_cuil("20-39494547-2"), "20394945472")

    def test_invalido(self):
        with self.assertRaises(ValueError):
            normalizar_cuit_cuil("123")


HTML_CON_HIT = """
<html><head>
<meta name="description" content="1 Resultados de 20394945472 incluyendo foo. con CuitOnline. MONTESINO JUAN IGNACIO - 20394945472; " />
</head><body>
<div class="results" id="searchResults">
  <div class="hit">
    <div class="denominacion">
      <a href="detalle/20394945472/montesino-juan-ignacio.html" class="denominacion">
        <h2 class="denominacion" style="margin-bottom:10px;">MONTESINO JUAN IGNACIO</h2>
      </a>
    </div>
    <span class="linea-cuit-persona"><span class="cuit">20-39494547-2</span></span>
  </div>
</div>
</body></html>
"""


class TestParseHTML(unittest.TestCase):
    def test_prioriza_h2_denominacion(self):
        r = parse_cuitonline_search_html(HTML_CON_HIT, "20394945472")
        self.assertTrue(r.ok)
        self.assertEqual(r.nombre, "MONTESINO JUAN IGNACIO")
        self.assertEqual(r.selector_origen, "div.hit h2.denominacion")
        self.assertIn("20394945472", r.url)

    def test_sin_resultados(self):
        html = "<html><body>Su búsqueda no obtuvo resultados, verifique</body></html>"
        r = parse_cuitonline_search_html(html, "11111111111")
        self.assertFalse(r.ok)
        self.assertIn("sin resultados", (r.error or "").lower())


@unittest.skipUnless(
    os.environ.get("RUN_CUITONLINE_INTEGRATION") == "1",
    "definir RUN_CUITONLINE_INTEGRATION=1 para probar contra la red",
)
class TestIntegracionRed(unittest.TestCase):
    """Elige un CUIT al azar de una lista que en pruebas devolvió bloque `div.hit`."""

    def test_lookup_aleatorio_muestra_origen_dom(self):
        pool = [
            "20394945472",
            "30714916501",
        ]
        cuit = random.choice(pool)
        with self.subTest(cuit=cuit):
            r = lookup_cuitonline(cuit)
            self.assertTrue(
                r.ok,
                msg=f"Fallo lookup {cuit}: {r.error} (¿bloqueo geográfico o HTML distinto?)",
            )
            self.assertIn(
                r.selector_origen,
                (
                    "div.hit h2.denominacion",
                    "div.hit a.denominacion h2",
                    'meta[name="description"] (tras "CuitOnline.")',
                ),
                msg=f"Selector inesperado: {r.selector_origen!r}",
            )
            self.assertGreaterEqual(len(r.nombre.strip()), 3)


def main():
    unittest.main(verbosity=2)


if __name__ == "__main__":
    main()
