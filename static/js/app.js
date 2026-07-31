// Progressive enhancement for the CSS-only drawer: Escape closes it, and the
// hamburger exposes its open/closed state to assistive tech.
function initDrawer() {
  var toggle = document.getElementById("drawer-toggle");
  if (!toggle) return;
  toggle.setAttribute("role", "button");
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("change", function () {
    toggle.setAttribute("aria-expanded", toggle.checked ? "true" : "false");
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && toggle.checked) {
      toggle.checked = false;
      toggle.dispatchEvent(new Event("change"));
    }
  });
}

// Large-title collapse: once a page's <h1> scrolls up under the sticky bar, the
// bar swaps the brand for that page's title and keeps it there.
function initBarTitle() {
  var bar = document.querySelector(".top-bar");
  var slot = bar && bar.querySelector(".top-bar-title");
  if (!slot) return;

  var heading = document.querySelector(".page-title");
  if (!heading) {
    // Pages with no heading to collapse (the account page) name themselves in
    // the view context, and the title simply stays pinned.
    if (slot.textContent.trim()) bar.classList.add("is-collapsed");
    return;
  }

  slot.textContent = heading.textContent.trim();
  // The negative top margin puts the trigger line at the bottom edge of the
  // bar, so the swap lands exactly as the heading disappears behind it.
  new IntersectionObserver(
    function (entries) {
      bar.classList.toggle("is-collapsed", !entries[0].isIntersecting);
    },
    { rootMargin: "-" + bar.offsetHeight + "px 0px 0px 0px" }
  ).observe(heading);
}

// The specialty preset is a useful group on a long journal list.  Its toggle
// selects every journal in that preset and reflects a partial manual choice.
function initJournalGroupToggles() {
  document.querySelectorAll("[data-journal-group-toggle]").forEach(function (toggle) {
    var group = toggle.getAttribute("data-journal-group-toggle");
    var journals = document.querySelectorAll('[data-journal-group="' + group + '"]');
    if (!journals.length) return;

    function syncToggle() {
      var selected = Array.prototype.filter.call(journals, function (journal) {
        return journal.checked;
      }).length;
      toggle.checked = selected === journals.length;
      toggle.indeterminate = selected > 0 && selected < journals.length;
    }

    toggle.addEventListener("change", function () {
      journals.forEach(function (journal) {
        journal.checked = toggle.checked;
      });
      toggle.indeterminate = false;
    });
    journals.forEach(function (journal) {
      journal.addEventListener("change", syncToggle);
    });
    syncToggle();
  });
}

function initFeedMenu() {
  var menu = document.querySelector(".feed-menu");
  if (!menu) return;
  document.addEventListener("click", function (event) {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") menu.open = false;
  });
}

// Authentication is a full-page POST, so provide immediate feedback and block
// accidental double-submits while the browser follows the response or redirect.
function initAuthSubmitLoading() {
  document.querySelectorAll(".auth-shell form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;

      var label = button.textContent.trim().toLowerCase();
      var loadingLabel = label.indexOf("sign up") >= 0 ? "Creating account…" :
        label.indexOf("sign") >= 0 || label.indexOf("log") >= 0 ? "Signing in…" :
        label.indexOf("continue") >= 0 ? "Continuing…" : "Please wait…";
      button.disabled = true;
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      button.replaceChildren();
      var spinner = document.createElement("span");
      spinner.className = "button-spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.appendChild(spinner);
      button.appendChild(document.createTextNode(loadingLabel));
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initDrawer();
  initBarTitle();
  initJournalGroupToggles();
  initFeedMenu();
  initAuthSubmitLoading();
});
