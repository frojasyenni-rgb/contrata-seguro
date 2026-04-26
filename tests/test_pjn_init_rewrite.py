"""Reescritura de URLs del widget en init.js (debe coincidir con api._rewrite_pjn_init_js_widget_urls)."""
import unittest

from api import _rewrite_pjn_init_js_widget_urls


class TestPjnInitRewrite(unittest.TestCase):
    def test_rewrite_widget_url(self):
        s = 'foo="https://captcha.pjn.gov.ar/api/widget.scw.ABC.html"'
        self.assertEqual(
            _rewrite_pjn_init_js_widget_urls(s),
            'foo="/pjn/captcha-widget/widget.scw.ABC.html"',
        )


if __name__ == "__main__":
    unittest.main()
