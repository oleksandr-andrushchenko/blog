const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const source = readFileSync(join(__dirname, "../static/scripts.js"), "utf8")
const start = source.indexOf("function initializeSiteNotifications(root)")
const script = source.slice(start, source.indexOf('\ndocument.addEventListener("DOMContentLoaded"', start))
const flush = () => new Promise(resolve => setImmediate(resolve))

function element(tag = "div") {
  return {tag, children: [], dataset: {}, attrs: {}, listeners: {}, hidden: false,
    setAttribute(name, value) {this.attrs[name] = value},
    getAttribute(name) {return this.attrs[name] ?? null},
    append(...nodes) {nodes.forEach(node => {node.parent = this; this.children.push(node)})},
    replaceChildren(...nodes) {this.text = ""; this.children = []; this.append(...nodes)},
    remove() {if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this)},
    addEventListener(name, fn) {(this.listeners[name] ||= []).push(fn)},
    removeEventListener(name, fn) {this.listeners[name] = (this.listeners[name] || []).filter(item => item !== fn)},
    emit(name, event = {}) {(this.listeners[name] || []).forEach(fn => fn(event))},
    querySelectorAll(selector) {
      const matches = node => selector.split(",").some(part => {
        const attribute = part.trim().match(/^\[data-([a-z-]+)(?:="([^"]+)")?\]$/)
        if (!attribute) return false
        const field = attribute[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
        return field in node.dataset && (attribute[2] === undefined || node.dataset[field] === attribute[2])
      })
      return this.children.flatMap(node => [...(matches(node) ? [node] : []), ...node.querySelectorAll(selector)])
    },
    closest(selector) {
      const field = selector.match(/data-([a-z-]+)/)[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
      return field in this.dataset ? this : this.parent?.closest(selector) || null
    },
    set textContent(value) {this.text = String(value); this.children = []},
    get textContent() {return (this.text || "") + this.children.map(node => node.textContent).join("")},
    set innerHTML(_) {assert.fail("Notification text must be inserted as text, never HTML")}}
}

function setup({seen = 1000000, unread = 0, revision = 0, hidden = false, memory = new Map(), switched = false, wholeScript = false} = {}) {
  let now = 1000000
  const requests = []
  const storageWrites = []
  const state = {user_id: switched ? "another-user" : "user", unread_count: unread, revision,
    last_seen_at: seen, presence_interval_ms: 300000}
  const records = []
  const body = element("body")
  const root = element("li")
  root.id = "site-notifications"
  root.dataset = {userId: "user", unread: String(unread), revision: String(revision), lastSeen: String(seen),
    statusUrl: "https://api.example/notifications/status", historyUrl: "https://api.example/notifications/history",
    presenceUrl: "https://api.example/presence", readUrl: "https://api.example/notifications/notification-id/read"}
  const items = element()
  const toggle = element("button")
  const badge = element("span")
  badge.dataset.unreadNotifications = ""
  root.append(items, toggle, badge)
  root.querySelector = selector => selector === "#site-notification-items" ? items : toggle
  body.append(root)
  const document = element("document")
  document.body = body
  document.hidden = hidden
  document.hasFocus = () => true
  document.createElement = element
  document.createTextNode = text => {const node = element("text"); node.textContent = text; return node}
  document.getElementById = id => {
    const find = node => node.id === id ? node : node.children.map(find).find(Boolean)
    return find(body)
  }
  document.querySelectorAll = selector => body.querySelectorAll(selector)
  let reloads = 0
  const window = element("window")
  window.location = {reload: () => {reloads++}}
  let interval
  const context = {root, document, window, URL, AbortController, Date: {now: () => now},
    localStorage: {getItem: key => memory.get(key) || null, setItem: (key, value) => {memory.set(key, value); storageWrites.push(key)}},
    setTimeout: () => 1, clearTimeout() {}, setInterval: fn => {interval = fn; return 1}, clearInterval() {},
    fetch: async (url, options) => {
      const path = new URL(url).pathname
      requests.push({path, options})
      let data
      if (path === "/presence") {state.last_seen_at = now; data = {...state}}
      else if (path === "/notifications/status") data = {...state}
      else if (path.endsWith("/read")) {
        const id = path.split("/")[2]
        const record = records.find(item => item.id === id)
        if (record && !record.is_read) {record.is_read = true; state.unread_count--; state.revision++}
        data = {...state}
      } else data = {user_id: state.user_id, notifications: records, offset: null}
      return {ok: true, json: async () => data}
    }}
  if (wholeScript) {
    const chain = {on() {return this}, each() {return this}, attr() {return this}}
    const $ = () => chain
    $.ajaxSetup = () => {}
    $.fn = {}
    window.CONFIG = {init_tinymce: false}
    document.readyState = "loading"
    context.$ = $
    context.bootstrap = {Tooltip: class {}}
    context.IntersectionObserver = class {observe() {}}
    runInNewContext(source, context)
    document.emit("DOMContentLoaded")
  } else {
    runInNewContext(script + "\ninitializeSiteNotifications(root)", context)
  }
  return {root, toggle, document, window, body, items, badge, state, records, requests, storageWrites, memory,
    advance: ms => {now += ms}, tick: () => interval(), reloads: () => reloads}
}

const notification = (id = "a".repeat(64)) => ({id, title: "<script>private update</script>", message: "A new message",
  url: "/messages/sender", icon: "chat-dots", is_read: false, delivery: "web"})

test("fresh page loads use the server-rendered count without querying status or notification history", async () => {
  const app = setup({unread: 3, revision: 7})
  await flush()
  assert.equal(app.requests.length, 0)
  assert.equal(app.badge.textContent, "3")
  app.advance(30000)
  await app.tick()
  assert.deepEqual(app.requests.map(request => request.path), ["/notifications/status"])
  const anotherPage = setup({unread: 3, revision: 7, memory: app.memory})
  await flush()
  assert.equal(anotherPage.requests.length, 0)
})

test("presence is throttled and requires recent visible interaction", async () => {
  const app = setup({seen: 0})
  await flush()
  const beats = () => app.requests.filter(request => request.path === "/presence")
  assert.equal(beats().length, 1)
  app.document.emit("scroll")
  app.document.emit("keydown")
  await flush()
  assert.equal(beats().length, 1)
  app.advance(300000)
  await app.tick()
  assert.equal(beats().length, 1)
  app.document.emit("pointerdown")
  await flush()
  assert.equal(beats().length, 2)
  app.document.hidden = true
  app.advance(300000)
  app.document.emit("scroll")
  await app.tick()
  assert.equal(beats().length, 2)
})

test("a changed revision loads alerts and renders untrusted text safely", async () => {
  const app = setup()
  await flush()
  app.records.push(notification())
  app.state.unread_count = 1
  app.state.revision = 1
  app.advance(30000)
  await app.tick()
  await flush()
  assert.equal(app.requests.filter(request => request.path === "/notifications/history").length, 1)
  assert.equal(app.badge.textContent, "1")
  const toasts = app.document.getElementById("site-notification-toasts")
  assert.equal(toasts.children.length, 1)
  assert.ok(toasts.textContent.includes("<script>private update</script>"))
  app.toggle.emit("show.bs.dropdown")
  await flush()
  assert.equal(toasts.children.length, 1)
})

test("storage events update badges without echoing writes or fetching history", async () => {
  const app = setup()
  await flush()
  const writes = app.storageWrites.length
  app.window.emit("storage", {key: "sysdespro-notifications-status:user",
    newValue: JSON.stringify({unread_count: 5, revision: 4, at: 1000000})})
  assert.equal(app.badge.textContent, "5")
  assert.equal(app.storageWrites.length, writes)
  assert.equal(app.requests.length, 0)
})

test("read actions use server counts and leave the page's other content intact", async () => {
  const app = setup({unread: 2, revision: 2})
  app.records.push(notification())
  app.toggle.emit("show.bs.dropdown")
  await flush()
  const button = app.items.querySelectorAll("[data-read-notification]")[0]
  app.document.emit("click", {target: button})
  await flush()
  assert.equal(app.badge.textContent, "1")
  assert.equal(app.items.querySelectorAll("[data-read-notification]").length, 0)
  assert.equal(app.requests.filter(request => request.path.endsWith("/read")).length, 1)
})

test("responses for another signed-in account reload instead of caching or rendering its alerts", async () => {
  const app = setup({seen: 0, switched: true})
  await flush()
  assert.equal(app.reloads(), 1)
  assert.equal(app.badge.textContent, "0")
  app.advance(30000)
  await app.tick()
  assert.equal(app.requests.length, 1)
})


test("opening the bell loads notifications into the dropdown and reopening refreshes them even with no unread count", async () => {
  const app = setup()
  await flush()
  assert.equal(app.requests.length, 0)
  app.toggle.emit("show.bs.dropdown")
  assert.equal(app.items.attrs["aria-busy"], "true")
  assert.equal(app.items.textContent, "Loading notifications…")
  await flush()
  assert.equal(app.items.attrs["aria-busy"], "false")
  assert.equal(app.items.textContent, "You're all caught up.")
  app.toggle.emit("hidden.bs.dropdown")
  app.records.push({...notification(), is_read: true})
  app.toggle.emit("show.bs.dropdown")
  await flush()
  assert.equal(app.requests.filter(request => request.path === "/notifications/history").length, 2)
  assert.equal(app.items.querySelectorAll("[data-notification-item]").length, 1)
  assert.ok(app.items.textContent.includes("A new message"))
  assert.equal(app.items.querySelectorAll("[data-read-notification]").length, 0)
})


test("clicking the header bell loads its contents and coalesces the Bootstrap opening event", async () => {
  const app = setup()
  app.records.push(notification())
  app.toggle.emit("click")
  app.toggle.emit("show.bs.dropdown")
  await flush()
  assert.equal(app.requests.filter(request => request.path === "/notifications/history").length, 1)
  assert.equal(app.items.querySelectorAll("[data-notification-item]").length, 1)
  app.toggle.setAttribute("aria-expanded", "true")
  app.toggle.emit("click")
  await flush()
  assert.equal(app.requests.length, 1)
})


test("the full shared script initializes the bell and loads existing notifications on its first opening", async () => {
  const app = setup({wholeScript: true})
  app.records.push({...notification(), is_read: true})
  await flush()
  assert.equal(app.requests.length, 0)
  app.toggle.emit("click")
  app.toggle.emit("show.bs.dropdown")
  await flush()
  assert.equal(app.requests.filter(request => request.path === "/notifications/history").length, 1)
  assert.equal(app.items.querySelectorAll("[data-notification-item]").length, 1)
  assert.ok(app.items.textContent.includes("A new message"))
})
