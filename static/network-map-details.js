'use strict';

globalThis.RunvardMapDetails = {
  create({getState, t, el, icon, deviceIcon, kindLabel, label, status, date, originLabel, request, refresh, onSelection, revealNode}) {
    const dialog = document.getElementById('details');
    const viewport = document.getElementById('viewport');
    const narrow = window.matchMedia('(max-width: 760px)');
    const drafts = new Map();
    const expanded = new Set();
    const saving = new Set();
    const feedback = new Map();
    let selected = '';

    const heading = el('header', 'detail-heading');
    const symbol = el('span', 'node-icon');
    const title = el('h2');
    title.id = 'detail-title';
    title.tabIndex = -1;
    const back = el('button', 'detail-close btn ghost');
    back.type = 'button';
    back.onclick = close;
    heading.append(symbol, title, back);
    const body = el('div', 'detail-body');
    const facts = el('div', 'detail-facts');
    const connections = el('section', 'detail-connections');
    const editArea = el('div', 'detail-edit');
    const notice = el('p', 'detail-feedback hint');
    notice.setAttribute('role', 'status');
    body.append(facts, connections, editArea, notice);
    dialog.append(heading, body);

    function close() {
      if (!selected) return;
      const previous = selected;
      selected = '';
      dialog.close();
      onSelection('');
      const node = Array.from(viewport.querySelectorAll('.node')).find(button => button.dataset.id === previous);
      (node || viewport).focus({preventScroll: true});
    }

    function present() {
      if (narrow.matches) dialog.showModal();
      else dialog.show();
    }

    function select(id) {
      if (!getState().devices.some(device => device.id === id)) return;
      if (selected !== id) {
        selected = id;
        onSelection(id);
        render(true);
      }
      if (!dialog.open) present();
      body.scrollTop = 0;
      dialog.scrollTop = 0;
      title.focus({preventScroll: true});
    }

    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      close();
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !dialog.open || event.defaultPrevented) return;
      const modal = document.querySelector('dialog:modal');
      if (modal && modal !== dialog) return;
      event.preventDefault();
      close();
    });
    narrow.addEventListener('change', () => {
      if (!dialog.open) return;
      const focused = dialog.contains(document.activeElement) ? document.activeElement : title;
      dialog.close();
      present();
      focused.focus({preventScroll: true});
    });

    function renderFacts(device) {
      const badge = el('div', 'detail-status');
      badge.append(el('i', 'status-dot ' + status(device)), el('span', '', t(status(device))));
      const kind = [kindLabel(device), device.room].filter(Boolean).join(' · ');
      facts.replaceChildren(badge, el('div', 'device-kind', kind));
      const values = [
        ['IP', device.ip], ['MAC', device.mac], ['IEEE', device.ieee],
        ['manufacturer', device.manufacturer], ['model', device.model],
        ['battery', device.battery == null ? null : device.battery + ' %'], ['lqi', device.linkquality],
        ['lastSeen', device.last_seen ? date(device.last_seen) : null],
      ];
      for (const [key, value] of values) {
        if (value == null || value === '') continue;
        const address = ['IP', 'MAC', 'IEEE'].includes(key);
        const row = el('div', address ? 'kv mono' : 'kv');
        row.append(el('span', '', address ? key : t(key)), el('strong', '', String(value)));
        facts.append(row);
      }
    }

    function renderConnections(device, state) {
      const list = el('div', 'neighbors');
      const devices = new Map(state.devices.map(other => [other.id, other]));
      const focused = connections.contains(document.activeElement) ? document.activeElement.dataset.edge : '';
      let replacement;
      for (const edge of state.edges) {
        if (device.id !== edge.source && device.id !== edge.target) continue;
        const other = devices.get(device.id === edge.source ? edge.target : edge.source);
        if (!other) continue;
        const link = el('button', 'neighbor');
        link.type = 'button';
        link.dataset.edge = JSON.stringify([other.id, edge.origin]);
        const copy = el('span');
        const description = originLabel(edge) + (edge.lqi == null ? '' : ' · LQI ' + edge.lqi);
        copy.append(el('strong', '', label(other)), el('small', '', description));
        link.append(icon(deviceIcon(other)), copy);
        link.onclick = () => { revealNode(other.id); select(other.id); };
        if (link.dataset.edge === focused) replacement = link;
        list.append(link);
      }
      if (!list.childElementCount) {
        const empty = el('div', 'connection-empty');
        empty.append(icon('link'), el('p', '', t('noConnections')));
        list.append(empty);
      }
      connections.replaceChildren(el('h3', '', t('connections')), list);
      if (focused) (replacement || title).focus({preventScroll: true});
    }

    function field(form, name, caption, tag, value, options) {
      const wrapper = el('label');
      const input = el(tag);
      input.name = name;
      if (tag === 'select') {
        for (const [id, text] of options) input.append(new Option(text, id));
      } else {
        input.maxLength = name === 'notes' ? 1000 : 160;
      }
      input.value = value;
      wrapper.append(el('span', '', t(caption)), input);
      form.append(wrapper);
    }

    function renderEditor(device, state) {
      if (!state.can_edit) {
        editArea.replaceChildren(el('p', 'hint', t('readOnly')));
        return;
      }
      const id = device.id;
      const draft = drafts.get(id) || {};
      const editor = el('details', 'editor');
      editor.open = expanded.has(id);
      editor.ontoggle = () => {
        if (editor.isConnected) {
          if (editor.open) expanded.add(id);
          else expanded.delete(id);
        }
      };
      const disclosure = el('summary');
      disclosure.append(icon('edit'), el('span', '', t('edit')));
      const form = el('form');
      const parents = state.devices.filter(other => other.id !== id).map(other => [other.id, label(other)]);
      field(form, 'label', 'name', 'input', draft.label ?? device.label ?? '');
      field(form, 'room', 'room', 'input', draft.room ?? device.room ?? '');
      field(form, 'notes', 'notes', 'textarea', draft.notes ?? device.notes ?? '');
      field(form, 'parent', 'parent', 'select', draft.parent ?? device.parent ?? '', [['', t('none')], ...parents]);
      field(form, 'connection_type', 'connectionType', 'select', draft.connection_type ?? device.connection_type ?? 'unknown', [
        ['unknown', t('groupUnknown')], ['lan', t('groupLan')], ['wlan', t('groupWlan')],
      ]);
      form.oninput = event => {
        const {name, value} = event.target;
        const updates = {...drafts.get(id), [name]: value};
        drafts.set(id, updates);
        expanded.add(id);
        feedback.delete(id);
        notice.textContent = '';
      };
      const save = el('button', 'btn', t('save'));
      save.type = 'submit';
      save.disabled = saving.has(id);
      form.append(save);
      form.onsubmit = async event => {
        event.preventDefault();
        if (saving.has(id)) return;
        const changes = drafts.get(id) || {};
        saving.add(id);
        save.disabled = true;
        try {
          if (Object.keys(changes).length) await request('device', {id, ...changes});
          const remaining = {...drafts.get(id)};
          for (const [key, value] of Object.entries(changes)) {
            if (remaining[key] === value) delete remaining[key];
          }
          if (Object.keys(remaining).length) drafts.set(id, remaining);
          else {
            drafts.delete(id);
            expanded.delete(id);
            feedback.set(id, 'saved');
          }
          await refresh(true);
        } catch (err) {
          feedback.set(id, err.message === 'invalid' ? 'invalid' : 'error');
        } finally {
          saving.delete(id);
          if (selected === id) render(true);
        }
      };
      editor.append(disclosure, form);
      editArea.replaceChildren(editor);
    }

    function render(forceEditor = false) {
      if (!selected) return;
      const state = getState();
      const device = state.devices.find(item => item.id === selected);
      if (!device) { close(); return; }
      symbol.replaceChildren(icon(deviceIcon(device)));
      title.textContent = label(device);
      back.textContent = t('backToMap');
      renderFacts(device);
      renderConnections(device, state);
      // Polls can update the topology while someone types or uses a native select.
      // Keep the open editor mounted until a language, device or permission change.
      const editor = editArea.querySelector('details');
      if (forceEditor || !state.can_edit || !editor?.open) {
        const oldFocus = editArea.contains(document.activeElement) ? document.activeElement : null;
        const caret = typeof oldFocus?.selectionStart === 'number' ? [oldFocus.selectionStart, oldFocus.selectionEnd] : null;
        renderEditor(device, state);
        if (oldFocus) {
          const form = editArea.querySelector('form');
          const visibleEditor = editArea.querySelector('details')?.open;
          const next = (visibleEditor && form?.elements.namedItem(oldFocus.name)) || editArea.querySelector('summary') || title;
          next.focus({preventScroll: true});
          if (caret && next.setSelectionRange) next.setSelectionRange(...caret);
        }
      }
      const message = feedback.has(selected) ? t(feedback.get(selected)) : '';
      if (notice.textContent !== message) notice.textContent = message;
    }

    return {select, close, render};
  }
};
