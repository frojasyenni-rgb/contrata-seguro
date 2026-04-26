"""
Sesión HTTP hacia SCW PJN con desafío resuelto por el usuario (widget oficial).

El sitio usa el widget `pjn-captcha` (script captcha.pjn.gov.ar). El token llega al
DOM como input oculto `#captcha-response` dentro de `.pjn-captcha`; el backend
debe enviarlo en el mismo POST que mantiene cookies JSESSIONID de la sesión creada
en /pjn/prepare.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_BASE = "https://scw.pjn.gov.ar"
_HOME = f"{_BASE}/scw/home.seam"

_LOCK = threading.Lock()
_STORE: Dict[str, Dict[str, Any]] = {}
_TTL_SEC = 900
_INIT_JS_CACHE: Tuple[float, str, Optional[str]] = (0.0, "", None)


def _now() -> float:
    return time.time()


def _purge() -> None:
    t = _now()
    dead = [k for k, v in _STORE.items() if t - float(v.get("created", 0)) > _TTL_SEC]
    for k in dead:
        _STORE.pop(k, None)


def pjn_captcha_widget_present(html: str) -> bool:
    if not html:
        return False
    t = html.lower()
    return "pjn-captcha" in t and "data-sitekey" in t


def es_captcha_pjn(html: str) -> bool:
    """Alineado con buscar_simple: bloqueos conocidos + widget PJN."""
    if not html:
        return False
    t = html.lower()
    if pjn_captcha_widget_present(html):
        return True
    señales = [
        "g-recaptcha",
        "h-captcha",
        "cf-challenge",
        "turnstile",
        "recaptcha/api.js",
        'name="campoverificador"',
        'id="campoverificador"',
    ]
    if any(s in t for s in señales):
        return True
    return ("desafio" in t or "desafío" in t) and ("verificador" in t or "captcha" in t)


def _fetch_init_js() -> str:
    global _INIT_JS_CACHE
    t, text, _ = _INIT_JS_CACHE
    if text and _now() - t < 3600:
        return text
    r = requests.get(
        "https://captcha.pjn.gov.ar/api/init.js?sitekey=SCW",
        headers={**_HDR, "Referer": _HOME, "Origin": "https://scw.pjn.gov.ar"},
        timeout=20,
    )
    r.raise_for_status()
    body = r.text or ""
    m = re.search(
        r"https://captcha\.pjn\.gov\.ar/api/(widget\.scw\.[^\"']+\.html)",
        body,
    )
    widget = f"https://captcha.pjn.gov.ar/api/{m.group(1)}" if m else None
    _INIT_JS_CACHE = (_now(), body, widget)
    return body


def widget_page_url() -> Optional[str]:
    _fetch_init_js()
    return _INIT_JS_CACHE[2]


def _cookies_to_json(session: requests.Session) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in session.cookies:
        out.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": getattr(c, "domain", None) or "",
                "path": getattr(c, "path", None) or "/",
            }
        )
    return out


def _apply_cookies_json(session: requests.Session, rows: List[Dict[str, Any]]) -> None:
    session.cookies.clear()
    for row in rows:
        domain = (row.get("domain") or "").strip() or None
        path = (row.get("path") or "/").strip() or "/"
        session.cookies.set(
            row["name"],
            row["value"],
            domain=domain,
            path=path,
        )


def _collect_form_publica(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"id": "formPublica"})
    if not form:
        return {}
    data: Dict[str, str] = {}
    for el in form.find_all("input"):
        name = el.get("name")
        if not name:
            continue
        itype = (el.get("type") or "text").lower()
        if itype in ("submit", "image", "button"):
            continue
        if itype == "checkbox":
            if el.has_attr("checked"):
                data[name] = el.get("value") or "on"
            continue
        data[name] = el.get("value") or ""
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        if selected is not None:
            data[name] = selected.get("value") or ""
            continue
        first = sel.find("option")
        data[name] = (first.get("value") or "") if first else ""
    return data


def _viewstate(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    return (vs.get("value") or "") if vs else ""


def prepare(nombre_busqueda: str) -> Dict[str, Any]:
    """
    GET home + POST búsqueda por parte (una cámara) para forzar el widget si aplica.
    Devuelve session_id y metadatos del widget para el front.
    """
    nombre_busqueda = (nombre_busqueda or "").strip().upper()
    if len(nombre_busqueda) < 2:
        return {"ok": False, "error": "nombre_invalido"}

    with _LOCK:
        _purge()
        sid = str(uuid.uuid4())
        s = requests.Session()
        s.headers.update(_HDR)
        r0 = s.get(_HOME, timeout=20)
        if r0.status_code != 200:
            return {"ok": False, "error": f"pjn_get_home_{r0.status_code}"}
        if es_captcha_pjn(r0.text):
            html0 = r0.text
            vs0 = _viewstate(html0)
            _STORE[sid] = {
                "created": _now(),
                "session": s,
                "last_html": html0,
                "viewstate": vs0,
                "nombre": nombre_busqueda,
                "phase": "captcha_home",
            }
            try:
                _fetch_init_js()
            except Exception:
                pass
            return {
                "ok": True,
                "session_id": sid,
                "needs_widget": True,
                "sitekey": "SCW",
                "init_js": "https://captcha.pjn.gov.ar/api/init.js?sitekey=SCW",
                "widget_page": widget_page_url(),
                "referrer": _HOME,
            }

        vs = _viewstate(r0.text)
        r1 = s.post(
            _HOME,
            data={
                "javax.faces.ViewState": vs,
                "formPublica": "formPublica",
                "formPublica:expedienteTab-value": "porParte",
                "formPublica:caratula": nombre_busqueda,
                "formPublica:camara": "CNT",
                "formPublica:btnSearch": "Buscar",
            },
            timeout=25,
        )
        if r1.status_code != 200:
            return {"ok": False, "error": f"pjn_post_buscar_{r1.status_code}"}

        needs = pjn_captcha_widget_present(r1.text) or es_captcha_pjn(r1.text)
        _STORE[sid] = {
            "created": _now(),
            "session": s,
            "last_html": r1.text,
            "viewstate": _viewstate(r1.text),
            "nombre": nombre_busqueda,
            "phase": "captcha_after_search" if needs else "ready",
            "captcha_ok": not needs,
        }
        try:
            _fetch_init_js()
        except Exception:
            pass
        return {
            "ok": True,
            "session_id": sid,
            "needs_widget": bool(needs),
            "sitekey": "SCW",
            "init_js": "https://captcha.pjn.gov.ar/api/init.js?sitekey=SCW",
            "widget_page": widget_page_url(),
            "referrer": _HOME,
        }


def _normalizar_pjn(texto: str) -> str:
    return unicodedata.normalize("NFD", (texto or "").upper()).encode("ascii", "ignore").decode("ascii")


def _contiene_partes(texto: str, partes: List[str]) -> bool:
    t = " " + _normalizar_pjn(texto) + " "
    return all((" " + p + " ") in t for p in partes)


def _parse_pjn_results(html: str, nombre: str, camara_nombre: str) -> List[Dict[str, Any]]:
    """Parsea la tabla de resultados de scw.pjn.gov.ar buscando la tabla con más filas de datos."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    partes = [_normalizar_pjn(p) for p in (nombre or "").split() if p]
    if not partes:
        return []

    # Encuentra la tabla con más filas que tengan al menos 3 celdas con texto
    result_table = None
    max_data_rows = 0
    for tbl in soup.find_all("table"):
        data_rows = 0
        for row in tbl.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 3 and len(cells[0].get_text(strip=True)) >= 5:
                data_rows += 1
        if data_rows > max_data_rows:
            max_data_rows = data_rows
            result_table = tbl

    if not result_table or max_data_rows == 0:
        return []

    causas = []
    for fila in result_table.find_all("tr")[1:]:
        celdas = [td.get_text(" ", strip=True) for td in fila.find_all("td")]
        if len(celdas) < 3 or len(celdas[0]) < 5:
            continue
        caratula = celdas[0]
        cn = _normalizar_pjn(caratula)
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


def verify(session_id: str, captcha_response: str) -> Dict[str, Any]:
    captcha_response = (captcha_response or "").strip()
    if not captcha_response:
        return {"ok": False, "error": "captcha_vacio"}

    with _LOCK:
        _purge()
        row = _STORE.get(session_id)
        if not row:
            return {"ok": False, "error": "sesion_no_encontrada"}
        s: requests.Session = row["session"]
        html = row.get("last_html") or ""
        nombre = row.get("nombre", "")

    if not nombre:
        return {"ok": False, "error": "nombre_no_disponible"}

    vs = _viewstate(html)
    if not vs:
        return {"ok": False, "error": "viewstate_no_encontrado"}

    # Combinar campos del formulario con los parámetros de búsqueda y el captcha
    data = _collect_form_publica(html) or {}
    data["javax.faces.ViewState"] = vs
    data["formPublica"] = "formPublica"
    data["formPublica:expedienteTab-value"] = "porParte"
    data["formPublica:caratula"] = nombre
    data["formPublica:camara"] = "CNT"
    data["formPublica:btnSearch"] = "Buscar"
    data["captcha-response"] = captcha_response

    r = s.post(_HOME, data=data, timeout=25)
    if r.status_code != 200:
        return {"ok": False, "error": f"pjn_verify_http_{r.status_code}"}

    if pjn_captcha_widget_present(r.text) or es_captcha_pjn(r.text):
        with _LOCK:
            if session_id in _STORE:
                _STORE[session_id]["last_html"] = r.text
                _STORE[session_id]["viewstate"] = _viewstate(r.text)
        return {"ok": False, "error": "captcha_rechazado_o_aun_pendiente"}

    # Captcha aceptado: parsear resultados CNT
    causas = _parse_pjn_results(r.text, nombre, "Camara Nacional del Trabajo")

    # Obtener nuevo ViewState para la siguiente cámara
    soup_cnt = BeautifulSoup(r.text, "html.parser")
    vs2_tag = soup_cnt.find("input", {"name": "javax.faces.ViewState"})
    vstate2 = (vs2_tag.get("value") or vs) if vs2_tag else vs

    # Búsqueda para Cámara Federal de Seguridad Social
    try:
        r_css = s.post(_HOME, data={
            "javax.faces.ViewState": vstate2,
            "formPublica": "formPublica",
            "formPublica:expedienteTab-value": "porParte",
            "formPublica:caratula": nombre,
            "formPublica:camara": "CSS",
            "formPublica:btnSearch": "Buscar",
        }, timeout=25)
        if not (pjn_captcha_widget_present(r_css.text) or es_captcha_pjn(r_css.text)):
            causas.extend(_parse_pjn_results(r_css.text, nombre, "Camara Federal Seg. Social"))
    except Exception:
        pass

    with _LOCK:
        if session_id in _STORE:
            _STORE[session_id]["last_html"] = r.text
            _STORE[session_id]["viewstate"] = _viewstate(r.text)
            _STORE[session_id]["captcha_ok"] = True
            _STORE[session_id]["phase"] = "ready"
            _STORE[session_id]["causas"] = causas
            _STORE[session_id]["estado_pjn"] = "ok"

    return {"ok": True, "causas_encontradas": len(causas)}


def load_cookies_file(session: requests.Session, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError("cookies_json_invalido")
    _apply_cookies_json(session, rows)


def export_cookies_path(session_id: str, path: str) -> bool:
    with _LOCK:
        row = _STORE.get(session_id)
        if not row:
            return False
        s = row["session"]
        payload = _cookies_to_json(s)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return True


def touch(session_id: str) -> None:
    with _LOCK:
        if session_id in _STORE:
            _STORE[session_id]["created"] = _now()


def get_results(session_id: str) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """Devuelve (causas, estado_pjn) si la sesión ya tiene resultados pre-computados, None si no."""
    with _LOCK:
        row = _STORE.get(session_id)
        if not row:
            return None
        if "causas" in row:
            return row["causas"], row.get("estado_pjn", "ok")
        return None
