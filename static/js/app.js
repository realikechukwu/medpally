// Progressive enhancement for the CSS-only drawer: Escape closes it, and the
// hamburger exposes its open/closed state to assistive tech.
document.addEventListener("DOMContentLoaded", function () {
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
});
