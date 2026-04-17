# Integración: búsqueda por nombre (carátula) o por CUIL/CUIT

Este documento describe cómo el backend resuelve un **CUIL/CUIT** a **denominación** usando el HTML público de [Cuit Online](https://www.cuitonline.com/) y luego ejecuta la misma búsqueda judicial (SCBA + PJN) que con nombre y apellido.

## Componentes

| Archivo | Rol |
|--------|-----|
| `cuitonline_lookup.py` | `GET https://www.cuitonline.com/search/{11 dígitos}` y parseo del nombre. |
| `buscar_simple.py` | CLI: `--cuil`, `--caratula apellido\|completo`, argumentos de nombre. |
| `api.py` | Construye la línea de comandos del scraper según query/body. |
| `index.html` | Selector de modo y campo CUIL en el flujo del usuario. |

## Dónde aparece el nombre en Cuit Online (parseo)

Cuando hay al menos un resultado, el sitio suele incluir un bloque similar a:

```html
<div class="hit" ...>
  <div class="denominacion">
    <a href="detalle/XXXXXXXXXXX/apellido-nombre.html" ...>
      <h2 class="denominacion" ...>APELLIDO NOMBRE</h2>
    </a>
  </div>
  ...
</div>
```

El código usa **primero** el selector CSS `div.hit h2.denominacion`. Si no hubiera `h2`, intenta `div.hit a.denominacion h2`. Como respaldo lee `meta[name="description"]` con un patrón del tipo `... CuitOnline. apellido nombre - 11dígitos`.

Si el HTML contiene el mensaje *“Su búsqueda no obtuvo resultados”*, se devuelve error (no hay denominación).

## Uso de la CLI (`buscar_simple.py`)

Requiere `SCBA_USUARIO` y `SCBA_PASSWORD` en el entorno (igual que antes).

```text
# Solo apellido (primera palabra) en el campo carátula del MEV — comportamiento histórico
python buscar_simple.py --caratula apellido GARCIA JUAN CARLOS

# Toda la cadena normalizada en carátula (más restrictivo en el MEV)
python buscar_simple.py --caratula completo GARCIA JUAN CARLOS

# CUIL/CUIT: resuelve nombre en CuitOnline y busca con ese nombre
python buscar_simple.py --cuil 20394945472
python buscar_simple.py --cuil 20-39494547-2 --caratula completo
```

Salida JSON (`RESULTADO:`) incluye, entre otros:

- `caratula_modo` y `caratula_scba_usada`: qué se envió al MEV.
- Si entró por CUIL: `modo_entrada`, `cuil_consultado`, `nombre_resuelto_cuitonline`, `cuitonline_selector`, `cuitonline_url`.

## API HTTP

### `GET /buscar/stream` (EventSource, como el front)

Parámetros de query:

| Parámetro | Descripción |
|-----------|-------------|
| `nombre` | Obligatorio si no hay `cuil`. Apellido y nombre. |
| `cuil` | Opcional. 11 dígitos (se aceptan guiones; se normalizan). |
| `caratula` | `apellido` (default) o `completo`. |
| `token` | Igual que antes para créditos / Supabase. |

Ejemplos:

```http
GET /buscar/stream?nombre=GARCIA%20JUAN&caratula=completo&token=...
GET /buscar/stream?cuil=20394945472&token=...
```

### `GET` o `POST /buscar`

Body JSON (POST) o query (GET) análogo: `nombre`, `cuil`, `caratula`, `token`.

## Riesgos y límites

1. **Términos de uso** de Cuit Online: uso intensivo o automatizado puede violar sus condiciones o provocar bloqueos (Cloudflare, captchas, cambios de HTML).
2. **Privacidad (Ley 25.326)**: el CUIL identifica personas; documentar finalidad y base legal en tu producto.
3. **Disponibilidad**: si el HTML cambia, actualizar `parse_cuitonline_search_html` en `cuitonline_lookup.py`.
4. **Sin resultados en CuitOnline**: la búsqueda judicial no arranca; el proceso termina con `RESULTADO` de error y mensaje en `cuitonline_error` / `error_config`.

## Pruebas

- **Unitarias** (sin red): `python tests/test_cuitonline_lookup.py`
- **Integración real** (red): `set RUN_CUITONLINE_INTEGRATION=1` y volver a ejecutar el mismo script; elige al azar un CUIT de una lista interna y comprueba que `selector_origen` coincida con el DOM esperado.
