const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const test = require('node:test')
const {runInNewContext} = require('node:vm')
const file = readFileSync('web-lambda/templates/fragments/topic-search.html', 'utf8')
const start = file.indexOf('function initializeTopicSearch()')
const source = file.slice(start, file.indexOf('document.addEventListener("DOMContentLoaded", initializeTopicSearch)', start))
const settle = () => new Promise(resolve => setImmediate(resolve))

function setup() {
  const timers = new Map(), calls = [], listeners = {}
  let nextTimer = 0
  const element = () => ({dataset: {}, children: [], textContent: '', attrs: {},
    classList: {values: new Set(), toggle(name, active) {active ? this.values.add(name) : this.values.delete(name)}}, setAttribute(k, v) {this.attrs[k] = v}, removeAttribute(k) {delete this.attrs[k]},
    append(child) {this.children.push(child)}, replaceChildren() {this.children = []}})
  const nodes = {}
  const grid = () => {
    const node = Object.assign(element(), {cloneNode: () => grid(), replaceWith(next) {nodes.tags = next}})
    Object.defineProperty(node, 'innerHTML', {set(html) {
      this.children = html ? [{dataset: {offset: 'next'}}] : []
      this.lastElementChild = this.children.at(-1)
    }})
    return node
  }
  nodes.tags = grid()
  for (const id of ['topic-search-clear', 'topic-search-loading', 'topic-pagination', 'topic-search-status', 'topic-empty', 'topic-sort']) nodes[id] = element()
  nodes['topic-search-clear'].addEventListener = (name, callback) => {listeners.clear = callback}
  const input = {value: '', focus() {this.focused = true}, addEventListener: (name, callback) => {listeners[name] = callback}}
  nodes['topic-filters'] = {dataset: {url: 'https://api.example.test/tags-fragment'},
    elements: {type: {value: 'popular'}, limit: {value: '2'}}, querySelector: () => input, querySelectorAll: () => [],
    addEventListener: (name, callback) => {listeners[name] = callback}}
  const context = {document: {getElementById: id => nodes[id], createElement: element},
    URL, AbortController, masonryContainers: new WeakMap(), getMasonryInstance: () => null,
    setTimeout(callback, delay) {const id = ++nextTimer; timers.set(id, {callback, delay}); return id},
    clearTimeout: id => timers.delete(id), fetch(url, options) {
      return new Promise(resolve => calls.push({url: new URL(url), options, resolve}))
    }}
  runInNewContext(source, context)
  context.initializeTopicSearch()
  return {nodes, calls, input, listeners,
    type(value, composing = false) {input.value = value; listeners.input({isComposing: composing})},
    fire(delay = 250) {for (const [id, timer] of [...timers]) if (timer.delay === delay) {timers.delete(id); timer.callback()}},
    reply(index, html = '<div>topic</div>', ok = true) {calls[index].resolve({ok, text: async () => html})}}
}

test('compact topic filter debounces typing and uses the API without a page submission', async () => {
  const app = setup()
  assert.equal(app.calls.length, 0)
  app.type('Por'); app.type(' Portrait Art ')
  assert.equal(app.nodes['topic-search-status'].textContent, 'Loading topics…')
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), false)
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), true)
  assert.equal(app.nodes['topic-search-status'].classList.values.has('visually-hidden'), true)
  app.fire()
  assert.equal(app.calls.length, 1)
  assert.equal(app.nodes['topic-search-status'].textContent, 'Loading topics…')
  assert.equal(app.nodes.tags.attrs['aria-busy'], 'true')
  assert.equal(app.calls[0].url.searchParams.get('prefix'), 'portrait-art')
  assert.equal(app.calls[0].url.searchParams.get('limit'), '2')
  assert.equal(app.calls[0].url.searchParams.has('offset'), false)
  app.reply(0)
  await settle()
  assert.equal(app.nodes['topic-pagination'].children[0].dataset.offset, 'next')
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), false)
  assert.equal(app.nodes['topic-search-status'].textContent, '')
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), true)
  assert.equal(app.nodes.tags.classList.values.has('opacity-50'), false)
})

test('new input cancels the old request and ignores late results', async () => {
  const app = setup()
  app.type('old'); app.fire()
  app.type('new'); app.fire()
  assert.equal(app.calls[0].options.signal.aborted, true)
  app.reply(1, '')
  await settle()
  app.reply(0)
  await settle()
  assert.equal(app.nodes.tags.children.length, 0)
  assert.equal(app.nodes['topic-pagination'].children.length, 0)
  assert.match(app.nodes['topic-empty'].textContent, /No topics match/)
})

test('clearing restores unfiltered results and failures allow another search', async () => {
  const app = setup()
  app.type(''); app.fire()
  assert.equal(app.calls[0].url.searchParams.has('prefix'), false)
  assert.equal(app.calls[0].url.searchParams.get('type'), 'popular')
  app.reply(0, '', false)
  await settle()
  assert.match(app.nodes['topic-search-status'].textContent, /retry/)
  assert.equal(app.nodes['topic-search-status'].classList.values.has('visually-hidden'), false)
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), true)
  app.type('retry'); app.fire(); app.reply(1)
  await settle()
  assert.equal(app.nodes['topic-search-status'].textContent, '')
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), true)
  assert.equal(app.nodes.tags.classList.values.has('opacity-50'), false)
})

test('composition waits until text entry completes', () => {
  const app = setup()
  app.type('partial', true); app.fire()
  assert.equal(app.calls.length, 0)
  app.listeners.compositionend(); app.fire()
  assert.equal(app.calls.length, 1)
})


test('the clear icon shares the spinner position and restores unfiltered results', async () => {
  const app = setup()
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), true)
  app.type('portrait'); app.fire(); app.reply(0)
  await settle()
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), false)
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), true)
  app.listeners.clear()
  assert.equal(app.input.value, '')
  assert.equal(app.input.focused, true)
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), true)
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), false)
  app.fire(); app.reply(1)
  await settle()
  assert.equal(app.calls[1].url.searchParams.has('prefix'), false)
  assert.equal(app.nodes['topic-search-clear'].classList.values.has('d-none'), true)
  assert.equal(app.nodes['topic-search-loading'].classList.values.has('d-none'), true)
})
