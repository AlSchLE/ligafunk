// Apply the saved theme before the stylesheet loads to avoid a flash of the
// wrong colour scheme. Kept as a separate self-hosted file for the strict CSP.
(function () {
  "use strict";

  var theme = "dark";
  try {
    var saved = window.localStorage.getItem("ligafunk-theme");
    if (saved === "light" || saved === "dark") {
      theme = saved;
    }
  } catch (err) {
    // Storage can be unavailable in private mode; keep the dark default.
  }

  document.documentElement.setAttribute("data-theme", theme);

  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", theme === "light" ? "#f2f7f2" : "#080b10");
  }
})();
