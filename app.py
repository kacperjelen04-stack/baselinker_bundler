"""
Kreator Zestawów BaseLinker — backend aplikacji webowej.
=========================================================

Tworzy zestawy (bundle) w katalogu BaseLinker na podstawie pliku CSV
z definicjami zestawów i ich składników (SKU + ilości).

Uruchomienie:
    python app.py
a następnie otwórz w przeglądarce: http://localhost:5050
(port 5050, nie 5000 - na macOS 5000 bywa zajęty przez systemowy AirPlay Receiver)

Struktura:
    - BaseLinkerAPIError   -> wyjątek zgłaszany przy błędach API
    - BaseLinkerClient     -> cienki klient do connector.php (SUCCESS/ERROR, cache, throttling)
    - BundleImportJob      -> przetwarza wiersze CSV i tworzy zestawy, raportując postęp
    - reszta pliku         -> serwer Flask spinający to z frontendem (UI w przeglądarce)
"""

from __future__ import annotations

import csv
import io
import json
import queue
import threading
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

# ---------------------------------------------------------------------------
# Stałe konfiguracyjne
# ---------------------------------------------------------------------------

BASELINKER_API_URL = "https://api.baselinker.com/connector.php"

# BaseLinker pozwala na maksymalnie 100 zapytań/minutę (potwierdzone w dokumentacji
# api.baselinker.com). Odstęp 0.65 s między KAŻDYM zapytaniem (nie tylko między
# wierszami CSV, jak w pierwotnej wersji skryptu) daje bezpieczny margines.
MIN_REQUEST_INTERVAL = 0.65

# Liczba obsługiwanych par kolumn SKU_x / ILOSC_x w szablonie CSV.
COMPONENT_SLOTS = 5

TEMPLATE_COLUMNS = ["NAZWA", "SKU", "EAN"]
for _slot in range(1, COMPONENT_SLOTS + 1):
    TEMPLATE_COLUMNS += [f"SKU_{_slot}", f"ILOSC_{_slot}"]

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Warstwa komunikacji z API BaseLinkera
# ---------------------------------------------------------------------------

class BaseLinkerAPIError(Exception):
    """Zgłaszany, gdy BaseLinker API zwróci status inny niż SUCCESS,
    albo gdy samo zapytanie sieciowe się nie powiedzie."""

    def __init__(self, method: str, message: str):
        self.method = method
        self.message = message
        super().__init__(f"[{method}] {message}")


class BaseLinkerClient:
    """Klient do API BaseLinkera (connector.php) dla operacji na katalogu produktów.

    Dba o trzy rzeczy, których brakowało w oryginalnym skrypcie:
      1. Poprawny adres API (api.baselinker.com/connector.php, nie baselinker.com).
      2. Zgodny z dokumentacją kształt zapytań (m.in. is_bundle=True oraz
         nazwa/cechy produktu wysyłane w polu text_fields, a nie jako osobne
         pola najwyższego poziomu).
      3. Throttling + pamięć podręczną SKU->ID i ID->cechy, żeby nie przekroczyć
         limitu 100 zapytań/min przy zestawach z wieloma powtarzającymi się
         składnikami.
    """

    def __init__(self, token: str, inventory_id: int):
        if not token:
            raise ValueError("Brak tokenu API BaseLinker.")
        self.token = token
        self.inventory_id = inventory_id
        self._headers = {"X-BLToken": token}
        self._last_call = 0.0
        self._sku_cache: dict[str, str | None] = {}
        self._product_data_cache: dict[str, dict] = {}  # id -> {"features": {...}, "prices": {...}}

    # -- niskopoziomowe wywołanie API -------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = MIN_REQUEST_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, method: str, parameters: dict) -> dict:
        self._throttle()
        payload = {"method": method, "parameters": json.dumps(parameters)}
        try:
            response = requests.post(
                BASELINKER_API_URL, data=payload, headers=self._headers, timeout=30
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise BaseLinkerAPIError(method, f"błąd połączenia z API ({exc})") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise BaseLinkerAPIError(method, "niepoprawna odpowiedź API (nie JSON)") from exc

        if data.get("status") != "SUCCESS":
            raise BaseLinkerAPIError(method, data.get("error_message", "nieznany błąd API"))
        return data

    # -- operacje wykorzystywane przy budowaniu zestawów -------------------

    def test_connection(self) -> None:
        """Prosty sanity-check tokenu i inventory_id - rzuca wyjątek, jeśli coś jest nie tak."""
        self._request("getInventoryProductsList", {"inventory_id": self.inventory_id, "page": 1})

    def list_price_groups(self) -> list[dict]:
        """Zwraca listę wszystkich grup cenowych dostępnych na koncie BaseLinker
        (id, nazwa, waluta, czy domyślna) - przydatne, żeby znaleźć poprawne
        price_group_id zamiast zgadywać. Uwaga: to lista globalna dla całego
        konta, nie tylko dla jednego katalogu - część grup może nie być
        przypisana do katalogu, którego aktualnie używasz."""
        data = self._request("getInventoryPriceGroups", {})
        return data.get("price_groups") or []

    def find_product_id_by_sku(self, sku: str) -> str | None:
        """Zwraca ID produktu o dokładnie takim SKU (z pamięcią podręczną na czas sesji)."""
        sku_clean = sku.strip()
        if sku_clean in self._sku_cache:
            return self._sku_cache[sku_clean]

        data = self._request(
            "getInventoryProductsList",
            {"inventory_id": self.inventory_id, "filter_sku": sku_clean},
        )
        products = data.get("products") or {}

        # BaseLinker może w teorii zwrócić dopasowania przybliżone - bierzemy tylko
        # produkt, którego SKU zgadza się dokładnie, żeby do zestawu nie trafił
        # przypadkowo podobny produkt.
        product_id = next(
            (
                pid
                for pid, info in products.items()
                if str(info.get("sku", "")).strip().lower() == sku_clean.lower()
            ),
            None,
        )
        self._sku_cache[sku_clean] = product_id
        return product_id

    def _ensure_product_data(self, product_ids: list[str]) -> None:
        """Pobiera i cache'uje pełne dane (cechy + ceny) podanych produktów -
        jedno zapytanie na wszystkie brakujące ID naraz, z pamięcią na czas importu."""
        missing = [pid for pid in product_ids if pid not in self._product_data_cache]
        if not missing:
            return
        data = self._request(
            "getInventoryProductsData",
            {"inventory_id": self.inventory_id, "products": missing},
        )
        products = data.get("products") or {}
        for pid in missing:
            product = products.get(str(pid), {})
            features = (product.get("text_fields") or {}).get("features") or {}
            prices = product.get("prices") or {}
            self._product_data_cache[pid] = {"features": features, "prices": prices}

    def get_merged_features(self, product_ids: list[str]) -> dict[str, str]:
        """Pobiera i scala cechy (text_fields.features) podanych produktów w jeden
        płaski słownik nazwa->wartość. Przy kilku składnikach mających tę samą
        cechę wygrywa wartość składnika podanego później na liście."""
        self._ensure_product_data(product_ids)
        merged: dict[str, str] = {}
        for pid in product_ids:
            merged.update(self._product_data_cache.get(pid, {}).get("features", {}))
        return merged

    def calculate_bundle_price(self, bundle_products: dict[str, int], price_group_id: str) -> float:
        """Sugerowana cena zestawu = suma (cena składnika w danej grupie cenowej
        x jego ilość w zestawie). Składnik bez ceny w tej grupie liczy się jako 0
        (nie przerywa tworzenia zestawu, tylko obniża sumę)."""
        self._ensure_product_data(list(bundle_products.keys()))
        total = 0.0
        for pid, qty in bundle_products.items():
            prices = self._product_data_cache.get(pid, {}).get("prices", {})
            unit_price = float(prices.get(str(price_group_id), 0) or 0)
            total += unit_price * qty
        return round(total, 2)

    def create_bundle(
        self,
        *,
        name: str,
        sku: str,
        ean: str,
        bundle_products: dict[str, int],
        features: dict[str, str],
        price_group_id: str,
        price: float = 0.0,
    ) -> str:
        """Tworzy nowy produkt typu zestaw (is_bundle=True) w katalogu."""
        text_fields: dict = {"name": name}
        if features:
            text_fields["features"] = features

        payload: dict = {
            "inventory_id": self.inventory_id,
            "is_bundle": True,
            "sku": sku,
            "text_fields": text_fields,
            "prices": {str(price_group_id): price},
            "bundle_products": bundle_products,
        }
        if ean:
            payload["ean"] = ean

        data = self._request("addInventoryProduct", payload)
        return data.get("product_id")


# ---------------------------------------------------------------------------
# Orkiestracja: przetwarzanie wierszy CSV -> zestawy w BaseLinkerze
# ---------------------------------------------------------------------------

class BundleImportJob:
    """Przetwarza listę wierszy CSV i tworzy zestawy w BaseLinkerze, raportując
    postęp przez callback `on_event` (wykorzystywany do strumieniowania SSE
    do przeglądarki). Zaprojektowana do uruchomienia w osobnym wątku."""

    def __init__(self, client: BaseLinkerClient, price_group_id: str, on_event):
        self.client = client
        self.price_group_id = price_group_id
        self.on_event = on_event
        self.cancelled = False

    def _emit(self, level: str, message: str, *, phase: str = "row", **extra) -> None:
        self.on_event({"level": level, "phase": phase, "message": message, "ts": time.time(), **extra})

    def run(self, rows: list[dict]) -> None:
        created = skipped = errors = 0
        total = len(rows)
        self._emit("info", f"Rozpoczynam przetwarzanie {total} wierszy z pliku CSV.", phase="start", total=total)

        for idx, row in enumerate(rows, start=1):
            if self.cancelled:
                self._emit("warning", "Przetwarzanie przerwane przez użytkownika.", phase="cancelled")
                break

            name = str(row.get("NAZWA") or "").strip()
            sku = str(row.get("SKU") or "").strip()
            ean = str(row.get("EAN") or "").strip()

            if not name or not sku:
                skipped += 1
                self._emit("warning", f"Wiersz {idx}: pominięto - brak NAZWY lub SKU zestawu.", row=idx)
                continue

            self._emit("info", f"Wiersz {idx}/{total}: przetwarzam '{name}' ({sku}).", row=idx)

            bundle_products: dict[str, int] = {}
            component_ids: list[str] = []
            # row_outcome pozostaje None dopóki wiersz da się kontynuować.
            # "skipped" = problem z danymi (do poprawy w CSV), "error" = zawiodło samo API.
            row_outcome: str | None = None

            for slot in range(1, COMPONENT_SLOTS + 1):
                component_sku = str(row.get(f"SKU_{slot}") or "").strip()
                if not component_sku:
                    continue

                qty_raw = str(row.get(f"ILOSC_{slot}") or "").strip()
                try:
                    qty = int(qty_raw) if qty_raw else 1
                    if qty <= 0:
                        raise ValueError("ilość musi być dodatnia")
                except ValueError:
                    self._emit(
                        "warning",
                        f"Wiersz {idx}: pominięto - niepoprawna ilość '{qty_raw}' dla składnika {component_sku}.",
                        row=idx,
                    )
                    row_outcome = "skipped"
                    break

                try:
                    product_id = self.client.find_product_id_by_sku(component_sku)
                except BaseLinkerAPIError as exc:
                    self._emit(
                        "error",
                        f"Wiersz {idx}: błąd API przy szukaniu SKU '{component_sku}': {exc.message}",
                        row=idx,
                    )
                    row_outcome = "error"
                    break

                if not product_id:
                    self._emit(
                        "warning",
                        f"Wiersz {idx}: pominięto - brak produktu o SKU '{component_sku}' w katalogu.",
                        row=idx,
                    )
                    row_outcome = "skipped"
                    break

                bundle_products[product_id] = qty
                component_ids.append(product_id)

            if row_outcome == "skipped":
                skipped += 1
                continue
            if row_outcome == "error":
                errors += 1
                continue
            if not bundle_products:
                skipped += 1
                self._emit("warning", f"Wiersz {idx}: pominięto - brak zdefiniowanych składników.", row=idx)
                continue

            try:
                features = self.client.get_merged_features(component_ids)
            except BaseLinkerAPIError as exc:
                features = {}
                self._emit(
                    "warning",
                    f"Wiersz {idx}: nie udało się pobrać cech składników ({exc.message}) - "
                    "tworzę zestaw bez przeniesionych cech.",
                    row=idx,
                )

            try:
                price = self.client.calculate_bundle_price(bundle_products, self.price_group_id)
            except BaseLinkerAPIError as exc:
                price = 0.0
                self._emit(
                    "warning",
                    f"Wiersz {idx}: nie udało się pobrać cen składników ({exc.message}) - "
                    "cena zestawu zostanie ustawiona na 0.",
                    row=idx,
                )

            try:
                product_id = self.client.create_bundle(
                    name=name,
                    sku=sku,
                    ean=ean,
                    bundle_products=bundle_products,
                    features=features,
                    price_group_id=self.price_group_id,
                    price=price,
                )
            except BaseLinkerAPIError as exc:
                errors += 1
                self._emit("error", f"Wiersz {idx}: błąd tworzenia zestawu '{name}': {exc.message}", row=idx)
                continue

            created += 1
            self._emit(
                "success",
                f"Wiersz {idx}: utworzono zestaw '{name}' (ID produktu: {product_id}, "
                f"cena: {price:.2f}).",
                row=idx,
                product_id=product_id,
                price=price,
            )

        self._emit(
            "info",
            f"Zakończono. Utworzono: {created}, pominięto: {skipped}, błędów: {errors} (z {total} wierszy).",
            phase="done",
            created=created,
            skipped=skipped,
            errors=errors,
            total=total,
        )


# ---------------------------------------------------------------------------
# Serwer Flask - spina backend z interfejsem w przeglądarce
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB - wystarczająco dla CSV
app.json.ensure_ascii = False  # ładne polskie znaki w odpowiedziach JSON

# Prosta pamięć procesu na wgrane pliki i trwające zadania.
# Wystarczająca dla lokalnego, jednoosobowego narzędzia uruchamianego na własnym komputerze.
UPLOADS: dict[str, list[dict]] = {}
JOBS: dict[str, dict] = {}
STATE_LOCK = threading.Lock()


def parse_csv_text(text: str) -> list[dict]:
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text), delimiter=";", restval="")
    return [dict(row) for row in reader]


def decode_upload(raw: bytes) -> str:
    """Excel w polskiej wersji językowej lubi zapisywać CSV w cp1250 zamiast UTF-8."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1250", errors="replace")


@app.route("/")
def index():
    return render_template(
        "index.html",
        template_columns=TEMPLATE_COLUMNS,
        component_slots=COMPONENT_SLOTS,
    )


@app.route("/api/preview", methods=["POST"])
def api_preview():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Nie przesłano pliku."}), 400

    raw = file.read()
    text = decode_upload(raw)

    try:
        rows = parse_csv_text(text)
    except csv.Error as exc:
        return jsonify({"ok": False, "error": f"Nie udało się odczytać CSV: {exc}"}), 400

    columns = list(rows[0].keys()) if rows else []
    missing_required = [c for c in ("NAZWA", "SKU") if columns and c not in columns]
    if not columns:
        missing_required = ["NAZWA", "SKU"]

    upload_id = str(uuid.uuid4())
    with STATE_LOCK:
        UPLOADS[upload_id] = rows

    return jsonify(
        {
            "ok": True,
            "upload_id": upload_id,
            "row_count": len(rows),
            "columns": columns,
            "missing_required_columns": missing_required,
            "preview": rows[:5],
        }
    )


@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    payload = request.get_json(force=True, silent=True) or {}
    token = str(payload.get("token") or "").strip()
    inventory_id_raw = payload.get("inventory_id")

    if not token or not inventory_id_raw:
        return jsonify({"ok": False, "error": "Podaj token API oraz ID katalogu."}), 400
    try:
        inventory_id = int(inventory_id_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "ID katalogu musi być liczbą całkowitą."}), 400

    client = BaseLinkerClient(token=token, inventory_id=inventory_id)
    try:
        client.test_connection()
    except BaseLinkerAPIError as exc:
        return jsonify({"ok": False, "error": exc.message}), 400

    return jsonify({"ok": True})


@app.route("/api/price-groups", methods=["POST"])
def api_price_groups():
    payload = request.get_json(force=True, silent=True) or {}
    token = str(payload.get("token") or "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Podaj token API BaseLinker."}), 400

    # inventory_id nie jest tu wymagane przez samo API BaseLinkera (lista grup
    # jest globalna dla konta), ale klient wymaga go w konstruktorze - wartość
    # nie ma znaczenia dla tego zapytania.
    client = BaseLinkerClient(token=token, inventory_id=1)
    try:
        groups = client.list_price_groups()
    except BaseLinkerAPIError as exc:
        return jsonify({"ok": False, "error": exc.message}), 400

    return jsonify({"ok": True, "price_groups": groups})


@app.route("/api/start", methods=["POST"])
def api_start():
    payload = request.get_json(force=True, silent=True) or {}
    upload_id = payload.get("upload_id")
    token = str(payload.get("token") or "").strip()
    inventory_id_raw = payload.get("inventory_id")
    price_group_id = str(payload.get("price_group_id") or "1").strip()

    with STATE_LOCK:
        rows = UPLOADS.get(upload_id)

    if rows is None:
        return jsonify({"ok": False, "error": "Nie znaleziono wgranego pliku. Wgraj CSV ponownie."}), 400
    if not rows:
        return jsonify({"ok": False, "error": "Plik CSV nie zawiera żadnych wierszy z danymi."}), 400
    if not token:
        return jsonify({"ok": False, "error": "Podaj token API BaseLinker."}), 400
    try:
        inventory_id = int(inventory_id_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "ID katalogu musi być liczbą całkowitą."}), 400

    event_queue: queue.Queue = queue.Queue()

    def on_event(event: dict) -> None:
        event_queue.put(event)

    client = BaseLinkerClient(token=token, inventory_id=inventory_id)
    job = BundleImportJob(client=client, price_group_id=price_group_id, on_event=on_event)

    def worker() -> None:
        try:
            job.run(rows)
        except Exception as exc:  # zabezpieczenie przed nieoczekiwanym błędem w wątku
            event_queue.put(
                {
                    "level": "error",
                    "phase": "done",
                    "message": f"Nieoczekiwany błąd podczas przetwarzania: {exc}",
                    "ts": time.time(),
                    "created": 0,
                    "skipped": 0,
                    "errors": len(rows),
                    "total": len(rows),
                }
            )
        finally:
            event_queue.put(None)  # sygnał końca strumienia

    job_id = str(uuid.uuid4())
    with STATE_LOCK:
        JOBS[job_id] = {"queue": event_queue, "job": job}

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/stream/<job_id>")
def api_stream(job_id):
    with STATE_LOCK:
        entry = JOBS.get(job_id)
    if not entry:
        return jsonify({"ok": False, "error": "Nieznane zadanie."}), 404

    event_queue: queue.Queue = entry["queue"]

    def generate():
        try:
            while True:
                item = event_queue.get()
                if item is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            with STATE_LOCK:
                JOBS.pop(job_id, None)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/cancel/<job_id>", methods=["POST"])
def api_cancel(job_id):
    with STATE_LOCK:
        entry = JOBS.get(job_id)
    if not entry:
        return jsonify({"ok": False, "error": "Nieznane zadanie (mogło już się zakończyć)."}), 404
    entry["job"].cancelled = True
    return jsonify({"ok": True})


@app.route("/api/download-template")
def download_template():
    return send_from_directory(
        BASE_DIR, "przyklad_zestawy.csv", as_attachment=True, download_name="szablon_zestawy.csv"
    )


if __name__ == "__main__":
    print("=" * 64)
    print(" Kreator Zestawow BaseLinker - serwer uruchomiony")
    print(" Otworz w przegladarce: http://localhost:5050")
    print(" Zatrzymanie: Ctrl+C")
    print("=" * 64)
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
