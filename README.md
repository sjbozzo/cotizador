# Cotizador Edge AI

Aplicación local en FastAPI y SQLite para administrar, filtrar y compartir cotizaciones técnicas.

La base curada viene versionada en `data/cotizador.db`; si falta, se crea y se carga automáticamente en el
primer inicio con los catálogos de `source_html/`. El encargo en curso (objetivo del proyecto, criterios,
qué está hecho y cómo continuar) está en `instructions.txt`.

## Inicio rápido

```powershell
uv sync
uv run uvicorn app.main:app --reload
```

Abra <http://127.0.0.1:8000>.

## Pruebas

```powershell
uv run pytest
```

## Datos

- SQLite: `data/cotizador.db` (versionada; los respaldos `data/*.backup-*.db` no)
- Imágenes nuevas: se comprimen en el navegador como WebP antes de guardarse.
- Se exporta a JSON, PDF, HTML o Excel. HTML y Excel abren un diálogo para elegir qué cotizaciones entran
  y si van todos los elementos o sólo los incluidos.
- El HTML es autónomo y se ve como la aplicación: pestañas, orden por encabezado, buscador y la ficha del
  ítem en sólo lectura. Permite marcar y guardar la selección en el mismo archivo, que se puede volver a
  importar en el Cotizador (en su cotización de origen o en otra), y descargar su propio Excel.
- El .xlsx se arma sin dependencias (`app/services/excel.py` en el servidor, y el mismo formato dentro del
  HTML exportado): una hoja por cotización, con las columnas de la tabla.
- La tabla usa Tabulator 6.4.0, vendorizado en `app/static/vendor/` para que la app funcione sin internet.
  Ordenar y filtrar se hacen desde el encabezado de cada columna.
- Las columnas se reparten el ancho de la pantalla en vez de desbordarla: el texto largo baja de línea
  y la fila crece. Los campos extra se angostan hasta 40px antes que aparezca un scroll horizontal, así
  que una cotización con muchos campos extra queda apretada. El HTML exportado sigue el mismo criterio.
- El orden de las pestañas se cambia arrastrándolas y queda guardado en `quotations.position`.
- Una cotización se archiva desde la tuerca: deja de tener pestaña pero conserva todo. Esa misma ventana
  lista las archivadas con su botón «Desarchivar», y la restaurada vuelve como la última pestaña. Si no
  queda ninguna a la vista, la tuerca sigue en la barra y abre sólo esa lista (`quotations.archived`).
- La descripción de cada cotización se muestra completa sobre la tabla, y viaja al PDF y al HTML exportado.

## Marcar filas

- La casilla de la fila y Ctrl/⌘+clic suman o restan de lo marcado.
- Un clic en la fila deja marcada sólo esa; Shift+clic marca un rango.
- El doble clic —o un clic en la foto— abre el ítem.
- Un clic en el nombre abre la página del producto, cuando el ítem tiene enlace.
  En el HTML exportado ese clic abre la ficha del ítem, que no se edita.

## Auditar el catálogo con agentes

`scripts/auditoria/` guarda los flujos de agentes (`workflows/*.js`, para el comando Workflow de
Claude Code) y sus resultados en JSON: la auditoría ítem por ítem (especificaciones, precio y
disponibilidad contrastados por un segundo agente, ajuste al proyecto, ranura M.2, y cada baja
sometida a tres jueces) y los candidatos nuevos elegidos por cotización (máximo 3).

`scripts/apply_audit.py` aplica un resultado a la base: crea las columnas «Disponibilidad en Chile»,
«Facilidad de programación (1-5)» y «M.2 para acelerador», corrige textos y precios, descarta o
elimina lo que los jueces sostuvieron y agrega los candidatos. Es idempotente.

```powershell
uv run python -m scripts.apply_audit scripts/auditoria/2026-09-02-auditoria.json --dry-run
uv run python -m scripts.apply_audit scripts/auditoria/2026-09-02-auditoria.json
```

`scripts/import_quotations.py` crea cotizaciones nuevas a partir de un HTML o JSON exportado
(la importación de la aplicación, en cambio, mete ítems en una cotización que ya existe).
`scripts/prepare_audit_batches.py` arma los lotes pequeños que consume el flujo ligero
`workflows/auditar-lean.js`, que es el que cabe en el límite de sesión de la cuenta.

## Completar el catálogo

`scripts/enrich_catalog.py` rellena los campos vacíos del catálogo, escribe la descripción
de cada cotización —un texto breve sobre la familia, sin nombrar modelos ni precios—, crea las
columnas de cada tabla (procesador y RAM en los PC industriales, TOPS en las plataformas host)
y da de baja los ítems sin evidencia pública de YOLO. Sólo toca lo que está vacío, así que se puede volver a ejecutar sin
pisar correcciones hechas a mano:

```powershell
uv run python -m scripts.enrich_catalog --dry-run
uv run python -m scripts.enrich_catalog
```
