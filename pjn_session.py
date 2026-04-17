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
        headers={**_HDR, "Referer": _HOME},
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

    data = _collect_form_publica(html)
    if not data:
        return {"ok": False, "error": "formulario_no_parseable"}
    vs = _viewstate(html)
    if vs:
        data["javax.faces.ViewState"] = vs
    data["formPublica"] = "formPublica"
    data["formPublica:expedienteTab-value"] = "porParte"
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

    with _LOCK:
        if session_id in _STORE:
            _STORE[session_id]["last_html"] = r.text
            _STORE[session_id]["viewstate"] = _viewstate(r.text)
            _STORE[session_id]["captcha_ok"] = True
            _STORE[session_id]["phase"] = "ready"

    return {"ok": True}


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
