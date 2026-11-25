import subprocess
from pathlib import Path


def test_device_details_preserve_drafts_navigation_and_read_only_access():
    # Exercise the native events consumed by the controller without starting a scan or browser.
    result = subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag, cls = '', text = '') {
    this.tag = tag; this.cls = cls; this.textContent = text;
    this.children = []; this.dataset = {}; this.events = {};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  get childElementCount() { return this.children.length; }
  descendants() { return this.children.flatMap(node => [node, ...node.descendants()]); }
  contains(node) { return this === node || this.descendants().includes(node); }
  querySelectorAll(selector) {
    return this.descendants().filter(node => node.tag === selector || node.cls.split(' ').includes(selector.slice(1)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  get elements() { return {namedItem: name => this.descendants().find(node => node.name === name)}; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, handle) { this.events[event] = handle; }
  focus() { document.activeElement = this; }
  show() { this.open = true; this.modal = false; }
  showModal() { this.open = true; this.modal = true; }
  close() { this.open = false; this.modal = false; }
}
const dialog = new Element('dialog'), viewport = new Element('div');
const media = new Element(); media.matches = false;
global.window = {matchMedia: () => media};
global.document = new Element('document');
document.getElementById = id => id === 'details' ? dialog : viewport;
document.querySelector = selector => selector === 'dialog:modal' && dialog.modal ? dialog : null;
global.Option = class extends Element {constructor(text, value) { super('option', '', text); this.value = value; }};
const devices = [{id: 'a', notes: 'original', status: 'outOfScope'}, {id: 'b', status: 'online'}];
const state = {devices, edges: [{source: 'a', target: 'b', origin: 'fritz', lqi: 0}], can_edit: true};
const buttons = devices.map(device => { const button = new Element('button', 'node'); button.dataset.id = device.id; return button; });
viewport.append(...buttons);
require('./static/network-map-details.js');
let locale = 'de', selected = '', fail = true, lastPatch, revealed;
const details = RunvardMapDetails.create({
  getState: () => state, t: key => locale + '.' + key, el: (...args) => new Element(...args),
  icon: () => new Element('svg'), deviceIcon: () => 'device', label: d => d.id,
  kindLabel: () => locale + '.endDevice', status: d => d.status, date: String, originLabel: e => e.origin,
  onSelection: id => {selected = id;}, revealNode: id => {revealed = id;},
  request: async (action, patch) => {
    assert.equal(action, 'device');
    if (fail) throw Error('invalid');
    lastPatch = patch;
    Object.assign(devices.find(device => device.id === patch.id), patch);
  },
  refresh: async () => details.render(),
});
const notes = () => dialog.querySelector('textarea');
const form = () => dialog.querySelector('form');
const submit = () => form().onsubmit({preventDefault() {}});
const type = value => {
  const input = notes(); input.value = value; input.focus();
  dialog.querySelector('details').open = true; form().oninput({target: input});
};
(async () => {
  details.select('a');
  assert(dialog.open && !dialog.modal);
  assert.equal(dialog.querySelector('.detail-status').children[1].textContent, 'de.outOfScope');
  type('unsaved a');
  const original = notes();
  details.render(); assert.equal(notes(), original);
  details.select('a'); assert.equal(notes(), original);
  dialog.querySelector('.neighbor').onclick();
  assert.equal(revealed, 'b'); assert.equal(selected, 'b'); assert.equal(notes().value, '');
  type('unsaved b'); details.close(); assert.equal(document.activeElement, buttons[1]);
  media.matches = true; details.select('a'); assert(dialog.modal); assert.equal(notes().value, 'unsaved a');
  locale = 'fr'; details.render(true); assert.equal(notes().value, 'unsaved a');
  assert.equal(dialog.querySelector('.detail-close').textContent, 'fr.backToMap');
  await submit(); assert.equal(notes().value, 'unsaved a');
  assert.equal(dialog.querySelector('.detail-feedback').textContent, 'fr.invalid');
  fail = false; await submit(); assert.deepEqual(lastPatch, {id: 'a', notes: 'unsaved a'});
  devices[0].notes = 'server updated'; details.render(true); assert.equal(notes().value, 'server updated');
  details.select('b'); assert.equal(notes().value, 'unsaved b');
  media.matches = false; media.events.change(); assert(dialog.open && !dialog.modal);
  document.events.keydown({key: 'Escape', preventDefault() {}}); assert(!dialog.open); assert.equal(selected, '');
  assert.equal(document.activeElement, buttons[1]);
  state.can_edit = false; details.select('b'); assert.equal(form(), undefined);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
