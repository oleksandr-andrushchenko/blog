const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const {installFormSubmit} = require("./form_ui_harness.cjs")

class ValidationStub {
  constructor() {this.fields = {}; this.errors = {}}
  addField(selector, rules) {this.fields[selector] = rules}
  onSuccess(callback) {this.success = callback}
  showErrors(errors) {this.errors = errors}
  refresh() {}
}

function setup({prefix = "user", resource, status = 204, body, JustValidate} = {}) {
  const kind = prefix === "user" ? "User" : "Article"
  resource ||= prefix === "user" ? "URL: https://example.test/@Author" : "Article: https://example.test/@Author/example"
  const reason = prefix === "user" ? "Impersonation or misleading profile" : "spam"
  const reasonLabel = prefix === "user" ? reason : "Spam"
  const detailsLimit = prefix === "user"
    ? 1000 - `${kind} report\nReason: ${reasonLabel}\n${resource}\n\nDetails: `.length
    : 1000 - resource.replace("Article: ", "").length - 100
  const fields = [
    {name: "reason", value: ""}, {name: "message", value: "", maxLength: detailsLimit},
    {name: "name", value: ""}, {name: "email", value: "", type: "email"}
  ]
  const button = {disabled: false}
  const form = {action: `https://api.example.test/${prefix === "user" ? "users" : "articles"}/creator/report`,
    dataset: {reportKind: kind, reportResource: resource, reportDetailsLimit: String(detailsLimit),
      reportUrl: resource.replace("Article: ", ""), reportReasons: JSON.stringify({spam: "Spam"})},
    elements: Object.fromEntries(fields.map(field => [field.name, field])), listeners: {},
    querySelector: () => button, addEventListener(event, callback) {this.listeners[event] = callback}}
  const requests = []
  const context = {window: {CONFIG: {current_user: null}, JustValidate},
    document: {getElementById: () => form, addEventListener: (_event, callback) => callback()},
    fetch: async (url, settings) => {
      requests.push({url, ...settings, data: JSON.parse(settings.body)})
      return {ok: status < 400, status, json: async () => body || {message: "Please try again later."}}
    }}
  installFormSubmit(context, [{selector: `#${prefix}-report-form`, form, fields}])
  let options, validator
  const submitForm = context.handleFormSubmit
  context.handleFormSubmit = (selector, url, settings) => {
    options = settings
    return validator = submitForm(selector, url, settings)
  }
  const template = readFileSync(join(__dirname, `../web-lambda/templates/fragments/${prefix}-report.html`), "utf8")
  runInNewContext(template.match(/<script>([\s\S]*?)<\/script>/)[1], context)
  return {form, button, requests, options, validator, fields: form.elements,
    fill() {
      form.elements.reason.value = reason
      form.elements.name.value = "Guest reader"
      form.elements.email.value = "guest@example.test"
    },
    expectedMessage: details => `${kind} report\nReason: ${reasonLabel}\n${resource}\n\nDetails: ${details}`,
    submit: () => validator ? validator.success() : form.listeners.submit({preventDefault() {}})}
}

for (const prefix of ["user", "article"]) {
  test(`${prefix} reports use the shared handler and preserve the canonical resource`, async () => {
    const app = setup({prefix})
    app.fill()
    app.fields.message.value = " Please review this resource. "
    await app.submit()
    assert.equal(app.requests.length, 1)
    assert.equal(app.requests[0].url, app.form.action)
    assert.deepEqual(app.requests[0].data, {name: "Guest reader", email: "guest@example.test",
      message: app.expectedMessage("Please review this resource.")})
    assert.ok(app.form.statusDiv.innerHTML.includes("Report sent"))
    assert.equal(app.fields.message.value, "")
    assert.equal(app.button.disabled, false)
  })

  test(`${prefix} reports register frontend validation and wait for its success callback`, async () => {
    const app = setup({prefix, JustValidate: ValidationStub})
    assert.equal(app.validator.fields['[name="reason"]'][0].rule, "required")
    assert.equal(app.validator.fields['[name="message"]'][0].rule, "maxLength")
    assert.equal(app.validator.fields['[name="message"]'][0].value, app.fields.message.maxLength)
    assert.equal(app.validator.fields['[name="name"]'][1].value, 2)
    assert.equal(app.validator.fields['[name="name"]'][2].value, 100)
    assert.equal(app.validator.fields['[name="email"]'][1].rule, "email")
    assert.equal(app.form.listeners.submit, undefined)
    assert.equal(app.requests.length, 0)
    app.fill()
    await app.submit()
    assert.equal(app.requests.length, 1)
  })

  test(`${prefix} report backend errors attach to the visible fields and preserve drafts`, async () => {
    const app = setup({prefix, JustValidate: ValidationStub, status: 422,
      body: {details: {name: "Use a longer name", email: "Invalid email", message: "Use shorter details"}}})
    app.fill()
    app.fields.message.value = "Unsaved report details"
    await app.submit()
    assert.equal(app.validator.errors['[name="name"]'], "Use a longer name")
    assert.equal(app.validator.errors['[name="email"]'], "Invalid email")
    assert.equal(app.validator.errors['[name="message"]'], "Use shorter details")
    assert.equal(app.fields.message.value, "Unsaved report details")
    assert.equal(app.fields.email.value, "guest@example.test")
    assert.ok(app.form.statusDiv.textContent.includes("Please fix the highlighted fields"))
    assert.equal(app.button.disabled, false)
  })

  test(`${prefix} report validation responses work when the validation library is unavailable`, async () => {
    const message = '<img src=x onerror="unsafe()">'
    const app = setup({prefix, status: 422, body: {details: {message}}})
    app.fill()
    app.fields.message.value = "Unsaved report"
    await app.submit()
    assert.equal(app.fields.message.classList.contains("is-invalid"), true)
    assert.equal(app.fields.message.nextElementSibling.textContent, message)
    assert.equal(app.fields.message.value, "Unsaved report")
    assert.equal(app.button.disabled, false)
  })

  test(`${prefix} report permission failures remain visible without clearing the form`, async () => {
    const app = setup({prefix, status: 403, body: {message: "You cannot report yourself or your own articles."}})
    app.fill()
    app.fields.message.value = "Unsaved report"
    await app.submit()
    assert.ok(app.form.statusDiv.textContent.includes("You cannot report yourself"))
    assert.equal(app.fields.message.value, "Unsaved report")
    assert.equal(app.button.disabled, false)
  })
}

test("user ID fallbacks fit the API limit and retain drafts after an error", async () => {
  const app = setup({resource: "User ID: " + "q".repeat(600), status: 500})
  app.fill()
  app.fields.message.value = "x".repeat(app.fields.message.maxLength)
  await app.submit()
  assert.equal(app.requests[0].data.message.length, 1000)
  assert.equal(app.fields.message.value.length, app.fields.message.maxLength)
  assert.ok(app.form.statusDiv.textContent.includes("Please try again later"))
})
