/* app.js를 node에서 돌리는 데 필요한 만큼만 흉내 낸 DOM. 화면 흐름(대화 상자, 정보 칸)을 버튼 누르기로 시험한다.
   브라우저의 동작을 모두 따르지 않는다. 시험이 쓰는 선택자(#id, .class, 태그, 태그:not([type=x]), 쉼표 목록)와
   app.js가 쓰는 속성만 있다. 터미널(xterm)은 없으므로 탭을 여는 흐름은 시험하지 않는다. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '../../src/hq/static');

class Node {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.parent = null;
    this.attributes = {};
    this.listeners = {};
    this.className = '';
    this.hidden = false;
    this.inert = false;
    this.disabled = false;
    this.value = '';
    this.type = '';
    this.id = '';
    this.style = {};
    this._text = null; // 글자 노드
  }

  get textContent() {
    if (this._text !== null) return this._text;
    return this.children.map((child) => child.textContent).join('');
  }

  set textContent(value) {
    this.replaceChildren(String(value));
  }

  setAttribute(name, value) {
    this.attributes[name] = value;
    if (name === 'hidden') this.hidden = true;
    else if (name === 'id') this.id = value;
    else if (name === 'type') this.type = value;
    else if (name === 'value') this.value = value;
  }

  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }

  removeEventListener(type, listener) {
    this.listeners[type] = (this.listeners[type] || []).filter((l) => l !== listener);
  }

  dispatch(type, extra = {}) {
    const event = { type, target: this, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}, ...extra };
    for (const listener of [...(this.listeners[type] || [])]) listener(event);
  }

  click() {
    if (!this.disabled) this.dispatch('click');
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  append(...items) {
    for (const item of items) {
      const node = typeof item === 'string' ? this.ownerDocument.createTextNode(item) : item;
      if (node.parent) node.remove();
      node.parent = this;
      this.children.push(node);
    }
  }

  replaceChildren(...items) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    this.append(...items);
  }

  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }

  contains(node) {
    for (let at = node; at; at = at.parent) if (at === this) return true;
    return false;
  }

  *descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }

  querySelectorAll(selectors) {
    const parts = selectors.split(',').map((s) => s.trim());
    return [...this.descendants()].filter((node) => node._text === null && parts.some((s) => matches(node, s)));
  }

  querySelector(selectors) {
    return this.querySelectorAll(selectors)[0] || null;
  }
}

function matches(node, selector) {
  if (selector.startsWith('#')) return node.id === selector.slice(1);
  if (selector.startsWith('.')) return node.className.split(/\s+/).includes(selector.slice(1));
  const match = selector.match(/^(\w+)(?::not\(\[type=(\w+)\]\))?$/);
  if (!match) throw new Error(`fake-dom이 모르는 선택자: ${selector}`);
  return node.tagName === match[1].toUpperCase() && !(match[2] && node.type === match[2]);
}

class Document {
  constructor() {
    this.listeners = {};
    this.activeElement = null;
    this.body = new Node('body', this);
  }

  createElement(tag) {
    return new Node(tag, this);
  }

  createTextNode(text) {
    const node = new Node('#text', this);
    node._text = text;
    return node;
  }

  querySelector(selectors) {
    return this.body.querySelector(selectors);
  }

  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }

  removeEventListener(type, listener) {
    this.listeners[type] = (this.listeners[type] || []).filter((l) => l !== listener);
  }
}

// index.html의 자리 가운데 app.js가 찾는 것(test_static.py의 test_index_has_the_places_app_js_needs와 같은 목록)
const PLACES = ['rail-servers', 'rail-settings', 'servers-view', 'settings-view', 'settings-body', 'refresh',
  'server-list', 'add-server', 'excel', 'search', 'banner', 'load-error', 'tabbar', 'panes', 'empty', 'info', 'menu',
  'modal'];
const HIDDEN = new Set(['settings-view', 'banner', 'load-error', 'info', 'menu', 'modal']);

/* app.js를 api(가짜 window.pywebview.api)와 함께 읽고 시작한다. 돌려주는 page로 자리를 찾고 버튼을 누른다. */
function startApp(api) {
  const document = new Document();
  const app = document.createElement('div');
  app.className = 'app';
  document.body.append(app);
  for (const id of PLACES) {
    const node = document.createElement('div');
    node.id = id;
    node.hidden = HIDDEN.has(id);
    (id === 'menu' || id === 'modal' ? document.body : app).append(node);
  }
  const window = new Node('window', document);
  window.HQ = require(path.join(STATIC, 'js/logic.js'));
  window.pywebview = { api };
  window.innerWidth = 1280;
  window.innerHeight = 820;
  const context = {
    window, document, console,
    requestAnimationFrame: (callback) => setImmediate(callback),
    navigator: { clipboard: { writeText: async () => {} } },
  };
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, 'js/app.js'), 'utf8'), context);
  return {
    window,
    document,
    $: (selector) => document.querySelector(selector),
    /* root 안에서 글자가 label인 버튼 */
    button(root, label) {
      const found = root.querySelectorAll('button').find((b) => b.textContent === label);
      if (!found) throw new Error(`버튼 '${label}'이 없다: ${root.textContent}`);
      return found;
    },
  };
}

/* 기다리는 약속(화면의 await)이 모두 이어 가도록 이벤트 루프를 몇 바퀴 돌린다 */
async function settle() {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/* 나중에 풀 수 있는 약속 */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

module.exports = { startApp, settle, deferred };
