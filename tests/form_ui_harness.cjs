// Execute the production form handler with the small DOM/transport surface used by UI tests.
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const {runInNewContext} = require("node:vm")
const source = readFileSync(join(__dirname, "../static/scripts.js"), "utf8")
const helper = source.slice(source.indexOf("function handleFormSubmit("), source.indexOf("\nconst imageUpload"))
const errors = source.slice(source.indexOf("const getApiErrorMessage ="), source.indexOf("\nconst uploadPublicFile"))

function decorate(element = {}) {
  element.children ||= []
  element.dataset ||= {}
  element.attrs ||= {}
  element.className ||= ""
  element.setAttribute ||= function (name, value) {this.attrs[name] = String(value)}
  element.getAttribute ||= function (name) {return this.attrs[name] ?? null}
  element.classList = {
    add(...names) {element.className = [...new Set([...element.className.split(/\s+/).filter(Boolean), ...names])].join(" ")},
    remove(...names) {element.className = element.className.split(/\s+/).filter(name => !names.includes(name)).join(" ")},
    contains(name) {return element.className.split(/\s+/).includes(name)},
  }
  if (!Object.getOwnPropertyDescriptor(element, "textContent")) {
    Object.defineProperty(element, "textContent", {
      get() {return (this.text || "") + this.children.map(node => node.textContent || "").join("")},
      set(value) {this.text = String(value); this.children = []}, configurable: true,
    })
  }
  element.replaceChildren ||= function (...nodes) {this.text = ""; this.children = nodes}
  return element
}

function installFormSubmit(context, records) {
  const originalCreate = context.document.createElement
  context.document.createElement = tag => {
    const node = decorate(originalCreate ? originalCreate(tag) : {})
    const originalHtml = Object.getOwnPropertyDescriptor(node, "innerHTML")
    Object.defineProperty(node, "innerHTML", {
      get() {return this.html || ""},
      set(value) {
        this.html = value
        if (originalHtml?.set && !this.className.includes("form-status")) originalHtml.set.call(this, value)
      }, configurable: true,
    })
    return node
  }
  context.document.createTextNode ||= text => ({textContent: text})
  context.document.querySelector = selector => records.find(record => record.selector === selector)?.form
  context.window.CONFIG ||= {current_user: {id: "viewer"}}
  context.$ = element => ({
    on(event, handler) {element.addEventListener(event, handler)},
    setButtonLoading() {element.disabled = true; element.loading = true},
    clearButtonLoading() {element.disabled = false; element.loading = false},
  })
  for (const {form, fields} of records) {
    form.fields = fields
    const query = form.querySelector.bind(form)
    const button = query("[type='submit']")
    button.insertAdjacentElement = (_position, element) => {form.statusDiv = element}
    form.querySelector = selector => {
      if (selector === ".form-status") return form.statusDiv || null
      const named = selector.match(/^\[name="([^"]+)"\]$/)
      return named ? fields.find(field => field.name === named[1]) : query(selector)
    }
    fields.forEach(field => {
      decorate(field)
      field.type ||= "text"
      field.value ??= ""
      field.defaultValue ??= field.value
      field.checkValidity ||= () => true
      field.focus ||= () => {field.focused = true}
      field.insertAdjacentElement ||= (_position, element) => {field.nextElementSibling = element}
    })
    form.querySelectorAll = selector => {
      if (selector.includes('input[type="file"]')) return fields.filter(field => field.type === "file")
      if (selector.includes('input[type="checkbox"]')) {
        const name = selector.match(/name="([^"]+)"/)[1]
        return fields.filter(field => field.type === "checkbox" && field.name === name)
      }
      return fields.filter(field => field.name && (!selector.includes("data-form-ignore") || !field.ignore))
    }
    form.reset ||= () => fields.forEach(field => {field.value = field.defaultValue})
  }
  context.ajaxResponse = async options => {
    const result = await context.fetch(options.url, {
      method: options.method, credentials: "include", headers: {"Content-Type": options.contentType}, body: options.data,
    })
    return {ok: result.ok, status: result.status ?? 200, json: () => result.json()}
  }
  runInNewContext(errors + "\n" + helper, context)
}

module.exports = {decorate, installFormSubmit}
