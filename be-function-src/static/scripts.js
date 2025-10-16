// Form validation & submission
function handleFormSubmit(formSelector, submitUrl, options = {}) {
  const {
    method = "POST",
    successMessage = "Success!",
    errorMessage = "Something went wrong.",
    networkErrorMessage = "Network error. Please try again.",
    validationFailedMessage = "Please fix the highlighted fields.",
    rules = {},
    onSuccess = () => {
    },
    loadingText = "Submitting..."
  } = options

  const form = document.querySelector(formSelector)
  if (!form) return

  // Insert status div before submit button
  const submitBtn = form.querySelector("[type='submit']")
  const statusDiv = document.createElement("div")
  statusDiv.className = "form-status alert d-none d-flex align-items-center"
  statusDiv.setAttribute("role", "alert")
  submitBtn.insertAdjacentElement("beforebegin", statusDiv)

  let originalBtnContent = submitBtn.innerHTML

  let validator = null
  let hasTags = false

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
              const values = JSON.parse(value)
              if (!Array.isArray(values)) return true
              const items = values.map(item => item.value.trim()).filter(Boolean)
              return items.length >= minCnt && items.length <= maxCnt && items.every(t => t.length >= minLen && t.length <= maxLen)
            },
            errorMessage: `Tags must be ${minCnt}–${maxCnt} items, ${minLen}–${maxLen} chars each`
          }
          hasTags = true
        }
      }

      validator.addField(`[name="${field}"]`, fieldRules)
    })

    validator.onSuccess(() => submitForm())
  } else {
    // Fallback: submit without frontend validation
    form.addEventListener("submit", (e) => {
      e.preventDefault()
      submitForm()
    })
  }

  async function submitForm() {
    // Show loading state
    submitBtn.disabled = true
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${loadingText}`

    statusDiv.className = "form-status alert d-none d-flex align-items-center"
    statusDiv.textContent = ""

    let data = {}
    form.querySelectorAll("[name]").forEach(input => {
      // handle radio buttons separately
      if (input.type === "radio") {
        if (input.checked) data[input.name] = input.value
      } else {
        const value = input.value.trim()
        data[input.name] = value === "" ? null : value
      }
      input.classList.remove("is-invalid") // reset invalid states
    })

    if (hasTags) {
      const values = JSON.parse(form.tags.value)
      data.tags = values.map(item => item.value.trim()).filter(Boolean)
    }

    try {
      if (!validator) {
        // Simple frontend validation fallback
        let hasError = false
        form.querySelectorAll("[required]").forEach(input => {
          if (!input.value.trim()) {
            input.classList.add("is-invalid")
            hasError = true

            // Optional: create invalid-feedback div if not present
            let feedback = input.nextElementSibling
            if (!feedback || !feedback.classList.contains("invalid-feedback")) {
              feedback = document.createElement("div")
              feedback.className = "invalid-feedback"
              input.insertAdjacentElement("afterend", feedback)
            }
            feedback.textContent = input.getAttribute("data-error") || "This field is required."
          }
        })

        if (hasError) {
          submitBtn.disabled = false
          submitBtn.innerHTML = originalBtnContent
          statusDiv.className = "form-status alert alert-warning d-flex align-items-center"
          statusDiv.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-2"></i> ${validationFailedMessage}`
          return
        }
      }

      // Upload all file inputs separately
      const fileInputs = form.querySelectorAll("input[type=\"file\"]")
      for (const input of fileInputs) {
        if (input.files.length === 0) continue

        const publicFile = input.dataset.hasOwnProperty("publicFile")

        const formData = new FormData()
        formData.append("file", input.files[0])

        const uploadResponse = await fetch(publicFile ? "/public-file" : "/private-file", {
          method: "POST",
          body: formData
        })

        if (!uploadResponse.ok) throw new Error("File upload failed")

        delete data[input.name]
        data[input.name + "name"] = await uploadResponse.json()
      }

      // Submit the JSON payload
      const response = await fetch(submitUrl, {
        method,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
      })

      submitBtn.disabled = false
      submitBtn.innerHTML = originalBtnContent

      if (response.ok) {
        statusDiv.className = "form-status alert alert-success d-flex align-items-center"
        statusDiv.innerHTML = `<i class="bi bi-check-circle-fill me-2"></i> ${successMessage}`
        form.reset()
        if (validator) validator.refresh()

        let responseBody = {}
        if (response.status !== 204) {
          try {
            responseBody = await response.json()
          } catch (_) {
          }
        }
        onSuccess(responseBody)
        return
      }

      if ([409, 422].includes(response.status)) {
        const json = await response.json()
        statusDiv.className = "form-status alert alert-warning d-flex align-items-center"
        statusDiv.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-2"></i> ${validationFailedMessage}`

        if (validator && json.details) {
          const errors = {}
          Object.entries(json.details).forEach(([field, msg]) => {
            errors[`[name="${field}"]`] = msg
          })
          validator.showErrors(errors)
        } else if (!validator && json.details) {
          // fallback: highlight invalid fields without validator
          Object.entries(json.details).forEach(([field, msg]) => {
            const input = form.querySelector(`[name="${field}"]`)
            if (input) {
              input.classList.add("is-invalid")
              let feedback = input.nextElementSibling
              if (!feedback || !feedback.classList.contains("invalid-feedback")) {
                feedback = document.createElement("div")
                feedback.className = "invalid-feedback"
                input.insertAdjacentElement("afterend", feedback)
              }
              feedback.textContent = msg
            }
          })
        }
        return
      }

      statusDiv.className = "form-status alert alert-danger d-flex align-items-center"
      statusDiv.innerHTML = `<i class="bi bi-x-circle-fill me-2"></i> ${errorMessage}`

    } catch (err) {
      submitBtn.disabled = false
      submitBtn.innerHTML = originalBtnContent
      statusDiv.className = "form-status alert alert-danger d-flex align-items-center"
      statusDiv.innerHTML = `<i class="bi bi-x-circle-fill me-2"></i> ${networkErrorMessage}`
    }
  }
}

// Load more
(() => {
  document.addEventListener("click", async function (e) {
    const btn = e.target.closest(".btn-load-more")
    if (!btn) return

    const container = document.querySelector(btn.dataset.container)
    if (!container) return console.error("Container not found:", btn.dataset.container)

    const limit = btn.dataset.limit
    const url = btn.dataset.url
    const offset = btn.dataset.offset

    btn.disabled = true
    const originalText = btn.textContent
    btn.textContent = "Loading..."

    try {
      const u = new URL(url, window.location.origin)
      u.searchParams.set("offset", offset)
      u.searchParams.set("limit", limit)
      const resp = await fetch(u.toString())
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

      container.insertAdjacentHTML("beforeend", content)

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
      btn.disabled = false
      btn.textContent = originalText
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
  document.querySelectorAll(".btn-load-more[data-auto-click]").forEach(btn => {
    observer.observe(btn)
  })
})();


// Tags input
(() => {
  const input = document.getElementById("tags-input")
  if (!input) return
  const url = input.dataset.url
  const injectHidden = input.dataset.hasOwnProperty("injectHidden")
  const autoSubmit = input.dataset.hasOwnProperty("autoSubmit")
  const form = input.closest("form")

  const tagify = new Tagify(input, {
    whitelist: [],
    // maxTags: 3,
    enforceWhitelist: false,
    // validate: tag => /^[0-9A-Za-z-.#]{2,20}$/.test(tag.value) || "Invalid tag",
    dropdown: {
      // enabled: 1,
      // maxItems: 10,
      closeOnSelect: true
    }
  })

  let controller // for aborting the previous fetch

  // normalize tags: lowercase + kebab-case
  const toKebabCase = str => str.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "")

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
    tagify.whitelist = []
    tagify.dropdown.hide()

    controller && controller.abort()
    controller = new AbortController()

    tagify.loading(true)

    const u = new URL(url, window.location.origin)
    u.searchParams.set("prefix", value)
    fetch(u.toString(), {
      headers: {"Content-Type": "application/json"},
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
        tagify.whitelist = data.map(d => (typeof d === "string" ? d : d.name)).filter(Boolean)
        tagify.loading(false)
        tagify.dropdown.show(value)
      })
      .catch(err => {
        if (err.name !== "AbortError") console.error(err)
        tagify.loading(false)
      })
  }

  // event fired when a tag is added
  tagify.on("add", (e) => {
    const normalized = toKebabCase(e.detail.data.value)
    if (normalized !== e.detail.data.value) {
      tagify.removeTag(e.detail.data.value, true) // remove old
      tagify.addTags([normalized], true) // add normalized
    }
  })

  if (autoSubmit) {
    tagify.on("change", () => form.requestSubmit())
  }
})()

// Enable bootstrap tooltip
const tooltipTriggerList = document.querySelectorAll("[data-bs-toggle=\"tooltip\"]")
const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl))

// Publish post
$(".btn-post-publish, .btn-post-unpublish").on("click", function () {
  const $btn = $(this)
  const postId = $btn.data("post-id")
  const status = $btn.data("status")
  const url = window.CONFIG.update_post_status_url.replace("{post_id}", postId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({status}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error(`Error on post ${status}:`, xhr.responseText)
      alert(`Failed to ${status} post.`)
    },
    complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Reject post
$(".btn-post-reject").on("click", function () {
  const $btn = $(this)
  const postId = $btn.data("post-id")
  const status = $btn.data("status")
  const url = window.CONFIG.update_post_status_url.replace("{post_id}", postId)
  const comment = $btn.closest(".input-group").find("input[name='comment']").val().trim()

  if (!comment) {
    alert("Please enter a rejection reason.")
    return
  }

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({status, comment}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error("Error rejecting post:", xhr.responseText)
      alert("Failed to reject post.")
    },
    complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Like/dislike post
$(".btn-post-like, .btn-post-dislike").on("click", function () {
  const $btn = $(this)
  const postId = $btn.data("post-id")
  const action = $btn.data("action")
  const url = window.CONFIG.update_post_impression_url.replace("{post_id}", postId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({action}),
    success: function () {
      location.reload()
    },
    error: function (xhr) {
      console.error(`Error on post ${action}:`, xhr.responseText)
      alert(`Failed to ${action} post. Please try again.`)
    },
    complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Follow/block user
$(".btn-user-follow, .btn-user-block").on("click", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const action = $btn.data("action")
  const url = window.CONFIG.update_user_impression_url.replace("{user_id}", userId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({action}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error(`Error on user ${action}:`, xhr.responseText)
      alert(`Failed to ${action} user. Please try again.`)
    },
    complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Activate user
$(".btn-user-activate").on("click", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const status = $btn.data("status")
  const url = window.CONFIG.update_user_status_url.replace("{user_id}", userId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url,
    method: "POST",
    contentType: "application/json",
    data: JSON.stringify({status}),
    success: function () {
      window.location.reload()
    },
    error: function (xhr) {
      console.error("Error activating user:", xhr.responseText)
      alert("Failed to activate user.")
    },
    complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Ban user
$(".btn-user-ban").on("click", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const status = $btn.data("status")
  const url = window.CONFIG.update_user_status_url.replace("{user_id}", userId)
  const comment = $btn.closest(".input-group").find("input[name='comment']").val().trim()

  if (!comment) {
    alert("Please enter a ban reason.")
    return
  }

  $btn.prop("disabled", true).addClass("disabled")

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
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})