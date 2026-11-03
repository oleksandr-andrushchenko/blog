const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const {installFormSubmit} = require("./form_ui_harness.cjs")
const template = readFileSync(join(__dirname, "../web-lambda/templates/fragments/user-status.html"), "utf8")
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]

function setup(responseStatus = 200) {
  const radios = ["active", "banned"].map(value => ({
    name: "status", type: "radio", value, checked: value === "active",
    addEventListener(_event, callback) {this.change = callback}
  }))
  const comment = {name: "comment", value: "", required: false}
  const button = {disabled: false}
  const form = {action: "/users/example/status", listeners: {}, elements: {comment, status: {
    get value() {return radios.find(radio => radio.checked).value}
  }}, addEventListener(event, callback) {this.listeners[event] = callback}, querySelector: () => button}
  let reloads = 0
  const requests = []
  const context = {window: {location: {reload() {reloads++}}},
    document: {getElementById: () => form, addEventListener: (_event, callback) => callback()},
    fetch: async (url, settings) => {
      requests.push({url, data: JSON.parse(settings.body)})
      return {ok: responseStatus === 200, status: responseStatus,
        json: async () => responseStatus === 200 ? "/profile" : {message: "Permission denied"}}
    }}
  installFormSubmit(context, [{selector: "#user-status-form", form, fields: [...radios, comment]}])
  form.elements.status = {get value() {return radios.find(radio => radio.checked).value}}
  const queryAll = form.querySelectorAll
  form.querySelectorAll = selector => selector === 'input[name="status"]' ? radios : queryAll(selector)
  runInNewContext(script, context)
  return {form, comment, button, requests, reloads: () => reloads,
    select(value) {
      radios.forEach(radio => {radio.checked = radio.value === value})
      radios[0].change()
    }, submit: () => form.listeners.submit({preventDefault() {}})}
}

test("banning requires a nonblank inline reason and preserves the draft", async () => {
  const app = setup()
  app.select("banned")
  assert.equal(app.comment.attrs["aria-required"], "true")
  app.comment.value = "   "
  await app.submit()
  assert.equal(app.requests.length, 0)
  assert.equal(app.comment.focused, true)
  assert.equal(app.button.disabled, false)
  app.comment.value = " Needs moderation "
  await app.submit()
  assert.deepEqual(app.requests[0].data, {status: "banned", comment: "Needs moderation"})
  assert.equal(app.comment.value, " Needs moderation ")
  assert.equal(app.reloads(), 1)
})

test("activation clears the stored ban reason without erasing an unsaved draft", async () => {
  const app = setup()
  app.select("banned")
  app.comment.value = "Previous reason"
  app.select("active")
  assert.equal(app.comment.attrs["aria-required"], "false")
  await app.submit()
  assert.deepEqual(app.requests[0].data, {status: "active", comment: null})
  assert.equal(app.comment.value, "Previous reason")
})

test("permission errors keep the selected status and reason inline", async () => {
  const app = setup(403)
  app.select("banned")
  app.comment.value = "Needs moderation"
  await app.submit()
  assert.equal(app.reloads(), 0)
  assert.equal(app.form.elements.status.value, "banned")
  assert.equal(app.comment.value, "Needs moderation")
  assert.equal(app.button.disabled, false)
  assert.ok(app.form.statusDiv.textContent.includes("Permission denied"))
})
