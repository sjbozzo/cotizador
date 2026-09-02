export const meta = {
  name: 'candidatos-chile',
  description: 'Busca productos comprables desde Chile por menos de US$500 que aporten a cada cotización, los verifica con un escéptico y elige como máximo 3 por tabla',
  phases: [
    { title: 'Buscar', detail: 'tres ángulos de búsqueda por cotización' },
    { title: 'Verificar', detail: 'un escéptico por candidato: precio, stock y ajuste' },
    { title: 'Elegir', detail: 'un juez por cotización elige hasta 3' },
  ],
}

const { brief, catalog, quotations } = args
const TOOLS = 'Usa WebSearch y WebFetch (si no las ves, cárgalas con ToolSearch "select:WebSearch,WebFetch"). Lee archivos con Read.'

const ANGLES = [
  {
    key: 'chile',
    prompt: 'Venta LOCAL en Chile: MercadoLibre Chile, Altronics, MCI Electronics, AFEL, Casa Royal, Winpy, PCFactory, SP Digital, Solotodo, Alameda Electrónica, tiendas de Raspberry Pi en Chile. Prioriza stock real hoy y vendedores con reputación.',
  },
  {
    key: 'importar',
    prompt: 'Distribuidores que DESPACHAN a Chile con stock: Mouser, DigiKey, Amazon (con envío a Chile), AliExpress (vendedor oficial), tienda del fabricante, Seeed, Waveshare, Radxa, Pimoroni. Estima costo total puesto en Chile (precio + envío aproximado).',
  },
  {
    key: 'nodo',
    prompt: 'Pensando en el NODO COMPLETO por menos de US$500: kits o equipos que integran procesador + acelerador + carcasa de aluminio y corren YOLO sobre varias cámaras IP (por ejemplo Raspberry Pi 5 + AI HAT+ 26 TOPS con carcasa metálica, reComputer / reTerminal de Seeed con Hailo, Jetson Orin Nano en carcasa industrial, mini PC N100 fanless con Hailo-8 M.2, Radxa ROCK 5 en caja de aluminio, Orange Pi 5 con NPU). Sólo propón lo que encaje en la cotización indicada.',
  },
]

const CANDIDATE_SCHEMA = {
  type: 'object',
  required: ['candidates'],
  properties: {
    candidates: {
      type: 'array',
      maxItems: 5,
      items: {
        type: 'object',
        required: ['name', 'price', 'country', 'purchase_link', 'photo_url', 'description', 'comment', 'extra_data', 'availability_chile', 'programming_score', 'programming_note', 'why', 'sources'],
        properties: {
          name: { type: 'string', description: 'Nombre exacto del producto' },
          price: { type: 'string', description: 'Precio con moneda, fuente y fecha, ej "US$110 (Seeed, sep. 2026)" o "$129.990 CLP (MercadoLibre, sep. 2026)"' },
          country: { type: 'string', description: 'País de origen del fabricante' },
          purchase_link: { type: 'string', description: 'URL http/https donde comprarlo desde Chile' },
          photo_url: { type: 'string', description: 'URL http/https de una foto del producto (o cadena vacía)' },
          description: { type: 'string', description: '1-2 frases factuales: qué es, chip, TOPS, red, carcasa' },
          comment: { type: 'string', description: 'Por qué sirve para este proyecto, riesgos, qué más hay que comprar' },
          extra_data: { type: 'object', additionalProperties: true, description: 'Valores para las columnas extra de ESA cotización (mismas claves que en catalogo.json)' },
          availability_chile: { type: 'string' },
          programming_score: { type: 'integer', minimum: 1, maximum: 5 },
          programming_note: { type: 'string' },
          why: { type: 'string', description: 'Qué aporta que no esté ya en la tabla' },
          sources: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['exists', 'available_from_chile', 'price_ok', 'price_found', 'price_source', 'fits_project', 'under_budget', 'programming_ok', 'notes'],
  properties: {
    exists: { type: 'boolean' },
    available_from_chile: { type: 'boolean', description: 'true si hoy se puede comprar desde Chile con stock' },
    price_ok: { type: 'boolean', description: 'true si el precio propuesto es correcto (misma moneda, <15% de diferencia)' },
    price_found: { type: 'string' },
    price_source: { type: 'string' },
    fits_project: { type: 'boolean', description: 'true si de verdad aporta al nodo (varias cámaras IP + YOLO + nube)' },
    under_budget: { type: 'boolean', description: 'true si cabe en un nodo completo de menos de US$500' },
    programming_ok: { type: 'boolean', description: 'true si tiene SDK maduro y ejemplo YOLO' },
    notes: { type: 'string' },
  },
}

const PICK_SCHEMA = {
  type: 'object',
  required: ['picks', 'rejected'],
  properties: {
    picks: {
      type: 'array',
      maxItems: 3,
      items: { type: 'object', required: ['name', 'reason'], properties: { name: { type: 'string' }, reason: { type: 'string' } } },
    },
    rejected: {
      type: 'array',
      items: { type: 'object', required: ['name', 'reason'], properties: { name: { type: 'string' }, reason: { type: 'string' } } },
    },
  },
}

function findPrompt(quotation, angle) {
  return `Buscas productos NUEVOS para agregar a la cotización "${quotation.name}" (id ${quotation.id}) de un catálogo de hardware.

1. Lee el brief del proyecto: ${brief}
2. Lee el catálogo: ${catalog}. Mira qué ítems ya tiene esa cotización (no los repitas ni propongas variantes triviales) y cuáles son sus columnas extra (extra_fields), porque cada candidato debe traer valores para esas claves.

${TOOLS}

Ángulo de búsqueda que te toca: ${angle.prompt}

Reglas:
- Sólo productos que encajen en ESTA cotización (misma familia: ${quotation.family}).
- Menos de US$500 el producto, y que quepa en un nodo completo de menos de US$500.
- Alta disponibilidad y vendedor confiable; fáciles de programar (Linux, SDK maduro, ejemplo YOLO).
- Si esta cotización no necesita nada nuevo (por ejemplo, las cámaras AI no son el foco del proyecto), devuelve una lista vacía: agregar es opcional.
- Máximo 5 candidatos, los mejores. Cada uno con precio real (con fuente y fecha), link de compra vigente, foto (URL http/https) y valores para las columnas extra de la cotización.
- No inventes productos ni precios. Cita las URLs en sources.

Devuelve exclusivamente el objeto estructurado, en español neutro.`
}

function verifyPrompt(quotation, candidate) {
  return `Eres un escéptico. Un buscador propone agregar este producto a la cotización "${quotation.name}" (familia: ${quotation.family}):

Nombre: ${candidate.name}
Precio propuesto: ${candidate.price}
Link: ${candidate.purchase_link}
Disponibilidad afirmada: ${candidate.availability_chile}
Descripción: ${candidate.description}
Por qué: ${candidate.why}

Lee el brief (${brief}). ${TOOLS}

Comprueba por tu cuenta: que el producto existe con ese nombre, que HOY se puede comprar desde Chile (abre el link; busca también en MercadoLibre Chile y en distribuidores que despachen a Chile), que el precio es correcto, que de verdad sirve para un nodo que decodifique varias cámaras IP y corra YOLO y mande detecciones a la nube, que cabe en menos de US$500 el nodo completo, y que tiene SDK maduro con ejemplo de YOLO. Ante la duda, responde false. Devuelve exclusivamente el objeto estructurado.`
}

function pickPrompt(quotation, verified) {
  const rows = verified.map(candidate => `- ${candidate.name} · ${candidate.price} · ${candidate.availability_chile} · programación ${candidate.programming_score}/5\n    ${candidate.why}\n    verificación: ${candidate.verify.notes}`).join('\n')
  return `Eres el juez final para la cotización "${quotation.name}" (familia: ${quotation.family}). Lee el brief (${brief}) y el catálogo actual (${catalog}).

Candidatos ya verificados:
${rows}

Elige COMO MÁXIMO 3 para agregar. Criterios, en orden: que aporte algo que la tabla no tenga, disponibilidad alta comprando desde Chile, confiabilidad del producto y del vendedor, facilidad de programación, precio. Si ninguno vale la pena, devuelve picks vacío. Explica cada elección y cada rechazo. Devuelve exclusivamente el objeto estructurado.`
}

const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

const results = await pipeline(
  quotations,
  async quotation => {
    const found = (await parallel(ANGLES.map(angle => () =>
      agent(findPrompt(quotation, angle), { label: `buscar:${angle.key}:${quotation.id}`, phase: 'Buscar', schema: CANDIDATE_SCHEMA })))).filter(Boolean)
    const existing = new Set(quotation.existing.map(normalize))
    const seen = new Set()
    const unique = []
    found.flatMap(result => result.candidates || []).forEach(candidate => {
      const key = normalize(candidate.name)
      if (!key || seen.has(key) || existing.has(key)) return
      seen.add(key)
      unique.push(candidate)
    })
    const CAP = 8
    if (unique.length > CAP) log(`${quotation.id}: ${unique.length} candidatos, se verifican los primeros ${CAP}`)
    return { quotation, candidates: unique.slice(0, CAP), dropped: Math.max(0, unique.length - CAP) }
  },
  async ({ quotation, candidates, dropped }) => {
    const checked = await parallel(candidates.map(candidate => () =>
      agent(verifyPrompt(quotation, candidate), { label: `verificar:${candidate.name}`, phase: 'Verificar', schema: VERIFY_SCHEMA })
        .then(verify => ({ ...candidate, verify }))))
    const verified = checked.filter(Boolean).filter(candidate => {
      const v = candidate.verify
      return v && v.exists && v.available_from_chile && v.fits_project && v.under_budget && v.programming_ok
    })
    log(`${quotation.id}: ${candidates.length} candidatos, ${verified.length} pasan la verificación`)
    return { quotation, verified, rejected: checked.filter(Boolean).filter(candidate => !verified.includes(candidate)), dropped }
  },
  async ({ quotation, verified, rejected, dropped }) => {
    if (!verified.length) return { quotation_id: quotation.id, picks: [], rejected, dropped }
    const ruling = await agent(pickPrompt(quotation, verified), { label: `elegir:${quotation.id}`, phase: 'Elegir', schema: PICK_SCHEMA })
    const chosen = ruling ? ruling.picks.map(pick => {
      const candidate = verified.find(entry => normalize(entry.name) === normalize(pick.name))
      return candidate ? { ...candidate, pick_reason: pick.reason } : null
    }).filter(Boolean).slice(0, 3) : verified.slice(0, 3)
    return { quotation_id: quotation.id, picks: chosen, judge: ruling, rejected, dropped }
  },
)

const done = results.filter(Boolean)
const total = done.reduce((sum, entry) => sum + entry.picks.length, 0)
log(`${total} productos elegidos en ${done.length} cotizaciones`)
return { quotations: done, total }