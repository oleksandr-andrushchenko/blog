const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const {installFormSubmit} = require("./form_ui_harness.cjs")
const template = readFileSync(join(__dirname, "../web-lambda/templates/direct-conversation.html"), "utf8")
const script = template.slice(template.indexOf("      function initializeDirectConversation"),
  template.indexOf('      document.addEventListener("DOMContentLoaded"'))
const flush = () => new Promise(resolve => setImmediate(resolve))
const key = number => `MESSAGE#${String(number).padStart(20, "0")}#00000000-0000-0000-0000-${String(number).padStart(12, "0")}`
const message = (number, sender = "peer") => ({id: `id-${number}`, key: key(number), sender_id: sender, text: `Text ${number}`})
const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data})

function setup({initial = [], hidden = false, handler} = {}) {
  const classes = () => ({add() {}, remove() {}, toggle() {}})
  const node = item => ({dataset: {messageId: item.id, messageKey: item.key}})
  const list = {children: initial.map(node), querySelector(selector) {
    const id = selector.match(/data-message-id="([^"]+)"/)[1]
    return this.children.find(child => child.dataset.messageId === id)
  }, insertBefore(item, next) {
    const index = next ? this.children.indexOf(next) : this.children.length
    this.children.splice(index, 0, item)
  }}
  const button = {disabled: false, addEventListener(event, fn) {this[event] = fn}}
  const older = {...button, dataset: {offset: "old-cursor"}}
  const refresh = {...button}
  const label = {textContent: "Send message"}
  const send = {...button, querySelector: () => label}
  const text = {name: "text", value: "", readOnly: false, required: true, attrs: {minlength: "1", maxlength: "5000"}, focus() {}}
  const form = {addEventListener(event, fn) {this[event] = fn},
    querySelector: selector => selector === "textarea" ? text : send}
  const status = {textContent: "", classList: classes()}
  const elements = {"#direct-message-list": list, "#direct-message-status": status,
    "#older-direct-messages": older, "#refresh-direct-messages": refresh,
    "#direct-message-form": form, "#empty-direct-messages": {classList: classes()}}
  const root = {dataset: {latestKey: initial.at(-1)?.key || key(0), historyUrl: "https://api.example/messages/peer/history",
    sendUrl: "https://api.example/messages/peer", readUrl: "https://api.example/messages/peer/read"},
  querySelector: selector => elements[selector]}
  const document = {hidden, listeners: {}, createElement: () => ({set innerHTML(value) {this.children = JSON.parse(value).map(node)}}),
    addEventListener(event, fn) {this.listeners[event] = fn}, removeEventListener(event) {delete this.listeners[event]}}
  const window = {listeners: {}, location: {reload() {}}, addEventListener(event, fn) {this.listeners[event] = fn}}
  const requests = []
  const server = [...initial]
  let sequence = 0
  let tick
  let stopped = false
  const context = {root, document, window, URL, crypto: {randomUUID: () => `client-${++sequence}`},
    setInterval: fn => {tick = fn; return 1}, clearInterval: () => {stopped = true},
    fetch: async (url, options) => {
      const request = {url: String(url), options, body: options.body ? JSON.parse(options.body) : null}
      requests.push(request)
      if (handler) {
        const result = await handler(request, server)
        if (result !== undefined) return result
      }
      if (request.url.endsWith("/read")) return response(null, 204)
      if (options.method === "POST") {
        let item = server.find(item => item.id === request.body.message_id)
        if (!item) {
          item = {...message(server.length + 1, "viewer"), id: request.body.message_id, text: request.body.text}
          server.push(item)
        }
        return response({message: item, html: JSON.stringify([item])})
      }
      const after = new URL(request.url).searchParams.get("after")
      const messages = server.filter(item => item.key > after)
      return response({messages, html: JSON.stringify(messages), offset: null})
    }}
  installFormSubmit(context, [{selector: "#direct-message-form", form, fields: [text]}])
  let submissionOptions
  const handleFormSubmit = context.handleFormSubmit
  context.handleFormSubmit = (selector, url, options) => {
    submissionOptions = options
    return handleFormSubmit(selector, url, options)
  }
  runInNewContext(script + "\ninitializeDirectConversation(root)", context)
  return {submissionOptions, root, document, window, requests, server, list, form, text, send, label, status, older, refresh,
    tick: () => tick(), stopped: () => stopped, submit: () => form.submit({preventDefault() {}})}
}

test("an uncertain send preserves its draft and UUID until an explicit retry acknowledges it", async () => {
  let fail = true
  const app = setup({handler: async (request, server) => {
    if (request.options.method === "POST" && !request.url.endsWith("/read") && fail) {
      fail = false
      server.push({...message(1, "viewer"), id: request.body.message_id, text: request.body.text})
      throw new Error("Network disconnected after delivery")
    }
  }})
  await flush()
  app.text.value = "  Keep this draft  "
  await app.submit()
  assert.equal(app.text.value, "  Keep this draft  ")
  assert.equal(app.text.readOnly, true)
  assert.equal(app.label.textContent, "Retry send")
  app.tick()
  await flush()
  const sends = () => app.requests.filter(request => request.options.method === "POST" && !request.url.endsWith("/read"))
  assert.equal(sends().length, 1)
  await app.submit()
  await flush()
  assert.equal(sends().length, 2)
  assert.deepEqual(sends()[0].body, sends()[1].body)
  assert.equal(sends()[0].body.text, "Keep this draft")
  assert.equal(app.server.length, 1)
  assert.equal(app.list.children.length, 1)
  assert.equal(app.text.value, "")
  assert.equal(app.text.readOnly, false)
})

test("concurrent incoming messages merge before the sent reply without duplication or cursor gaps", async () => {
  let incoming = false
  const app = setup({handler: async (request, server) => {
    if (request.options.method === "POST" && !request.url.endsWith("/read") && !incoming) {
      incoming = true
      server.push(message(1))
    }
  }})
  await flush()
  app.text.value = "Reply"
  await app.submit()
  await flush()
  assert.deepEqual(app.list.children.map(item => item.dataset.messageKey), [key(1), key(2)])
  const read = app.requests.filter(request => request.url.endsWith("/read")).at(-1)
  assert.equal(read.body.message_key, key(2))
  app.tick()
  await flush()
  assert.equal(app.list.children.length, 2)
})

test("hidden pages do not poll or mark messages read, and navigation stops future work", async () => {
  const app = setup({hidden: true})
  app.server.push(message(1))
  app.tick()
  await flush()
  assert.equal(app.requests.length, 0)
  app.document.hidden = false
  app.document.listeners.visibilitychange()
  await flush()
  assert.equal(app.list.children.length, 1)
  assert.equal(app.requests.filter(request => request.url.endsWith("/read")).length, 1)
  app.window.listeners.pagehide()
  assert.equal(app.stopped(), true)
  const count = app.requests.length
  app.tick()
  app.text.value = "Leave unsent"
  await app.submit()
  assert.equal(app.requests.length, count)
})

test("older history is prepended in order without moving the newest read position backwards", async () => {
  const app = setup({initial: [message(3), message(4)], handler: async request => {
    if (new URL(request.url).searchParams.has("offset")) {
      const messages = [message(1), message(2), message(3)]
      return response({messages, html: JSON.stringify(messages), offset: null})
    }
  }})
  await flush()
  await app.older.click()
  app.tick()
  await flush()
  assert.deepEqual(app.list.children.map(item => item.dataset.messageKey), [key(1), key(2), key(3), key(4)])
  assert.equal(app.older.hidden, true)
  const positions = app.requests.filter(request => request.url.endsWith("/read")).map(request => request.body.message_key)
  assert.deepEqual(positions, [key(4)])
})

test("the message form registers whitespace and length rules with the shared handler", async () => {
  const app = setup()
  await flush()
  const rules = app.submissionOptions.rules.text
  assert.ok(rules.some(rule => rule.rule === "required"))
  assert.equal(rules.find(rule => rule.rule === "maxLength").value, 5000)
  const nonblank = rules.find(rule => rule.validator).validator
  assert.equal(nonblank("   "), false)
  assert.equal(nonblank("A message"), true)
  assert.equal(app.form.noValidate, true)
  assert.equal(app.requests.filter(request => request.options.method === "POST").length, 0)
})

test("a definitive message rejection releases the draft for editing and uses a new ID on the next send", async () => {
  let reject = true
  const app = setup({handler: request => {
    if (request.options.method === "POST" && !request.url.endsWith("/read") && reject) {
      reject = false
      return response({details: {text: "Please review this message."}}, 422)
    }
  }})
  await flush()
  app.text.value = "First draft"
  await app.submit()
  assert.equal(app.text.readOnly, false)
  assert.equal(app.text.value, "First draft")
  app.text.value = "Edited draft"
  await app.submit()
  const sends = app.requests.filter(request => request.options.method === "POST" && !request.url.endsWith("/read"))
  assert.notEqual(sends[0].body.message_id, sends[1].body.message_id)
  assert.equal(app.text.value, "")
})
