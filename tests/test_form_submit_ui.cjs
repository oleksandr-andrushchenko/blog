const assert = require("node:assert/strict")
const test = require("node:test")
const {installFormSubmit} = require("./form_ui_harness.cjs")
const flush = () => new Promise(resolve => setImmediate(resolve))
const reply = (body = {}, status = 200) => ({ok: status < 400, status, json: async () => body})

function setup({fields = [{name: "text", value: "A message", required: true}], options = {}, handler} = {}) {
  const button = {disabled: false}
  const form = {listeners: {}, querySelector: () => button,
    addEventListener(event, callback) {this.listeners[event] = callback}}
  const requests = []
  const context = {window: {}, document: {}, fetch: async (url, settings) => {
    const request = {url, ...settings, data: JSON.parse(settings.body)}
    requests.push(request)
    return handler ? handler(request) : reply()
  }}
  installFormSubmit(context, [{selector: "#form", form, fields}])
  context.handleFormSubmit("#form", options.url || "/messages/peer", options)
  return {form, fields, button, requests, submit: () => form.listeners.submit({preventDefault() {}})}
}

test("inline constraints reject blank and oversized values without native validation or a request", async () => {
  for (const value of ["   ", "x".repeat(5001)]) {
    const field = {name: "text", value, required: true, attrs: {maxlength: "5000"},
      reportValidity() {assert.fail("Never open native validation")}}
    const app = setup({fields: [field]})
    await app.submit()
    assert.equal(app.form.noValidate, true)
    assert.equal(app.requests.length, 0)
    assert.ok(field.classList.contains("is-invalid"))
    assert.equal(field.focused, true)
    assert.ok(field.nextElementSibling.textContent)
    assert.equal(app.button.disabled, false)
  }
})

test("duplicate submissions share one in-flight request and async success finishes before the lifecycle settles", async () => {
  let finish
  const calls = []
  const app = setup({handler: () => new Promise(resolve => {finish = resolve}), options: {
    onSuccess: async () => {await flush(); calls.push("success")},
    onFinally: () => calls.push("finally"),
  }})
  const first = app.submit()
  const second = app.submit()
  await flush()
  assert.equal(app.requests.length, 1)
  assert.equal(app.button.disabled, true)
  finish(reply())
  await Promise.all([first, second])
  assert.deepEqual(calls, ["success", "finally"])
  assert.equal(app.button.disabled, false)
})

test("preparation failures preserve the draft, restore controls, and display error text safely", async () => {
  const text = '<img src=x onerror="alert(1)">'
  let observed
  const app = setup({options: {
    prepareData: () => {throw new Error(text)}, onError: error => {observed = error.message},
  }})
  await app.submit()
  assert.equal(app.requests.length, 0)
  assert.equal(app.button.disabled, false)
  assert.equal(app.fields[0].value, "A message")
  assert.equal(observed, text)
  assert.ok(app.form.statusDiv.textContent.includes(text))
  assert.equal(app.form.statusDiv.innerHTML, "")
})

test("server field errors stay inline and reach page lifecycle hooks with their status", async () => {
  let status
  const app = setup({handler: () => reply({details: {text: "Use a shorter message"}}, 422),
    options: {onError: error => {status = error.status}}})
  await app.submit()
  assert.equal(status, 422)
  assert.ok(app.fields[0].classList.contains("is-invalid"))
  assert.equal(app.fields[0].nextElementSibling.textContent, "Use a shorter message")
  assert.equal(app.fields[0].value, "A message")
})

test("dynamic URLs and prepared payloads preserve input values when reset is disabled", async () => {
  let destination = "/old"
  const app = setup({options: {
    url: () => destination, resetOnSuccess: false, successMessage: "",
    prepareData: data => ({body: data.text}), beforeSubmit: () => {destination = "/current"},
    onFinally: () => {app.button.disabled = true},
  }})
  app.fields[0].value = "A new draft"
  await app.submit()
  assert.equal(app.requests[0].url, "/current")
  assert.deepEqual(app.requests[0].data, {body: "A new draft"})
  assert.equal(app.fields[0].value, "A new draft")
  assert.equal(app.button.disabled, true)
})

test("a cancelled pre-submit hook sends nothing and does not show a false failure", async () => {
  const app = setup({options: {beforeSubmit: () => false}})
  await app.submit()
  assert.equal(app.requests.length, 0)
  assert.ok(app.form.statusDiv.className.includes("d-none"))
  assert.equal(app.button.disabled, false)
})
