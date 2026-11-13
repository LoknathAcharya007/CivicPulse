/* CivicPulse - small progressive enhancements. Every page also works without JavaScript.
   No calculations happen here: all amounts come from the server. No innerHTML is used. */
(function () {
  "use strict";
  document.documentElement.classList.add("js");
  var GRADES = ["E", "D", "C", "B", "A"];

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); } else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    // Mobile navigation toggle
    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.getElementById("site-nav");
    if (toggle && nav) {
      toggle.addEventListener("click", function () {
        var open = nav.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && nav.classList.contains("is-open")) {
          nav.classList.remove("is-open"); toggle.setAttribute("aria-expanded", "false"); toggle.focus();
        }
      });
    }

    // Filter forms: apply as soon as a drop-down changes
    document.querySelectorAll("form[data-autosubmit] select").forEach(function (select) {
      select.addEventListener("change", function () {
        var form = select.form;
        if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
      });
    });

    // Scenario switcher on the estimate page: show the server's breakdown for the chosen target grade
    document.querySelectorAll("form[data-scenarios]").forEach(function (form) {
      var save = form.querySelector("[data-save-label]");
      function show() {
        var checked = form.querySelector("input[name=target_grade]:checked");
        if (!checked) { return; }
        document.querySelectorAll("[data-scenario]").forEach(function (panel) {
          panel.hidden = panel.getAttribute("data-scenario") !== checked.value;
        });
        if (save) {
          save.textContent = save.getAttribute("data-save-label").replace("{grade}", checked.value);
        }
      }
      form.querySelectorAll("input[name=target_grade]").forEach(function (r) { r.addEventListener("change", show); });
      show();
    });

    // Submit handling: upgrade-only check (the server checks again), then a busy state that
    // prevents duplicate submissions.
    document.querySelectorAll("form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (form.hasAttribute("data-upgrade-form")) {
          var current = form.getAttribute("data-current-grade");
          var picked = form.querySelector("input[name=target_grade]:checked, input[name=to_grade]:checked, [data-target-grade]");
          var value = picked ? picked.value : "";
          var message = form.querySelector("[data-upgrade-message]");
          if (GRADES.indexOf(value) <= GRADES.indexOf(current)) {
            event.preventDefault();
            if (message) {
              message.textContent = "Choose a grade better than " + current + ". Downgrades are not allowed.";
              message.hidden = false;
            }
            return;
          }
        }
        if (!form.hasAttribute("data-once")) { return; }
        if (form.getAttribute("aria-busy") === "true") { event.preventDefault(); return; }
        form.setAttribute("aria-busy", "true");
        var button = form.querySelector("button[type=submit]");
        if (button) {
          window.setTimeout(function () { button.disabled = true; }, 0);
          var busy = button.getAttribute("data-busy-label");
          if (busy) {
            button.setAttribute("data-original-label", button.textContent.trim());
            while (button.firstChild) { button.removeChild(button.firstChild); }
            var spin = document.createElement("span");
            spin.className = "spinner"; spin.setAttribute("aria-hidden", "true");
            button.appendChild(spin);
            button.appendChild(document.createTextNode(busy));
          }
        }
      });
    });

    // Re-enable forms when the page is shown again from the back/forward cache
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) { return; }
      document.querySelectorAll("form[aria-busy=true]").forEach(function (form) {
        form.removeAttribute("aria-busy");
        form.querySelectorAll("button[type=submit]").forEach(function (b) {
          b.disabled = false;
          if (b.hasAttribute("data-original-label")) { b.textContent = b.getAttribute("data-original-label"); }
        });
      });
    });

    document.querySelectorAll("[data-print]").forEach(function (b) {
      b.addEventListener("click", function () { window.print(); });
    });
  });
})();
