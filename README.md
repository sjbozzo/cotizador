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

## Estructura del catálogo (septiembre 2026)

Cinco categorías visibles, con un máximo de 10 ítems cada una: **1 · Aceleradores USB**, **2 · Aceleradores M.2
(PCIe 3.0)**, **3 · PC industriales con ranura M.2**, **4 · PC con NPU** y **5 · Microcomputadores**. Todo lo que
salió de esas categorías quedó en la cotización archivada «Reserva · fuera de las cinco categorías» (se ve con
«Ver archivadas» y se puede volver a mover). `scripts/reorganizar_2026_09.py` es el script que hizo el reparto.

El tope subió de 8 a 9 el 2026-09-03 para auditar cuatro mini PC de oficina reacondicionados y de 9 a 10 el
2026-09-04 para incorporar el KINGDEL HT690-4 pedido por el usuario sin borrar candidatos descartados. Se conservaron
con foto y ficha técnica, pero quedaron fuera de inclusión porque la revisión en vivo de sus enlaces de
Mercado Libre terminó en verificación de cuenta y no permitió confirmar aviso, configuración, stock, precio ni
despacho. No son equipos industriales —tienen ventilador y trabajan entre 5 y 35 °C—; sirven como candidatos
para banco de pruebas si se confirma una publicación concreta.

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

`scripts/apply_availability.py` aplica el resultado del flujo «disponibilidad-mercados» (dónde comprar
desde Chile: Mercado Libre, Amazon con envío a Chile o AliExpress).
`scripts/import_quotations.py` crea cotizaciones nuevas a partir de un HTML o JSON exportado
(la importación de la aplicación, en cambio, mete ítems en una cotización que ya existe).
`scripts/prepare_audit_batches.py` arma los lotes pequeños que consume el flujo ligero
`workflows/auditar-lean.js`, que es el que cabe en el límite de sesión de la cuenta.

`scripts/agregar_candidatos.py` agrega a una cotización que ya existe los ítems elegidos a mano, con la foto
embebida en la base (`photo_file` apunta a un archivo de `scripts/auditoria/fotos/`, `photo_url` se descarga).
A diferencia de `apply_audit.py`, exige que no quede ningún campo ni columna vacía, que la nota de durabilidad
esté entre 1 y 10 y que la página del fabricante sea distinta del link de compra. Es idempotente: omite el
ítem cuyo nombre ya está en la cotización.

```powershell
uv run python -m scripts.agregar_candidatos scripts/auditoria/2026-09-03-minipc-oficina-m2.json --dry-run
uv run python -m scripts.agregar_candidatos scripts/auditoria/2026-09-03-minipc-oficina-m2.json
```

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
