"""
Rutas HTTP del captcha PJN (proxy same-origin) y página embed.

Ejecutar:
  cd contrata-seguro
  python -m unittest tests.test_pjn_captcha_routes -v

Prueba manual en navegador (API en esta máquina):
  1. python api.py
  2. Abrir http://127.0.0.1:5000/pjn/captcha-embed.html
     Debe cargar /pjn/captcha-init.js y el HTML del widget vía /pjn/captcha-widget/...

Flujo en la app principal:
  http://127.0.0.1:5000/ → búsqueda PJN → si la API responde needs_widget, el modal
  monta un iframe apuntando a /pjn/captcha-embed.html (requiere mismo origen que API_URL).
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestPjnCaptchaRoutes(unittest.TestCase):
    """Cliente de prueba Flask; las llamadas a PJN van mockeadas salvo que se indique lo contrario."""

    @classmethod
    def setUpClass(cls):
        import api

        api.app.config["TESTING"] = True
        cls.app = api.app

    def setUp(self):
        self.client = self.app.test_client()

    def test_root_serves_static_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Contrata Seguro", r.data)
        self.assertIn(b'name="api-base"', r.data)

    def test_captcha_embed_page(self):
        r = self.client.get("/pjn/captcha-embed.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"/pjn/captcha-init.js", r.data)
        self.assertIn(b"strict-origin-when-cross-origin", r.data.lower())
        self.assertIn(b"pjn-captcha", r.data.lower())

    @patch("api.requests.get")
    def test_captcha_init_js_rewrites_widget_url(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            text='src="https://captcha.pjn.gov.ar/api/widget.scw.UNITTEST.html"',
            raise_for_status=lambda: None,
        )
        r = self.client.get("/pjn/captcha-init.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"/pjn/captcha-widget/widget.scw.UNITTEST.html", r.data)

    @patch("api.requests.get")
    def test_captcha_init_js_pjn_error_returns_502_body(self, mock_get):
        mock_get.side_effect = RuntimeError("red")
        r = self.client.get("/pjn/captcha-init.js")
        self.assertEqual(r.status_code, 502)
        self.assertIn(b"pjn captcha-init error", r.data)

    @patch("api.requests.get")
    def test_widget_proxy_rejects_bad_filename(self, mock_get):
        r = self.client.get("/pjn/captcha-widget/not-a-widget.html")
        self.assertEqual(r.status_code, 400)
        mock_get.assert_not_called()

    @patch("api.requests.get")
    def test_widget_proxy_short_html_502(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            text="<html>x</html>",
            raise_for_status=lambda: None,
        )
        r = self.client.get("/pjn/captcha-widget/widget.scw.SHORT.html")
        self.assertEqual(r.status_code, 502)
        self.assertIn(b"respuesta corta", r.data)

    @patch("api.requests.get")
    def test_widget_proxy_inserts_base_in_head(self, mock_get):
        body = "<head><title>t</title></head><body>" + ("x" * 3500)
        mock_get.return_value = MagicMock(
            status_code=200,
            text=body,
            raise_for_status=lambda: None,
        )
        r = self.client.get("/pjn/captcha-widget/widget.scw.OK.html")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'base href="https://captcha.pjn.gov.ar/api/"', r.data)


@unittest.skipUnless(os.environ.get("RUN_PJN_NETWORK") == "1", "definir RUN_PJN_NETWORK=1 para golpear PJN")
class TestPjnCaptchaLiveNetwork(unittest.TestCase):
    """Opcional: comprobar init.js real y tamaño del HTML del widget (Referer SCW lo hace el servidor)."""

    @classmethod
    def setUpClass(cls):
        import api

        api.app.config["TESTING"] = True
        cls.app = api.app

    def test_live_init_js_returns_widget_path(self):
        import re

        client = self.app.test_client()
        r = client.get("/pjn/captcha-init.js")
        self.assertEqual(r.status_code, 200, msg=r.data[:500])
        m = re.search(br"/pjn/captcha-widget/(widget\.scw\.[^\"'\\s>]+)", r.data)
        self.assertIsNotNone(m, msg="init.js debería referenciar el proxy del widget")
        wpath = m.group(1).decode("ascii")
        r2 = client.get("/pjn/captcha-widget/" + wpath)
        n = len(r2.data)
        if r2.status_code == 502 or n < 3000:
            self.skipTest(
                f"PJN devolvió respuesta corta o 502 (status={r2.status_code}, {n} B). "
                "El proxy y Referer suelen funcionar en escritorio/VPN; en CI a veces no."
            )
        self.assertGreater(n, 8000, msg="HTML del widget debería ser grande si PJN responde bien")


if __name__ == "__main__":
    unittest.main()
