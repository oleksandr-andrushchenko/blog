// Send the auth cookie with cross-origin API requests (localhost:5000 -> localhost:5002).
$.ajaxSetup({
  xhrFields: {withCredentials: true}
})

function formatUrl(template, params = {}) {
  return template.replace(/(?:\{|%7B)(\w+)(?:\}|%7D)/gi, (match, key) =>
    Object.hasOwn(params, key)
      ? encodeURIComponent(String(params[key]))
      : match
  );
}

const ajaxResponse = options => new Promise(resolve => {
  let request
  const finish = (ok, data, xhr) => resolve({
    ok,
    status: xhr.status,
    json: async () => data ?? {},
    text: async () => typeof data === "string" ? data : JSON.stringify(data ?? {})
  })
  request = $.ajax(options).done((data, _textStatus, xhr) => finish(true, data, xhr))
    .fail(xhr => finish(false, xhr.responseJSON ?? xhr.responseText, xhr))
  if (options.signal) options.signal.addEventListener("abort", () => request.abort(), {once: true})
})
const toKebabCase = str => String(str || "").trim().toLowerCase().replace(/[^\w\s-]/g, "").replace(/\s+/g, "-")

function useImageFallback(image) {
  if (!(image instanceof HTMLImageElement) || !image.dataset.fallbackSrc) return

  const fallbackSrc = image.dataset.fallbackSrc
  delete image.dataset.fallbackSrc
  image.parentElement?.querySelector("source[type='image/webp']")?.remove()
  image.removeAttribute("srcset")
  image.removeAttribute("sizes")
  image.src = fallbackSrc
}

// Image errors do not bubble, so listen during capture. The initial scan also
// recovers images that failed before this deferred script started executing.
window.addEventListener("error", event => useImageFallback(event.target), true)
document.querySelectorAll("img[data-fallback-src]").forEach(image => {
  if (image.complete && image.naturalWidth === 0) useImageFallback(image)
})

// Own initialization: data-masonry-options avoids the library's data-masonry
// auto-init creating a second instance after DOMContentLoaded.
const masonryContainers = new WeakMap()

function observeMasonryItems(container, masonry) {
  let state = masonryContainers.get(container)
  if (!state) {
    let pendingLayout = false
    const scheduleLayout = () => {
      if (pendingLayout) return
      pendingLayout = true
      requestAnimationFrame(() => {
        pendingLayout = false
        if (container.isConnected) masonry.layout()
      })
    }
    // Listen during capture: media load/error events do not bubble. This also
    // handles lazy images, responsive sources, fallbacks, and appended cards.
    for (const event of ["load", "error", "loadedmetadata"]) {
      container.addEventListener(event, scheduleLayout, true)
    }
    const sizes = new WeakMap()
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(entries => {
      for (const {target} of entries) {
        const {width, height} = target.getBoundingClientRect()
        const previous = sizes.get(target)
        sizes.set(target, {width, height})
        if (previous && (width !== previous.width || height !== previous.height)) scheduleLayout()
      }
    })
    state = {observer, sizes}
    masonryContainers.set(container, state)
    // Moving cards must keep pace with changing media heights, without a
    // transition that temporarily leaves them overlapping.
    masonry.option({transitionDuration: 0})
  }
  for (const item of masonry.getItemElements()) {
    if (state.sizes.has(item)) continue
    const {width, height} = item.getBoundingClientRect()
    state.sizes.set(item, {width, height})
    // Observe cards rather than the grid, whose height is changed by layout().
    state.observer?.observe(item)
  }
}

function getMasonryInstance(container) {
  if (typeof Masonry === "undefined" || !container.matches("[data-masonry-options]")) return null

  let masonry = Masonry.data(container)
  try {
    if (!masonry) {
      const options = JSON.parse(container.dataset.masonryOptions || "{}")
      masonry = new Masonry(container, {...options, transitionDuration: 0})
    }
    observeMasonryItems(container, masonry)
  } catch (err) {
    console.error("Unable to initialize Masonry:", err)
  }
  return masonry
}

function initializeMasonryContainers() {
  if (typeof Masonry === "undefined") return
  document.querySelectorAll("[data-masonry-options]").forEach(getMasonryInstance)
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeMasonryContainers, {once: true})
} else {
  initializeMasonryContainers()
}


$.fn.setButtonLoading = function (loadingText = "Loading...") {
  return this.each(function () {
    const $button = $(this)

    if ($button.data("loading-original-content") === undefined) {
      $button.data("loading-original-content", $button.contents().detach())
    }

    const $spinner = $("<span>", {
      class: "spinner-border spinner-border-sm me-2",
      role: "status",
      "aria-hidden": "true"
    })

    $button
      .prop("disabled", true)
      .addClass("disabled")
      .attr("aria-busy", "true")
      .empty()
      .append($spinner, document.createTextNode(String(loadingText)))
  })
}

$.fn.clearButtonLoading = function () {
  return this.each(function () {
    const $button = $(this)
    const originalContent = $button.data("loading-original-content")

    if (originalContent !== undefined) {
      $button.empty().append(originalContent).removeData("loading-original-content")
    }

    $button
      .prop("disabled", false)
      .removeClass("disabled")
      .removeAttr("aria-busy")
  })
}

function handleFormSubmit(formSelector, submitUrl, options = {}) {
  const {
    method = "POST",
    successMessage = "Success!",
    errorMessage = "Something went wrong. Please try again.",
    validationFailedMessage = "Please fix the highlighted fields.",
    rules = {},
    prepareData = data => data,
    onSuccess = () => {},
    onError = () => {},
    onFinally = () => {},
    beforeSubmit = async () => {},
    resetOnSuccess = true,
    loadingText = "Submitting...",
    authRequired = true
  } = options

  const form = document.querySelector(formSelector)
  if (!form) return
  form.noValidate = true

  const submitBtn = form.querySelector("[type='submit']")
  const existingStatus = form.querySelector(".form-status")
  const statusDiv = existingStatus || document.createElement("div")
  statusDiv.className = "form-status alert d-none"
  statusDiv.setAttribute("role", "alert")
  if (!existingStatus) submitBtn.insertAdjacentElement("beforebegin", statusDiv)
  let submitting = false

  let validator = null
  const tagFields = new Set()

  if (typeof window.JustValidate !== "undefined") {
    validator = new window.JustValidate(formSelector, {
      errorFieldCssClass: "is-invalid",
      errorLabelCssClass: "invalid-feedback",
      successFieldCssClass: "is-valid",
      successLabelCssClass: "valid-feedback",
      focusInvalidField: true,
      lockForm: true
    })

    Object.entries(rules).forEach(([field, fieldRules]) => {
      for (const fieldRuleId in fieldRules) {
        if (fieldRules[fieldRuleId].rule === "tags") {
          const {minCnt, maxCnt, minLen, maxLen} = fieldRules[fieldRuleId]
          fieldRules[fieldRuleId] = {
            validator: (value) => {
              if (!value) return true
              let values
              try {
                values = JSON.parse(value)
              } catch (error) {
                return false
              }
              if (!Array.isArray(values)) return true
              const normalizeKebab = form.elements[field].dataset.normalizeKebab === "true"
              const items = values
                .map(item => typeof item === "string" ? item : item.value)
                .map(item => normalizeKebab ? toKebabCase(item) : String(item || "").trim())
                .filter(Boolean)
              return items.length >= minCnt && items.length <= maxCnt && items.every(t => t.length >= minLen && t.length <= maxLen)
            }, errorMessage: `Values must be ${minCnt}–${maxCnt} items, ${minLen}–${maxLen} chars each`
          }
          tagFields.add(field)
        }
      }

      validator.addField(`[name="${field}"]`, fieldRules)
    })

    validator.onSuccess(() => submitForm())
  } else {
    // Keep inline constraint feedback when JustValidate is unavailable.
    $(form).on("submit", (e) => {
      e.preventDefault()
      return submitForm()
    })
  }

  async function submitForm() {
    if (submitting) return
    submitting = true
    $(submitBtn).setButtonLoading(loadingText)
    statusDiv.className = "form-status alert d-none"
    statusDiv.textContent = ""
    let msgClass = "danger"
    let msgIcon = "exclamation-triangle-fill"
    let msgText = errorMessage

    try {
      let data = {}
      form.querySelectorAll("input[name]:not([data-form-ignore]), select[name]:not([data-form-ignore]), textarea[name]:not([data-form-ignore])").forEach(input => {
        if (input.disabled) return

        // handle radio buttons separately
        if (input.type === "radio") {
          if (input.checked) data[input.name] = input.value
        } else if (input.type === "checkbox") {
          const sameNameCheckboxes = form.querySelectorAll(`input[type="checkbox"][name="${input.name}"]`)
          if (sameNameCheckboxes.length > 1) {
            if (!Array.isArray(data[input.name])) data[input.name] = []
            if (input.checked) data[input.name].push(input.value)
          } else {
            data[input.name] = input.checked
          }
        } else if (input.multiple) {
          data[input.name] = Array.from(input.selectedOptions).map(option => option.value)
        } else {
          const value = input.value.trim()
          data[input.name] = value === "" ? null : value
        }
        input.classList.remove("is-invalid") // reset invalid states
      })

      for (const field of tagFields) {
        let values = []
        try {
          values = JSON.parse(form.elements[field].value || "[]")
        } catch (error) {
          values = []
        }
        const normalizeKebab = form.elements[field].dataset.normalizeKebab === "true"
        data[field] = Array.isArray(values)
          ? values.map(item => typeof item === "string" ? item : item.value)
            .map(value => normalizeKebab ? toKebabCase(value) : String(value || "").trim())
            .filter(Boolean)
          : []
      }

      // Use inline feedback for HTML constraints, including dynamically added fields.
      // JustValidate handles the explicit rules above; never open native validation popups.
      let firstInvalid = null
      form.querySelectorAll("input[name]:not([data-form-ignore]), select[name]:not([data-form-ignore]), textarea[name]:not([data-form-ignore])").forEach(input => {
        if (input.disabled || ["hidden", "submit", "button"].includes(input.type)) return
        const value = input.value.trim()
        const minimum = Number(input.getAttribute("minlength")) || 0
        const maximum = Number(input.getAttribute("maxlength")) || Infinity
        const missing = input.required && !value && !["checkbox", "radio"].includes(input.type)
        const tooShort = value && value.length < minimum
        const tooLong = value.length > maximum
        if (missing || tooShort || tooLong || !input.checkValidity()) {
          input.classList.add("is-invalid")
          let feedback = input.nextElementSibling
          if (!feedback || !feedback.classList.contains("invalid-feedback")) {
            feedback = document.createElement("div")
            feedback.className = "invalid-feedback"
            input.insertAdjacentElement("afterend", feedback)
          }
          feedback.textContent = input.getAttribute("data-error") || (missing ? "This field is required."
            : tooShort ? `Use at least ${minimum} characters.` : tooLong ? `Use at most ${maximum} characters.`
              : input.validationMessage)
          firstInvalid ||= input
        }
      })
      if (firstInvalid) {
        firstInvalid.focus()
        msgClass = "warning"
        msgText = validationFailedMessage
        return
      }

      if (authRequired && !window.CONFIG.current_user) {
        msgText = "You need to be logged in to perform this action."
        msgClass = "warning"
        return
      }

      data = await prepareData(data)
      if (await beforeSubmit(data) === false) {
        msgText = ""
        return
      }

      const fileInputs = form.querySelectorAll("input[type=\"file\"]:not([data-form-ignore])")
      for (const input of fileInputs) {
        if (input.files.length === 0) continue
        const filename = await uploadPublicFile(input.files[0])
        delete data[input.name]
        data[input.name + "name"] = filename
      }

      const response = await ajaxResponse({
        url: typeof submitUrl === "function" ? submitUrl(data) : submitUrl,
        method,
        contentType: "application/json",
        data: JSON.stringify(data),
        dataType: "json"
      })
      let body = {}
      if (response.status !== 204) body = await response.json().catch(() => ({}))
      if (!response.ok) {
        const error = new Error(getApiErrorMessage(body, errorMessage))
        error.status = response.status
        error.details = body.details
        throw error
      }

      msgClass = "success"
      msgIcon = "check-circle-fill"
      if (resetOnSuccess) form.reset()
      if (validator) validator.refresh()
      msgText = await onSuccess(body) || successMessage
    } catch (error) {
      msgClass = "warning"
      msgText = error.message || errorMessage
      if ([409, 422].includes(error.status) && error.details && typeof error.details === "object") {
        msgText = validationFailedMessage
        const errors = {}
        Object.entries(error.details).forEach(([field, message]) => {
          const input = [...form.querySelectorAll("[name]")].find(input => input.name === field)
          if (!input) return
          const messageText = String(message).replace(/^Value error, /, "")
          if (validator) {
            const selector = `[name="${field}"]`
            if (!validator.fields[selector]) validator.addField(selector, [{validator: () => true, errorMessage: ""}])
            errors[selector] = messageText
          } else {
            input.classList.add("is-invalid")
            let feedback = input.nextElementSibling
            if (!feedback || !feedback.classList.contains("invalid-feedback")) {
              feedback = document.createElement("div")
              feedback.className = "invalid-feedback"
              input.insertAdjacentElement("afterend", feedback)
            }
            feedback.textContent = messageText
          }
        })
        if (validator) validator.showErrors(errors)
      } else if (error.status === 401) {
        msgText = "You need to be logged in to perform this action."
      }
      const customMessage = await onError(error)
      if (typeof customMessage === "string") msgText = customMessage
    } finally {
      $(submitBtn).clearButtonLoading()
      submitting = false
      statusDiv.className = `form-status alert${msgText ? " alert-" + msgClass : " d-none"}`
      if (msgClass === "success") {
        // Existing success callbacks may supply trusted application links.
        statusDiv.innerHTML = `<i class="bi bi-${msgIcon} me-2" aria-hidden="true"></i> ${msgText}`
      } else {
        const icon = document.createElement("i")
        icon.className = "bi bi-" + msgIcon + " me-2"
        icon.setAttribute("aria-hidden", "true")
        statusDiv.replaceChildren(icon, document.createTextNode(String(msgText)))
      }
      await onFinally()
    }
  }
}

const imageUpload = document.getElementById("upload-image")
document.querySelectorAll("input[name='image_action']").forEach(radio => {
  radio.addEventListener("change", () => {
    if (imageUpload) imageUpload.style.display = radio.checked && radio.value === "replace" ? "block" : "none"
  })
})
const avatarUpload = document.getElementById("upload-avatar")
document.querySelectorAll("input[name='avatar_action']").forEach(radio => {
  radio.addEventListener("change", () => {
    if (avatarUpload) avatarUpload.style.display = radio.checked && radio.value === "replace" ? "block" : "none"
  })
})

// Load more
;(() => {
  $(document).on("click", ".btn-load-more", async function (event) {
    const btn = this
    event.preventDefault()
    if (btn.disabled) return

    const container = document.querySelector(btn.dataset.container)
    if (!container) return console.error("Container not found:", btn.dataset.container)

    const limit = btn.dataset.limit
    const url = btn.dataset.url
    const offset = btn.dataset.offset

    const $btn = $(btn)
    $btn.setButtonLoading()

    try {
      const u = new URL(url, window.location.origin)
      u.searchParams.set("offset", offset)
      u.searchParams.set("limit", limit)
      const resp = await ajaxResponse({url: u.toString(), method: "GET", dataType: "text"})
      if (!resp.ok) {
        console.log(`Request failed with status ${resp.status}`)
        btn.remove()
        return
      }

      const content = await resp.text()
      if (content === "") {
        btn.remove()
        return
      }

      const fragment = document.createRange().createContextualFragment(content)
      const newElements = Array.from(fragment.children)
      const masonry = getMasonryInstance(container)
      container.append(fragment)

      if (masonry && newElements.length) {
        masonry.appended(newElements)
        observeMasonryItems(container, masonry)
      }

      const lastElement = container.lastElementChild
      const newOffset = lastElement.dataset.offset

      if (!newOffset || newOffset === offset) {
        btn.remove()
        return
      }

      btn.dataset.offset = newOffset
    } catch (err) {
      console.error(err)
    } finally {
      $btn.clearButtonLoading()
    }
  })

  // --- Auto-load support only for [data-auto-load] buttons ---
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.click() // Trigger the same handler
      }
    }
  }, {
    rootMargin: "200px" // start loading earlier than fully visible
  })

// Attach observer only to buttons with data-auto-load
  $(".btn-load-more[data-auto-click]").each(function () {
    observer.observe(this)
  })
})();


// Tags input
(() => {
  const input = document.getElementById("tags-input")
  if (!input || typeof Tagify === "undefined") return
  const url = input.dataset.url
  const injectHidden = input.dataset.hasOwnProperty("injectHidden")
  const autoSubmit = input.dataset.hasOwnProperty("autoSubmit")
  const form = input.closest("form")
  const escapeHtml = value => String(value || "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;"
  })[char])

  const tagify = new Tagify(input, {
    whitelist: [], // maxTags: 3,
    tagTextProp: "name",
    enforceWhitelist: false, // validate: tag => /^[0-9A-Za-z-.#]{2,20}$/.test(tag.value) || "Invalid tag",
    transformTag(tagData) {
      // Keep an existing tag's slug/name pair intact. Only normalize free-form
      // values; replacing tags from the `add` event makes Tagify briefly see the
      // selected value twice and reject/remove it as a duplicate.
      if (!tagData.name || tagData.name === tagData.value) {
        tagData.value = toKebabCase(tagData.value)
        tagData.name = tagData.value
      }
    },
    templates: {
      dropdownItem(tagData) {
        const className = tagData.class ? ` ${tagData.class}` : ""
        const count = Number(tagData.articles_count)
        const badge = Number.isFinite(count) ? `<span class="badge text-bg-secondary flex-shrink-0">${escapeHtml(count.toLocaleString())} article${count === 1 ? "" : "s"}</span>` : ""
        return `<div ${this.getAttributes(tagData)} class="tagify__dropdown__item${className} d-flex justify-content-between align-items-center gap-3" tabindex="0" role="option"><span class="text-break">${escapeHtml(tagData.name || tagData.value)}</span>${badge}</div>`
      }
    },
    dropdown: {
      // enabled: 1,
      // maxItems: 10,
      closeOnSelect: true
    }
  })

  let controller // for aborting the previous fetch
  let currentSuggestions = []

  const toTagItem = d => {
    if (typeof d === "string") return {value: d, name: d}
    const value = d.slug || d.value || d.name
    const name = d.name || d.slug || d.value
    return value ? {value, name, articles_count: d.articles_count} : null
  }

  if (injectHidden) {
    input.removeAttribute("name")
    // container for hidden inputs
    let hiddenContainer = document.createElement("div")
    hiddenContainer.style.display = "none"
    form.appendChild(hiddenContainer)

    // rebuild hidden inputs on change
    function syncHiddenInputs() {
      hiddenContainer.innerHTML = ""
      tagify.value.forEach(tag => {
        const hidden = document.createElement("input")
        hidden.type = "hidden"
        hidden.name = "tags"  // use "tags" so backend maps correctly
        hidden.value = tag.value
        hiddenContainer.appendChild(hidden)
      })
    }

    tagify.on("change", syncHiddenInputs)

    // Immediately sync hidden inputs for any preloaded tags
    if (tagify.value.length) {
      syncHiddenInputs()
    }
  }

  // event fired when user types
  tagify.on("input", onInput)

  function onInput(e) {
    const value = e.detail.value
    const prefix = toKebabCase(value)
    tagify.whitelist = []
    currentSuggestions = []
    tagify.dropdown.hide()

    controller && controller.abort()

    if (!prefix) {
      tagify.loading(false)
      return
    }

    controller = new AbortController()

    tagify.loading(true)

    const u = new URL(url, window.location.origin)
    u.searchParams.set("prefix", prefix)
    ajaxResponse({
      url: u.toString(),
      method: "GET",
      dataType: "json",
      signal: controller.signal
    })
      .then(async res => {
        if (!res.ok) {
          if ([409, 422].includes(res.status)) {
            const json = await res.json().catch(() => null)
            if (json?.details) {
              Object.entries(json.details).forEach(([field, msg]) => {
                input.classList.add("is-invalid")
                let feedback = input.nextElementSibling
                if (!feedback || !feedback.classList.contains("invalid-feedback")) {
                  feedback = document.createElement("div")
                  feedback.className = "invalid-feedback"
                  input.insertAdjacentElement("afterend", feedback)
                }
                feedback.textContent = msg
              })
            }
          } else {
            console.error(`Tags fetch failed with status ${res.status}`)
          }
          tagify.loading(false)
          return null
        }

        // success: clear any previous error state
        input.classList.remove("is-invalid")
        const feedback = input.nextElementSibling
        if (feedback && feedback.classList.contains("invalid-feedback")) {
          feedback.remove()
        }

        // parse JSON
        return await res.json()
      })
      .then(data => {
        if (!data) return
        currentSuggestions = data.map(toTagItem).filter(Boolean)
        tagify.whitelist = currentSuggestions
        tagify.loading(false)
        tagify.dropdown.show(value)
      })
      .catch(err => {
        if (err.name !== "AbortError") console.error(err)
        tagify.loading(false)
      })
  }


  if (autoSubmit) {
    tagify.on("change", () => form.requestSubmit())
  }
})()

// Enable bootstrap tooltip
const tooltipTriggerList = document.querySelectorAll("[title], [data-bs-toggle=\"tooltip\"]")
const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl))

// Publish article
$(".btn-article-publish, .btn-article-unpublish").on("click", function () {
  const $btn = $(this)
  const articleId = $btn.data("article-id")
  const status = $btn.data("status")
  const url = formatUrl(window.CONFIG.update_article_status_url, {article_id: articleId})

  $btn.setButtonLoading()

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({status}), success: function () {
      window.location.reload()
    }, error: function (xhr) {
      console.error(`Error on article ${status}:`, xhr.responseText)
      alert(`Failed to ${status} article.`)
    }, complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

// Reject article
$(".btn-article-reject").on("click", function () {
  const $btn = $(this)
  const articleId = $btn.data("article-id")
  const status = $btn.data("status")
  const url = formatUrl(window.CONFIG.update_article_status_url, {article_id: articleId})
  const comment = $btn.closest(".input-group").find("input[name='comment']").val().trim()

  if (!comment) {
    alert("Please enter a rejection reason.")
    return
  }

  $btn.setButtonLoading()

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({status, comment}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error("Error rejecting article:", xhr.responseText)
      alert("Failed to reject article.")
    },
    complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

// Like/dislike article (article impression)
$(document).on("click", ".btn-article-like, .btn-article-dislike", function () {
  const $btn = $(this)
  const articleId = $btn.data("article-id")
  const action = $btn.data("action")
  const url = formatUrl(window.CONFIG.update_article_impression_url, {article_id: articleId})

  $btn.setButtonLoading()

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({action}), success: function (res) {
      $btn.closest(".article-impressions").replaceWith(res)
    }, error: function (xhr) {
      console.error(`Error on article ${action}:`, xhr.responseText)
      alert(`Failed to ${action} article. Please try again.`)
    }, complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

// Follow/block user (user impression)
$(document).on("click", ".btn-user-follow, .btn-user-block", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const action = $btn.data("action")
  const url = formatUrl(window.CONFIG.update_user_impression_url, {user_id: userId})

  $btn.setButtonLoading()

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({action}), success: function (res) {
      $btn.closest(".user-impressions").replaceWith(res)
    }, error: function (xhr) {
      console.error(`Error on user ${action}:`, xhr.responseText)
      alert(`Failed to ${action} user. Please try again.`)
    }, complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

// Activate user
$(".btn-user-activate").on("click", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const status = $btn.data("status")
  const url = formatUrl(window.CONFIG.update_user_status_url, {user_id: userId})

  $btn.setButtonLoading()

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({status}), success: function () {
      window.location.reload()
    }, error: function (xhr) {
      console.error("Error activating user:", xhr.responseText)
      alert("Failed to activate user.")
    }, complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

// Ban user
$(".btn-user-ban").on("click", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const status = $btn.data("status")
  const url = formatUrl(window.CONFIG.update_user_status_url, {user_id: userId})
  const comment = $btn.closest(".input-group").find("input[name='comment']").val().trim()

  if (!comment) {
    alert("Please enter a ban reason.")
    return
  }

  $btn.setButtonLoading()

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({status, comment}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error("Error banning user:", xhr.responseText)
      alert("Failed to ban user.")
    },
    complete: function () {
      $btn.clearButtonLoading()
    }
  })
})

const getApiErrorMessage = (errorBody, fallbackMessage) => {
  const details = errorBody?.details
  if (typeof details === "string" && details) return details
  if (details && typeof details === "object") {
    const messages = Object.values(details).filter(Boolean)
    if (messages.length > 0) return messages.join(" ")
  }
  return errorBody?.message || fallbackMessage
}

const uploadPublicFile = async function (file, progress = undefined, endpoint = window.CONFIG.upload_public_file_url) {
  try {
    const formData = new FormData()
    formData.append("file", file)

    // Upload to your existing endpoint
    const uploadResponse = await ajaxResponse({
      url: endpoint,
      method: "POST",
      data: formData,
      processData: false,
      contentType: false,
      dataType: "json"
    })

    if (!uploadResponse.ok) {
      let errorBody = {}
      try {
        errorBody = await uploadResponse.json()
      } catch (_) {
      }
      throw new Error(getApiErrorMessage(errorBody, "File upload failed (" + uploadResponse.status + ")"))
    }

    return await uploadResponse.json()
  } catch (err) {
    console.error("Image upload failed:", err)
    throw err
  }
}

if (window.CONFIG.init_tinymce) {
  $("textarea.editor").tinymce({
    skin: "bootstrap",
    plugins: "importcss autolink code fullscreen image link codesample table charmap advlist lists autosave",
    menubar: false,
    toolbar: "h2 h3 blockquote bold italic underline strikethrough align numlist bullist outdent indent "
      + "link image table charmap codesample removeformat code fullscreen",
    autosave_ask_before_unload: true,
    contextmenu: false,
    content_css: ["default", ...window.CONFIG.css_filenames],
    width: "100%",
    height: 1000,
    content_style: "body { min-height: 100%; margin: .75rem!important }",
    browser_spellcheck: true,
    powerpaste_allow_local_images: true,
    powerpaste_word_import: "clean",
    powerpaste_html_import: "clean",
    valid_elements: "img[src|alt],"
      + "h2[id],h3[id],h4[id],h5[id],h6[id],"
      + "a[href|target|title],"
      + "b/strong,i/em,u,span[class],"
      + "ul,ol,li,"
      + "table[class|border|cellpadding|cellspacing],thead,tbody,tfoot,tr,th[colspan|rowspan],td[colspan|rowspan],"
      + "div[class],br,p,pre[class],code[class],blockquote",
    relative_urls: false,
    convert_urls: false,
    table_default_attributes: {class: "table"},
    table_class_list: [
      {title: "Regular", value: "table"},
      {title: "Striped", value: "table table-striped"},
      {title: "Bordered", value: "table table-bordered"}
    ],
    link_default_target: "_blank",
    link_target_list: false,
    link_context_toolbar: true,
    images_reuse_filename: true,
    image_title: false,
    image_dimensions: false,
    images_upload_handler: async (blobInfo, progress) => {
      const filename = await uploadPublicFile(blobInfo.blob(), progress, window.CONFIG.upload_article_image_url)
      return formatUrl(window.CONFIG.static_relative_url, {filename})
    },
    codesample_languages: [
      {text: "HTML/XML", value: "markup"},
      {text: "JavaScript", value: "javascript"},
      {text: "TypeScript", value: "typescript"},
      {text: "Python", value: "python"},
      {text: "CSS", value: "css"},
      {text: "SCSS", value: "scss"},
      {text: "PHP", value: "php"},
      {text: "Ruby", value: "ruby"},
      {text: "Go", value: "go"},
      {text: "C", value: "c"},
      {text: "C++", value: "cpp"},
      {text: "C#", value: "csharp"},
      {text: "Java", value: "java"},
      {text: "Bash/Shell", value: "bash"},
      {text: "JSON", value: "json"},
      {text: "YAML", value: "yaml"},
      {text: "SQL", value: "sql"}
    ],
    setup: (editor) => {
      editor.on("init", () => {
        editor.getContainer().style.transition = "border-color 0.15s ease-in-out, box-shadow 0.15s ease-in-out"
      })
      editor.on("focus", () => {
        editor.getContainer().style.boxShadow = "0 0 0 .2rem rgba(0, 123, 255, .25)"
        editor.getContainer().style.borderColor = "#80bdff"
      })
      editor.on("blur", () => {
        editor.getContainer().style.boxShadow = ""
        editor.getContainer().style.borderColor = ""
      })
      editor.on("NodeChange", (e) => {
        if (e && e.element.nodeName === "TABLE" && !e.element.className) {
          e.element.className = "table"
        }
        if (e && e.element.nodeName === "IMG" && !e.element.alt) {
          e.element.alt = prompt("Enter a short description (alt text) for the image:", "") || "Image"
        }
        if (e && e.element.nodeName === "A") {
          e.element.target = "_blank"
          e.element.rel = "noopener noreferrer"
        }
      })
      editor.on("GetContent", function (e) {
        e.content = e.content
          .replace(/<p>\s*<\/p>/g, "<br>")
          .replace(/^(<br\s*\/?>\s*)+/i, "")
          .replace(/(<br\s*\/?>\s*)+$/i, "")
          .trim()
      })
      editor.on("change keyup", () => {
        tinymce.triggerSave()
      })
    }
  })
}

if (typeof Prism !== "undefined") {
  Prism.plugins.autoloader.languages_path = "https://cdn.jsdelivr.net/npm/prismjs@1.x/components/"
  Prism.highlightAll()
}

$(function () {
  const $cookieAlert = $("#cookie-alert")
  const $acceptBtn = $("#accept-cookies")

  if (localStorage.getItem("cookiesAccepted")) {
    $cookieAlert.remove()
    return
  }

  $cookieAlert.addClass("show")

  $acceptBtn.on("click", function () {
    localStorage.setItem("cookiesAccepted", "true")
    $cookieAlert.fadeOut(300, function () {
      $(this).remove()
    })
  })
})

$(function () {
  $(".copy-url button").on("click", function () {
    const $btn = $(this)
    const $input = $btn.closest(".input-group").find("input")
    const textToCopy = $input.val()

    navigator.clipboard.writeText(textToCopy).then(() => {
      // Temporary icon swap for visual feedback
      $btn.find("i").removeClass("bi-copy").addClass("bi-check")

      // Create tooltip programmatically
      const tooltip = new bootstrap.Tooltip($btn[0], {
        title: "Copied!",
        placement: "top",
        trigger: "manual"
      })

      // Show tooltip and clean up after 1s
      tooltip.show()
      setTimeout(() => {
        tooltip.hide()
        tooltip.dispose()
        $btn.find("i").removeClass("bi-check").addClass("bi-copy")
      }, 1000)

    }).catch(() => {
      // Fallback for older browsers
      $input[0].select()
      document.execCommand("copy")
    })
  })
})

$(function () {
  const canonicalUrl = document.querySelector('link[rel="canonical"]')?.href || window.location.href
  const pageUrl = encodeURIComponent(canonicalUrl)
  const pageTitle = encodeURIComponent(document.title)

  const shareText = encodeURIComponent(`${document.title}\n${canonicalUrl}`)

  $(".share-btn.whatsapp").attr("href", `https://wa.me/?text=${shareText}`)
  $(".share-btn.telegram").attr("href", `https://t.me/share/url?url=${pageUrl}&text=${pageTitle}`)
  $(".share-btn.twitter").attr("href", `https://x.com/intent/tweet?url=${pageUrl}&text=${pageTitle}`)
  $(".share-btn.bluesky").attr("href", `https://bsky.app/intent/compose?text=${shareText}`)
  $(".share-btn.threads").attr("href", `https://www.threads.com/intent/post?text=${shareText}`)
  $(".share-btn.facebook").attr("href", `https://www.facebook.com/sharer/sharer.php?u=${pageUrl}`)
  $(".share-btn.linkedin").attr("href", `https://www.linkedin.com/sharing/share-offsite/?url=${pageUrl}`)
  $(".share-btn.reddit").attr("href", `https://www.reddit.com/submit?url=${pageUrl}&title=${pageTitle}`)
  $(".share-btn.email").attr("href", `mailto:?subject=${pageTitle}&body=${shareText}`)
})

// Tag subscriptions
$(document).on("click", ".btn-tag-subscription", function () {
  const button = $(this)
  const block = button.closest(".tag-subscription-block, .tag-subscription-item")
  const message = block.find(".tag-subscription-message")
  button.setButtonLoading()
  const subscriptionId = button.attr("data-tag-subscription-id")
  const request = subscriptionId
    ? $.ajax({
      url: formatUrl(window.CONFIG.delete_tag_subscription_url, {tag_subscription_id: subscriptionId}),
      method: "DELETE"
    })
    : $.ajax({
      url: window.CONFIG.create_tag_subscription_url,
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({tags: JSON.parse(button.attr("data-tags"))})
    })
  request.done((html) => {
    if (block.hasClass("tag-subscription-item")) {
      block.remove()
      const interestsContent = $("#profile-interests-content")
      if (interestsContent.length && !interestsContent.find(".tag-subscription-item").length) {
        interestsContent.html('<div class="text-muted small tag-subscription-empty">You have no interests.</div>')
      }
    } else {
      block.replaceWith(html)
    }
  })
    .fail((xhr) => {
      message.text(xhr.responseJSON?.detail?.message || "Unable to update tag subscription.");
      button.clearButtonLoading()
    })
})


$(document).on("click", ".share[data-share-url] .share-btn", function () {
  const platform = this.dataset.sharePlatform
  if (!platform) return
  fetch(this.closest(".share").dataset.shareUrl, {
    method: "POST", credentials: "include", keepalive: true,
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({channel: `${platform}_share_click`, event_id: crypto.randomUUID()})
  }).catch(() => {})
})

$(document).on("click", ".btn-comment-reaction", async function () {
  const button = this
  const block = button.closest(".comment-impressions")
  const buttons = block.querySelectorAll("button")
  buttons.forEach(value => { value.disabled = true })
  try {
    const response = await fetch(button.dataset.url, {
      method: "POST", credentials: "include", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: button.dataset.action})
    })
    if (!response.ok) throw new Error("Could not update this reaction. Refresh the page and try again.")
    const data = await response.json()
    block.querySelector(".comment-likes-count").textContent = data.likes_count
    block.querySelector(".comment-dislikes-count").textContent = data.dislikes_count
  } catch (error) {
    alert(error.message)
  } finally {
    buttons.forEach(value => { value.disabled = false })
  }
})


function initializeSiteNotifications(root) {
  const pollInterval = 30000
  let presenceInterval = 300000
  const statusKey = "sysdespro-notifications-status:" + root.dataset.userId
  const presenceKey = "sysdespro-presence:" + root.dataset.userId
  const seenKey = "sysdespro-notifications-seen:" + root.dataset.userId
  const load = key => {try {return JSON.parse(localStorage.getItem(key))} catch (_) {return null}}
  const save = (key, value) => {try {localStorage.setItem(key, JSON.stringify(value))} catch (_) {}}
  const previous = load(statusKey)
  const initialRevision = Number(root.dataset.revision) || 0
  let unread = Number(root.dataset.unread) || 0
  let revision = initialRevision
  let lastSeen = Number(root.dataset.lastSeen) || 0
  let lastPresenceAt = Math.max(lastSeen, Number(load(presenceKey)) || 0)
  let lastStatusAt = Date.now()
  let activityAt = Date.now()
  let menuOpen = false
  let loading = false
  let toastWanted = false
  let polling = false
  let beating = false
  let closed = false
  const seenValues = load(seenKey)
  const seen = new Set(Array.isArray(seenValues) ? seenValues : [])
  const items = root.querySelector("#site-notification-items")
  const toggle = root.querySelector("#site-notifications-toggle")
  const request = async (url, data, keepalive = false) => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 20000)
    try {
      const options = {credentials: "include", cache: "no-store", signal: controller.signal,
        headers: {Accept: "application/json"}, keepalive}
      if (data) {
        options.method = "POST"
        options.headers["Content-Type"] = "application/json"
        options.body = JSON.stringify(data)
      }
      const response = await fetch(url, options)
      if (!response.ok) throw new Error("Please try again.")
      const result = await response.json()
      if (result.user_id !== root.dataset.userId) {
        closed = true
        window.location.reload()
        throw new Error("The account changed. Reload this page.")
      }
      return result
    } finally {clearTimeout(timer)}
  }
  const icon = name => {
    const element = document.createElement("i")
    element.className = "bi bi-" + name
    element.setAttribute("aria-hidden", "true")
    return element
  }
  const toast = notification => {
    let container = document.getElementById("site-notification-toasts")
    if (!container) {
      container = document.createElement("div")
      container.id = "site-notification-toasts"
      container.className = "toast-container position-fixed bottom-0 end-0 p-3"
      container.setAttribute("aria-live", "polite")
      document.body.append(container)
    }
    const card = document.createElement("div")
    card.className = "toast show mb-2"
    card.setAttribute("role", "status")
    const header = document.createElement("div")
    header.className = "toast-header gap-2"
    const title = document.createElement("strong")
    title.className = "me-auto text-break"
    title.textContent = notification.title
    const dismiss = document.createElement("button")
    dismiss.type = "button"
    dismiss.className = "btn btn-sm btn-link p-0"
    dismiss.setAttribute("aria-label", "Dismiss notification")
    dismiss.append(icon("x-lg"))
    dismiss.addEventListener("click", () => card.remove())
    header.append(icon(notification.icon || "bell"), title, dismiss)
    const body = document.createElement("div")
    body.className = "toast-body text-break"
    const text = document.createElement("p")
    text.className = "mb-2"
    text.textContent = notification.message
    body.append(text)
    if (notification.url) {
      const link = document.createElement("a")
      link.href = notification.url
      link.className = "btn btn-primary btn-sm"
      link.dataset.notificationLink = notification.id
      link.append(icon("arrow-right"), document.createTextNode(" Open"))
      body.append(link)
    }
    card.append(header, body)
    container.append(card)
    setTimeout(() => card.remove(), 10000)
  }
  const render = notifications => {
    const rows = notifications.map(notification => {
      const row = document.createElement("div")
      row.className = "d-flex align-items-start gap-2 border-bottom p-2"
      row.dataset.notificationItem = notification.id
      const link = document.createElement("a")
      link.href = notification.url
      link.className = "flex-grow-1 text-decoration-none link-body-emphasis text-break"
      link.dataset.notificationLink = notification.id
      const title = document.createElement("strong")
      title.textContent = notification.title
      const text = document.createElement("p")
      text.className = "small mb-0"
      text.textContent = notification.message
      link.append(title, text)
      row.append(icon(notification.icon), link)
      if (!notification.is_read) {
        row.dataset.notificationUnread = "true"
        const read = document.createElement("button")
        read.type = "button"
        read.className = "btn btn-outline-secondary btn-sm"
        read.dataset.readNotification = notification.id
        read.setAttribute("aria-label", "Mark notification as read")
        read.append(icon("check2"))
        row.append(read)
      }
      return row
    })
    if (rows.length) items.replaceChildren(...rows)
    else {
      const empty = document.createElement("p")
      empty.className = "small text-body-secondary px-2 mb-0"
      empty.textContent = "You're all caught up."
      items.replaceChildren(empty)
    }
  }
  const loadNotifications = async (showToasts = false) => {
    toastWanted ||= showToasts
    if (loading || closed) return
    loading = true
    items.setAttribute("aria-busy", "true")
    if (menuOpen) items.textContent = "Loading notifications…"
    try {
      const url = new URL(root.dataset.historyUrl)
      url.searchParams.set("limit", "10")
      const data = await request(url)
      if (closed) return
      render(data.notifications)
      const fresh = data.notifications.filter(item => !item.is_read && item.delivery === "web" && !seen.has(item.id))
      if (toastWanted && !document.hidden && (!document.hasFocus || document.hasFocus())) fresh.slice(0, 3).forEach(toast)
      data.notifications.forEach(item => seen.add(item.id))
      save(seenKey, [...seen].slice(-100))
    } catch (_) {
      if (menuOpen) items.textContent = "Could not load notifications. Close and reopen to retry."
    } finally {
      items.setAttribute("aria-busy", "false")
      loading = false
      toastWanted = false
    }
  }
  const paint = () => {
    root.querySelectorAll("[data-unread-notifications]").forEach(badge => {
      badge.textContent = unread > 99 ? "99+" : String(unread)
      badge.hidden = unread === 0
    })
    toggle.setAttribute("aria-label", "Notifications, " + unread + " unread")
  }
  const apply = (state, announce = false, persist = true) => {
    if (Number(state.revision) < revision) return
    const changed = Number(state.revision) > revision
    unread = Math.max(0, Number(state.unread_count) || 0)
    revision = Number(state.revision) || 0
    lastSeen = Math.max(lastSeen, Number(state.last_seen_at) || 0)
    presenceInterval = Number(state.presence_interval_ms) || presenceInterval
    lastStatusAt = persist ? Date.now() : Number(state.at) || Date.now()
    if (persist) save(statusKey, {unread_count: unread, revision, last_seen_at: lastSeen, at: lastStatusAt})
    paint()
    if (changed && !document.hidden && (menuOpen || announce && unread > 0)) loadNotifications(announce)
  }
  const heartbeat = async () => {
    const now = Date.now()
    const last = Math.max(lastSeen, lastPresenceAt)
    if (beating || closed || document.hidden || now - activityAt > 60000 || now - last < presenceInterval) return false
    beating = true
    lastStatusAt = now
    try {
      const state = await request(root.dataset.presenceUrl, {})
      lastPresenceAt = Date.now()
      save(presenceKey, lastPresenceAt)
      apply(state, true)
    } catch (_) {} finally {beating = false}
    return true
  }
  const tick = async () => {
    if (closed || document.hidden) return
    if (await heartbeat()) return
    const cached = load(statusKey)
    if (cached && cached.revision > revision) apply(cached, false, false)
    const last = Math.max(lastStatusAt, Number(cached?.at) || 0)
    if (polling || Date.now() - last < pollInterval) return
    polling = true
    try {apply(await request(root.dataset.statusUrl), true)} catch (_) {} finally {polling = false}
  }
  const activity = () => {activityAt = Date.now(); heartbeat()}
  const visibility = () => {if (!document.hidden) {activityAt = Date.now(); tick(); if (menuOpen) loadNotifications()}}
  const storage = event => {
    if (event.key === presenceKey) {
      try {lastPresenceAt = Math.max(lastPresenceAt, Number(JSON.parse(event.newValue)) || 0)} catch (_) {}
      return
    }
    if (event.key !== statusKey || !event.newValue) return
    try {apply(JSON.parse(event.newValue), false, false)} catch (_) {}
  }
  const read = async (identifier, button) => {
    if (button) button.disabled = true
    try {
      const state = await request(root.dataset.readUrl.replace("notification-id", identifier), {}, true)
      apply(state)
      document.querySelectorAll('[data-notification-item="' + identifier + '"]').forEach(row => {
        delete row.dataset.notificationUnread
        row.querySelectorAll("[data-read-notification], [data-notification-unread-badge]").forEach(item => item.remove())
      })
    } catch (_) {
      if (button) button.disabled = false
      toast({title: "Could not update notification", message: "Please try again.", icon: "exclamation-circle"})
    }
  }
  const click = event => {
    const button = event.target.closest("[data-read-notification]")
    const link = event.target.closest("[data-notification-link]")
    if (button) read(button.dataset.readNotification, button)
    else if (link) read(link.dataset.notificationLink)
  }
  const openNotifications = () => {menuOpen = true; loadNotifications()}
  toggle.addEventListener("click", () => {
    if (toggle.getAttribute("aria-expanded") !== "true") openNotifications()
  })
  toggle.addEventListener("show.bs.dropdown", openNotifications)
  toggle.addEventListener("hidden.bs.dropdown", () => {menuOpen = false})
  document.addEventListener("click", click)
  document.addEventListener("visibilitychange", visibility)
  ;["pointerdown", "keydown", "scroll", "touchstart"].forEach(name => document.addEventListener(name, activity, {passive: true}))
  window.addEventListener("storage", storage)
  const changedOnNavigation = previous && initialRevision > previous.revision && unread > 0
  if (previous && previous.revision > revision) apply(previous, false, false)
  else apply({unread_count: unread, revision, last_seen_at: lastSeen})
  if (changedOnNavigation) loadNotifications(true)
  const timer = setInterval(tick, 15000)
  window.addEventListener("pagehide", () => {
    closed = true
    clearInterval(timer)
    document.removeEventListener("click", click)
    document.removeEventListener("visibilitychange", visibility)
    ;["pointerdown", "keydown", "scroll", "touchstart"].forEach(name => document.removeEventListener(name, activity))
    window.removeEventListener("storage", storage)
  }, {once: true})
  window.addEventListener("pageshow", event => {if (event.persisted) window.location.reload()})
  heartbeat()
}

document.addEventListener("DOMContentLoaded", () => {
  const root = document.getElementById("site-notifications")
  if (root) initializeSiteNotifications(root)
})
