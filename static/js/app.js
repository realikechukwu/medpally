// The navigation bar follows iOS: transparent while the page's large title is
// in view, then a frosted bar with a hairline once content scrolls under it,
// and the page's title fades into the bar as the large one disappears.
function updateNavBarEdge() {
  var bar = document.getElementById("nav-bar");
  if (bar) bar.classList.toggle("is-scrolled", window.scrollY > 2);
}

function initBarTitle() {
  var bar = document.getElementById("nav-bar");
  var slot = bar && bar.querySelector(".nav-bar-title");
  if (!slot) return;

  if (window.medpallyBarTitleObserver) window.medpallyBarTitleObserver.disconnect();
  window.medpallyBarTitleObserver = null;

  var anchor = document.querySelector("#app-main .page-title, #app-main [data-title-anchor]");
  if (!anchor) {
    // A screen with no large title keeps its name in the bar throughout.
    bar.classList.toggle("is-collapsed", Boolean(slot.textContent.trim()));
    return;
  }

  if (!slot.textContent.trim()) slot.textContent = anchor.textContent.trim();
  // The negative top margin puts the trigger line at the bottom edge of the
  // bar, so the swap lands exactly as the heading disappears behind it.
  var observer = new IntersectionObserver(
    function (entries) {
      bar.classList.toggle("is-collapsed", !entries[0].isIntersecting);
    },
    { rootMargin: "-" + bar.offsetHeight + "px 0px 0px 0px" }
  );
  observer.observe(anchor);
  window.medpallyBarTitleObserver = observer;
}

// The specialty preset is a useful group on a long journal list.  Its toggle
// selects every journal in that preset and reflects a partial manual choice.
var initialisedJournalGroupToggles = new WeakSet();

function initJournalGroupToggles() {
  document.querySelectorAll("[data-journal-group-toggle]").forEach(function (toggle) {
    if (initialisedJournalGroupToggles.has(toggle)) return;
    initialisedJournalGroupToggles.add(toggle);
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

// Journal selection should stay easy even as the catalogue grows: search looks
// across the full list, filters keep the common choices close, and every change
// updates the count and save action immediately.
var initialisedJournalPickers = new WeakSet();

function initJournalPickers() {
  document.querySelectorAll("[data-journal-picker]").forEach(function (picker) {
    if (initialisedJournalPickers.has(picker)) return;
    initialisedJournalPickers.add(picker);

    var form = picker.closest("form");
    var search = picker.querySelector("[data-journal-search]");
    var clearSearch = picker.querySelector("[data-journal-search-clear]");
    var reset = picker.querySelector("[data-journal-reset]");
    var empty = picker.querySelector("[data-journal-empty]");
    var rows = picker.querySelectorAll("[data-journal-row]");
    var sections = picker.querySelectorAll("[data-journal-section]");
    var filters = picker.querySelectorAll("[data-journal-filter]");
    var checkboxes = form.querySelectorAll('input[name="journals"]');
    var groupToggles = picker.querySelectorAll("[data-journal-group-toggle]");
    var activeFilter = "recommended";

    function syncGroupToggles() {
      groupToggles.forEach(function (toggle) {
        var group = toggle.getAttribute("data-journal-group-toggle");
        var journals = form.querySelectorAll('[data-journal-group="' + group + '"]');
        var selected = Array.prototype.filter.call(journals, function (journal) {
          return journal.checked;
        }).length;
        toggle.checked = selected === journals.length;
        toggle.indeterminate = selected > 0 && selected < journals.length;
      });
    }

    function updatePicker() {
      var query = search.value.trim().toLocaleLowerCase();
      var selectedCount = Array.prototype.filter.call(checkboxes, function (checkbox) {
        return checkbox.checked;
      }).length;
      var visibleCount = 0;

      rows.forEach(function (row) {
        var checkbox = row.querySelector('input[name="journals"]');
        var matchesSearch = !query || row.dataset.journalSearchText.indexOf(query) >= 0;
        var matchesFilter = query || activeFilter === "all" ||
          (activeFilter === "recommended" && row.dataset.journalKind === "recommended") ||
          (activeFilter === "selected" && checkbox.checked);
        row.hidden = !(matchesSearch && matchesFilter);
        row.classList.toggle("is-selected", checkbox.checked);
        if (!row.hidden) visibleCount += 1;
      });

      sections.forEach(function (section) {
        var visibleRows = Array.prototype.filter.call(
          section.querySelectorAll("[data-journal-row]"),
          function (row) { return !row.hidden; }
        ).length;
        section.hidden = visibleRows === 0;
        if (query || activeFilter === "selected") section.open = visibleRows > 0;
        if (activeFilter === "recommended" && section.dataset.journalKind === "recommended") {
          section.open = true;
        }
      });

      filters.forEach(function (filter) {
        var selected = filter.dataset.journalFilter === activeFilter;
        filter.classList.toggle("is-active", selected);
        filter.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      picker.querySelectorAll("[data-selected-count]").forEach(function (count) {
        count.textContent = selectedCount;
      });
      picker.querySelectorAll("[data-selected-filter-count]").forEach(function (count) {
        count.textContent = selectedCount;
      });
      picker.querySelectorAll("[data-selected-noun]").forEach(function (noun) {
        noun.textContent = selectedCount === 1 ? "journal" : "journals";
      });

      var saveLabel = form.querySelector("[data-journal-save-label]");
      if (saveLabel) {
        var prefix = form.querySelector(".settings-save-bar.is-onboarding") ?
          "Continue with " : "Save ";
        saveLabel.textContent = prefix + selectedCount +
          (selectedCount === 1 ? " journal" : " journals");
      }
      if (clearSearch) clearSearch.hidden = !query;
      if (empty) empty.hidden = visibleCount > 0;
      if (reset) {
        reset.disabled = Array.prototype.every.call(checkboxes, function (checkbox) {
          return checkbox.checked === checkbox.hasAttribute("data-journal-recommended");
        });
      }
      syncGroupToggles();
    }

    filters.forEach(function (filter) {
      filter.addEventListener("click", function () {
        activeFilter = filter.dataset.journalFilter;
        updatePicker();
      });
    });
    checkboxes.forEach(function (checkbox) {
      checkbox.addEventListener("change", updatePicker);
    });
    groupToggles.forEach(function (toggle) {
      toggle.addEventListener("change", updatePicker);
    });
    search.addEventListener("input", function () {
      if (search.value.trim()) activeFilter = "all";
      updatePicker();
    });
    clearSearch.addEventListener("click", function () {
      search.value = "";
      search.focus();
      updatePicker();
    });
    reset.addEventListener("click", function () {
      checkboxes.forEach(function (checkbox) {
        checkbox.checked = checkbox.hasAttribute("data-journal-recommended");
      });
      search.value = "";
      activeFilter = "recommended";
      updatePicker();
    });
    updatePicker();
  });
}

// A flash message is a receipt for something the reader just did, so it takes
// itself away again instead of sitting under the feed for the rest of the
// session.
var MESSAGE_DISMISS_MS = 5000;
var MESSAGE_FADE_MS = 400;

function initFlashMessages() {
  document.querySelectorAll(".messages[data-autodismiss]").forEach(function (list) {
    if (list.dataset.dismissing) return;
    // A message with a next action should remain available until the reader
    // acts or navigates away; ordinary receipts still clear themselves.
    if (list.querySelector(".message-action")) return;
    list.dataset.dismissing = "true";
    window.setTimeout(function () {
      list.classList.add("is-dismissed");
      window.setTimeout(function () {
        list.remove();
      }, MESSAGE_FADE_MS);
    }, MESSAGE_DISMISS_MS);
  });
}

// Pull-down menus (filter and sort, a card's "more" actions) are <details>
// elements, so they open without JavaScript. This adds what a menu is
// expected to do: only one open at a time, a tap outside or Escape closes it,
// choosing an item closes it, and it opens upwards when there is no room below.
function closeMenus(except) {
  document.querySelectorAll("details[data-menu][open]").forEach(function (menu) {
    if (menu !== except) menu.open = false;
  });
}

// Opens downwards unless it would run under the tab bar and there is more
// room above; whichever way it opens, it is capped to the space it has and
// scrolls inside itself rather than running off the screen.
var MENU_MARGIN = 8;

function placeMenu(menu) {
  var panel = menu.querySelector(".menu-panel");
  var trigger = menu.querySelector("summary");
  if (!panel || !trigger) return;
  menu.classList.remove("opens-up");
  panel.style.maxHeight = "";

  var bottomBar = document.getElementById("bottom-nav");
  // Not offsetParent: it is always null for a fixed element like the tab bar.
  var floor = bottomBar && getComputedStyle(bottomBar).display !== "none" ?
    bottomBar.getBoundingClientRect().top : window.innerHeight;
  var navBar = document.getElementById("nav-bar");
  var ceiling = navBar ? navBar.getBoundingClientRect().bottom : 0;
  var anchor = trigger.getBoundingClientRect();
  var below = floor - anchor.bottom - MENU_MARGIN;
  var above = anchor.top - Math.max(ceiling, 0) - MENU_MARGIN;

  var opensUp = panel.offsetHeight > below && above > below;
  menu.classList.toggle("opens-up", opensUp);
  panel.style.maxHeight = Math.max(160, opensUp ? above : below) + "px";
}

function initMenus() {
  if (window.medpallyMenusInitialised) return;
  window.medpallyMenusInitialised = true;
  // toggle does not bubble, so listen during capture.
  document.addEventListener("toggle", function (event) {
    var menu = event.target;
    if (!menu.matches || !menu.matches("details[data-menu]") || !menu.open) return;
    closeMenus(menu);
    placeMenu(menu);
  }, true);
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details[data-menu][open]").forEach(function (menu) {
      if (!menu.contains(event.target)) {
        menu.open = false;
      } else if (event.target.closest(".menu-item")) {
        // After the click has done its work (a submit, a new tab).
        window.setTimeout(function () { menu.open = false; }, 0);
      }
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var open = document.querySelector("details[data-menu][open]");
    if (!open) return;
    open.open = false;
    var summary = open.querySelector("summary");
    if (summary) summary.focus();
  });
}

// The feed can grow for many pages through infinite scroll. Keep the return
// trip short without occupying space until the reader has moved well past the
// first screen. Event delegation also survives in-app navigation swaps.
var BACK_TO_TOP_THRESHOLD = 480;

function updateBackToTop() {
  var button = document.querySelector("[data-back-to-top]");
  if (!button) return;
  button.hidden = window.scrollY <= BACK_TO_TOP_THRESHOLD;
}

function scrollToPageTop() {
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
}

function initScrollEffects() {
  if (!window.medpallyScrollEffectsInitialised) {
    window.medpallyScrollEffectsInitialised = true;
    var queued = false;
    window.addEventListener("scroll", function () {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () {
        queued = false;
        updateNavBarEdge();
        updateBackToTop();
      });
    }, { passive: true });
    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-back-to-top]")) scrollToPageTop();
    });
  }
  updateNavBarEdge();
  updateBackToTop();
}

// A short confirmation that floats above the tab bar, for actions whose
// result is otherwise invisible (a copied link).
var TOAST_MS = 1800;
var toastTimer;

function showToast(message) {
  var toast = document.querySelector("[data-toast]");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  window.requestAnimationFrame(function () {
    toast.classList.add("is-visible");
  });
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(function () {
    toast.classList.remove("is-visible");
    window.setTimeout(function () { toast.hidden = true; }, 250);
  }, TOAST_MS);
}

// Sharing hands the paper's public page to the system share sheet where there
// is one (phones, Safari), and copies the link everywhere else.
function initShare() {
  if (window.medpallyShareInitialised) return;
  window.medpallyShareInitialised = true;
  document.addEventListener("click", async function (event) {
    var button = event.target.closest("[data-share]");
    if (!button) return;
    event.preventDefault();
    var url = new URL(button.dataset.sharePath || window.location.pathname, window.location.origin).href;
    var title = button.dataset.shareTitle || document.title;
    if (navigator.share) {
      try {
        await navigator.share({ title: title, url: url });
      } catch (error) {
        // Closing the share sheet is not a failure worth reporting.
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      showToast("Link copied");
    } catch (error) {
      showToast("Couldn't copy the link");
    }
  });
}

// ---------------------------------------------------------------- navigation
//
// Signed-in navigation stays inside the page, like a native app, while every
// link and form still works as a plain request without JavaScript.
//
// Each history entry the app creates carries {id, depth, rootDepth}: depth
// counts screens pushed since the app was opened, and rootDepth is the depth
// of the tab screen the current stack started from. That is what lets a back
// button step back through history instead of reloading its parent, and a tap
// on the current tab pop straight back to the tab's list.
//
// Leaving a screen keeps its live DOM — every page infinite scroll has loaded,
// which weeks are open, the scroll position — so stepping back to it puts the
// reader exactly where they were. A serialised copy also goes into
// sessionStorage, so a tab visited again after a reload still opens at once.
// We deliberately use sessionStorage, not a shared HTTP cache: every page
// contains a reader's personalised feed and saved papers.
var NAVIGATION_CACHE_VERSION = "v5";
var NAVIGATION_CACHE_TTL_MS = 5 * 60 * 1000;
var LIVE_PAGE_LIMIT = 10;
var LIVE_PAGE_MAX_AGE_MS = 30 * 60 * 1000;
// How long a tap waits for the next screen before showing a placeholder. Long
// enough that a quick response slides in finished, short enough to feel
// immediate when it does not.
var CONTENT_WAIT_MS = 250;

var livePages = new Map();
var activeEntry = null;
var activeHref = "";
var navigationRequest;
var pendingPop = null;
var pendingPages = new Map();

function navigationScope() {
  return document.body.dataset.navigationUser || "";
}

// The stylesheet and scripts a document was built against. Production names
// them by content hash, so this changes with every deploy that touches them.
function assetVersion(pageDocument) {
  return Array.prototype.map.call(
    pageDocument.querySelectorAll('head link[rel="stylesheet"], head script[src]'),
    function (node) { return node.getAttribute("href") || node.getAttribute("src"); }
  ).join(" ");
}

var ASSET_VERSION = assetVersion(document);

// Markup saved under one deploy's assets is never shown under another's.
function navigationCachePrefix() {
  var scope = navigationScope();
  if (!scope) return "";
  return "medpally:navigation:" + NAVIGATION_CACHE_VERSION + ":" + ASSET_VERSION + ":" +
    scope + ":";
}

function navigationCacheKey(url) {
  var prefix = navigationCachePrefix();
  return prefix ? prefix + url.pathname + url.search : "";
}

function safeSessionGet(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function safeSessionSet(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch (error) {
    // Private browsing and a full storage area should not stop navigation.
  }
}

function readCachedPage(url) {
  var key = navigationCacheKey(url);
  var raw = key && safeSessionGet(key);
  if (!raw) return null;
  try {
    var cached = JSON.parse(raw);
    if (!cached.savedAt || Date.now() - cached.savedAt > NAVIGATION_CACHE_TTL_MS) return null;
    return cached;
  } catch (error) {
    return null;
  }
}

function saveCachedPage(url, page) {
  var key = navigationCacheKey(url);
  if (!key || !page) return;
  page.savedAt = Date.now();
  safeSessionSet(key, JSON.stringify(page));
}

// Flash messages are deliberately left out of the cache: "signed in as …"
// reappearing on the way back to the feed reads like a second login.
function cacheableMainMarkup(main) {
  if (!main.querySelector(".messages")) return main.outerHTML;
  var clone = main.cloneNode(true);
  clone.querySelectorAll(".messages").forEach(function (list) {
    list.remove();
  });
  return clone.outerHTML;
}

function pageFromDocument(pageDocument, options) {
  var settings = options || {};
  var main = pageDocument.getElementById("app-main");
  var bottomNav = pageDocument.getElementById("bottom-nav");
  if (!main || !bottomNav) return null;

  var navBar = pageDocument.getElementById("nav-bar");
  var sidebarNav = pageDocument.querySelector(".sidebar-nav");
  return {
    main: settings.includeMessages ? main.outerHTML : cacheableMainMarkup(main),
    navBar: navBar ? navBar.outerHTML : "",
    bottomNav: bottomNav.outerHTML,
    sidebarNav: sidebarNav ? sidebarNav.innerHTML : "",
    title: pageDocument.title,
    scrollY: 0,
  };
}

function snapshotCurrentPage() {
  var page = pageFromDocument(document);
  if (page) page.scrollY = window.scrollY;
  return page;
}

function cacheCurrentPage() {
  saveCachedPage(new URL(activeHref || window.location.href), snapshotCurrentPage());
}

function nodeFromMarkup(markup) {
  var template = document.createElement("template");
  template.innerHTML = markup.trim();
  return template.content.firstElementChild;
}

function swapNode(current, replacement) {
  if (current && replacement && current !== replacement) current.replaceWith(replacement);
}

function hydratePage() {
  initBarTitle();
  initJournalGroupToggles();
  initJournalPickers();
  initScrollEffects();
  initFlashMessages();
  applyWeekState();
  if (window.htmx) window.htmx.process(document.getElementById("app-main"));
  document.dispatchEvent(new CustomEvent("medpally:page-change"));
}

// Puts a screen on display: either a live one kept from earlier (DOM nodes) or
// one rebuilt from markup (a cache entry or a fresh response).
function showPage(page) {
  var currentMain = document.getElementById("app-main");
  var currentBottomNav = document.getElementById("bottom-nav");
  if (!currentMain || !currentBottomNav) return false;

  var asNode = function (value) {
    return typeof value === "string" ? (value ? nodeFromMarkup(value) : null) : value;
  };
  var main = asNode(page.main);
  if (!main) return false;
  swapNode(currentMain, main);
  swapNode(currentBottomNav, asNode(page.bottomNav));
  swapNode(document.getElementById("nav-bar"), asNode(page.navBar));

  var sidebarNav = document.querySelector(".sidebar-nav");
  if (sidebarNav && page.sidebarNav) sidebarNav.innerHTML = page.sidebarNav;
  if (page.title) document.title = page.title;
  hydratePage();
  return true;
}

// Keeps the screen on display alive under its history entry, so stepping back
// to it restores it as it was rather than rebuilding it.
function stashActivePage() {
  if (!activeEntry) return;
  var main = document.getElementById("app-main");
  var bottomNav = document.getElementById("bottom-nav");
  if (!main || !bottomNav || main.classList.contains("is-page-loading")) return;
  var sidebarNav = document.querySelector(".sidebar-nav");
  livePages.delete(activeEntry.id);
  livePages.set(activeEntry.id, {
    main: main,
    navBar: document.getElementById("nav-bar"),
    bottomNav: bottomNav,
    sidebarNav: sidebarNav ? sidebarNav.innerHTML : "",
    title: document.title,
    href: activeHref,
    scrollY: window.scrollY,
    storedAt: Date.now(),
    stale: false,
  });
  while (livePages.size > LIVE_PAGE_LIMIT) {
    livePages.delete(livePages.keys().next().value);
  }
}

function isFresh(page) {
  return !page.stale && Date.now() - page.storedAt < LIVE_PAGE_MAX_AGE_MS;
}

// The most recent live copy of a URL, taken out of the store so one screen is
// never on display twice.
function takeLivePageByHref(href) {
  var match = null;
  livePages.forEach(function (page, id) {
    if (page.href === href && isFresh(page)) match = { id: id, page: page };
  });
  if (!match) return null;
  livePages.delete(match.id);
  return match.page;
}

function showNavigationSkeleton() {
  var main = document.getElementById("app-main");
  if (!main) return;
  // A fresh element: the outgoing one may be kept alive for the way back.
  var placeholder = main.cloneNode(false);
  placeholder.classList.add("is-page-loading");
  placeholder.setAttribute("aria-busy", "true");
  placeholder.innerHTML =
    '<div class="page-loading" role="status"><span class="visually-hidden">Loading page</span>' +
    '<div class="skeleton skeleton-title"></div><div class="skeleton skeleton-subtitle"></div>' +
    '<div class="skeleton-card"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line skeleton-line-short"></div><div class="skeleton skeleton-copy"></div><div class="skeleton skeleton-copy skeleton-copy-short"></div></div>' +
    '<div class="skeleton-card"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line skeleton-line-short"></div><div class="skeleton skeleton-copy"></div></div></div>';
  main.replaceWith(placeholder);
}

// iOS-style push and pop. Only runs where the browser has same-document view
// transitions and the reader has not asked for reduced motion; Safari's own
// swipe-back animation is never doubled up.
function transition(direction, update, skip) {
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!direction || skip || reduceMotion || !document.startViewTransition) {
    update();
    return Promise.resolve();
  }
  var root = document.documentElement;
  root.dataset.navDirection = direction;
  var viewTransition = document.startViewTransition(update);
  viewTransition.finished.finally(function () {
    delete root.dataset.navDirection;
  });
  return viewTransition.updateCallbackDone.catch(function () {});
}

async function fetchPage(url, signal) {
  var response = await window.fetch(url.href, {
    credentials: "same-origin",
    cache: "no-store",
    signal: signal,
    headers: { "X-MedPally-Navigation": "1" },
  });
  if (response.redirected || response.url !== url.href) return { redirect: response.url };
  if (!response.ok) throw new Error("Navigation request failed");
  var html = await response.text();
  var pageDocument = new DOMParser().parseFromString(html, "text/html");
  // A tab left open across a deploy still has the old stylesheet and script,
  // which do not know the new markup, so the next screen is loaded for real.
  if (assetVersion(pageDocument) !== ASSET_VERSION) return { redirect: url.href };
  var page = pageFromDocument(pageDocument);
  // A page outside the app shell (sign-in, onboarding) is loaded for real.
  return page ? { page: page } : { redirect: url.href };
}

// One request per URL in flight, so a press that starts loading a screen and
// the click that follows share the same response.
function requestPage(url) {
  var pending = pendingPages.get(url.href);
  if (pending && Date.now() - pending.at < 10000) return pending.promise;
  var promise = fetchPage(url);
  pendingPages.set(url.href, { promise: promise, at: Date.now() });
  promise.catch(function () {
    pendingPages.delete(url.href);
  });
  return promise;
}

function within(promise, ms) {
  return Promise.race([
    promise,
    new Promise(function (resolve) { window.setTimeout(resolve, ms, null); }),
  ]);
}

function newEntry(depth, rootDepth, parentHref) {
  return {
    medpally: true,
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    depth: depth,
    rootDepth: rootDepth,
    parentHref: parentHref || "",
  };
}

function readEntry(state) {
  return state && state.medpally ? state : null;
}

function isModifiedNavigation(event) {
  return event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
    event.shiftKey || event.altKey;
}

// mode: "push" (a new screen), "replace" (a different view of this screen —
// a segment or a filter), or "history" (back/forward). isTab marks a tab-bar
// destination, which starts a new stack and comes back as it was left.
async function navigateTo(url, options) {
  var settings = options || {};
  var mode = settings.mode || "push";
  if (!navigationScope()) {
    window.location.assign(url.href);
    return;
  }
  if (mode !== "history" && url.href === window.location.href) return;

  closeMenus();
  var popped = mode === "history" && pendingPop && pendingPop.href === url.href ? pendingPop : null;
  pendingPop = null;
  // A form screen being left mid-save is not worth keeping in either form.
  if (activeHref && !popped) cacheCurrentPage();
  if (mode !== "replace" && !popped) stashActivePage();

  if (navigationRequest) navigationRequest.abort();
  var request = new AbortController();
  navigationRequest = request;

  var previous = activeEntry || newEntry(0, 0);
  var entry;
  if (mode === "history") {
    entry = readEntry(window.history.state);
    if (!entry) {
      entry = newEntry(0, 0);
      window.history.replaceState(entry, "", url.href);
    }
  } else if (mode === "replace") {
    entry = newEntry(previous.depth, settings.isTab ? previous.depth : previous.rootDepth,
      previous.parentHref);
    livePages.delete(previous.id);
    window.history.replaceState(entry, "", url.href);
  } else {
    var depth = previous.depth + 1;
    entry = newEntry(depth, settings.isTab ? depth : previous.rootDepth, activeHref);
    window.history.pushState(entry, "", url.href);
  }
  activeEntry = entry;
  activeHref = url.href;

  var direction = settings.direction || "";
  var keepScroll = mode === "history" || settings.isTab;

  // A form that saved and returned to its parent screen: show the parent as
  // the server just rendered it, with its confirmation message.
  if (popped) {
    livePages.delete(entry.id);
    await transition(direction, function () {
      showPage(popped.page);
      window.scrollTo(0, 0);
    }, settings.skipTransition);
    saveCachedPage(url, snapshotCurrentPage());
    return;
  }

  // Back or forward to a screen still alive: exactly as it was left.
  var restoreY = null;
  if (mode === "history") {
    var kept = livePages.get(entry.id);
    if (kept && kept.href === url.href) {
      livePages.delete(entry.id);
      if (isFresh(kept)) {
        await transition(direction, function () {
          showPage(kept);
          window.scrollTo(0, kept.scrollY);
        }, settings.skipTransition);
        return;
      }
      // Its data changed underneath it (a settings change): rebuild it, but
      // still come back to the same place.
      restoreY = kept.scrollY;
    }
  }

  // A tab comes back as the reader left it.
  if (settings.isTab) {
    var tab = takeLivePageByHref(url.href);
    if (tab) {
      await transition(direction, function () {
        showPage(tab);
        window.scrollTo(0, tab.scrollY);
      }, settings.skipTransition);
      return;
    }
  }

  var cached = readCachedPage(url);
  if (cached) {
    await transition(direction, function () {
      showPage(cached);
      window.scrollTo(0, restoreY !== null ? restoreY : keepScroll ? cached.scrollY : 0);
    }, settings.skipTransition);
    // Keep the rendered page stable while a quiet refresh makes the next
    // visit current. Replacing it a second time would make scrolling jump.
    try {
      var refreshed = await fetchPage(url, request.signal);
      if (refreshed && refreshed.page) saveCachedPage(url, refreshed.page);
    } catch (error) {
      // The cached page is still a useful, private fallback while offline.
    }
    return;
  }

  try {
    var pending = requestPage(url);
    pendingPages.delete(url.href);
    var result = await within(pending, CONTENT_WAIT_MS);
    if (navigationRequest !== request) return;
    var showedPlaceholder = false;
    if (!result) {
      showedPlaceholder = true;
      await transition(direction, function () {
        showNavigationSkeleton();
        window.scrollTo(0, 0);
      }, settings.skipTransition);
      result = await pending;
      if (navigationRequest !== request) return;
    }
    if (result.redirect) {
      window.location.assign(result.redirect);
      return;
    }
    saveCachedPage(url, result.page);
    await transition(showedPlaceholder ? "" : direction, function () {
      showPage(result.page);
      window.scrollTo(0, restoreY || 0);
    }, settings.skipTransition);
  } catch (error) {
    if (error.name !== "AbortError" && navigationRequest === request) {
      window.location.assign(url.href);
    }
  }
}

var INVALIDATION_PATHS = {
  feed: "/feed/",
  saved: "/feed/read-later/",
  liked: "/feed/liked/",
  external: "/feed/external/",
  account: "/account/",
  profile: "/settings/profile/",
  journals: "/settings/journals/",
  notifications: "/settings/notifications/",
};

function invalidateCachedTabs(tabNames) {
  var prefix = navigationCachePrefix();
  if (!prefix) return;
  try {
    for (var index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
      var key = window.sessionStorage.key(index);
      if (!key || key.indexOf(prefix) !== 0) continue;
      tabNames.forEach(function (tabName) {
        if (INVALIDATION_PATHS[tabName] && key.indexOf(prefix + INVALIDATION_PATHS[tabName]) === 0) {
          window.sessionStorage.removeItem(key);
        }
      });
    }
  } catch (error) {
    // Cache invalidation is an optimisation; the server remains authoritative.
  }
}

function markLivePagesStale(tabNames) {
  livePages.forEach(function (page) {
    var path = new URL(page.href).pathname;
    tabNames.forEach(function (tabName) {
      if (INVALIDATION_PATHS[tabName] && path.indexOf(INVALIDATION_PATHS[tabName]) === 0) {
        page.stale = true;
      }
    });
  });
}

// Saving or liking a paper on one screen updates the same paper on every
// screen kept for the way back, so the feed a reader returns to never shows a
// bookmark they have just removed; a dismissed paper leaves them all.
function syncLivePages(form, markup) {
  var action = form.getAttribute("hx-post");
  if (!action) return;
  var dismissed = /\/dismiss\/$/.test(action);
  livePages.forEach(function (page) {
    page.main.querySelectorAll('form[hx-post="' + action + '"]').forEach(function (copy) {
      if (dismissed) {
        var card = copy.closest("article");
        if (card) card.remove();
        return;
      }
      var fresh = nodeFromMarkup(markup);
      if (fresh) copy.replaceWith(fresh);
    });
  });
}

function setSubmitPending(button, pending) {
  if (!button) return;
  if (pending) {
    button.dataset.pendingMarkup = button.innerHTML;
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");
    button.replaceChildren();
    var spinner = document.createElement("span");
    spinner.className = "button-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.appendChild(spinner);
    button.appendChild(document.createTextNode(button.dataset.pendingLabel || "Saving…"));
    return;
  }
  button.disabled = false;
  button.classList.remove("is-loading");
  button.removeAttribute("aria-busy");
  if (button.dataset.pendingMarkup) button.innerHTML = button.dataset.pendingMarkup;
}

function showFormFailure(form) {
  var status = form.querySelector("[data-form-status]");
  if (!status) return;
  status.textContent = "We couldn't save that. Check your connection and try again.";
  status.classList.add("is-error");
}

async function submitAppForm(form, submitter) {
  if (form.dataset.submitting) return;
  form.dataset.submitting = "true";
  var button = submitter || form.querySelector('button[type="submit"]');
  var request = new AbortController();
  if (navigationRequest) navigationRequest.abort();
  navigationRequest = request;
  cacheCurrentPage();
  setSubmitPending(button, true);

  try {
    var response = await window.fetch(form.action || window.location.href, {
      method: (form.method || "post").toUpperCase(),
      body: new FormData(form),
      credentials: "same-origin",
      cache: "no-store",
      signal: request.signal,
      headers: { "X-MedPally-Navigation": "1" },
    });
    if (!response.ok) throw new Error("Settings request failed");

    var responseUrl = new URL(response.url, window.location.href);
    if (responseUrl.origin !== window.location.origin) {
      window.location.assign(responseUrl.href);
      return;
    }
    var html = await response.text();
    var page = pageFromDocument(
      new DOMParser().parseFromString(html, "text/html"),
      { includeMessages: true }
    );
    if (!page) {
      window.location.assign(responseUrl.href);
      return;
    }

    var invalidations = (form.dataset.cacheInvalidate || "").split(" ").filter(Boolean);
    invalidateCachedTabs(invalidations);
    markLivePagesStale(invalidations);

    // Saved and sent back to the screen this one was opened from: step back
    // to it, as a settings screen does on iOS, rather than stacking a second
    // copy of it on top.
    if (activeEntry && activeEntry.depth > activeEntry.rootDepth &&
      activeEntry.parentHref === responseUrl.href) {
      pendingPop = { href: responseUrl.href, page: page };
      window.history.back();
      return;
    }

    window.history.replaceState(activeEntry, "", responseUrl.href);
    activeHref = responseUrl.href;
    if (!showPage(page)) {
      window.location.assign(responseUrl.href);
      return;
    }
    saveCachedPage(responseUrl, snapshotCurrentPage());
    window.scrollTo(0, 0);
  } catch (error) {
    if (error.name !== "AbortError") {
      delete form.dataset.submitting;
      setSubmitPending(button, false);
      showFormFailure(form);
    }
  } finally {
    if (navigationRequest === request) navigationRequest = null;
  }
}

function initAppFormSubmissions() {
  if (!navigationScope() || window.medpallyAppFormsInitialised) return;
  window.medpallyAppFormsInitialised = true;
  document.addEventListener("submit", function (event) {
    var search = event.target.closest("form[data-app-search]");
    if (search) {
      event.preventDefault();
      var url = new URL(search.action || window.location.href);
      url.search = new URLSearchParams(new FormData(search)).toString();
      // Put the keyboard away so the results are visible.
      if (document.activeElement) document.activeElement.blur();
      navigateTo(url, { mode: "replace" });
      return;
    }
    var form = event.target.closest("form[data-app-submit]");
    if (!form) return;
    event.preventDefault();
    submitAppForm(form, event.submitter);
  });
}

function prefetchFrequentlyUsedTabs() {
  var links = document.querySelectorAll('[data-tab-nav][href="/feed/"], [data-tab-nav][href="/feed/read-later/"]');
  var urls = [];
  links.forEach(function (link) {
    var url = new URL(link.href, window.location.href);
    if (url.href !== window.location.href && !readCachedPage(url) &&
      !urls.some(function (candidate) { return candidate.href === url.href; })) {
      urls.push(url);
    }
  });
  urls.reduce(function (promise, url) {
    return promise.then(async function () {
      try {
        var result = await fetchPage(url);
        if (result && result.page) saveCachedPage(url, result.page);
      } catch (error) {
        // Prefetching must never make the current page feel slower.
      }
    });
  }, Promise.resolve());
}

function stripHash(href) {
  return href.split("#")[0];
}

function initTabNavigation() {
  if (!navigationScope() || window.medpallyTabNavigationInitialised) return;
  window.medpallyTabNavigationInitialised = true;
  // The app restores positions itself; the browser doing it too fights it.
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";

  activeEntry = readEntry(window.history.state);
  if (!activeEntry) {
    activeEntry = newEntry(0, 0);
    window.history.replaceState(activeEntry, "", window.location.href);
  }
  activeHref = window.location.href;
  cacheCurrentPage();

  document.addEventListener("click", function (event) {
    if (isModifiedNavigation(event)) return;

    var back = event.target.closest("a[data-nav-back]");
    if (back) {
      event.preventDefault();
      // The screen underneath is the one the reader came from: step back to
      // it. Otherwise (a shared link opened cold) go to the parent instead.
      if (activeEntry && activeEntry.depth > activeEntry.rootDepth) {
        window.history.back();
      } else {
        navigateTo(new URL(back.href, window.location.href),
          { mode: "replace", isTab: true, direction: "back" });
      }
      return;
    }

    var link = event.target.closest("a[data-tab-nav], a[data-app-nav]");
    if (!link || link.target) return;
    var url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();

    if (link.hasAttribute("data-tab-nav")) {
      var isCurrentTab = link.classList.contains("is-active");
      // On iOS a tap on the tab you are in pops back to its list, and a tap
      // while on the list scrolls it to the top.
      if (isCurrentTab && activeEntry && activeEntry.depth > activeEntry.rootDepth) {
        window.history.go(activeEntry.rootDepth - activeEntry.depth);
        return;
      }
      if (isCurrentTab || url.href === window.location.href) {
        scrollToPageTop();
        return;
      }
      navigateTo(url, { mode: "push", isTab: true });
      return;
    }

    // Grey the card out now, as the server will on the way back.
    if (link.hasAttribute("data-card-link")) {
      var card = link.closest(".card");
      if (card) card.classList.add("seen");
    }
    var replace = link.getAttribute("data-app-nav") === "replace";
    navigateTo(url, { mode: replace ? "replace" : "push", direction: replace ? "" : "forward" });
  });

  // A mouse press is a near-certain click, so start loading on the way down.
  // Touch is left alone: a finger landing on a card is usually a scroll.
  document.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    var link = event.target.closest("a[data-app-nav]");
    if (!link || link.target) return;
    var url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.href === window.location.href) return;
    if (!readCachedPage(url)) requestPage(url).catch(function () {});
  });

  window.addEventListener("popstate", function (event) {
    var entry = readEntry(event.state);
    if (!entry && stripHash(window.location.href) === stripHash(activeHref)) return;
    var direction = "";
    if (entry && activeEntry) {
      direction = entry.depth < activeEntry.depth ? "back" :
        entry.depth > activeEntry.depth ? "forward" : "";
    }
    navigateTo(new URL(window.location.href), {
      mode: "history",
      direction: direction,
      skipTransition: Boolean(event.hasUAVisualTransition),
    });
  });
  window.addEventListener("pagehide", cacheCurrentPage);
  // beforeOnLoad rather than afterRequest: these forms swap themselves out,
  // and htmx fires afterRequest on the detached original, which never
  // bubbles to the document.
  document.addEventListener("htmx:beforeOnLoad", function (event) {
    var form = event.detail.elt && event.detail.elt.closest &&
      event.detail.elt.closest("form[data-cache-invalidate]");
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (!form || status < 200 || status >= 300) return;
    var names = form.dataset.cacheInvalidate.split(" ");
    invalidateCachedTabs(names);
    syncLivePages(form, event.detail.xhr.responseText);
    // The feed is kept current by syncLivePages; collections change shape.
    markLivePagesStale(names.filter(function (name) { return name !== "feed"; }));
  });

  var idle = window.requestIdleCallback || function (callback) { window.setTimeout(callback, 700); };
  idle(prefetchFrequentlyUsedTabs, { timeout: 1500 });
}

// Week groups.  A week's cards are flat siblings tagged with data-week rather
// than nested in a container: a week that straddles a page boundary arrives in
// two separate swaps, and the second half could never be nested back inside a
// container the first half rendered.  Selecting by attribute lets the whole
// week collapse as one thing however it arrived.
//
// Which weeks are open is a view preference rather than data, so it lives on
// the device and never reaches the server.  It records only the weeks the
// reader has actually changed, so the default (the newest weeks open) keeps
// applying as the weeks roll forward rather than pinning one week open for good.
var WEEK_STATE_VERSION = "v1";

function safeLocalGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function safeLocalSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Private browsing and a full storage area should not break the toggle.
  }
}

function weekStateKey() {
  var scope = navigationScope();
  return scope ? "medpally:weeks:" + WEEK_STATE_VERSION + ":" + scope : "";
}

function readWeekState() {
  var key = weekStateKey();
  var raw = key && safeLocalGet(key);
  if (!raw) return {};
  try {
    var parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};
  }
}

function setWeekOpen(week, open) {
  document.querySelectorAll('.card[data-week="' + week + '"]').forEach(function (card) {
    card.hidden = !open;
  });
  document.querySelectorAll('[data-week-toggle="' + week + '"]').forEach(function (header) {
    header.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

// Cards arrive from the server closed or open by its own default, because the
// server has no idea what this reader has since opened.  Reconciling on swap is
// what stops a week the reader opened from snapping shut when its second page
// lands.
function applyWeekState() {
  var headers = document.querySelectorAll("[data-week-toggle]");
  if (!headers.length) return;
  var state = readWeekState();
  headers.forEach(function (header) {
    var week = header.getAttribute("data-week-toggle");
    if (state[week] !== undefined) setWeekOpen(week, state[week] === true);
  });
}

function initWeekGroups() {
  if (window.medpallyWeekGroupsInitialised) return;
  window.medpallyWeekGroupsInitialised = true;
  document.addEventListener("click", function (event) {
    var header = event.target.closest("[data-week-toggle]");
    if (!header) return;
    var week = header.getAttribute("data-week-toggle");
    var open = header.getAttribute("aria-expanded") !== "true";
    setWeekOpen(week, open);
    var state = readWeekState();
    state[week] = open;
    safeLocalSet(weekStateKey(), JSON.stringify(state));
  });
  document.addEventListener("htmx:afterSwap", applyWeekState);
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

// The service worker is what makes MedPally installable, and it keeps an
// offline notice to hand. Registration is best-effort: nothing on the page
// waits on it, and a browser without support simply carries on.
function initServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initBarTitle();
  initJournalGroupToggles();
  initJournalPickers();
  initMenus();
  initScrollEffects();
  initShare();
  initWeekGroups();
  initFlashMessages();
  applyWeekState();
  initAuthSubmitLoading();
  initTabNavigation();
  initAppFormSubmissions();
  initServiceWorker();
});
