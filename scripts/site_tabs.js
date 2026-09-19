(function () {
  "use strict";

  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tabs [role="tab"]'));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".tab-panel"));
  if (!tabs.length || !panels.length) return;

  function activate(name, opts) {
    opts = opts || {};
    var found = false;
    tabs.forEach(function (tab) {
      var on = tab.dataset.tab === name;
      if (on) found = true;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
    });
    if (!found) return;
    panels.forEach(function (panel) {
      var on = panel.dataset.tab === name;
      panel.hidden = !on;
      panel.tabIndex = on ? 0 : -1;
    });
    if (opts.updateHash) {
      history.replaceState(null, "", "#" + name);
    }
    if (opts.scroll) {
      document.querySelector(".tabs").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () {
      activate(tab.dataset.tab, { updateHash: true });
    });
    tab.addEventListener("keydown", function (e) {
      var idx = null;
      if (e.key === "ArrowRight" || e.key === "Right") idx = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft" || e.key === "Left") idx = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") idx = 0;
      else if (e.key === "End") idx = tabs.length - 1;
      else return;
      e.preventDefault();
      tabs[idx].focus();
      activate(tabs[idx].dataset.tab, { updateHash: true });
    });
  });

  window.addEventListener("hashchange", function () {
    var name = location.hash.replace("#", "");
    if (name) activate(name, { scroll: true });
  });

  var initial = location.hash.replace("#", "");
  activate(initial && tabs.some(function (t) { return t.dataset.tab === initial; }) ? initial : tabs[0].dataset.tab, {});
})();
