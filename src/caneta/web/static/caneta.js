/* CANETA front-end helpers: markdown editor, Ctrl+S, selection for prompt edits. */
window.caneta = (function () {
  let mde = null;

  function initEditor() {
    const area = document.getElementById("editor");
    if (!area || typeof EasyMDE === "undefined") return;
    mde = new EasyMDE({
      element: area,
      spellChecker: false,
      autoDownloadFontAwesome: false,
      status: ["words", "lines"],
      placeholder: area.dataset.placeholder || "",
      // text labels instead of FontAwesome icons: no external font download
      toolbar: [
        { name: "bold", action: EasyMDE.toggleBold, text: "B", title: "Negrito", className: "tb-bold" },
        { name: "italic", action: EasyMDE.toggleItalic, text: "I", title: "Itálico", className: "tb-italic" },
        { name: "heading", action: EasyMDE.toggleHeading3, text: "H", title: "Subtítulo" },
        "|",
        { name: "quote", action: EasyMDE.toggleBlockquote, text: "❝", title: "Citação em bloco" },
        { name: "ul", action: EasyMDE.toggleUnorderedList, text: "•", title: "Lista" },
        { name: "ol", action: EasyMDE.toggleOrderedList, text: "1.", title: "Lista numerada" },
        { name: "table", action: EasyMDE.drawTable, text: "▦", title: "Tabela" },
        "|",
        { name: "preview", action: EasyMDE.togglePreview, text: "👁", title: "Pré-visualizar", noDisable: true },
        { name: "side", action: EasyMDE.toggleSideBySide, text: "⇔", title: "Lado a lado", noDisable: true },
        { name: "full", action: EasyMDE.toggleFullScreen, text: "⛶", title: "Tela cheia", noDisable: true },
      ],
      forceSync: true,
    });
    mde.codemirror.on("change", () => { area.value = mde.value(); dirty(true); });
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        htmx.trigger("#editor-form", "submit");
      }
    });
    window.addEventListener("beforeunload", (e) => { if (isDirty) { e.preventDefault(); e.returnValue = ""; } });
    document.body.addEventListener("htmx:beforeRequest", (e) => {
      // any action that includes #editor sends the current text: it is saved server-side first
      area.value = mde.value();
    });
    document.body.addEventListener("htmx:afterRequest", (e) => {
      if (e.detail.successful && e.detail.elt && e.detail.elt.querySelector && e.detail.elt.querySelector("#editor")) dirty(false);
      if (e.detail.successful && e.detail.requestConfig && e.detail.requestConfig.parameters && "text" in e.detail.requestConfig.parameters) dirty(false);
    });
  }

  // one task per section: while one runs (#job .running), the action buttons stay disabled
  function syncBusy() {
    const busy = !!document.querySelector("#job .job.running");
    document.querySelectorAll(".tool button, .busy-lock").forEach((b) => { b.disabled = busy; });
    const note = document.getElementById("busy-note");
    if (note) note.hidden = !busy;
  }
  document.addEventListener("DOMContentLoaded", syncBusy);
  document.addEventListener("htmx:afterSettle", syncBusy);
  document.addEventListener("htmx:beforeRequest", (e) => {
    const form = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest(".tool");
    if (form) document.querySelectorAll(".tool button, .busy-lock").forEach((b) => { b.disabled = true; });
  });

  // a failed click must never be silent (a 404 once left every review button doing nothing)
  document.addEventListener("htmx:responseError", (e) => {
    const msg = (document.body.dataset.requestFailed || "Erro {status}").replace("{status}", e.detail.xhr.status);
    let toast = document.getElementById("toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "toast";
      toast.className = "notice err toast";
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toast.hidden = true; }, 8000);
  });

  let isDirty = false;
  function dirty(v) { isDirty = v; }

  function fillSelection(form) {
    const field = form.querySelector("[name=selection]");
    if (field && mde) field.value = mde.codemirror.getSelection() || "";
  }

  return { initEditor, fillSelection };
})();
