const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const {installFormSubmit} = require("./form_ui_harness.cjs")
const template = readFileSync(join(__dirname, "../web-lambda/templates/fragments/user-report.html"), "utf8")
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]

function setup({kind = "User", resource = "URL: https://example.test/@Author", status = 204} = {}) {
  const prefix = "user"
  const detailsLimit = 1000 - `${kind} report\nReason: Impersonation or misleading profile\n${resource}\n\nDetails: `.length
  const fields = [
    {name: "reason", value: ""},
    {name: "details", value: "", maxLength: detailsLimit},
    {name: "name", value: ""},
    {name: "email", value: "", type: "email"}
  ]
  const button = {disabled: false}
  const form = {action: "https://api.example.test/contacts/message", dataset: {reportKind: kind, reportResource: resource, reportDetailsLimit: String(detailsLimit)},
    elements: Object.fromEntries(fields.map(field => [field.name, field])), listeners: {},
    querySelector: () => button, addEventListener(event, callback) {this.listeners[event] = callback}}
  const requests = []
  const context = {window: {CONFIG: {current_user: null}},
    document: {getElementById: () => form, addEventListener: (_event, callback) => callback()},
    fetch: async (url, settings) => {
      requests.push({url, ...settings, data: JSON.parse(settings.body)})
      return {ok: status < 400, status, json: async () => ({message: "Please try again later."})}
    }}
  installFormSubmit(context, [{selector: `#${prefix}-report-form`, form, fields}])
  let options
  const submitForm = context.handleFormSubmit
  context.handleFormSubmit = (selector, url, settings) => {
    options = settings
    return submitForm(selector, url, settings)
  }
  runInNewContext(script, context)
  return {form, button, requests, options, fields: form.elements,
    fill() {
      form.elements.reason.value = "Impersonation or misleading profile"
      form.elements.name.value = "Guest reader"
      form.elements.email.value = "guest@example.test"
    }, submit: () => form.listeners.submit({preventDefault() {}})}
}

test("guests submit a canonical resource report and see acknowledgment in the popup", async () => {
  const app = setup()
  app.fill()
  app.fields.details.value = " Please review this profile. "
  await app.submit()
  assert.equal(app.requests.length, 1)
  assert.equal(app.requests[0].url, app.form.action)
  assert.equal(app.requests[0].method, "POST")
  assert.deepEqual(app.requests[0].data, {name: "Guest reader", email: "guest@example.test",
    message: "User report\nReason: Impersonation or misleading profile\nURL: https://example.test/@Author\n\nDetails: Please review this profile."})
  assert.ok(app.form.statusDiv.innerHTML.includes("Report sent"))
  assert.equal(app.fields.details.value, "")
  assert.equal(app.button.disabled, false)
})

test("report fields register explicit required and length rules", () => {
  const app = setup()
  assert.equal(app.options.rules.reason[0].rule, "required")
  assert.equal(app.options.rules.details[0].rule, "maxLength")
  assert.equal(app.options.rules.details[0].value, app.fields.details.maxLength)
  assert.equal(app.options.rules.name[1].value, 2)
  assert.equal(app.options.rules.name[2].value, 100)
  assert.equal(app.options.rules.email[1].rule, "email")
})

test("user ID fallbacks fit the API limit and retain drafts after an error", async () => {
  const app = setup({kind: "User", resource: "User ID: " + "q".repeat(600), status: 500})
  app.fill()
  app.fields.details.value = "x".repeat(app.fields.details.maxLength)
  await app.submit()
  assert.equal(app.requests[0].data.message.length, 1000)
  assert.ok(app.requests[0].data.message.startsWith("User report\n"))
  assert.equal(app.fields.details.value.length, app.fields.details.maxLength)
  assert.ok(app.form.statusDiv.textContent.includes("Please try again later"))
  assert.equal(app.button.disabled, false)
})
