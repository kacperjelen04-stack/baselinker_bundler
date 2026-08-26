(function () {
  "use strict";

  const TEMPLATE_COLUMNS = window.__TEMPLATE_COLUMNS__ || [];

  const SAMPLE_ROW = {
    NAZWA: "Zestaw Wiosenny",
    SKU: "SET-001",
    EAN: "5901234123457",
    SKU_1: "PROD-A",
    ILOSC_1: "1",
    SKU_2: "PROD-B",
    ILOSC_2: "2",
  };

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  const els = {
    token: document.getElementById("input-token"),
    inventory: document.getElementById("input-inventory"),
    priceGroup: document.getElementById("input-price-group"),
    remember: document.getElementById("input-remember"),
    toggleTokenVisibility: document.getElementById("toggle-token-visibility"),
    testBtn: document.getElementById("btn-test-connection"),
    testResult: document.getElementById("test-connection-result"),
    loadPriceGroupsBtn: document.getElementById("btn-load-price-groups"),
    priceGroupsResult: document.getElementById("price-groups-result"),
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("file-input"),
    uploadStatus: document.getElementById("upload-status"),
    previewHead: document.getElementById("preview-head"),
    previewBody: document.getElementById("preview-body"),
    previewSummary: document.getElementById("preview-summary"),
    startBtn: document.getElementById("btn-start"),
    cancelBtn: document.getElementById("btn-cancel"),
    startHint: document.getElementById("start-hint"),
    progressBar: document.getElementById("progress-bar"),
    progressFill: document.getElementById("progress-fill"),
    consoleBody: document.getElementById("console-body"),
    downloadLogBtn: document.getElementById("btn-download-log"),
    summaryCards: document.getElementById("summary-cards"),
    summaryCreated: document.getElementById("summary-created"),
    summarySkipped: document.getElementById("summary-skipped"),
    summaryErrors: document.getElementById("summary-errors"),
    themeButtons: document.querySelectorAll(".theme-btn"),
  };

  /* ---------------------------------------------------------------
     Motyw: ciemny / jasny / automatyczny (jak urządzenie)
     --------------------------------------------------------------- */

  const THEME_KEY = "blk_theme";
  const mediaDark = window.matchMedia("(prefers-color-scheme: dark)");

  function resolveTheme(pref) {
    return pref === "auto" ? (mediaDark.matches ? "dark" : "light") : pref;
  }

  function applyTheme(pref) {
    document.documentElement.setAttribute("data-theme", resolveTheme(pref));
    els.themeButtons.forEach((btn) => {
      const active = btn.dataset.themeChoice === pref;
      btn.setAttribute("aria-pressed", String(active));
      btn.classList.toggle("is-active", active);
    });
  }

  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY) || "light";
    applyTheme(saved);
    mediaDark.addEventListener("change", () => {
      if ((localStorage.getItem(THEME_KEY) || "light") === "auto") applyTheme("auto");
    });
    els.themeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        localStorage.setItem(THEME_KEY, btn.dataset.themeChoice);
        applyTheme(btn.dataset.themeChoice);
      });
    });
  }

  /* ---------------------------------------------------------------
     Ustawienia (opcjonalnie zapamiętywane w localStorage)
     --------------------------------------------------------------- */

  const SETTINGS_KEY = "blk_settings";

  function loadSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      els.token.value = data.token || "";
      els.inventory.value = data.inventory_id || "";
      els.priceGroup.value = data.price_group_id || "1";
      els.remember.checked = true;
    } catch (e) {
      /* ignorujemy uszkodzone dane w localStorage */
    }
  }

  function persistSettingsIfNeeded() {
    if (els.remember.checked) {
      localStorage.setItem(
        SETTINGS_KEY,
        JSON.stringify({
          token: els.token.value.trim(),
          inventory_id: els.inventory.value.trim(),
          price_group_id: els.priceGroup.value.trim(),
        })
      );
    } else {
      localStorage.removeItem(SETTINGS_KEY);
    }
  }

  [els.token, els.inventory, els.priceGroup].forEach((el) =>
    el.addEventListener("change", persistSettingsIfNeeded)
  );
  els.remember.addEventListener("change", persistSettingsIfNeeded);

  els.toggleTokenVisibility.addEventListener("click", () => {
    els.token.type = els.token.type === "password" ? "text" : "password";
  });

  els.testBtn.addEventListener("click", async () => {
    els.testResult.textContent = "Sprawdzam…";
    els.testResult.className = "test-result";
    try {
      const res = await fetch("/api/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: els.token.value.trim(),
          inventory_id: els.inventory.value.trim(),
        }),
      });
      const data = await res.json();
      if (data.ok) {
        els.testResult.textContent = "✓ Połączenie działa.";
        els.testResult.className = "test-result test-ok";
      } else {
        els.testResult.textContent = "✕ " + (data.error || "Błąd połączenia.");
        els.testResult.className = "test-result test-fail";
      }
    } catch (e) {
      els.testResult.textContent = "✕ Nie udało się połączyć z lokalnym serwerem.";
      els.testResult.className = "test-result test-fail";
    }
  });

  if (els.loadPriceGroupsBtn) {
    els.loadPriceGroupsBtn.addEventListener("click", async () => {
      const box = els.priceGroupsResult;
      box.hidden = false;
      box.className = "price-groups-result";
      box.textContent = "Sprawdzam…";
      try {
        const res = await fetch("/api/price-groups", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: els.token.value.trim() }),
        });
        const data = await res.json();
        if (!data.ok) {
          box.className = "price-groups-result price-groups-result--error";
          box.textContent = "✕ " + (data.error || "Nie udało się pobrać listy.");
          return;
        }
        const groups = data.price_groups || [];
        if (!groups.length) {
          box.textContent = "Brak zdefiniowanych grup cenowych na tym koncie.";
          return;
        }
        const list = document.createElement("ul");
        groups.forEach((g) => {
          const li = document.createElement("li");
          const label = document.createElement("span");
          label.textContent = g.name + (g.is_default ? " (domyślna)" : "") + " — " + (g.currency || "");
          const idSpan = document.createElement("span");
          idSpan.className = "pg-id";
          idSpan.textContent = "ID: " + g.price_group_id;
          li.appendChild(label);
          li.appendChild(idSpan);
          list.appendChild(li);
        });
        box.innerHTML = "";
        box.appendChild(list);
      } catch (e) {
        box.className = "price-groups-result price-groups-result--error";
        box.textContent = "✕ Nie udało się połączyć z lokalnym serwerem.";
      }
    });
  }

  /* ---------------------------------------------------------------
     Tabela podglądu (szablon + realne dane po wgraniu)
     --------------------------------------------------------------- */

  function renderTableHeader() {
    els.previewHead.innerHTML = "";
    TEMPLATE_COLUMNS.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      els.previewHead.appendChild(th);
    });
  }

  function renderPreviewRows(rows, opts) {
    const placeholder = !!(opts && opts.placeholder);
    els.previewBody.innerHTML = "";
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      if (placeholder) tr.classList.add("row-placeholder");
      const nameVal = (row.NAZWA || "").toString().trim();
      const skuVal = (row.SKU || "").toString().trim();
      if (!placeholder && (!nameVal || !skuVal)) tr.classList.add("row-warning");
      TEMPLATE_COLUMNS.forEach((col) => {
        const td = document.createElement("td");
        const val = row[col];
        td.textContent = val && String(val).trim() ? val : "–";
        tr.appendChild(td);
      });
      els.previewBody.appendChild(tr);
    });
  }

  renderTableHeader();
  renderPreviewRows([SAMPLE_ROW], { placeholder: true });

  /* ---------------------------------------------------------------
     Wgrywanie pliku CSV (drag&drop lub kliknięcie)
     --------------------------------------------------------------- */

  let currentUploadId = null;

  function setUploadStatus(text, kind) {
    els.uploadStatus.hidden = false;
    els.uploadStatus.textContent = text;
    els.uploadStatus.className = "upload-status" + (kind ? " upload-status--" + kind : "");
  }

  function setStartHint(text) {
    if (!text) {
      els.startHint.hidden = true;
      els.startHint.textContent = "";
      return;
    }
    els.startHint.hidden = false;
    els.startHint.textContent = text;
  }

  async function handleFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setUploadStatus("Wybierz plik z rozszerzeniem .csv.", "error");
      return;
    }

    setUploadStatus(`Wczytuję „${file.name}”…`, "loading");
    els.startBtn.disabled = true;
    setStartHint("");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch("/api/preview", { method: "POST", body: formData });
      const data = await res.json();

      if (!data.ok) {
        setUploadStatus("✕ " + (data.error || "Nie udało się odczytać pliku."), "error");
        return;
      }

      currentUploadId = data.upload_id;

      if (data.row_count === 0) {
        renderPreviewRows([SAMPLE_ROW], { placeholder: true });
        setUploadStatus(`Plik „${file.name}” nie zawiera żadnych wierszy z danymi.`, "error");
        els.startBtn.disabled = true;
        return;
      }

      renderPreviewRows(data.preview, { placeholder: false });
      els.previewSummary.textContent = `Plik zawiera ${data.row_count} wierszy — pokazuję pierwsze ${Math.min(5, data.row_count)}.`;

      if (data.missing_required_columns.length) {
        setUploadStatus(
          `⚠ Brakuje wymaganych kolumn: ${data.missing_required_columns.join(", ")}. Popraw plik i wgraj ponownie.`,
          "warning"
        );
        els.startBtn.disabled = true;
      } else {
        setUploadStatus(`✓ „${file.name}” wczytany poprawnie — ${data.row_count} wierszy gotowych do przetworzenia.`, "success");
        els.startBtn.disabled = false;
      }
    } catch (e) {
      setUploadStatus("✕ Błąd komunikacji z lokalnym serwerem.", "error");
    }
  }

  ["dragenter", "dragover"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("is-dragover");
    })
  );
  ["dragleave", "dragend", "drop"].forEach((evt) =>
    els.dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("is-dragover");
    })
  );
  els.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    handleFile(file);
  });
  els.dropzone.addEventListener("click", () => els.fileInput.click());
  els.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      els.fileInput.click();
    }
  });
  els.fileInput.addEventListener("change", () => handleFile(els.fileInput.files[0]));

  /* ---------------------------------------------------------------
     Uruchomienie zadania + log na żywo (Server-Sent Events)
     --------------------------------------------------------------- */

  let eventSource = null;
  let currentJobId = null;
  let logLines = [];
  let totalRows = 0;

  function appendLog(event) {
    const placeholder = els.consoleBody.querySelector(".console-placeholder");
    if (placeholder) placeholder.remove();

    const level = event.level || "info";
    const time = new Date((event.ts || Date.now() / 1000) * 1000);
    const stamp = [time.getHours(), time.getMinutes(), time.getSeconds()]
      .map((n) => String(n).padStart(2, "0"))
      .join(":");

    const line = document.createElement("div");
    line.className = "log-line log-line--" + level;

    const timeSpan = document.createElement("span");
    timeSpan.className = "log-time";
    timeSpan.textContent = stamp;

    const glyphSpan = document.createElement("span");
    glyphSpan.className = "log-glyph";
    glyphSpan.setAttribute("aria-hidden", "true");

    const msgSpan = document.createElement("span");
    msgSpan.className = "log-msg";
    msgSpan.textContent = event.message || "";

    line.append(timeSpan, glyphSpan, msgSpan);
    els.consoleBody.appendChild(line);
    els.consoleBody.scrollTop = els.consoleBody.scrollHeight;

    logLines.push(`[${stamp}] ${level.toUpperCase()}: ${event.message || ""}`);
  }

  function resetRunUI() {
    els.consoleBody.innerHTML =
      '<p class="console-placeholder">Tu pojawi się log na żywo podczas tworzenia zestawów.</p>';
    els.summaryCards.hidden = true;
    els.progressBar.hidden = false;
    els.progressFill.style.width = "0%";
    els.downloadLogBtn.hidden = true;
    logLines = [];
    totalRows = 0;
  }

  function finishRun() {
    els.startBtn.disabled = !currentUploadId;
    els.startBtn.hidden = false;
    els.cancelBtn.hidden = true;
    els.cancelBtn.disabled = false;
    if (logLines.length) els.downloadLogBtn.hidden = false;
    currentJobId = null;
  }

  function openStream(jobId) {
    eventSource = new EventSource(`/api/stream/${jobId}`);

    eventSource.onmessage = (e) => {
      const data = JSON.parse(e.data);
      appendLog(data);

      if (data.phase === "start" && data.total) {
        totalRows = data.total;
      }
      if (data.phase === "row" && totalRows) {
        const ratio = Math.min(1, (data.row || 0) / totalRows);
        els.progressFill.style.width = ratio * 100 + "%";
      }
      if (data.phase === "done") {
        els.summaryCreated.textContent = data.created ?? 0;
        els.summarySkipped.textContent = data.skipped ?? 0;
        els.summaryErrors.textContent = data.errors ?? 0;
        els.summaryCards.hidden = false;
        els.progressFill.style.width = "100%";
      }
    };

    eventSource.addEventListener("end", () => {
      eventSource.close();
      finishRun();
    });

    eventSource.onerror = () => {
      appendLog({ level: "error", message: "Połączenie z lokalnym serwerem zostało przerwane." });
      eventSource.close();
      finishRun();
    };
  }

  els.startBtn.addEventListener("click", async () => {
    if (!currentUploadId) return;

    const token = els.token.value.trim();
    const inventoryId = els.inventory.value.trim();
    if (!token || !inventoryId) {
      setStartHint("Uzupełnij token API oraz ID katalogu w Ustawieniach, zanim uruchomisz import.");
      return;
    }
    setStartHint("");

    resetRunUI();
    els.startBtn.disabled = true;
    els.startBtn.hidden = true;
    els.cancelBtn.hidden = false;

    try {
      const res = await fetch("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          upload_id: currentUploadId,
          token,
          inventory_id: inventoryId,
          price_group_id: els.priceGroup.value.trim() || "1",
        }),
      });
      const data = await res.json();

      if (!data.ok) {
        appendLog({ level: "error", message: data.error || "Nie udało się uruchomić zadania." });
        finishRun();
        return;
      }

      currentJobId = data.job_id;
      openStream(currentJobId);
    } catch (e) {
      appendLog({ level: "error", message: "Błąd komunikacji z lokalnym serwerem." });
      finishRun();
    }
  });

  els.cancelBtn.addEventListener("click", async () => {
    if (!currentJobId) return;
    els.cancelBtn.disabled = true;
    try {
      await fetch(`/api/cancel/${currentJobId}`, { method: "POST" });
    } catch (e) {
      /* zadanie i tak zakończy się po stronie serwera / strumień to zgłosi */
    }
  });

  els.downloadLogBtn.addEventListener("click", () => {
    const blob = new Blob([logLines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "log_tworzenia_zestawow.txt";
    a.click();
    URL.revokeObjectURL(url);
  });

  /* ---------------------------------------------------------------
     Start
     --------------------------------------------------------------- */

  loadSettings();
  initTheme();
})();
