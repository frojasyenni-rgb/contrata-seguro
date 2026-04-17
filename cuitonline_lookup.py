"""
Resolución CUIT/CUIL → denominación vía HTML público de cuitonline.com/search/<digits>.

Solo para integrar el flujo de búsqueda judicial del proyecto; revisar términos del sitio
y normativa de datos personales antes de uso masivo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

CUITONLINE_SEARCH_URL = "https://www.cuitonline.com/search/{digits}"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}


@dataclass
class CuitOnlineLookupResult:
    ok: bool
    """True si se obtuvo denominación usable."""

    nombre: str
    """Texto tal como figura en el sitio (mayúsculas habituales)."""

    digits: str
    """CUIT/CUIL solo dígitos (11)."""

    selector_origen: str
    """Descripción del nodo donde se leyó el nombre (para depuración / tests)."""

    url: str
    error: Optional[str] = None


def normalizar_cuit_cuil(valor: str) -> str:
    """Extrae 11 dígitos; lanza ValueError si no hay exactamente 11."""
    s = re.sub(r"\D", "", (valor or "").strip())
    if len(s) != 11:
        raise ValueError("CUIT/CUIL debe tener 11 dígitos")
    return s


def parse_cuitonline_search_html(html: str, digits: str) -> CuitOnlineLookupResult:
    url = CUITONLINE_SEARCH_URL.format(digits=digits)
    if not html:
        return CuitOnlineLookupResult(
            False, "", digits, "", url, error="Respuesta vacía o demasiado corta"
        )

    low = html.lower()
    if "su búsqueda no obtuvo resultados" in low or "no obtuvo resultados" in low:
        return CuitOnlineLookupResult(
            False, "", digits, "", url, error="CuitOnline: sin resultados para ese número"
        )

    if len(html) < 500:
        return CuitOnlineLookupResult(
            False, "", digits, "", url, error="Respuesta vacía o demasiado corta"
        )

    soup = BeautifulSoup(html, "html.parser")

    hit = soup.select_one("div.hit h2.denominacion")
    if hit:
        nombre = hit.get_text(" ", strip=True)
        if nombre and len(nombre) > 2:
            return CuitOnlineLookupResult(
                True,
                nombre,
                digits,
                "div.hit h2.denominacion",
                url,
            )

    link = soup.select_one("div.hit a.denominacion h2")
    if link:
        nombre = link.get_text(" ", strip=True)
        if nombre:
            return CuitOnlineLookupResult(
                True, nombre, digits, "div.hit a.denominacion h2", url
            )

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        content = meta["content"]
        # Ej.: "... con CuitOnline. montesino juan ignacio - 20394945472; "
        m = re.search(
            r"CuitOnline\.\s*(.+?)\s*-\s*" + re.escape(digits),
            content,
            flags=re.I | re.DOTALL,
        )
        if m:
            nombre = re.sub(r"\s+", " ", m.group(1).strip())
            if nombre and len(nombre) > 2:
                return CuitOnlineLookupResult(
                    True,
                    nombre.upper(),
                    digits,
                    'meta[name="description"] (tras "CuitOnline.")',
                    url,
                )

    return CuitOnlineLookupResult(
        False,
        "",
        digits,
        "",
        url,
        error="No se encontró denominación en el HTML (estructura cambió o paywall)",
    )


def lookup_cuitonline(cuit_o_cuil: str, timeout: int = 25) -> CuitOnlineLookupResult:
    digits = normalizar_cuit_cuil(cuit_o_cuil)
    url = CUITONLINE_SEARCH_URL.format(digits=digits)
    try:
        r = requests.get(
            url,
            headers=_BROWSER_HEADERS,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return CuitOnlineLookupResult(
            False, "", digits, "", url, error=f"Error de red: {e}"
        )

    if r.status_code != 200:
        return CuitOnlineLookupResult(
            False,
            "",
            digits,
            "",
            url,
            error=f"HTTP {r.status_code}",
        )

    enc = r.encoding or "windows-1252"
    html = r.content.decode(enc, errors="replace")
    return parse_cuitonline_search_html(html, digits)
