# contrata-seguro

## GitHub Pages (evitar 404)

1. En GitHub: **Settings → Pages → Build and deployment**.
2. En **Source** elegí **GitHub Actions** (no “Deploy from a branch” salvo que tengas `index.html` en esa rama y carpeta).
3. Hacé push a `main`: el workflow **GitHub Pages** sube el contenido de la raíz del repo (incluye `index.html`).
4. La URL de un **repositorio** suele ser `https://TU_USUARIO.github.io/contrata-seguro/` (incluye el nombre del repo en la ruta). Si entrás solo a `https://TU_USUARIO.github.io/` sin subcarpeta, GitHub muestra **404** aunque el proyecto esté bien.
5. Si el front queda en Pages y el API en **Railway** (u otro host), editá en `index.html` el meta `api-base`, por ejemplo:  
   `<meta name="api-base" content="https://tu-app.up.railway.app">`  
   y en el backend permití CORS para el origen de Pages.

Si tenías **Source: Deploy from a branch** con carpeta **/docs** vacía, también da 404: pasá a GitHub Actions o mové el sitio a `/docs`.