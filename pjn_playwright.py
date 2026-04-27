"""
Búsqueda PJN automatizada con Playwright.

El captcha de PJN (F5 TSPD) evalúa el fingerprint del navegador.
Playwright usa Chromium real y pasa el check sin necesidad de resolver el captcha
visualmente en la mayoría de los casos.

Cuando el captcha sí aparece (forma visual "VER DESAFÍO"), lo resuelve con:
  a) 2captcha API  (TWO_CAPTCHA_API_KEY en entorno) — más confiable
  b) easyocr / pytesseract (OCR local, gratis, ~70% de éxito)

El scraper HTTP (buscar_pjn) siempre fallará porque la IP del servidor
es detectada como bot. Esta función hace TODO en Playwright.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

log = logging.getLogger("pjn_playwright")

_PJN_HOME = "https://scw.pjn.gov.ar/scw/home.seam"

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}
CACHE_TTL = int(os.environ.get("PJN_SESSION_TTL", "1800"))

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--disable-infobars",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['es-AR','es','en-US','en']});
window.chrome = {runtime: {}};
"""

# ──────────────────────────────────────────────
# Captcha solving helpers
# ──────────────────────────────────────────────

def _b64_decode_safe(b64: str) -> bytes:
    b64 = b64.strip().replace("\n", "").replace(" ", "")
    pad = 4 - len(b64) % 4
    if pad != 4:
        b64 += "=" * pad
    return base64.b64decode(b64)


def _preprocess_captcha_image(img_data: bytes):
    from PIL import Image, ImageEnhance, ImageFilter
    img = Image.open(io.BytesIO(img_data)).convert("RGB")
    w, h = img.size
    img = img.resize((w * 3, h * 3), Image.LANCZOS)
    gray = img.convert("L")
    gray = ImageEnhance.Contrast(gray).enhance(3.0)
    bw = gray.point(lambda p: 0 if p < 160 else 255)
    return bw.filter(ImageFilter.MaxFilter(3))


def _solve_with_2captcha(img_b64: str) -> Optional[str]:
    api_key = (os.environ.get("TWO_CAPTCHA_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        import requests as req
        r = req.post(
            "https://2captcha.com/in.php",
            data={"key": api_key, "method": "base64", "body": img_b64, "json": 1},
            timeout=15,
        )
        j = r.json()
        if j.get("status") != 1:
            log.warning("2captcha: error al enviar: %s", j)
            return None
        task_id = j["request"]
        for _ in range(20):
            time.sleep(5)
            r2 = req.get(
                "https://2captcha.com/res.php",
                params={"key": api_key, "action": "get", "id": task_id, "json": 1},
                timeout=10,
            )
            j2 = r2.json()
            if j2.get("status") == 1:
                sol = str(j2["request"]).strip()
                log.info("2captcha: solución: %r", sol)
                return sol
            if j2.get("request") != "CAPCHA_NOT_READY":
                return None
        return None
    except Exception as exc:
        log.error("2captcha: %s", exc)
        return None


def _solve_with_ocr(img_b64: str) -> Optional[str]:
    try:
        img_data = _b64_decode_safe(img_b64)
    except Exception as exc:
        log.error("OCR base64 decode: %s", exc)
        return None

    try:
        import pytesseract
        processed = _preprocess_captcha_image(img_data)
        text = pytesseract.image_to_string(
            processed,
            config="--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-",
        ).strip()
        text = "".join(c for c in text if c.isalnum() or c == "-")
        log.info("pytesseract: %r", text)
        if len(text) >= 2:
            return text
    except ImportError:
        pass
    except Exception as exc:
        log.warning("pytesseract: %s", exc)

    try:
        import easyocr
        import numpy as np
        processed = _preprocess_captcha_image(img_data)
        arr = np.array(processed)
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        results = reader.readtext(
            arr, detail=0,
            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-",
        )
        text = "".join(results).strip()
        text = "".join(c for c in text if c.isalnum() or c == "-")
        log.info("easyocr: %r", text)
        if len(text) >= 2:
            return text
    except ImportError:
        pass
    except Exception as exc:
        log.warning("easyocr: %s", exc)

    return None


def _solve_captcha_image(img_b64: str) -> Optional[str]:
    sol = _solve_with_2captcha(img_b64)
    if sol:
        return sol
    return _solve_with_ocr(img_b64)


def _try_solve_captcha_if_present(page) -> None:
    """Si el iframe del captcha está presente, intenta resolverlo."""
    captcha_frame = next(
        (f for f in page.frames if "captcha.pjn.gov.ar" in f.url), None
    )
    if not captcha_frame:
        return

    btn = captcha_frame.query_selector(".loading-button, button")
    if not btn:
        return

    log.info("PJN Playwright: captcha presente, haciendo click en VER DESAFÍO")
    btn.click()
    try:
        captcha_frame.wait_for_selector('img[src^="data:image"]', timeout=8_000)
    except Exception:
        page.wait_for_timeout(3_000)

    img_b64 = captcha_frame.evaluate("""
        () => {
            const img = document.querySelector('img[src^="data:image"]');
            if (!img) return null;
            const src = img.src;
            const idx = src.indexOf('base64,');
            return idx >= 0 ? src.substring(idx + 7) : null;
        }
    """)

    if not img_b64:
        log.warning("PJN Playwright: no se encontró imagen del challenge")
        return

    log.info("PJN Playwright: imagen obtenida (%d chars base64)", len(img_b64))
    sol = _solve_captcha_image(img_b64)
    if not sol:
        log.warning("PJN Playwright: no se pudo resolver el captcha")
        return

    log.info("PJN Playwright: solución captcha: %r", sol)
    inp = captcha_frame.query_selector("input.text-challenge-input, input[type='text']")
    if not inp:
        return

    # Limpiar la solución (quitar guiones al final, espacios)
    sol = sol.strip().rstrip("-").strip()
    inp.fill(sol)
    page.wait_for_timeout(400)

    # Hacer click en Aceptar via JavaScript (evita problemas de visibilidad)
    try:
        captcha_frame.evaluate("""
            () => {
                const btn = document.querySelector('.accept-challenge-button')
                    || document.querySelector('button.loading-button.circular-loading')
                    || Array.from(document.querySelectorAll('button')).pop();
                if (btn) btn.click();
            }
        """)
        page.wait_for_timeout(3_000)
        log.info("PJN Playwright: solución enviada")
    except Exception as exc:
        log.warning("PJN Playwright: error al hacer click en Aceptar: %s", exc)


# ──────────────────────────────────────────────
# Parsing de resultados
# ──────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFD", (texto or "").upper()).encode("ascii", "ignore").decode("ascii")


def _contiene_partes(texto: str, partes: List[str]) -> bool:
    t = " " + _normalizar(texto) + " "
    return all((" " + p + " ") in t for p in partes)


def _parse_resultados_pjn(html: str, nombre: str, camara_nombre: str) -> List[Dict]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    partes = [_normalizar(p) for p in (nombre or "").split() if p]
    if not partes:
        return []

    result_table = None
    max_rows = 0
    for tbl in soup.find_all("table"):
        data_rows = sum(
            1 for row in tbl.find_all("tr")
            if len(row.find_all("td")) >= 3 and len(row.find_all("td")[0].get_text(strip=True)) >= 5
        )
        if data_rows > max_rows:
            max_rows = data_rows
            result_table = tbl

    if not result_table:
        return []

    causas = []
    for fila in result_table.find_all("tr")[1:]:
        celdas = [td.get_text(" ", strip=True) for td in fila.find_all("td")]
        if len(celdas) < 3 or len(celdas[0]) < 5:
            continue
        caratula = celdas[0]
        cn = _normalizar(caratula)
        sep = cn.find(" C/ ")
        actor_parte = cn[:sep] if sep >= 0 else cn
        demandado_parte = cn[sep + 1:] if sep >= 0 else ""
        if _contiene_partes(actor_parte, partes):
            rol = "ACTOR"
        elif _contiene_partes(demandado_parte, partes):
            rol = "DEMANDADO"
        elif _contiene_partes(cn, partes):
            rol = "INDETERMINADO"
        else:
            continue
        causas.append({
            "caratula": caratula,
            "expediente": celdas[1] if len(celdas) > 1 else "",
            "juzgado": celdas[2] if len(celdas) > 2 else camara_nombre,
            "fecha_inicio": celdas[3] if len(celdas) > 3 else "",
            "ultima_actuacion": celdas[4] if len(celdas) > 4 else "",
            "estado": celdas[5] if len(celdas) > 5 else "",
            "fuente": "PJN",
            "rol": rol,
            "dni_actor": "",
            "dni_validacion": "no_validado",
        })
    return causas


# ──────────────────────────────────────────────
# Búsqueda principal
# ──────────────────────────────────────────────

def buscar_pjn_playwright(nombre: str) -> Tuple[List[Dict], str]:
    """
    Busca causas en PJN usando Playwright.
    Devuelve (causas, estado) donde estado es 'ok' o 'error'.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("playwright no instalado")
        return [], "playwright_no_disponible"

    log.info("PJN Playwright: búsqueda nombre=%r", nombre)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        ctx = browser.new_context(
            user_agent=_USER_AGENT,
            locale="es-AR",
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_init_script(_STEALTH)
        page = ctx.new_page()

        try:
            page.goto(_PJN_HOME, timeout=30_000, wait_until="networkidle")
            page.wait_for_timeout(2_000)

            # Intentar resolver captcha si aparece
            _try_solve_captcha_if_present(page)

            # Obtener ViewState
            vs = page.evaluate("""
                () => {
                    const inp = document.querySelector('input[name="javax.faces.ViewState"]');
                    return inp ? inp.value : '';
                }
            """)
            captcha_tok = page.evaluate("""
                () => {
                    const inp = document.querySelector('#captcha-response, input[name="captcha-response"]');
                    return inp ? inp.value : '';
                }
            """) or ""

            log.info("PJN Playwright: viewstate=%d chars, captcha_tok=%d chars", len(vs), len(captcha_tok))

            # Valores numéricos del select formPublica:camaraPartes (inspeccionados en vivo)
            camaras = [("7", "Camara Nacional del Trabajo"), ("5", "Camara Federal Seg. Social")]
            causas_total = []

            for cod, cam_nombre in camaras:
                log.info("PJN Playwright: búsqueda cámara=%s (%s)", cod, cam_nombre)
                try:
                    # Recargar home para estado limpio por cada cámara
                    page.goto(_PJN_HOME, timeout=30_000, wait_until="networkidle")
                    page.wait_for_timeout(1_500)

                    # Activar tab "Por parte" (genera nueva captcha en esa página)
                    page.click("text=Por parte", timeout=5_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    page.wait_for_timeout(1_000)

                    # Resolver captcha AQUÍ, en la página con el formulario activo
                    _try_solve_captcha_if_present(page)
                    page.wait_for_timeout(500)

                    # Verificar que el captcha-response fue llenado
                    captcha_tok = page.evaluate(
                        "() => document.querySelector('#captcha-response')?.value || ''"
                    ) or ""
                    log.info("PJN Playwright: captcha_tok=%d chars para %s", len(captcha_tok), cam_nombre)

                    # Seleccionar cámara
                    page.select_option(
                        'select[name="formPublica:camaraPartes"]',
                        value=cod,
                        timeout=5_000,
                    )

                    # Escribir el nombre
                    page.fill('input[name="formPublica:nomIntervParte"]', nombre)

                    # Click en Consultar
                    page.click('input[name="formPublica:buscarPorParteButton"]', timeout=5_000)
                    page.wait_for_load_state("networkidle", timeout=30_000)
                    page.wait_for_timeout(3_000)

                    content = page.content()

                    # Verificar si el captcha fue rechazado
                    if "campo verificador" in content.lower():
                        log.warning("PJN Playwright: captcha rechazado en %s (OCR incorrecto)", cam_nombre)
                        continue

                    # Si está procesando, esperar
                    if "puede demorar" in content.lower():
                        log.info("PJN Playwright: %s procesando, esperando 15s...", cam_nombre)
                        page.wait_for_timeout(15_000)
                        content = page.content()

                    log.debug("PJN Playwright: HTML len=%d", len(content))
                    nuevas = _parse_resultados_pjn(content, nombre, cam_nombre)
                    log.info("PJN Playwright: cámara=%s → %d causas", cod, len(nuevas))
                    causas_total.extend(nuevas)

                except Exception as exc:
                    log.error("PJN Playwright: error en cámara %s: %s", cod, exc)
                    continue

            log.info("PJN Playwright: total %d causas", len(causas_total))
            return causas_total, "ok"

        except Exception as exc:
            log.error("PJN Playwright: error: %s", exc, exc_info=True)
            return [], "error"
        finally:
            browser.close()


# ──────────────────────────────────────────────
# API pública (compatibilidad con código anterior)
# ──────────────────────────────────────────────

def get_pjn_session() -> Optional[Dict[str, Any]]:
    """Mantiene compatibilidad con código que usa get_pjn_session()."""
    return None  # Ya no se usa: toda la búsqueda va por buscar_pjn_playwright()


def get_pjn_cookies() -> Optional[List[Dict]]:
    return None


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
    log.info("PJN Playwright: caché invalidada")
