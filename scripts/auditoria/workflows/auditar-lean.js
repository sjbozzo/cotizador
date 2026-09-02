export const meta = {
  name: 'auditar-lean',
  description: 'Auditoría ligera del catálogo: cada agente lee un archivo pequeño con 4 ítems, verifica con pocas búsquedas y devuelve precio, disponibilidad en Chile, ajuste al proyecto y ranura M.2',
  phases: [{ title: 'Auditar', detail: 'un agente por lote de 4 ítems, máximo ~6 búsquedas por ítem' }],
}

// args: { brief: string (texto corto, va dentro del prompt), batches: [{ q, file, names }] }
const { brief, batches } = args
log(`${batches.length} lotes`)

const SCHEMA = {
  type: 'object',
  required: ['items'],
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'name', 'verdict', 'verdict_reason', 'price', 'price_source', 'availability_chile', 'programming_score', 'programming_note', 'description', 'comment', 'country', 'purchase_link', 'spec_corrections', 'extra_data', 'm2', 'sources'],
        properties: {
          id: { type: 'string' },
          name: { type: 'string' },
          verdict: { type: 'string', enum: ['keep', 'discard', 'delete'] },
          verdict_reason: { type: 'string' },
          price: { type: 'string', description: 'Con moneda, fuente y fecha: "US$179 (Mouser, sep. 2026)"' },
          price_source: { type: 'string' },
          availability_chile: { type: 'string' },
          programming_score: { type: 'integer', minimum: 1, maximum: 5 },
          programming_note: { type: 'string' },
          description: { type: 'string' },
          comment: { type: 'string' },
          country: { type: 'string' },
          purchase_link: { type: 'string' },
          spec_corrections: { type: 'string' },
          extra_data: { type: 'object', additionalProperties: true },
          m2: {
            type: 'object',
            required: ['applies', 'key', 'length', 'pcie', 'free', 'ok_for_accelerator', 'note'],
            properties: {
              applies: { type: 'boolean' }, key: { type: 'string' }, length: { type: 'string' }, pcie: { type: 'string' },
              free: { type: 'boolean' }, ok_for_accelerator: { type: 'boolean' }, note: { type: 'string' },
            },
          },
          sources: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

function prompt(batch) {
  const m2 = batch.q === 'pcs-industriales-m2-ai' || batch.q === 'pares-pc-fanless-npu'
  return `Eres auditor técnico de un catálogo de hardware. Cotización: "${batch.q}". Ítems: ${batch.names.join(' · ')}.

BRIEF DEL PROYECTO
${brief}

Lee con Read el archivo ${batch.file}: trae los datos actuales de tus ítems (pueden estar mal) y las claves de las columnas extra de la cotización. No leas ningún otro archivo.

MÉTODO (sé económico: como máximo 6 búsquedas o fetch por ítem; usa WebSearch y WebFetch, si no las ves cárgalas con ToolSearch "select:WebSearch,WebFetch"):
1. Confirma que el producto existe y el nombre exacto; corrige el nombre si hace falta.
2. Precio actual con fuente y fecha (tienda oficial, Mouser, DigiKey, Amazon, AliExpress, MercadoLibre Chile).
3. Cómo se compra desde Chile y si hay stock (venta local o quién despacha a Chile).
4. Facilidad de programación 1-5 (SDK, Python/C++, ejemplo YOLO oficial, comunidad). Para empresas de servicios: 3 y "no aplica".
5. Ajuste al proyecto según el brief: keep / discard / delete. Los SunFounder Pironman se eliminan (pedido del usuario). Duplicados: se elimina el segundo.
${m2 ? '6. Ranura M.2 del PC: key, largo, PCIe lanes/gen o SATA, si queda libre (hay otra bahía para el disco) y si sirve para un acelerador Hailo-8 / DEEPX DX-M1 / Axelera / MemryX (M-key o B+M, 2242/2280, PCIe). Si no sirve, verdict = discard.' : '6. m2.applies = false (no es un PC industrial).'}
7. Redacta descripción (1-2 frases factuales: qué es, chip, TOPS, red, carcasa) y comentario (por qué sí o no para ESTE proyecto, riesgos, qué falta comprar) nuevos, en español neutro.
8. extra_data: SOLO claves que existan en las columnas extra de esta cotización; corrige lo que esté mal; vacío si nada cambia.

No inventes: si un dato no aparece, escribe "sin dato". Cita URLs en sources. Devuelve exclusivamente el objeto estructurado.`
}

const results = await parallel(batches.map((batch, index) => () =>
  agent(prompt(batch), { label: `auditar:${batch.q}#${index}`, phase: 'Auditar', schema: SCHEMA, effort: 'medium' })
    .then(result => (result && result.items ? { q: batch.q, index, items: result.items } : null))))

const done = results.filter(Boolean)
const lost = batches.length - done.length
if (lost) log(`${lost} lote(s) sin resultado`)
const all = done.flatMap(batch => batch.items)
log(`${all.length} ítems auditados · ${all.filter(item => item.verdict !== 'keep').length} bajas propuestas (se revisan a mano)`)
return { batches: done, summary: { audited: all.length, lost } }
