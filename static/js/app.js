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

document.addEventListener("DOMContentLoaded", function () {
  initDrawer();
  initBarTitle();
});
