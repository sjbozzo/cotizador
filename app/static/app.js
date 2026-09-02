(() => {
  'use strict';

  const state = {
    quotations: [],
    activeId: localStorage.getItem('cotizador.activeQuotation') || '',
    editingItemId: null,
    creatingQuotation: false,
    photoChanged: false,
    pendingPhoto: null,
    importPreview: null,
    table: null,
    tableQuotationId: '',
    search: '',
    columnFilters: {},
    columnTypes: {},
    numericColumns: new Set(),
    columnMenuField: null,
    rowMenuItemId: null,
    moveMenuOpen: false,
    selectAllBox: null,
    lastSelectedId: null,
  };

  const $ = id => document.getElementById(id);
  const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const today = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  };
  const activeQuotation = () => state.quotations.find(quotation => quotation.id === state.activeId) || null;

  async function api(url, options = {}) {
    const headers = {...(options.headers || {})};
    if (options.body && typeof options.body !== 'string') {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }
    const response = await fetch(url, {...options, headers});
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === 'string') detail = payload.detail;
        else if (Array.isArray(payload.detail)) detail = payload.detail.map(entry => entry.msg).join('. ');
      } catch (_) {}
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function showToast(message, kind = 'success') {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.toggle('error', kind === 'error');
    toast.classList.add('show');
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove('show'), 3200);
  }

  async function loadQuotations(preferredId = state.activeId) {
    try {
      state.quotations = await api('/api/quotations');
      state.activeId = state.quotations.some(q => q.id === preferredId)
        ? preferredId
        : (state.quotations[0]?.id || '');
      if (state.activeId) localStorage.setItem('cotizador.activeQuotation', state.activeId);
      $('loading-state').hidden = true;
      renderAll();
    } catch (error) {
      $('loading-state').innerHTML = '';
      const message = document.createElement('p');
      message.textContent = `No fue posible cargar las cotizaciones: ${error.message}`;
      $('loading-state').append(message);
      showToast(error.message, 'error');
    }
  }

  function renderAll() {
    renderTabs();
    const quotation = activeQuotation();
    $('quotation-view').hidden = !quotation;
    $('blank-state').hidden = Boolean(quotation);
    renderActiveName(quotation);
    if (!quotation) return;
    renderQuotation(quotation);
  }

  function renderActiveName(quotation) {
    const label = $('active-quotation-name');
    label.textContent = quotation ? quotation.name : '';
    label.title = quotation ? `${quotation.name} · ${quotation.quote_date} · revisión ${quotation.revision}` : '';
    $('brand-sep').hidden = !quotation;
    $('edit-quotation').hidden = !quotation;
    $('header-actions').hidden = !quotation;
    if (!quotation) { closeMenus(); closeMoveMenu(); }
  }

  function renderTabs() {
    const tabs = $('quotation-tabs');
    tabs.replaceChildren();
    state.quotations.forEach(quotation => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `tab${quotation.id === state.activeId ? ' active' : ''}`;
      button.dataset.id = quotation.id;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', String(quotation.id === state.activeId));
      button.title = `${quotation.name} · arrástrala para cambiar el orden`;
      const name = document.createElement('span');
      name.className = 'tab-name';
      name.textContent = quotation.name;
      const count = document.createElement('span');
      count.className = 'tab-count';
      count.textContent = quotation.item_count;
      button.append(name, count);
      button.addEventListener('click', () => {
        // El clic que cierra un arrastre no debe cambiar de cotización.
        if (button.dataset.dragged === '1') return;
        activateQuotation(quotation.id);
      });
      button.addEventListener('pointerdown', event => startTabDrag(event, button));
      tabs.append(button);
    });
  }

  // --- Orden de las pestañas: se arrastra una y las demás se corren ---------

  const TAB_DRAG_THRESHOLD = 5;

  function startTabDrag(event, button) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (state.quotations.length < 2) return;
    const tabs = $('quotation-tabs');
    const startX = event.clientX;
    const before = [...tabs.children].map(tab => tab.dataset.id);
    let dragging = false;

    const move = pointerEvent => {
      if (!dragging) {
        if (Math.abs(pointerEvent.clientX - startX) < TAB_DRAG_THRESHOLD) return;
        dragging = true;
        button.classList.add('dragging');
        tabs.classList.add('reordering');
        closeMenus();
        closeMoveMenu();
        closeRowMenu();
        closeColumnMenu();
      }
      pointerEvent.preventDefault();
      const over = [...tabs.children].find(tab => {
        if (tab === button) return false;
        const rect = tab.getBoundingClientRect();
        return pointerEvent.clientX >= rect.left && pointerEvent.clientX <= rect.right;
      });
      if (!over) return;
      const rect = over.getBoundingClientRect();
      const after = pointerEvent.clientX > rect.left + rect.width / 2;
      tabs.insertBefore(button, after ? over.nextSibling : over);
    };

    const finish = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', finish);
      document.removeEventListener('pointercancel', finish);
      if (!dragging) return;
      button.classList.remove('dragging');
      tabs.classList.remove('reordering');
      // El click llega justo después del pointerup; se limpia en el siguiente turno.
      button.dataset.dragged = '1';
      setTimeout(() => { delete button.dataset.dragged; }, 0);
      const order = [...tabs.children].map(tab => tab.dataset.id);
      if (order.join('|') !== before.join('|')) saveTabOrder(order);
    };

    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', finish);
    document.addEventListener('pointercancel', finish);
  }

  /** Aplica el orden nuevo de inmediato y lo persiste; si falla, se revierte. */
  async function saveTabOrder(order) {
    const previous = state.quotations;
    const byId = new Map(previous.map(quotation => [quotation.id, quotation]));
    state.quotations = order.map(id => byId.get(id)).filter(Boolean);
    renderTabs();
    try {
      const saved = await api('/api/quotations/reorder', {method: 'POST', body: {order}});
      const positions = new Map(saved.map(quotation => [quotation.id, quotation.position]));
      state.quotations.forEach(quotation => {
        quotation.position = positions.get(quotation.id) ?? quotation.position;
      });
      showToast('Se guardó el nuevo orden de las cotizaciones');
    } catch (error) {
      state.quotations = previous;
      renderTabs();
      showToast(error.message, 'error');
    }
  }

  function activateQuotation(id) {
    state.activeId = id;
    localStorage.setItem('cotizador.activeQuotation', id);
    resetFilters();
    renderAll();
    document.querySelector('.tab.active')?.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
  }

  function renderQuotation(quotation) {
    renderActiveName(quotation);
    renderIntro(quotation);
    renderTable(quotation);
  }

  /** Descripción de la cotización, arriba de la tabla. */
  function renderIntro(quotation) {
    const section = $('quotation-intro');
    const body = $('quotation-intro-text');
    const text = (quotation.description || '').trim();
    body.replaceChildren();
    section.hidden = !text;
    if (!text) return;
    text.split(/\n\s*\n/).forEach(block => {
      const paragraph = document.createElement('p');
      paragraph.textContent = block.replace(/\s*\n\s*/g, ' ').trim();
      if (paragraph.textContent) body.append(paragraph);
    });
  }

  // ---------------------------------------------------------------------------
  // Tabla estilo Excel: selección múltiple, menú por columna y orden con lógica.
  // ---------------------------------------------------------------------------

  // Aproximación para poder comparar precios en USD contra precios en CLP.
  // Cambiá este número si el tipo de cambio se movió mucho.
  const CLP_PER_USD = 950;

  /** Extrae el primer número de un texto libre, resolviendo separadores mixtos. */
  function parseNumber(raw) {
    const token = String(raw).match(/\d[\d.,]*/);
    if (!token) return null;
    let value = token[0].replace(/[.,]$/, '');
    if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(value)) value = value.replace(/\./g, '').replace(',', '.');
    else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(value)) value = value.replace(/,/g, '');
    else if (value.includes(',') && !value.includes('.')) value = value.replace(',', '.');
    else value = value.replace(/,/g, '');
    const number = Number.parseFloat(value);
    return Number.isFinite(number) ? number : null;
  }

  /** Precio comparable: prefiere la cifra en CLP; si sólo hay USD la convierte.
   *  En un rango toma el extremo inferior, que es como se lee "desde". */
  function priceValue(raw) {
    const text = String(raw ?? '');
    if (!text.trim()) return null;
    const tagged = text.replace(/US\s?\$/gi, 'USD ');
    if (/CLP/i.test(tagged)) {
      const clp = tagged.match(/\$\s?([\d.,]+)/);
      if (clp) {
        const number = parseNumber(clp[1]);
        if (number !== null) return number;
      }
    }
    const usd = tagged.match(/USD\s?([\d.,]+)/i);
    if (usd) {
      const number = parseNumber(usd[1]);
      if (number !== null) return number * CLP_PER_USD;
    }
    const plain = tagged.match(/\$\s?([\d.,]+)/);
    if (plain) return parseNumber(plain[1]);
    return parseNumber(tagged);
  }

  /** Ordena numéricamente cuando el texto empieza con un número ("~0.35 TOPS"), si no alfabético. */
  function smartSorter(a, b) {
    const left = parseNumber(a);
    const right = parseNumber(b);
    const leftEmpty = a === null || a === undefined || a === '';
    const rightEmpty = b === null || b === undefined || b === '';
    if (leftEmpty && rightEmpty) return 0;
    if (leftEmpty) return 1;
    if (rightEmpty) return -1;
    if (left !== null && right !== null && left !== right) return left - right;
    if (left !== null && right === null) return -1;
    if (left === null && right !== null) return 1;
    return String(a).localeCompare(String(b), 'es', {numeric: true, sensitivity: 'base'});
  }

  function priceSorter(a, b) {
    const left = priceValue(a);
    const right = priceValue(b);
    if (left === null && right === null) return String(a || '').localeCompare(String(b || ''), 'es');
    if (left === null) return 1;
    if (right === null) return -1;
    return left - right;
  }

  function fieldValue(item, field) {
    if (field.startsWith('extra_data.')) return (item.extra_data || {})[field.slice(11)];
    return item[field];
  }

  function displayValue(item, field, type) {
    const value = fieldValue(item, field);
    if (type === 'boolean') return value === true ? 'Sí' : value === false ? 'No' : '';
    return value === null || value === undefined ? '' : String(value);
  }

  // --- Filtros por columna (se aplican todos juntos con el buscador global) ---

  function passesFilters(item) {
    if (state.search) {
      const haystack = normalize([
        item.name, item.country, item.price, item.description, item.comment,
        ...Object.values(item.extra_data || {}),
      ].join(' '));
      if (!haystack.includes(state.search)) return false;
    }
    for (const [field, allowed] of Object.entries(state.columnFilters)) {
      if (!allowed || !allowed.size) continue;
      const meta = state.columnTypes[field];
      if (!allowed.has(displayValue(item, field, meta))) return false;
    }
    return true;
  }

  function refreshFilters() {
    if (!state.table) return;
    const active = Object.values(state.columnFilters).some(set => set && set.size);
    if (active || state.search) state.table.setFilter(passesFilters);
    else state.table.clearFilter();
    document.querySelectorAll('.tabulator-col').forEach(element => {
      const field = element.getAttribute('tabulator-field');
      const set = field && state.columnFilters[field];
      element.classList.toggle('has-filter', Boolean(set && set.size));
    });
    updateVisibleCount();
  }

  // --- Menú de columna, al estilo del filtro de Excel ---

  function closeColumnMenu() {
    const menu = $('column-menu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    menu.replaceChildren();
    state.columnMenuField = null;
  }

  function openColumnMenu(anchor, column) {
    const field = column.getField();
    if (state.columnMenuField === field) { closeColumnMenu(); return; }
    closeColumnMenu();
    closeRowMenu();
    closeMenus();
    const menu = $('column-menu');
    const type = state.columnTypes[field];
    const numeric = type === 'number' || field === 'price' || state.numericColumns.has(field);
    menu.replaceChildren();

    const actions = document.createElement('div');
    actions.className = 'column-menu-actions';
    [
      [numeric ? 'Menor a mayor' : 'A → Z', 'asc'],
      [numeric ? 'Mayor a menor' : 'Z → A', 'desc'],
    ].forEach(([label, dir]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'column-menu-action';
      button.textContent = label;
      button.addEventListener('click', () => { state.table.setSort([{column: field, dir}]); closeColumnMenu(); });
      actions.append(button);
    });
    const clearSort = document.createElement('button');
    clearSort.type = 'button';
    clearSort.className = 'column-menu-action quiet';
    clearSort.textContent = 'Quitar orden';
    clearSort.addEventListener('click', () => { state.table.clearSort(); closeColumnMenu(); });
    actions.append(clearSort);
    menu.append(actions);

    const quotation = activeQuotation();
    const values = [...new Set((quotation?.items || []).map(item => displayValue(item, field, type)))]
      .sort((a, b) => (numeric ? smartSorter(a, b) : String(a).localeCompare(String(b), 'es', {numeric: true})));
    const selected = state.columnFilters[field] || new Set(values);

    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'column-menu-search';
    search.placeholder = 'Buscar valores…';
    menu.append(search);

    const list = document.createElement('div');
    list.className = 'column-menu-list';
    menu.append(list);

    const draft = new Set(selected);

    function apply() {
      if (draft.size === values.length) delete state.columnFilters[field];
      else state.columnFilters[field] = new Set(draft);
      refreshFilters();
    }

    function paint() {
      const query = normalize(search.value);
      const shown = values.filter(value => !query || normalize(value).includes(query));
      list.replaceChildren();

      const allRow = document.createElement('label');
      allRow.className = 'column-menu-item all';
      const allBox = document.createElement('input');
      allBox.type = 'checkbox';
      allBox.checked = shown.every(value => draft.has(value));
      allBox.indeterminate = !allBox.checked && shown.some(value => draft.has(value));
      allBox.addEventListener('change', () => {
        shown.forEach(value => allBox.checked ? draft.add(value) : draft.delete(value));
        apply();
        paint();
      });
      const allText = document.createElement('span');
      allText.textContent = query ? `Todos los visibles (${shown.length})` : `Seleccionar todo (${values.length})`;
      allRow.append(allBox, allText);
      list.append(allRow);

      shown.forEach(value => {
        const row = document.createElement('label');
        row.className = 'column-menu-item';
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = draft.has(value);
        box.addEventListener('change', () => {
          if (box.checked) draft.add(value); else draft.delete(value);
          apply();
          paint();
        });
        const text = document.createElement('span');
        text.textContent = value === '' ? '(vacío)' : value;
        text.title = text.textContent;
        row.append(box, text);
        list.append(row);
      });
      if (!shown.length) {
        const empty = document.createElement('p');
        empty.className = 'column-menu-empty';
        empty.textContent = 'Sin coincidencias';
        list.append(empty);
      }
    }

    search.addEventListener('input', paint);
    paint();

    const footer = document.createElement('div');
    footer.className = 'column-menu-foot';
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'column-menu-action quiet';
    clear.textContent = 'Limpiar filtro';
    clear.addEventListener('click', () => {
      delete state.columnFilters[field];
      refreshFilters();
      closeColumnMenu();
    });
    footer.append(clear);
    menu.append(footer);

    menu.hidden = false;
    placeFloating(menu, anchor);
    state.columnMenuField = field;
    search.focus();
  }

  /** Posiciona un popup fijo junto a su disparador, sin salirse de la ventana. */
  function placeFloating(menu, anchor) {
    const rect = anchor.getBoundingClientRect();
    const {offsetWidth: width, offsetHeight: height} = menu;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const top = rect.bottom + 4 + height > window.innerHeight
      ? Math.max(8, rect.top - height - 4)
      : rect.bottom + 4;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  // --- Celdas ---

  // --- Marcado de filas -------------------------------------------------------
  // Tabulator maneja el clic por su cuenta y en modo rango sólo sabe *agregar*:
  // volver a pulsar la casilla no desmarcaba nunca. Acá se apaga ese manejo
  // ('highlight' deja la API de selección pero sin listeners propios) y el clic
  // lo resuelve la app, que sí distingue marcar de desmarcar.

  function setRowSelected(row, selected) {
    if (selected) row.select(); else row.deselect();
  }

  /** Rango con Shift, tomando el orden que se ve (filtrado y ordenado incluidos). */
  function selectRowRange(row) {
    const rows = state.table.getRows('active');
    const ids = rows.map(current => current.getData().id);
    const to = ids.indexOf(row.getData().id);
    const from = state.lastSelectedId ? ids.indexOf(state.lastSelectedId) : -1;
    if (to < 0) return;
    const [start, end] = from < 0 ? [to, to] : from <= to ? [from, to] : [to, from];
    state.table.deselectRow();
    state.table.selectRow(rows.slice(start, end + 1));
  }

  function handleRowClick(event, row) {
    // La foto abre el ítem y los controles (casilla, enlace, ⋮) se manejan solos.
    if (event.target.closest?.('.photo-cell, .kebab-cell, input, a, button')) return;
    if (event.shiftKey) {
      selectRowRange(row);
      return;
    }
    // Ctrl/⌘ o el clic dentro de la celda "Incluir" suman o restan de lo marcado;
    // un clic normal en la fila deja marcada sólo esa, como en una planilla.
    if (event.ctrlKey || event.metaKey || event.target.closest?.('.include-cell')) {
      setRowSelected(row, !row.isSelected());
    } else {
      state.table.deselectRow();
      row.select();
    }
    state.lastSelectedId = row.getData().id;
  }

  /** Casilla por fila: marca y desmarca sin tocar el resto de la selección. */
  function rowSelectFormatter(cell) {
    const row = cell.getRow();
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'row-select-box';
    box.checked = row.isSelected();
    box.setAttribute('aria-label', `Marcar ${row.getData().name}`);
    box.addEventListener('click', event => event.stopPropagation());
    box.addEventListener('change', () => {
      setRowSelected(row, box.checked);
      state.lastSelectedId = row.getData().id;
    });
    return box;
  }

  /** Las filas se reciclan al hacer scroll: hay que reflejar el estado real. */
  function syncRowCheckboxes() {
    if (!state.table) return;
    state.table.getRows().forEach(row => {
      const box = row.getElement()?.querySelector('.row-select-box');
      if (box) box.checked = row.isSelected();
    });
  }

  /** Encabezado de la columna Incluir: casilla de "marcar todo lo visible" + título.
   *  La casilla sólo marca filas; el estado incluido/descartado lo cambian los
   *  botones del pie de la tabla. */
  function selectAllFormatter() {
    const head = document.createElement('span');
    head.className = 'include-head';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'include-box';
    box.title = 'Marcar todo lo visible';
    box.setAttribute('aria-label', 'Marcar todo lo visible');
    box.addEventListener('click', event => event.stopPropagation());
    box.addEventListener('change', () => {
      if (!state.table) return;
      if (box.checked) state.table.selectRow(state.table.getRows('active'));
      else state.table.deselectRow();
    });
    const label = document.createElement('span');
    label.textContent = 'Incluir';
    head.append(box, label);
    state.selectAllBox = box;
    return head;
  }

  /** Deja la casilla del encabezado en marcado / indeterminado / vacío. */
  function syncSelectAll() {
    const box = state.selectAllBox;
    if (!box || !box.isConnected) return;
    if (!state.table) { box.checked = false; box.indeterminate = false; return; }
    const visible = state.table.getRows('active');
    const marked = visible.filter(row => row.isSelected()).length;
    box.checked = visible.length > 0 && marked === visible.length;
    box.indeterminate = marked > 0 && marked < visible.length;
  }

  function photoFallback() {
    const fallback = document.createElement('span');
    fallback.className = 'photo-fallback';
    fallback.textContent = 'Sin foto';
    return fallback;
  }

  function photoFormatter(cell) {
    const source = cell.getValue();
    if (!source) return photoFallback();
    const image = document.createElement('img');
    image.className = 'item-photo';
    image.src = source;
    image.alt = '';
    image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => image.replaceWith(photoFallback()));
    return image;
  }

  function nameFormatter(cell) {
    const name = cell.getValue() || '';
    const href = cell.getRow().getData().purchase_link;
    if (!href) {
      const plain = document.createElement('span');
      plain.textContent = name;
      return plain;
    }
    const link = document.createElement('a');
    link.className = 'item-name-link';
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = name;
    link.title = `Abrir la página de ${name}`;
    // El clic navega y no debe además cambiar lo marcado en la tabla. El
    // segundo clic de un doble clic se ignora, para no abrir dos pestañas.
    let lastClick = 0;
    link.addEventListener('click', event => {
      event.stopPropagation();
      const now = Date.now();
      if (now - lastClick < 400) event.preventDefault();
      lastClick = now;
    });
    return link;
  }

  function linkFormatter(cell) {
    const href = cell.getValue();
    if (!href) return '';
    const link = document.createElement('a');
    link.className = 'table-link';
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'Abrir';
    link.addEventListener('click', event => event.stopPropagation());
    return link;
  }

  function kebabFormatter(cell) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kebab-button';
    button.innerHTML = '<span aria-hidden="true">⋮</span>';
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-label', `Acciones de ${cell.getRow().getData().name}`);
    button.addEventListener('click', event => {
      event.stopPropagation();
      openRowMenu(button, cell.getRow());
    });
    return button;
  }

  // --- Menú de fila (los tres puntos) ---

  function closeRowMenu() {
    const menu = $('row-menu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    menu.replaceChildren();
    state.rowMenuItemId = null;
  }

  function openRowMenu(anchor, row) {
    const item = row.getData();
    if (state.rowMenuItemId === item.id) { closeRowMenu(); return; }
    closeRowMenu();
    closeColumnMenu();
    closeMenus();
    const menu = $('row-menu');
    menu.replaceChildren();

    const selected = state.table.getSelectedRows();
    const many = selected.length > 1 && selected.some(current => current.getData().id === item.id);
    const targets = state.quotations.filter(quotation => quotation.id !== item.quotation_id);

    const rows = many ? selected : [row];
    const willInclude = !rows.every(current => current.getData().included);

    const entries = [];
    if (!many) entries.push(['Editar ítem', () => openItemModal(item)]);
    entries.push([
      many
        ? `${willInclude ? 'Incluir' : 'Descartar'} ${selected.length} ítems`
        : (willInclude ? 'Incluir ítem' : 'Descartar ítem'),
      () => bulkInclude(willInclude, rows),
    ]);
    entries.push([
      many ? `Mover ${selected.length} ítems a…` : 'Mover a otra cotización',
      () => openMoveMenu(anchor, many ? selected.map(current => current.getData()) : [item]),
      targets.length === 0,
    ]);
    if (!many) {
      entries.push(['Exportar sólo este ítem', () => {
        const quotation = activeQuotation();
        if (!quotation) return;
        downloadUrl(`/api/quotations/${encodeURIComponent(quotation.id)}/export.html?item_id=${encodeURIComponent(item.id)}`);
      }]);
    }
    entries.push([
      many ? `Eliminar ${selected.length} ítems` : 'Eliminar ítem',
      () => deleteItems(many ? selected.map(current => current.getData()) : [item]),
      false,
      true,
    ]);

    entries.forEach(([label, action, disabled, danger]) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = `menu-item plain${danger ? ' danger' : ''}`;
      option.setAttribute('role', 'menuitem');
      option.textContent = label;
      option.disabled = Boolean(disabled);
      option.addEventListener('click', event => {
        event.stopPropagation();
        closeRowMenu();
        action();
      });
      menu.append(option);
    });

    menu.hidden = false;
    placeFloating(menu, anchor);
    state.rowMenuItemId = item.id;
    menu.querySelector('.menu-item:not(:disabled)')?.focus();
  }

  // --- Columnas ---

  function extraColumn(field) {
    const numeric = field.type === 'number';
    return {
      title: field.label,
      field: `extra_data.${field.key}`,
      minWidth: 110,
      hozAlign: field.type === 'boolean' ? 'center' : numeric ? 'right' : 'left',
      sorter: field.type === 'boolean' ? 'boolean' : field.type === 'date' ? 'date' : smartSorter,
      formatter: field.type === 'boolean'
        ? cell => (cell.getValue() === true ? 'Sí' : cell.getValue() === false ? 'No' : '')
        : undefined,
    };
  }

  function buildColumns(quotation) {
    return [
      {
        title: 'Incluir',
        field: 'included',
        formatter: rowSelectFormatter,
        titleFormatter: selectAllFormatter,
        width: 96,
        minWidth: 96,
        hozAlign: 'left',
        headerHozAlign: 'left',
        sorter: 'boolean',
        cssClass: 'include-cell',
      },
      {
        title: 'Foto',
        field: 'photo',
        width: 74,
        headerSort: false,
        formatter: photoFormatter,
        cssClass: 'photo-cell',
        // La foto es el atajo para abrir el ítem, igual que el doble clic.
        cellClick: (event, cell) => openItemModal(cell.getRow().getData()),
      },
      {title: 'Nombre', field: 'name', minWidth: 220, widthGrow: 2, sorter: smartSorter, formatter: nameFormatter, cssClass: 'item-name-cell'},
      {title: 'Precio', field: 'price', minWidth: 150, sorter: priceSorter},
      {title: 'Descripción', field: 'description', minWidth: 200, widthGrow: 2, sorter: smartSorter, cssClass: 'text-cell'},
      {title: 'Comentario', field: 'comment', minWidth: 200, widthGrow: 2, sorter: smartSorter, cssClass: 'text-cell'},
      {title: 'País de origen', field: 'country', minWidth: 130, sorter: smartSorter},
      ...quotation.extra_fields.map(extraColumn),
      {title: 'Link', field: 'purchase_link', width: 64, headerSort: false, hozAlign: 'center', formatter: linkFormatter},
      {title: '', field: '_actions', width: 42, headerSort: false, hozAlign: 'center', cssClass: 'kebab-cell', formatter: kebabFormatter},
    ];
  }

  /** Cuelga el disparador del menú de columna en cada encabezado ordenable. */
  function decorateHeaders() {
    if (!state.table) return;
    state.table.getColumns().forEach(column => {
      const field = column.getField();
      if (!field || field.startsWith('_') || field === 'photo' || field === 'purchase_link') return;
      const element = column.getElement();
      const content = element.querySelector('.tabulator-col-content');
      if (!content || content.querySelector('.column-menu-trigger')) return;
      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'column-menu-trigger';
      trigger.innerHTML = '<span aria-hidden="true">▾</span>';
      trigger.title = `Ordenar y filtrar por ${column.getDefinition().title}`;
      trigger.setAttribute('aria-label', trigger.title);
      trigger.addEventListener('click', event => {
        event.stopPropagation();
        openColumnMenu(trigger, column);
      });
      content.append(trigger);
    });
  }

  const selectedRows = () => (state.table ? state.table.getSelectedRows() : []);
  const selectedItems = () => selectedRows().map(row => row.getData());

  /** Los botones del pie viven siempre ahí y se apagan cuando no hay nada marcado. */
  function updateSelectionActions() {
    const count = selectedRows().length;
    const move = $('selection-move');
    move.disabled = count === 0 || state.quotations.length < 2;
    move.title = state.quotations.length < 2 ? 'No hay otra cotización a la que mover' : '';
    $('selection-include').disabled = count === 0;
    $('selection-exclude').disabled = count === 0;
    $('selection-delete').disabled = count === 0;
    if (!count) closeMoveMenu();
    syncSelectAll();
    syncRowCheckboxes();
  }

  function updateVisibleCount() {
    const quotation = activeQuotation();
    if (!quotation || !state.table) return;
    let visible = quotation.items.length;
    try { visible = state.table.getDataCount('active'); } catch (_) {}
    const filtered = Object.values(state.columnFilters).some(set => set && set.size) || Boolean(state.search);
    $('visible-count').textContent = `${visible} de ${quotation.items.length} elementos visibles`;
    $('clear-filters').hidden = !filtered;
  }

  function renderTable(quotation) {
    if (!quotation) return;
    closeRowMenu();
    closeColumnMenu();
    closeMoveMenu();
    if (state.tableQuotationId !== quotation.id) {
      state.columnFilters = {};
      state.search = '';
      const search = $('filter-search');
      if (search) search.value = '';
    }
    state.columnTypes = {included: 'boolean'};
    state.numericColumns = new Set(['price']);
    quotation.extra_fields.forEach(field => {
      state.columnTypes[`extra_data.${field.key}`] = field.type;
      const values = quotation.items.map(item => (item.extra_data || {})[field.key]).filter(
        value => value !== null && value !== undefined && value !== '');
      const numericish = values.filter(value => parseNumber(value) !== null).length;
      if (field.type === 'number' || (values.length && numericish / values.length >= 0.6)) {
        state.numericColumns.add(`extra_data.${field.key}`);
      }
    });

    if (state.table) { state.table.destroy(); state.table = null; }
    state.tableQuotationId = quotation.id;
    state.table = new Tabulator('#items-table', {
      data: quotation.items,
      columns: buildColumns(quotation),
      index: 'id',
      layout: 'fitDataStretch',
      height: '100%',
      placeholder: 'No hay elementos para estos filtros.',
      headerSortTristate: true,
      // 'highlight' mantiene la API de selección pero sin los listeners de clic
      // de Tabulator: el marcado lo decide handleRowClick / la casilla de la fila.
      selectableRows: 'highlight',
      columnDefaults: {resizable: 'header', tooltip: true, headerTooltip: true},
      rowFormatter: row => {
        row.getElement().classList.toggle('excluded', !row.getData().included);
      },
    });
    state.lastSelectedId = null;
    state.table.on('tableBuilt', () => {
      decorateHeaders();
      refreshFilters();
      updateSelectionActions();
    });
    state.table.on('renderComplete', () => { decorateHeaders(); syncRowCheckboxes(); });
    state.table.on('dataFiltered', () => { updateVisibleCount(); syncSelectAll(); });
    state.table.on('rowSelectionChanged', updateSelectionActions);
    state.table.on('rowClick', handleRowClick);
    // Como opción del constructor rowDblClick no se dispara en Tabulator 6:
    // los callbacks de evento sólo se atienden si se suscriben con .on().
    state.table.on('rowDblClick', (event, row) => openItemModal(row.getData()));
  }

  function applySearch(value) {
    state.search = normalize(value);
    refreshFilters();
  }

  function resetFilters() {
    state.search = '';
    state.columnFilters = {};
    const search = $('filter-search');
    if (search) search.value = '';
    if (state.table) {
      try { state.table.clearSort(); } catch (_) {}
    }
    refreshFilters();
  }

  async function bulkInclude(included, rows = selectedRows()) {
    if (!rows.length) return;
    const quotation = activeQuotation();
    let done = 0;
    for (const row of rows) {
      const item = row.getData();
      if (item.included === included) continue;
      try {
        await api(`/api/items/${encodeURIComponent(item.id)}`, {method: 'PATCH', body: {included}});
        const stored = quotation?.items.find(current => current.id === item.id);
        if (stored) stored.included = included;
        row.update({included});
        row.getElement().classList.toggle('excluded', !included);
        done += 1;
      } catch (error) {
        showToast(error.message, 'error');
        break;
      }
    }
    if (quotation) {
      quotation.included_count = quotation.items.filter(item => item.included).length;
      quotation.revision += 1;
    }
    refreshFilters();
    showToast(`${done} ítem(s) ${included ? 'incluidos' : 'descartados'}`);
  }

  async function deleteItems(items) {
    const label = items.length === 1 ? `“${items[0].name}”` : `${items.length} ítems`;
    if (!confirm(`¿Eliminar ${label} de forma permanente?`)) return;
    try {
      for (const item of items) {
        await api(`/api/items/${encodeURIComponent(item.id)}`, {method: 'DELETE'});
      }
      showToast(items.length === 1 ? 'Ítem eliminado' : `${items.length} ítems eliminados`);
      await loadQuotations(state.activeId);
    } catch (error) {
      showToast(error.message, 'error');
    }
  }

  // ---------------------------------------------------------------------------
  // Menús emergentes: Exportar en la cabecera, y mover a otra cotización.
  // ---------------------------------------------------------------------------

  function closeMenus(except = null) {
    document.querySelectorAll('.menu-wrap .menu').forEach(menu => {
      if (menu === except || menu.hidden) return;
      menu.hidden = true;
      menu.parentElement?.querySelector('[aria-haspopup="menu"]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMenu(trigger) {
    const menu = trigger.parentElement.querySelector('.menu');
    const opening = menu.hidden;
    closeMenus(opening ? menu : null);
    closeMoveMenu();
    closeRowMenu();
    closeColumnMenu();
    menu.hidden = !opening;
    trigger.setAttribute('aria-expanded', String(opening));
    if (opening) menu.querySelector('.menu-item')?.focus();
  }

  function closeMoveMenu() {
    const menu = $('move-menu');
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    menu.replaceChildren();
    state.moveMenuOpen = false;
  }

  /** Acepta uno o varios ítems: el mismo menú sirve para la fila y para la selección. */
  function openMoveMenu(anchor, items) {
    closeMenus();
    closeColumnMenu();
    const wasOpen = state.moveMenuOpen;
    closeMoveMenu();
    if (wasOpen) return;
    if (!items.length) return;
    const origins = new Set(items.map(item => item.quotation_id));
    const targets = state.quotations.filter(quotation => !origins.has(quotation.id) || origins.size > 1);
    if (!targets.length) { showToast('No hay otra cotización a la que mover', 'error'); return; }

    const menu = $('move-menu');
    menu.replaceChildren();
    const title = document.createElement('p');
    title.className = 'menu-title';
    title.textContent = items.length === 1
      ? `Mover “${items[0].name}” a:`
      : `Mover ${items.length} ítems a:`;
    menu.append(title);

    targets.forEach(target => {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'menu-item';
      option.setAttribute('role', 'menuitem');
      const name = document.createElement('strong');
      name.textContent = target.name;
      const detail = document.createElement('span');
      detail.textContent = `${target.item_count} ítems · ${target.quote_date}`;
      option.append(name, detail);
      option.addEventListener('click', event => {
        event.stopPropagation();
        closeMoveMenu();
        moveItemsTo(items, target);
      });
      menu.append(option);
    });

    menu.hidden = false;
    placeFloating(menu, anchor);
    state.moveMenuOpen = true;
    menu.querySelector('.menu-item')?.focus();
  }

  async function moveItemsTo(items, target) {
    const movable = items.filter(item => item.quotation_id !== target.id);
    if (!movable.length) { showToast('Esos ítems ya están en esa cotización', 'error'); return; }
    let moved = 0;
    const failures = [];
    for (const item of movable) {
      try {
        await api(`/api/items/${encodeURIComponent(item.id)}/move`, {
          method: 'POST', body: {quotation_id: target.id},
        });
        moved += 1;
      } catch (error) {
        failures.push(`${item.name}: ${error.message}`);
      }
    }
    const hidden = new Set();
    movable.forEach(item => Object.keys(item.extra_data || {}).forEach(key => {
      if (!target.extra_fields.some(field => field.key === key)) hidden.add(key);
    }));
    if (failures.length) {
      showToast(`Se movieron ${moved} de ${movable.length}. Falló: ${failures[0]}`, 'error');
    } else {
      showToast(hidden.size
        ? `${moved} ítem(s) movidos a ${target.name}. ${hidden.size} campo(s) quedan ocultos hasta definirlos allí.`
        : `${moved} ítem(s) movidos a ${target.name}`);
    }
    await loadQuotations(state.activeId);
  }

  function openItemModal(item = null) {
    const quotation = activeQuotation();
    if (!quotation) return;
    state.editingItemId = item?.id || null;
    state.photoChanged = false;
    state.pendingPhoto = null;
    $('item-modal-kicker').textContent = quotation.name;
    $('item-modal-title').textContent = item ? 'Editar ítem' : 'Agregar ítem';
    $('save-item').textContent = item ? 'Guardar cambios' : 'Agregar ítem';
    $('delete-item').hidden = !item;
    $('export-item').hidden = !item;
    const moveTargets = state.quotations.filter(other => other.id !== quotation.id);
    const canMove = Boolean(item) && moveTargets.length > 0;
    $('move-item-field').hidden = !canMove;
    $('move-item').hidden = !canMove;
    const moveSelect = $('move-item-target');
    moveSelect.replaceChildren();
    moveTargets.forEach(target => moveSelect.add(new Option(target.name, target.id)));
    $('item-name').value = item?.name || '';
    $('item-price').value = item?.price || '';
    $('item-country').value = item?.country || '';
    $('item-link').value = item?.purchase_link || '';
    $('item-description').value = item?.description || '';
    $('item-comment').value = item?.comment || '';
    $('item-included').checked = item?.included ?? true;
    $('item-photo').value = item?.photo_url || '';
    $('item-photo-file').value = '';
    renderPhotoPreview(item?.photo || '');
    renderExtraInputs(quotation, item);
    $('item-modal').showModal();
    setTimeout(() => $('item-name').focus(), 0);
  }

  function renderPhotoPreview(source) {
    const preview = $('item-photo-preview');
    preview.replaceChildren();
    if (!source) {
      const span = document.createElement('span');
      span.textContent = 'Sin foto';
      preview.append(span);
      return;
    }
    const image = document.createElement('img');
    image.src = source;
    image.alt = 'Vista previa';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => {
      preview.replaceChildren();
      const span = document.createElement('span');
      span.textContent = 'No se pudo cargar';
      preview.append(span);
    });
    preview.append(image);
  }

  function renderExtraInputs(quotation, item) {
    const container = $('item-extra-fields');
    container.replaceChildren();
    if (!quotation.extra_fields.length) return;
    const title = document.createElement('p');
    title.className = 'extra-form-title';
    title.textContent = 'Campos específicos de esta cotización';
    container.append(title);
    quotation.extra_fields.forEach(field => {
      const wrapper = document.createElement('div');
      wrapper.className = 'extra-input';
      const label = document.createElement('label');
      label.textContent = field.label;
      const input = document.createElement('input');
      input.dataset.extraKey = field.key;
      input.dataset.extraType = field.type;
      const value = item?.extra_data?.[field.key];
      if (field.type === 'boolean') {
        input.type = 'checkbox';
        input.checked = value === true;
        input.style.width = '18px';
        input.style.height = '18px';
      } else {
        input.type = field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : field.type === 'date' ? 'date' : 'text';
        input.value = value ?? '';
        if (field.type === 'number') input.step = 'any';
      }
      wrapper.append(label, input);
      container.append(wrapper);
    });
  }

  function itemPayload() {
    const extraData = {};
    $('item-extra-fields').querySelectorAll('[data-extra-key]').forEach(input => {
      let value;
      if (input.dataset.extraType === 'boolean') value = input.checked;
      else if (input.dataset.extraType === 'number') value = input.value === '' ? null : Number(input.value);
      else value = input.value.trim();
      extraData[input.dataset.extraKey] = value;
    });
    const payload = {
      name: $('item-name').value.trim(),
      price: $('item-price').value.trim(),
      country: $('item-country').value.trim(),
      purchase_link: $('item-link').value.trim(),
      description: $('item-description').value.trim(),
      comment: $('item-comment').value.trim(),
      included: $('item-included').checked,
      extra_data: extraData,
    };
    if (state.photoChanged) payload.photo = state.pendingPhoto ?? $('item-photo').value.trim();
    if (!state.editingItemId && !state.photoChanged) payload.photo = $('item-photo').value.trim();
    return payload;
  }

  async function saveItem(event) {
    event.preventDefault();
    const quotation = activeQuotation();
    if (!quotation) return;
    const button = $('save-item');
    button.disabled = true;
    try {
      const payload = itemPayload();
      if (state.editingItemId) {
        await api(`/api/items/${encodeURIComponent(state.editingItemId)}`, {method: 'PATCH', body: payload});
        showToast('Ítem actualizado');
      } else {
        await api(`/api/quotations/${encodeURIComponent(quotation.id)}/items`, {method: 'POST', body: payload});
        showToast('Ítem agregado');
      }
      $('item-modal').close();
      await loadQuotations(quotation.id);
    } catch (error) {
      showToast(error.message, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async function deleteItem() {
    const quotation = activeQuotation();
    const item = quotation?.items.find(current => current.id === state.editingItemId);
    if (!item) return;
    $('item-modal').close();
    await deleteItems([item]);
  }

  async function compressPhoto(file) {
    if (file.size > 12 * 1024 * 1024) throw new Error('La foto seleccionada supera 12 MB');
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', .8));
    if (!blob) throw new Error('No fue posible comprimir la foto');
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('No fue posible leer la foto'));
      reader.readAsDataURL(blob);
    });
  }

  function openQuotationModal(create = false) {
    const quotation = activeQuotation();
    state.creatingQuotation = create;
    $('quotation-modal-title').textContent = create ? 'Nueva cotización' : 'Editar cotización';
    $('delete-quotation').hidden = create;
    $('quote-name').value = create ? '' : quotation?.name || '';
    $('quote-date').value = create ? today() : quotation?.quote_date || today();
    $('quote-link').value = create ? '' : quotation?.purchase_link || '';
    $('quote-description').value = create ? '' : quotation?.description || '';
    $('quote-extra-fields').value = create ? '' : (quotation?.extra_fields || [])
      .map(field => `${field.key}:${field.type} | ${field.label}`).join('\n');
    $('quotation-modal').showModal();
    setTimeout(() => $('quote-name').focus(), 0);
  }

  function parseExtraDefinitions() {
    return $('quote-extra-fields').value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
      const [definition, providedLabel] = line.split('|', 2).map(part => part.trim());
      const [key, rawType = 'text'] = definition.split(':', 2).map(part => part.trim());
      return {key, type: rawType || 'text', label: providedLabel || key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())};
    });
  }

  async function saveQuotation(event) {
    event.preventDefault();
    const payload = {
      name: $('quote-name').value.trim(),
      quote_date: $('quote-date').value,
      purchase_link: $('quote-link').value.trim(),
      description: $('quote-description').value.trim(),
      extra_fields: parseExtraDefinitions(),
    };
    try {
      let saved;
      if (state.creatingQuotation) saved = await api('/api/quotations', {method: 'POST', body: payload});
      else saved = await api(`/api/quotations/${encodeURIComponent(state.activeId)}`, {method: 'PATCH', body: payload});
      $('quotation-modal').close();
      showToast(state.creatingQuotation ? 'Cotización creada' : 'Cotización actualizada');
      await loadQuotations(saved.id);
    } catch (error) { showToast(error.message, 'error'); }
  }

  async function deleteQuotation() {
    const quotation = activeQuotation();
    if (!quotation || !confirm(`¿Eliminar la cotización “${quotation.name}” y todos sus ítems?`)) return;
    try {
      await api(`/api/quotations/${encodeURIComponent(quotation.id)}`, {method: 'DELETE'});
      $('quotation-modal').close();
      showToast('Cotización eliminada');
      await loadQuotations('');
    } catch (error) { showToast(error.message, 'error'); }
  }

  async function copyFormattingPrompt() {
    const quotation = activeQuotation();
    if (!quotation) return;
    try {
      const {prompt} = await api(`/api/quotations/${encodeURIComponent(quotation.id)}/formatting-prompt`);
      try { await navigator.clipboard.writeText(prompt); }
      catch (_) {
        const area = document.createElement('textarea');
        area.value = prompt;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.append(area);
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      showToast('Prompt copiado al portapapeles');
    } catch (error) { showToast(error.message, 'error'); }
  }

  function openImportModal() {
    state.importPreview = null;
    $('import-content').value = '';
    $('import-file').value = '';
    $('import-preview').hidden = true;
    $('preview-summary').replaceChildren();
    $('preview-warnings').replaceChildren();
    $('preview-list').replaceChildren();
    $('import-status').textContent = '';
    $('adopt-extra-fields-field').hidden = true;
    $('adopt-extra-fields').checked = true;
    $('confirm-import').disabled = true;
    $('import-modal').showModal();
  }

  async function loadImportFile(file) {
    if (!file) return;
    if (file.size > 12 * 1024 * 1024) {
      showToast('El archivo supera el máximo de 12 MB', 'error');
      return;
    }
    $('import-content').value = await file.text();
    $('import-status').textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
    state.importPreview = null;
    $('confirm-import').disabled = true;
    $('import-preview').hidden = true;
  }

  async function previewImport() {
    const quotation = activeQuotation();
    const content = $('import-content').value.trim();
    if (!quotation || !content) { showToast('Selecciona un archivo o pega contenido', 'error'); return; }
    const button = $('preview-import');
    button.disabled = true;
    $('import-status').textContent = 'Analizando sin aplicar cambios…';
    try {
      const preview = await api(`/api/quotations/${encodeURIComponent(quotation.id)}/import/preview`, {method: 'POST', body: {content}});
      state.importPreview = preview;
      renderImportPreview(preview);
      $('confirm-import').disabled = preview.changes.length === 0;
      $('import-status').textContent = preview.changes.length ? 'Revisa el resumen antes de confirmar.' : 'No hay cambios por aplicar.';
    } catch (error) {
      state.importPreview = null;
      $('confirm-import').disabled = true;
      $('import-status').textContent = '';
      showToast(error.message, 'error');
    } finally { button.disabled = false; }
  }

  function renderImportPreview(preview) {
    $('import-preview').hidden = false;
    const summary = $('preview-summary');
    summary.replaceChildren();
    const stats = [
      [preview.summary.include, 'a incluir'], [preview.summary.exclude, 'a descartar'],
      [preview.summary.update, 'a actualizar'], [preview.summary.add, 'a agregar'],
    ];
    stats.forEach(([value, label]) => {
      const card = document.createElement('div');
      card.className = 'preview-stat';
      const strong = document.createElement('strong'); strong.textContent = value;
      const span = document.createElement('span'); span.textContent = label;
      card.append(strong, span); summary.append(card);
    });
    const adopt = $('adopt-extra-fields-field');
    const incoming = preview.new_extra_fields || [];
    adopt.hidden = incoming.length === 0;
    if (incoming.length) {
      $('adopt-extra-fields-labels').textContent = incoming.map(field => field.label).join(', ');
      $('adopt-extra-fields').checked = true;
    }
    const warnings = $('preview-warnings');
    warnings.replaceChildren();
    preview.warnings.forEach(message => {
      const warning = document.createElement('div'); warning.className = 'preview-warning'; warning.textContent = message; warnings.append(warning);
    });
    const list = $('preview-list');
    list.replaceChildren();
    const actionNames = {include: 'Incluir', exclude: 'Descartar', update: 'Actualizar', add: 'Agregar'};
    preview.changes.slice(0, 150).forEach(change => {
      const row = document.createElement('div'); row.className = 'preview-change';
      const name = document.createElement('span'); name.textContent = change.name;
      const action = document.createElement('span'); action.className = 'change-tag'; action.textContent = actionNames[change.action] || change.action;
      row.append(name, action); list.append(row);
    });
    if (preview.changes.length > 150) {
      const row = document.createElement('div'); row.className = 'preview-change'; row.textContent = `Y ${preview.changes.length - 150} cambios más…`; list.append(row);
    }
  }

  async function confirmImport() {
    const quotation = activeQuotation();
    if (!quotation || !state.importPreview) return;
    const button = $('confirm-import');
    button.disabled = true;
    try {
      const result = await api(`/api/quotations/${encodeURIComponent(quotation.id)}/import/apply`, {
        method: 'POST',
        body: {
          content: $('import-content').value.trim(),
          expected_revision: state.importPreview.target_revision,
          adopt_extra_fields: $('adopt-extra-fields').checked,
        },
      });
      $('import-modal').close();
      showToast(`${result.applied} cambios aplicados`);
      await loadQuotations(quotation.id);
    } catch (error) {
      showToast(error.message, 'error');
      button.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Exportar HTML: se elige qué cotizaciones y si van todos los elementos.
  // ---------------------------------------------------------------------------

  function exportBoxes() {
    return [...document.querySelectorAll('#export-quotation-list input[type="checkbox"]')];
  }

  function syncExportSelection() {
    const boxes = exportBoxes();
    const chosen = boxes.filter(box => box.checked);
    const all = $('export-select-all');
    all.checked = boxes.length > 0 && chosen.length === boxes.length;
    all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
    const scope = document.querySelector('input[name="export-scope"]:checked')?.value || 'all';
    const items = state.quotations
      .filter(quotation => chosen.some(box => box.value === quotation.id))
      .reduce((total, quotation) => total + (scope === 'included' ? quotation.included_count : quotation.items.length), 0);
    $('export-html-status').textContent = chosen.length
      ? `${chosen.length} cotización(es) · ${items} elemento(s)`
      : 'Elige al menos una cotización';
    $('confirm-export-html').disabled = chosen.length === 0;
  }

  const EXPORT_FORMATS = {
    html: {
      title: 'Archivo HTML autónomo',
      lead: 'Se ve igual que esta pantalla, funciona sin internet y se puede volver a importar con lo que marquen.',
      url: '/api/export.html',
    },
    xlsx: {
      title: 'Libro de Excel',
      lead: 'Una hoja por cotización, con las mismas columnas de la tabla.',
      url: '/api/export.xlsx',
    },
  };

  function openExportModal(format) {
    const quotation = activeQuotation();
    if (!quotation) return;
    state.exportFormat = format;
    $('export-modal-title').textContent = EXPORT_FORMATS[format].title;
    $('export-modal-lead').textContent = EXPORT_FORMATS[format].lead;
    const list = $('export-quotation-list');
    list.replaceChildren();
    state.quotations.forEach(entry => {
      const label = document.createElement('label');
      label.className = 'check-field';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = entry.id;
      box.checked = entry.id === quotation.id;
      box.addEventListener('change', syncExportSelection);
      const wrap = document.createElement('span');
      const name = document.createElement('strong');
      name.textContent = entry.name;
      const detail = document.createElement('small');
      detail.textContent = `${entry.included_count} incluidos de ${entry.items.length} elementos`;
      wrap.append(name, detail);
      label.append(box, wrap);
      list.append(label);
    });
    document.querySelector('input[name="export-scope"][value="all"]').checked = true;
    syncExportSelection();
    $('export-html-modal').showModal();
  }

  /** El formulario es method="dialog": al enviar se cierra solo. */
  function runExport() {
    const ids = exportBoxes().filter(box => box.checked).map(box => box.value);
    if (!ids.length) return;
    const scope = document.querySelector('input[name="export-scope"]:checked')?.value || 'all';
    const query = ids.map(id => `quotation_id=${encodeURIComponent(id)}`).join('&');
    downloadUrl(`${EXPORT_FORMATS[state.exportFormat || 'html'].url}?${query}&scope=${scope}`);
  }

  function downloadUrl(url) {
    const link = document.createElement('a');
    link.href = url;
    document.body.append(link);
    link.click();
    link.remove();
  }

  function bindEvents() {
    $('new-quotation').addEventListener('click', () => openQuotationModal(true));
    document.querySelector('[data-action="new-quotation"]').addEventListener('click', () => openQuotationModal(true));
    $('edit-quotation').addEventListener('click', () => openQuotationModal(false));
    $('new-item').addEventListener('click', () => openItemModal());
    $('import-data').addEventListener('click', openImportModal);
    $('import-copy-prompt').addEventListener('click', copyFormattingPrompt);
    document.querySelectorAll('.menu-wrap > [aria-haspopup="menu"]').forEach(trigger => {
      trigger.addEventListener('click', event => { event.stopPropagation(); toggleMenu(trigger); });
    });
    document.querySelectorAll('.menu-wrap .menu').forEach(menu => {
      menu.addEventListener('click', () => closeMenus());
    });
    document.addEventListener('click', event => {
      if (!event.target.closest?.('.menu-wrap')) closeMenus();
      if (!event.target.closest?.('#move-menu')) closeMoveMenu();
      if (!event.target.closest?.('#row-menu') && !event.target.closest?.('.kebab-button')) closeRowMenu();
      if (!event.target.closest?.('#column-menu') && !event.target.closest?.('.column-menu-trigger')) closeColumnMenu();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { closeMenus(); closeMoveMenu(); closeRowMenu(); closeColumnMenu(); }
    });
    const dropAll = () => { closeMoveMenu(); closeRowMenu(); closeColumnMenu(); };
    window.addEventListener('resize', dropAll);
    window.addEventListener('scroll', dropAll, {passive: true});
    document.addEventListener('scroll', event => {
      if (event.target?.classList?.contains('tabulator-tableholder')) dropAll();
    }, true);
    $('item-form').addEventListener('submit', saveItem);
    $('quotation-form').addEventListener('submit', saveQuotation);
    $('delete-item').addEventListener('click', deleteItem);
    $('delete-quotation').addEventListener('click', deleteQuotation);
    $('item-photo').addEventListener('input', event => {
      state.photoChanged = true;
      state.pendingPhoto = null;
      renderPhotoPreview(event.target.value.trim());
    });
    $('item-photo-file').addEventListener('change', async event => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        $('item-photo-preview').textContent = 'Comprimiendo…';
        state.pendingPhoto = await compressPhoto(file);
        state.photoChanged = true;
        $('item-photo').value = '';
        renderPhotoPreview(state.pendingPhoto);
      } catch (error) { renderPhotoPreview(''); showToast(error.message, 'error'); }
    });
    $('export-item').addEventListener('click', () => {
      const quotation = activeQuotation();
      const item = quotation?.items.find(current => current.id === state.editingItemId);
      if (!quotation || !item) return;
      downloadUrl(`/api/quotations/${encodeURIComponent(quotation.id)}/export.html?item_id=${encodeURIComponent(item.id)}`);
    });
    $('move-item').addEventListener('click', async () => {
      const quotation = activeQuotation();
      const item = quotation?.items.find(current => current.id === state.editingItemId);
      const target = state.quotations.find(other => other.id === $('move-item-target').value);
      if (!quotation || !item || !target) return;
      const button = $('move-item');
      button.disabled = true;
      try {
        await api(`/api/items/${encodeURIComponent(item.id)}`, {method: 'PATCH', body: itemPayload()});
        $('item-modal').close();
        await moveItemsTo([{...item, ...itemPayload(), quotation_id: quotation.id}], target);
      } catch (error) {
        showToast(error.message, 'error');
      } finally {
        button.disabled = false;
      }
    });
    $('export-html').addEventListener('click', () => openExportModal('html'));
    $('export-excel').addEventListener('click', () => openExportModal('xlsx'));
    $('export-select-all').addEventListener('change', event => {
      exportBoxes().forEach(box => { box.checked = event.target.checked; });
      syncExportSelection();
    });
    document.querySelectorAll('input[name="export-scope"]').forEach(radio => radio.addEventListener('change', syncExportSelection));
    $('export-html-form').addEventListener('submit', runExport);
    $('export-pdf').addEventListener('click', () => { const q = activeQuotation(); if (q) downloadUrl(`/api/quotations/${encodeURIComponent(q.id)}/export.pdf`); });
    $('export-json').addEventListener('click', () => { const q = activeQuotation(); if (q) downloadUrl(`/api/quotations/${encodeURIComponent(q.id)}/export.json`); });
    $('filter-search').addEventListener('input', event => applySearch(event.target.value));
    $('clear-filters').addEventListener('click', resetFilters);
    $('selection-move').addEventListener('click', event => {
      event.stopPropagation();
      openMoveMenu($('selection-move'), selectedItems());
    });
    $('selection-include').addEventListener('click', () => bulkInclude(true));
    $('selection-exclude').addEventListener('click', () => bulkInclude(false));
    $('selection-delete').addEventListener('click', () => deleteItems(selectedItems()));
    document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
    $('import-file').addEventListener('change', event => loadImportFile(event.target.files?.[0]));
    $('preview-import').addEventListener('click', previewImport);
    $('confirm-import').addEventListener('click', confirmImport);
    const drop = $('import-drop');
    ['dragenter', 'dragover'].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.add('dragging'); }));
    ['dragleave', 'drop'].forEach(name => drop.addEventListener(name, event => { event.preventDefault(); drop.classList.remove('dragging'); }));
    drop.addEventListener('drop', event => loadImportFile(event.dataTransfer?.files?.[0]));
    document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
      const rect = dialog.getBoundingClientRect();
      const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) dialog.close();
    }));
  }

  bindEvents();
  loadQuotations();
})();
