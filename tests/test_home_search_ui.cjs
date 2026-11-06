const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')
const script = readFileSync(join(__dirname, '../web-lambda/templates/fragments/article-search.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1]
const reply = (items, status = 200) => ({ok: status < 400, status, json: async () => items})
function classList() {
  const values = new Set()
  return {add(...items) {items.forEach(item => values.add(item))},
    toggle(item, enabled) {enabled ? values.add(item) : values.delete(item)}, contains: item => values.has(item)}
}

function setup({categories = [{name: 'API Design', slug: 'api-design', published_articles_count: 1200}],
  fetcher = async () => reply([]), available = true} = {}) {
  const form = {action: 'https://example.test/articles', listeners: {},
    addEventListener(name, callback) {this.listeners[name] = callback}}
  const status = {id: 'home-search-help', textContent: '', classList: classList()}
  status.classList.add('invisible')
  const input = {dataset: {categories: JSON.stringify(categories), tagsUrl: 'https://api.example.test/tags'},
    closest: () => form, getAttribute: () => status.id}
  const requests = [], navigation = [], timers = new Map()
  let finder, timerId = 0, ready
  class Finder {
    constructor(input, settings) {
      finder = this; this.settings = settings; this.whitelist = []; this.listeners = {}; this.busy = false
      this.DOM = {scope: {classList: classList()}, input: {classList: classList(),
        attrs: {}, setAttribute(name, value) {this.attrs[name] = value}}}
      this.dropdown = {query: null, show: query => {this.dropdown.query = query}, hide: () => {this.dropdown.query = null}}
    }
    on(name, callback) {this.listeners[name] = callback}
    loading(value) {this.busy = value}
  }
  const context = {URL, AbortController, window: {location: {origin: 'https://example.test',
    assign: url => navigation.push(url)}},
    document: {getElementById: id => id === 'home-search' ? input : status,
      addEventListener(name, callback) {assert.equal(name, 'DOMContentLoaded'); ready = callback}},
    setTimeout(callback, delay) {assert.equal(delay, 250); const id = ++timerId; timers.set(id, callback); return id},
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {requests.push({url: new URL(url), ...options}); return fetcher(url, options)}}
  if (available) context.Tagify = Finder
  runInNewContext(script, context)
  assert.equal(finder, undefined)
  ready()
  return {finder, form, status, input, requests, navigation, timers,
    type: text => finder.listeners.input({detail: {value: text}}),
    select: item => finder.listeners.add({detail: {data: item}}),
    tick() {const [id, callback] = [...timers][0] || []; assert.ok(callback); timers.delete(id); return callback()}}
}

test('finder waits for deferred dependencies and exposes accessible selection-only input', () => {
  const app = setup()
  assert.equal(app.finder.settings.mode, 'select')
  assert.equal(app.finder.settings.enforceWhitelist, true)
  assert.equal(app.finder.DOM.input.attrs['aria-describedby'], app.status.id)
  assert.match(app.finder.DOM.input.attrs['aria-label'], /category or tag/)
  assert.ok(app.finder.DOM.scope.classList.contains('text-body'))
  const fallback = setup({available: false})
  assert.equal(fallback.finder, undefined)
  assert.equal(fallback.form.action, 'https://example.test/articles')
})

test('categories appear immediately, tag requests debounce and use only the published API', async () => {
  const app = setup({fetcher: async () => reply([
    {name: 'API design tag', slug: 'api-design', articles_count: 15},
    {name: 'Empty tag', slug: 'empty', articles_count: 0}
  ])})
  app.type('API')
  assert.equal(app.requests.length, 0)
  assert.equal(app.finder.whitelist[0].kind, 'Category')
  app.type('API Design')
  assert.equal(app.timers.size, 1)
  await app.tick()
  assert.equal(app.requests.length, 1)
  const url = app.requests[0].url
  assert.equal(url.origin, 'https://api.example.test')
  assert.equal(url.pathname, '/tags')
  assert.equal(url.searchParams.get('prefix'), 'api-design')
  assert.equal(url.searchParams.get('limit'), '4')
  assert.equal(url.searchParams.get('published_only'), 'true')
  assert.deepEqual(Array.from(app.finder.whitelist, item => item.kind), ['Category', 'Tag'])
  assert.equal(app.finder.whitelist[1].count, 15)
  assert.equal(app.finder.busy, false)
})

test('selection navigates to existing filters and preserves returned Unicode and legacy slugs', async () => {
  const app = setup({fetcher: async () => reply([{name: 'Кешування', slug: 'legacy__Кешування', articles_count: 1}])})
  app.type('Кешування')
  await app.tick()
  assert.equal(app.requests[0].url.searchParams.get('prefix'), 'кешування')
  app.select(app.finder.whitelist[0])
  const url = new URL(app.navigation[0])
  assert.equal(url.origin, 'https://example.test')
  assert.equal(url.pathname, '/articles')
  assert.equal(url.searchParams.get('tags'), 'legacy__Кешування')
  assert.equal(url.searchParams.has('offset'), false)
  const category = setup()
  category.type('API')
  category.select(category.finder.whitelist[0])
  assert.equal(new URL(category.navigation[0]).searchParams.get('category'), 'api-design')
  assert.equal(category.timers.size, 0)
  assert.equal(category.requests.length, 0)
})

test('outdated responses cannot replace newer suggestions even when transport ignores abort', async () => {
  let resolveFirst
  const app = setup({fetcher: url => new URL(url).searchParams.get('prefix') === 'old'
    ? new Promise(resolve => {resolveFirst = resolve}) : Promise.resolve(reply([{slug: 'new', articles_count: 2}]))})
  app.type('old')
  const pending = app.tick()
  app.type('new')
  assert.equal(app.requests[0].signal.aborted, true)
  await app.tick()
  resolveFirst(reply([{slug: 'old', articles_count: 9}]))
  await pending
  assert.deepEqual(Array.from(app.finder.whitelist, item => item.slug), ['new'])
  assert.equal(app.finder.dropdown.query, 'new')
})

test('clearing or selecting cancels in-flight suggestions', async () => {
  let resolve
  const app = setup({fetcher: () => new Promise(done => {resolve = done})})
  app.type('API')
  const pending = app.tick()
  app.type('')
  resolve(reply([{slug: 'stale', articles_count: 1}]))
  await pending
  assert.equal(app.requests[0].signal.aborted, true)
  assert.equal(app.finder.whitelist.length, 0)
  assert.equal(app.finder.dropdown.query, null)
  assert.equal(app.status.textContent, '')
  assert.equal(app.finder.busy, false)
})

test('API failure preserves categories and provides a usable fallback message', async () => {
  const app = setup({fetcher: async () => reply({}, 503)})
  app.type('API')
  await app.tick()
  assert.equal(app.finder.whitelist[0].kind, 'Category')
  assert.match(app.status.textContent, /could not load/)
  assert.equal(app.status.classList.contains('invisible'), false)
  assert.equal(app.finder.busy, false)
})

test('empty results, long prefixes and free text submission have explicit guidance', async () => {
  const app = setup()
  app.type('no-match')
  await app.tick()
  assert.match(app.status.textContent, /No matching categories or tags/)
  app.type('x'.repeat(41))
  assert.equal(app.timers.size, 0)
  assert.match(app.status.textContent, /shorter search/)
  let prevented = false
  app.form.listeners.submit({preventDefault() {prevented = true}})
  assert.equal(prevented, true)
  assert.match(app.status.textContent, /Choose a category or tag/)
  assert.equal(app.navigation.length, 0)
  app.select({param: 'redirect_url', slug: 'https://untrusted.test'})
  assert.equal(app.navigation.length, 0)
})

test('suggestion names are escaped and counts are formatted as article counts', () => {
  const app = setup()
  const render = app.finder.settings.templates.dropdownItem
  const html = render.call({getAttributes: () => 'data-value="safe"'},
    {name: '<img src=x onerror="unsafe()"> & name', kind: 'Tag', count: 1200})
  assert.ok(html.includes('&lt;img'))
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('1,200 articles'))
  assert.ok(render.call({getAttributes: () => ''}, {name: 'One', kind: 'Category', count: 1}).includes('1 article<'))
})
