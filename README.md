# contrata-seguro

## Estructura del repo

| Ruta | Contenido |
|------|------------|
| `static/` | Front estático (`index.html`, `pjn_captcha_embed.html`). GitHub Pages publica esta carpeta. |
| `docs/` | Notas y documentación (`INTEGRACION_CUIL_CUITONLINE.md`, `mejoras.md`). |
| `artifacts/html-captures/` | HTML de depuración o capturas locales (no usados por el runtime). |
| `sql/` | Consultas SQL de referencia. |
| `tests/` | Pruebas con `pytest`. |
| Raíz | Backend Flask (`api.py`), scraper (`buscar_simple.py`), integraciones (`pjn_session.py`, `cuitonline_lookup.py`), despliegue (`Dockerfile`, `entrypoint_gunicorn.py`). |

## Despliegue

El frontend (`static/`) y el backend se sirven desde el mismo proceso Flask en **Railway**.
La base de datos y autenticación están en **Supabase**.