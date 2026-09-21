/* 地図は押したときだけ読み込む（ADR 0013 追記、2026-09-21）。
   押すまで Google へ一切通信しない。依存なし。JavaScript が動かない環境でも、
   枠の下に Google マップへの外部リンクが残る。 */
(function () {
  "use strict";

  function openMap(box) {
    var frame = document.createElement("iframe");
    frame.src = box.getAttribute("data-map-src");
    frame.title = box.getAttribute("data-map-title") || "";
    frame.width = box.getAttribute("data-map-width") || "600";
    frame.height = box.getAttribute("data-map-height") || "400";
    frame.loading = "eager";
    frame.style.maxWidth = "100%";
    frame.style.border = "0";
    frame.referrerPolicy = "no-referrer-when-downgrade";
    frame.allowFullscreen = true;
    box.replaceWith(frame);
    frame.focus();
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest(".map-open");
    if (!button) return;
    var box = button.closest(".map-facade");
    if (box && box.getAttribute("data-map-src")) openMap(box);
  });
})();
