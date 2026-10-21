# TikTok Data Extractor

Aplicación web local que extrae métricas y comentarios de perfiles públicos de TikTok y genera reportes en CSV y JSON. Proyecto académico de fin de ciclo.

## Descripción

El proyecto levanta un servidor local con **Flask** que muestra una interfaz oscura y responsive en el navegador. Desde ahí se ingresa el nombre de usuario de TikTok y el backend ejecuta el scraping con **Playwright** y **BeautifulSoup** sin congelar la pantalla: la interfaz consulta el avance cada 1,5 segundos mediante `fetch`.

## Funcionalidades

El proceso se divide en cuatro fases:

| Fase | Qué hace |
|------|----------|
| **A. Perfil** | Lee el JSON de hidratación de la página (`__UNIVERSAL_DATA_FOR_REHYDRATION__` / `SIGI_STATE`) y obtiene usuario, nickname, biografía, seguidores y seguidos. |
| **B. Videos** | Mapea las últimas 10 publicaciones con URL, likes, vistas (`playCount`) y total de comentarios. |
| **C. Comentarios** | Entra a cada video, fuerza la carga perezosa de comentarios y extrae los primeros 5. |
| **D. Relaciones** | Obtiene una muestra de cuentas que siguen al perfil y de cuentas que el perfil sigue (hasta 30 de cada una). |

Además, la interfaz incluye:

- Botón "Extraer Datos" con spinner animado y bloqueo mientras se procesa.
- Registro de actividad en tiempo real.
- Tarjetas de perfil, listas de seguidores y seguidos, y tabla de videos.
- Descarga del reporte en **CSV** y en **JSON**.

## Tecnologías

- **Backend:** Python 3.10+, Flask
- **Scraping:** Playwright (motor asíncrono) y BeautifulSoup4
- **Persistencia:** librerías nativas `csv` y `json`
- **Frontend:** HTML5, Tailwind CSS (CDN) y JavaScript ES6+

Restricciones del proyecto: sin Selenium, sin librerías específicas de TikTok y sin pandas.

## Estructura del proyecto

```
tiktok_scraper/
├── app.py              # Servidor Flask y endpoints de la API REST
├── scraper.py          # Clase TikTokScraper (Playwright + BeautifulSoup)
├── templates/
│   └── index.html      # Interfaz (HTML + Tailwind + JavaScript)
├── requirements.txt    # Dependencias
├── .gitignore
└── README.md
```

Al ejecutar el programa se crean automáticamente `output/` (reportes) y `chrome_profile/` (sesión del navegador). Ninguna de las dos se sube al repositorio.

## Instalación

1. Clona el repositorio:
   ```bash
   git clone https://github.com/TU_USUARIO/TU_REPOSITORIO.git
   cd TU_REPOSITORIO
   ```

2. Crea y activa un entorno virtual:
   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS / Linux
   source venv/bin/activate
   ```

3. Instala las dependencias y el navegador de Playwright:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

## Uso

1. Inicia el servidor:
   ```bash
   python app.py
   ```
2. Abre en el navegador: <http://127.0.0.1:5000>
3. Escribe el usuario de TikTok **sin @** y pulsa **Extraer Datos**.
4. Se abrirá una ventana del navegador de Playwright. La primera vez conviene **iniciar sesión en TikTok** (necesario para ver las listas de seguidores) y resolver cualquier captcha a mano. La sesión queda guardada en `chrome_profile/`.
5. Cuando termine, revisa los resultados en pantalla y descarga el CSV o el JSON.

El proceso tarda entre 3 y 6 minutos por perfil, porque incluye pausas aleatorias para simular navegación humana.

## Configuración

Las opciones están al inicio de `scraper.py`:

| Variable | Descripción |
|----------|-------------|
| `RUTA_USER_DATA` | Carpeta donde se guardan cookies y sesión del navegador. |
| `CANAL_NAVEGADOR` | `None` usa Chromium de Playwright; `"chrome"` usa tu Google Chrome instalado. |
| `MODO_HEADLESS` | `False` muestra el navegador (recomendado). |
| `MAX_VIDEOS` / `MAX_COMENTARIOS` / `MAX_RELACIONES` | Límites de extracción. |

## Formato de los reportes

- **CSV** (`reporte_tiktok_[usuario].csv`): bloques de resumen del perfil, lista de seguidores, lista de seguidos y matriz de videos (ID, URL, Likes, Vistas, Comentarios Totales y comentarios separados por `|`).
- **JSON** (`reporte_tiktok_[usuario].json`): la misma información en formato estructurado.

## API REST

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` | Interfaz gráfica |
| POST | `/api/extraer` | Inicia la extracción (`{"username": "..."}`) |
| GET | `/api/estado` | Estado, registro de actividad y resultados |
| GET | `/api/descargar` | Descarga el CSV |
| GET | `/api/descargar_json` | Descarga el JSON |

## Limitaciones conocidas

- TikTok aplica medidas anti-bot. Pueden aparecer captchas o errores HTTP 403; en ese caso conviene esperar un rato, iniciar sesión en la ventana del navegador y reintentar.
- Los comentarios y las listas de seguidores dependen de cómo TikTok cargue la página y pueden salir incompletos o vacíos.
- Los nombres internos de los datos de TikTok cambian con el tiempo, por lo que los selectores pueden requerir ajustes.

## Aviso de uso responsable

Este proyecto tiene fines **exclusivamente educativos**. Úsalo solo con perfiles públicos, respeta los Términos de Servicio de TikTok y la privacidad de las personas, y no lo emplees para recopilación masiva de datos. No subas al repositorio reportes con datos de terceros ni la carpeta de sesión del navegador.

## Autor

**[Tu nombre]** — [Materia / Universidad] — [Año o ciclo]
