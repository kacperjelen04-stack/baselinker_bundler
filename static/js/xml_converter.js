// Frontend narzędzia "XML -> CSV". Celowo osobny plik od app.js (bundler),
// żeby nie dotykać ani nie ryzykować istniejącej logiki importu zestawów.
(function () {
  "use strict";

  var PROFILES_KEY = "blk_xml_profiles";
  var PRESET_COLUMNS = ["NAZWA", "SKU", "EAN", "CENA", "STAN", "OPIS", "KATEGORIA", "PRODUCENT"];
  var SKIP_VALUE = "__skip__";
  var CUSTOM_VALUE = "__custom__";

  var state = {
    uploadId: null,
    fields: {}, // path -> {label, sample}
  };

  function el(id) {
    return document.getElementById(id);
  }

  // ---------- profile w localStorage ----------

  function loadProfiles() {
    try {
      var raw = localStorage.getItem(PROFILES_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveProfiles(profiles) {
    try {
      localStorage.setItem(PROFILES_KEY, JSON.stringify(profiles));
    } catch (e) {
      /* localStorage niedostępny - po prostu nie zapiszemy profilu */
    }
  }

  function refreshProfileSelect() {
    var select = el("xml-profile-select");
    var profiles = loadProfiles();
    var names = Object.keys(profiles).sort();
    select.innerHTML = '<option value="">— brak / nowy —</option>';
    names.forEach(function (name) {
      var opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
  }

  // ---------- status / komunikaty ----------

  function setStatus(elId, message, kind) {
    var box = el(elId);
    if (!message) {
      box.hidden = true;
      box.textContent = "";
      return;
    }
    box.hidden = false;
    box.textContent = message;
    box.className = "upload-status" + (kind ? " upload-status--" + kind : "");
  }

  // ---------- tabela mapowania ----------

  function buildColumnSelect(path, presetValue, customValue) {
    var wrap = document.createElement("div");

    var select = document.createElement("select");
    select.dataset.path = path;

    var skipOpt = document.createElement("option");
    skipOpt.value = SKIP_VALUE;
    skipOpt.textContent = "— pomiń —";
    select.appendChild(skipOpt);

    PRESET_COLUMNS.forEach(function (col) {
      var opt = document.createElement("option");
      opt.value = col;
      opt.textContent = col;
      select.appendChild(opt);
    });

    var customOpt = document.createElement("option");
    customOpt.value = CUSTOM_VALUE;
    customOpt.textContent = "— własna nazwa —";
    select.appendChild(customOpt);

    var customInput = document.createElement("input");
    customInput.type = "text";
    customInput.placeholder = "nazwa kolumny";
    customInput.style.marginTop = "6px";
    customInput.hidden = true;

    function syncCustomVisibility() {
      customInput.hidden = select.value !== CUSTOM_VALUE;
    }

    select.addEventListener("change", syncCustomVisibility);

    if (presetValue && PRESET_COLUMNS.indexOf(presetValue) !== -1) {
      select.value = presetValue;
    } else if (presetValue) {
      select.value = CUSTOM_VALUE;
      customInput.value = customValue || presetValue;
    } else {
      select.value = SKIP_VALUE;
    }
    syncCustomVisibility();

    wrap.appendChild(select);
    wrap.appendChild(customInput);
    return wrap;
  }

  function deriveColumnName(path) {
    if (path.indexOf("param::") === 0) return path.slice(7);
    return path
      .replace(/\[xml:lang=([a-zA-Z-]+)\]/g, "_$1")
      .replace(/[@/]/g, "_")
      .replace(/_+/g, "_")
      .replace(/^_|_$/g, "");
  }

  function selectAllFields() {
    var body = el("xml-mapping-body");
    var selects = body.querySelectorAll("select");
    selects.forEach(function (select) {
      var wrap = select.parentElement;
      var customInput = wrap.querySelector("input[type=text]");
      var name = deriveColumnName(select.dataset.path);
      if (PRESET_COLUMNS.indexOf(name.toUpperCase()) !== -1) {
        select.value = name.toUpperCase();
      } else {
        select.value = CUSTOM_VALUE;
        customInput.value = name;
        customInput.hidden = false;
      }
    });
  }

  function renderMappingTable(fields) {
    var table = el("xml-mapping-table");
    var body = el("xml-mapping-body");
    var summary = el("xml-mapping-summary");
    body.innerHTML = "";

    var paths = Object.keys(fields);
    if (!paths.length) {
      table.hidden = true;
      summary.textContent = "Nie wykryto żadnych pól w tym pliku.";
      return;
    }

    paths.forEach(function (path) {
      var info = fields[path];
      var tr = document.createElement("tr");

      var tdLabel = document.createElement("td");
      var labelStrong = document.createElement("div");
      labelStrong.textContent = info.label || path;
      var pathSmall = document.createElement("div");
      pathSmall.style.fontFamily = "var(--font-mono)";
      pathSmall.style.fontSize = "0.7rem";
      pathSmall.style.color = "var(--text-faint)";
      pathSmall.textContent = path;
      tdLabel.appendChild(labelStrong);
      tdLabel.appendChild(pathSmall);

      var tdSample = document.createElement("td");
      var sample = info.sample || "";
      tdSample.textContent = sample.length > 60 ? sample.slice(0, 60) + "…" : sample;
      tdSample.title = sample;

      var tdMap = document.createElement("td");
      tdMap.appendChild(buildColumnSelect(path, null, null));

      tr.appendChild(tdLabel);
      tr.appendChild(tdSample);
      tr.appendChild(tdMap);
      body.appendChild(tr);
    });

    table.hidden = false;
    summary.textContent = "Wykryto " + paths.length + " pól. Wybierz kolumnę CSV dla tych, które chcesz uwzględnić.";
    el("btn-xml-generate").disabled = false;
    el("btn-xml-select-all").hidden = false;
  }

  function applyMappingToTable(mapping) {
    // mapping: {path: columnName}. Ustawia istniejące wiersze tabeli zgodnie z zapisanym profilem.
    var body = el("xml-mapping-body");
    var selects = body.querySelectorAll("select");
    selects.forEach(function (select) {
      var path = select.dataset.path;
      var wrap = select.parentElement;
      var customInput = wrap.querySelector("input[type=text]");
      var value = mapping[path];
      if (!value) {
        select.value = SKIP_VALUE;
      } else if (PRESET_COLUMNS.indexOf(value) !== -1) {
        select.value = value;
      } else {
        select.value = CUSTOM_VALUE;
        customInput.value = value;
      }
      customInput.hidden = select.value !== CUSTOM_VALUE;
    });
  }

  function collectMapping() {
    var body = el("xml-mapping-body");
    var selects = body.querySelectorAll("select");
    var mapping = {};
    selects.forEach(function (select) {
      var path = select.dataset.path;
      var wrap = select.parentElement;
      var customInput = wrap.querySelector("input[type=text]");
      var value = select.value;
      if (value === SKIP_VALUE) return;
      if (value === CUSTOM_VALUE) {
        var name = customInput.value.trim();
        if (name) mapping[path] = name;
        return;
      }
      mapping[path] = value;
    });
    return mapping;
  }

  // ---------- akcje ----------

  function doInspect() {
    var f1 = el("xml-file-1").files[0];
    var f2 = el("xml-file-2").files[0];
    if (!f1) {
      setStatus("xml-inspect-status", "Wybierz przynajmniej jeden plik XML.", "error");
      return;
    }

    var formData = new FormData();
    formData.append("files", f1);
    if (f2) formData.append("files", f2);

    setStatus("xml-inspect-status", "Wczytuję i analizuję plik…", null);
    el("btn-xml-inspect").disabled = true;

    fetch("/api/xml/inspect", { method: "POST", body: formData })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        el("btn-xml-inspect").disabled = false;
        if (!result.ok || !result.data.ok) {
          setStatus("xml-inspect-status", "✕ " + (result.data.error || "Nie udało się przetworzyć pliku."), "error");
          return;
        }
        state.uploadId = result.data.upload_id;
        state.fields = result.data.fields;

        var msg = "✓ Wykryto " + result.data.total_records + " produktów.";
        if (result.data.matched_records !== null && result.data.matched_records !== undefined) {
          msg += " Sparowano między plikami: " + result.data.matched_records + ".";
          if (result.data.unmatched_records) {
            msg += " Bez pary (pominięte): " + result.data.unmatched_records + ".";
          }
        }
        setStatus("xml-inspect-status", msg, "success");
        renderMappingTable(state.fields);
      })
      .catch(function () {
        el("btn-xml-inspect").disabled = false;
        setStatus("xml-inspect-status", "✕ Nie udało się połączyć z lokalnym serwerem.", "error");
      });
  }

  function doSaveProfile() {
    var name = el("xml-profile-name").value.trim();
    if (!name) {
      window.alert("Podaj nazwę profilu.");
      return;
    }
    var mapping = collectMapping();
    if (!Object.keys(mapping).length) {
      window.alert("Zmapuj przynajmniej jedno pole przed zapisaniem profilu.");
      return;
    }
    var profiles = loadProfiles();
    profiles[name] = mapping;
    saveProfiles(profiles);
    refreshProfileSelect();
    el("xml-profile-select").value = name;
  }

  function doLoadProfile() {
    var name = el("xml-profile-select").value;
    if (!name) return;
    var profiles = loadProfiles();
    var mapping = profiles[name];
    if (!mapping) return;
    applyMappingToTable(mapping);
    el("xml-profile-name").value = name;
  }

  function doGenerate() {
    if (!state.uploadId) {
      el("xml-generate-hint").hidden = false;
      el("xml-generate-hint").textContent = "Najpierw wczytaj plik XML.";
      return;
    }
    var mapping = collectMapping();
    if (!Object.keys(mapping).length) {
      el("xml-generate-hint").hidden = false;
      el("xml-generate-hint").textContent = "Zmapuj przynajmniej jedno pole na kolumnę CSV.";
      return;
    }
    el("xml-generate-hint").hidden = true;
    el("btn-xml-generate").disabled = true;

    fetch("/api/xml/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_id: state.uploadId, mapping: mapping }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (data) {
            throw new Error(data.error || "Nie udało się wygenerować CSV.");
          });
        }
        return res.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "konwersja_xml_csv.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        el("btn-xml-generate").disabled = false;
      })
      .catch(function (err) {
        el("btn-xml-generate").disabled = false;
        el("xml-generate-hint").hidden = false;
        el("xml-generate-hint").textContent = "✕ " + err.message;
      });
  }

  // ---------- inicjalizacja ----------

  document.addEventListener("DOMContentLoaded", function () {
    if (!el("page-xmlconv")) return; // strona niedostępna - nic do zrobienia

    refreshProfileSelect();
    el("btn-xml-inspect").addEventListener("click", doInspect);
    el("btn-xml-save-profile").addEventListener("click", doSaveProfile);
    el("xml-profile-select").addEventListener("change", doLoadProfile);
    el("btn-xml-generate").addEventListener("click", doGenerate);
    el("btn-xml-select-all").addEventListener("click", selectAllFields);
  });
})();
