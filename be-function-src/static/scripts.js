// Form validation & submission
function handleFormSubmit(formSelector, submitUrl, options = {}) {
  const {
    method = "POST",
    successMessage = "Success!",
    errorMessage = "Something went wrong. Please try again.",
    validationFailedMessage = "Please fix the highlighted fields.",
    rules = {},
    onSuccess = () => {
    },
    loadingText = "Submitting...",
    authRequired = true
  } = options

  const form = document.querySelector(formSelector)
  if (!form) return

  // Insert status div before submit button
  const submitBtn = form.querySelector("[type='submit']")
  const statusDiv = document.createElement("div")
  statusDiv.className = "form-status alert d-none"
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
            }, errorMessage: `Tags must be ${minCnt}–${maxCnt} items, ${minLen}–${maxLen} chars each`
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

    statusDiv.className = "form-status alert d-none"
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

    let msgClass = "danger"
    let msgIcon = "exclamation-triangle-fill"
    let msgText = errorMessage

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
          msgClass = "warning"
          msgText = validationFailedMessage
          return
        }
      }

      if (authRequired && !window.CONFIG.current_user) {
        msgText = "You need to be logged in to perform this action."
        msgClass = "warning"
        return
      }

      // Upload all file inputs separately
      const fileInputs = form.querySelectorAll("input[type=\"file\"]")
      for (const input of fileInputs) {
        if (input.files.length === 0) continue

        const filename = await uploadPublicFile(input.files[0])

        delete data[input.name]
        data[input.name + "name"] = filename
      }

      // Submit the JSON payload
      const response = await fetch(submitUrl, {
        method,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
      })

      if (response.ok) {
        msgClass = "success"
        msgIcon = "check-circle-fill"
        form.reset()
        if (validator) validator.refresh()

        let responseBody = {}
        if (response.status !== 204) {
          try {
            responseBody = await response.json()
          } catch (_) {
          }
        }
        msgText = onSuccess(responseBody) || successMessage
        return
      }

      if ([409, 422].includes(response.status)) {
        const json = await response.json()
        msgClass = "warning"
        msgText = validationFailedMessage

        if (validator && json.details) {
          const errors = {}
          Object.entries(json.details).forEach(([field, msg]) => {
            const sel = `[name="${field}"]`
            if (!validator.fields[sel]) {
              validator.addField(sel, [
                {
                  validator: () => true,
                  errorMessage: ''
                }
              ])
            }
            errors[sel] = msg.replace(/^Value error, /, '')
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

      if ([401].includes(response.status)) {
        msgText = "You need to be logged in to perform this action."
        msgClass = "warning"
        return
      }
    } catch (err) {
    } finally {
      submitBtn.disabled = false
      submitBtn.innerHTML = originalBtnContent
      statusDiv.className = `form-status alert alert-${msgClass}`
      statusDiv.innerHTML = `<i class="bi bi-${msgIcon} me-2"></i> ${msgText}`
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
    whitelist: [], // maxTags: 3,
    enforceWhitelist: false, // validate: tag => /^[0-9A-Za-z-.#]{2,20}$/.test(tag.value) || "Invalid tag",
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
      headers: {"Content-Type": "application/json"}, signal: controller.signal
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
const tooltipTriggerList = document.querySelectorAll("[title], [data-bs-toggle=\"tooltip\"]")
const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl))

// Publish post
$(".btn-post-publish, .btn-post-unpublish").on("click", function () {
  const $btn = $(this)
  const postId = $btn.data("post-id")
  const status = $btn.data("status")
  const url = window.CONFIG.update_post_status_url.replace("{post_id}", postId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({status}), success: function () {
      window.location.reload()
    }, error: function (xhr) {
      console.error(`Error on post ${status}:`, xhr.responseText)
      alert(`Failed to ${status} post.`)
    }, complete: function () {
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

// Like/dislike post (post impression)
$(document).on("click", ".btn-post-like, .btn-post-dislike", function () {
  const $btn = $(this)
  const postId = $btn.data("post-id")
  const action = $btn.data("action")
  const url = window.CONFIG.update_post_impression_url.replace("{post_id}", postId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({action}), success: function (res) {
      $btn.closest(".post-impressions").replaceWith(res)
    }, error: function (xhr) {
      console.error(`Error on post ${action}:`, xhr.responseText)
      alert(`Failed to ${action} post. Please try again.`)
    }, complete: function () {
      $btn.prop("disabled", false).removeClass("disabled")
    }
  })
})

// Follow/block user (user impression)
$(document).on("click", ".btn-user-follow, .btn-user-block", function () {
  const $btn = $(this)
  const userId = $btn.data("user-id")
  const action = $btn.data("action")
  const url = window.CONFIG.update_user_impression_url.replace("{user_id}", userId)

  $btn.prop("disabled", true).addClass("disabled")

  $.ajax({
    url, method: "POST", contentType: "application/json", data: JSON.stringify({action}), success: function (res) {
      $btn.closest(".user-impressions").replaceWith(res)
    }, error: function (xhr) {
      console.error(`Error on user ${action}:`, xhr.responseText)
      alert(`Failed to ${action} user. Please try again.`)
    }, complete: function () {
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
    url, method: "POST", contentType: "application/json", data: JSON.stringify({status}), success: function () {
      window.location.reload()
    }, error: function (xhr) {
      console.error("Error activating user:", xhr.responseText)
      alert("Failed to activate user.")
    }, complete: function () {
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

const uploadPublicFile = async function (file, progress = undefined) {
  try {
    const formData = new FormData()
    formData.append("file", file)

    // Upload to your existing endpoint
    const uploadResponse = await fetch(window.CONFIG.upload_public_file_url, {
      method: "POST", body: formData
    })

    if (!uploadResponse.ok) throw new Error("File upload failed")

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
    valid_elements: "figure[class],figcaption[class]," + "img[src|alt|title|class|width|height|style],"
      + "h2,h3,h4,h5,h6,"
      + "a[href|target|title],"
      + "b/strong,i/em,u,span[class],"
      + "ul,ol,li,"
      + "table[class|border|cellpadding|cellspacing],thead,tbody,tfoot,tr,th[colspan|rowspan],td[colspan|rowspan],"
      + "div[class],br,p,pre[class],code[class],blockquote",
    document_base_url: window.CONFIG.base_url + "/",
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
    image_title: true,
    images_upload_handler: async (blobInfo, progress) => {
      const filename = await uploadPublicFile(blobInfo.blob(), progress)
      return window.CONFIG.static_relative_url.replace("{filename}", filename)
    },
    image_class_list: [
      {title: "Responsive", value: "img-fluid"},
      {title: "Left", value: "float-start"},
      {title: "Right", value: "float-end"},
      {title: "Rounded", value: "rounded"},
      {title: "Thumbnail", value: "img-thumbnail"}
    ],
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
        if (e && e.element.nodeName === "IMG" && !e.element.className) {
          e.element.className = "img-fluid"
        }
        if (e && e.element.nodeName === "IMG" && !e.element.alt) {
          e.element.alt = prompt("Enter a short description (alt text) for the image:", "") || "Image"
        }
        if (e && e.element.nodeName === "IMG") {
          const img = e.element
          if (!img.classList.contains("figure-img")) {
            img.classList.add("figure-img", "img-fluid", "rounded")
          }
          if (!img.alt) {
            img.alt = prompt("Enter alt text (for accessibility & SEO):", "") || "Image"
          }
          if (!img.closest("figure")) {
            const fig = editor.dom.create("figure", {class: "figure"}, "")
            const frag = editor.dom.createFragment(img.outerHTML)
            const caption = editor.dom.create("figcaption", {class: "figure-caption"}, "Image caption")
            fig.appendChild(frag)
            fig.appendChild(caption)
            editor.dom.replace(fig, img)
          }
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

Prism.plugins.autoloader.languages_path = "https://cdn.jsdelivr.net/npm/prismjs@1.x/components/"
Prism.highlightAll()

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
  const pageUrl = encodeURIComponent(window.location.href)
  const pageTitle = encodeURIComponent(document.title)

  $(".share-btn.twitter").attr("href", `https://twitter.com/intent/tweet?url=${pageUrl}&text=${pageTitle}`)
  $(".share-btn.facebook").attr("href", `https://www.facebook.com/sharer/sharer.php?u=${pageUrl}`)
  $(".share-btn.linkedin").attr("href", `https://www.linkedin.com/shareArticle?mini=true&url=${pageUrl}&title=${pageTitle}`)
  $(".share-btn.email").attr("href", `mailto:?subject=${pageTitle}&body=Check out this article: ${pageUrl}`)
})