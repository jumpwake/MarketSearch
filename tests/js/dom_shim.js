'use strict';
// Minimal hand-rolled DOM stand-in — just enough of the API surface
// dashboard.py's `_JS` actually calls (querySelector(All), dataset,
// classList, appendChild, addEventListener, textContent/value/hidden) — so
// the *real*, unmodified `_JS` string can run under plain Node without a
// browser or a third-party DOM library such as jsdom (not installed in this
// project; see FINDING 1 in the dashboard review for why this exists).
//
// This file is concatenated with the literal `_JS` text from
// marketsearch.dashboard and a footer script by tests/test_dashboard_js.py.
// It builds a page shaped like the one render_dashboard() produces for
// three listings (svl95, svl90, t770), using the exact hours/price numbers
// from test_ranking_inverts_at_ten_thousand_hours in
// tests/test_dashboard_ranking.py — the fixture proving the assumed-life
// figure inverts the ranking rather than merely adjusting it.

class FakeElement {
  constructor(tag, attrs = {}) {
    this.tag = tag;
    this.id = attrs.id || '';
    this.className = attrs.className || '';
    this.dataset = attrs.dataset || {};
    this.textContent = attrs.textContent || '';
    this.value = attrs.value !== undefined ? attrs.value : '';
    this.hidden = false;
    this.children = [];
    this._listeners = {};
    this._classSet = new Set(this.className.split(' ').filter(Boolean));
  }
  get classList() {
    const self = this;
    return {
      toggle(c) {
        if (self._classSet.has(c)) self._classSet.delete(c); else self._classSet.add(c);
        self.className = [...self._classSet].join(' ');
      },
      contains(c) { return self._classSet.has(c); },
      add(c) { self._classSet.add(c); self.className = [...self._classSet].join(' '); },
    };
  }
  addEventListener(evt, fn) { (this._listeners[evt] = this._listeners[evt] || []).push(fn); }
  appendChild(child) {
    const i = this.children.indexOf(child);
    if (i !== -1) this.children.splice(i, 1);
    this.children.push(child);
    return child;
  }
  _matches(sel) {
    if (sel.startsWith('.')) return this._classSet.has(sel.slice(1));
    if (sel.startsWith('[') && sel.endsWith(']')) {
      const key = sel.slice(1, -1).split('=')[0].trim();
      const camel = key.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      return Object.prototype.hasOwnProperty.call(this.dataset, camel);
    }
    return false;
  }
  _descendants() {
    const out = [];
    for (const c of this.children) { out.push(c); out.push(...c._descendants()); }
    return out;
  }
  querySelectorAll(sel) { return this._descendants().filter(e => e._matches(sel)); }
  querySelector(sel) { return this._descendants().find(e => e._matches(sel)) || null; }
}

function el(tag, attrs, children = []) {
  const e = new FakeElement(tag, attrs);
  for (const c of children) e.appendChild(c);
  return e;
}

function machine(id, priceCents, hours) {
  const valueEl = el('span', {className: 'pick-value'});
  const li = el('li', {
    dataset: {pick: id, hours: String(hours), priceCents: String(priceCents)},
  }, [valueEl]);
  const chip = el('span', {className: 'value-chip'});
  const card = el('article', {
    className: 'card',
    dataset: {
      model: 'bobcat-t770', verdict: 'match', confidence: '0.8',
      seen: '2026-07-27T10:00:00+00:00',
      hours: String(hours), priceCents: String(priceCents),
    },
  }, [chip]);
  return {li, card};
}

const svl95 = machine('svl95', 3_200_000, 2984);
const svl90 = machine('svl90', 4_500_000, 1005);
const t770 = machine('t770', 3_950_000, 2200);

// Named *Built/*El to avoid colliding with the identifiers the real `_JS`
// text (concatenated right after this file) declares for the very same
// elements via document.getElementById/querySelector — e.g. `_JS` itself
// declares `const life = document.getElementById('life');`. Both files
// share one top-level scope, so a duplicate `const life` here would be a
// SyntaxError. `_JS`'s own bindings are what the footer script uses to
// mutate the page and re-run apply().
const cardsHost = el('div', {id: 'cards'}, [svl95.card, svl90.card, t770.card]);
const picksHostEl = el('ol', {className: 'picks'}, [svl95.li, svl90.li, t770.li]);

const lifeInput = el('input', {id: 'life', value: '6000'});
const lifeOutputEl = el('output', {id: 'lifeOut'});
const searchInput = el('input', {id: 'q', value: ''});
const modelSelect = el('select', {id: 'model', value: ''});
const sortSelect = el('select', {id: 'sort', value: 'value'});

const registry = new Map([
  ['cards', cardsHost], ['life', lifeInput], ['lifeOut', lifeOutputEl],
  ['q', searchInput], ['model', modelSelect], ['sort', sortSelect],
]);

const root = el('div', {}, [
  cardsHost, picksHostEl, lifeInput, lifeOutputEl, searchInput, modelSelect, sortSelect,
]);

global.document = {
  getElementById(id) { return registry.get(id) || null; },
  querySelector(sel) { return root.querySelector(sel); },
  querySelectorAll(sel) { return root.querySelectorAll(sel); },
};

function pickOrder() { return picksHostEl.children.map(li => li.dataset.pick); }
function pickValues() {
  return Object.fromEntries(
    picksHostEl.children.map(li => [li.dataset.pick, li.querySelector('.pick-value').textContent]),
  );
}
