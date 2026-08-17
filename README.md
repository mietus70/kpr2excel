# kpr2excel — lokalny konwerter KPiR (PDF → XLSX)

Aplikacja webowa działająca **w całości lokalnie i offline**, która wydobywa zapisy
z tekstowych PDF-ów podatkowej księgi przychodów i rozchodów, pozwala je
skontrolować obok oryginalnej strony dokumentu i wyeksportować do znormalizowanego
pliku XLSX.

Dokumenty projektowe: [`AGENTS.md`](./AGENTS.md) i [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Najważniejsze cechy

- **Bez internetu.** Żaden komponent nie wykonuje żądań na zewnątrz, nie pobiera
  modeli i nie wysyła telemetrii. Backend nasłuchuje domyślnie na `127.0.0.1`.
- **Kwoty jako `Decimal`.** Nigdzie nie używamy `float`; w bazie kwoty są
  kanonicznym tekstem dziesiętnym, w JSON stringami, a w XLSX liczbami z formatem `0.00`.
- **Pełna śledzalność.** Każda wartość zna swój dokument, stronę, prostokąt
  źródłowy, tekst surowy, pewność i historię ręcznych zmian.
- **Oryginał jest niezmienny.** Korekty żyją w osobnej warstwie i można je cofnąć.
- **Bez cichego poprawiania danych.** Rozbieżność sumy tworzy problem do kontroli,
  a nie podmianę wartości źródłowej.
- **Konfigurowalny eksport.** Wybór i kolejność kolumn, ręczny wybór/wykluczenie
  wierszy, filtr kwotowy „od/do” oraz podgląd liczby wynikowych wierszy.

## Wymagania

| Składnik | Wersja |
|---|---|
| Python | 3.11 lub nowszy |
| Node.js | 18 lub nowszy |

## Instalacja (jednorazowo, wymaga internetu)

Pobranie zależności to osobny etap konfiguracji, nie działanie aplikacji.
Sama aplikacja po instalacji nie łączy się z siecią.

Najprościej skryptem, który tworzy środowisko wirtualne i instaluje
FastAPI, Uvicorn, PyMuPDF, OpenPyXL, Pydantic, PyYAML i python-multipart:

```bash
python scripts/install_deps.py --create-venv --dev

# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

cd frontend && npm install && cd ..
```

Skrypt czyta listę pakietów z `pyproject.toml`, pokazuje plan i pyta
o potwierdzenie, a na końcu weryfikuje instalację realnym importem.

| Polecenie | Działanie |
|---|---|
| `python scripts/install_deps.py --check --dev` | tylko sprawdź stan, nic nie instaluj |
| `python scripts/install_deps.py --create-venv --dev` | utwórz `.venv` i zainstaluj tam |
| `python scripts/install_deps.py --yes` | zainstaluj bez pytania (aktywne środowisko) |
| `python scripts/install_deps.py --how-to-activate` | przypomnij, jak wejść do `.venv` |

Alternatywnie klasycznie: `pip install -e ".[dev]"`.

### Wejście do środowiska wirtualnego

```bash
cd kpr2excel
source .venv/bin/activate       # Linux/macOS (bash, zsh)
.venv\Scripts\activate          # Windows (cmd.exe)
.venv\Scripts\Activate.ps1      # Windows (PowerShell)
```

Po aktywacji w wierszu poleceń pojawia się prefiks `(.venv)`. Sprawdzenie
interpretera: `which python` (Windows: `where python`) i `python -V`.
Wyjście: `deactivate`.

Aktywacja nie jest konieczna — interpreter można wołać wprost, co nie zmienia
stanu powłoki:

```bash
.venv/bin/python scripts/dev.py
.venv/bin/python -m pytest backend/tests -q
```

Gdy PowerShell zablokuje skrypt aktywacyjny komunikatem o polityce wykonywania,
wystarczy jednorazowo w bieżącej sesji:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Pełną instrukcję wypisze też `python scripts/install_deps.py --how-to-activate`.

### Instalacja offline

Na maszynie z internetem przygotuj paczkę kół, a następnie przenieś ją
na maszynę docelową (`AGENTS.md` §11):

```bash
python scripts/install_deps.py --build-wheelhouse ./wheels          # z internetem
python scripts/install_deps.py --wheelhouse ./wheels --offline --yes # bez internetu
```

## Uruchomienie

```bash
python scripts/dev.py
```

Skrypt sprawdza wersje i zależności, uruchamia migracje, startuje API, workera
i Vite, czeka na gotowość i otwiera przeglądarkę. Zatrzymanie: `Ctrl+C`.

Przydatne przełączniki:

```bash
python scripts/dev.py --no-browser        # nie otwieraj przeglądarki
python scripts/dev.py --api-port 9000     # inny port API
python scripts/dev.py --no-worker         # tylko API i UI
```

## Konfiguracja

Ustawienia pochodzą ze zmiennych środowiskowych (opcjonalnie z pliku `.env`):

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `KPIR_DATA_DIR` | katalog danych użytkownika | miejsce na PDF-y, bazę i eksporty |
| `KPIR_API_PORT` | `8756` | port API |
| `KPIR_FRONTEND_PORT` | `5173` | port interfejsu |
| `KPIR_MAX_UPLOAD_MB` | `200` | limit rozmiaru pliku |
| `KPIR_MAX_PAGES_PER_DOCUMENT` | `5000` | limit stron dokumentu |
| `KPIR_WORKER_CONCURRENCY` | `1` | liczba równoległych dokumentów |
| `KPIR_LOG_LEVEL` | `INFO` | poziom logowania |

Dane **nie trafiają do repozytorium** — domyślnie lądują w katalogu danych systemu
(np. `~/.local/share/kpir-converter`, `%LOCALAPPDATA%\kpir-converter`).

## Jak się tego używa

1. **Import** — przeciągnij jeden lub wiele PDF-ów. Import tylko zapisuje pliki
   i tworzy zadania; ekstrakcję wykonuje worker, więc można zamknąć kartę.
2. **Kontrola** — po lewej strona PDF z podświetlonym źródłem aktywnej komórki,
   po prawej tabela. Kliknięcie komórki pokazuje jej źródło, `Enter` rozpoczyna
   edycję, `Esc` anuluje. Oznaczenia: `M` — wartość poprawiona ręcznie,
   `!` — do sprawdzenia, `!!` — pewność krytyczna, *(puste)* — brak wartości.
3. **Eksport** — konfigurator w czterech krokach: zakres, kolumny (wybór
   i kolejność), wiersze i filtry, podsumowanie. Podgląd pokazuje liczbę wierszy,
   które trafią do pliku, oraz ile odpadło i z jakiego powodu.

### Semantyka filtra kwotowego

- granice `od`/`do` są **włączne** (`value >= min`, `value <= max`);
- można podać tylko jedną granicę;
- filtry działają na **wartościach efektywnych**, czyli uwzględniają korekty;
- wiersz z pustą lub niepoprawną kwotą **nie spełnia** aktywnego filtra, a podgląd
  pokazuje liczbę takich odrzuceń osobno;
- kolumna filtrowana **nie musi** być eksportowana — można filtrować po
  „Wydatki razem”, a pominąć tę kolumnę w pliku;
- `min > max` blokuje eksport z czytelnym błędem.

### Polityki eksportu

| Polityka | Zachowanie |
|---|---|
| `strict` | blokuje eksport, gdy wybrane wiersze mają otwarte problemy krytyczne |
| `reviewed` | pozwala eksportować po świadomym potwierdzeniu problemów |
| `draft` | eksport roboczy; dodaje arkusz „Metadane” z wersjami i konfiguracją |

## Testy

```bash
python scripts/test.py          # pełna bramka jakości
python -m pytest backend/tests -q
```

Zestawy: `unit` (kwoty, daty, geometria, reguły eksportu), `golden` (oczekiwane
komórki i prostokąty na reprezentatywnych stronach), `integration`
(PDF → baza → skonfigurowany XLSX) oraz `contract` (kształt API, CSRF, Host,
polityki eksportu). Testy są deterministyczne i nie korzystają z sieci.

Golden fixtures są generowane syntetycznie i **nie zawierają danych osobowych**.
Nie aktualizuj ich automatycznie bez obejrzenia różnicy — regresja jakości
ekstrakcji jest błędem nawet wtedy, gdy testy techniczne przechodzą.

## Diagnostyka: dlaczego eksport jest zablokowany

Gdy podsumowanie eksportu pokazuje problemy krytyczne, przyczynę wskaże:

```bash
python scripts/diagnose.py                     # wszystkie dokumenty
python scripts/diagnose.py --document <uuid>   # jeden dokument
python scripts/diagnose.py --show-values       # dołącz wartości komórek
```

Skrypt grupuje problemy według kodu, pokazuje najczęściej dotknięte kolumny
i wypisuje przykładowe wiersze z tekstem surowym oraz wartością sparsowaną.

Gdy problemy dotyczą tylko części stron, rozkład per strona pokaże:

```bash
python scripts/diagnose_pages.py              # wszystkie dokumenty
python scripts/diagnose_pages.py --doc 1812   # fragment nazwy pliku
```

Raport zestawia liczbę problemów i rzeczywistą geometrię kolumn strona po
stronie. Stała liczba problemów niezależna od rozmiaru dokumentu oznacza defekt
lokalny (zwykle strony 1), rosnąca proporcjonalnie — defekt systemowy.
Jeśli wartości trafiły do niewłaściwych kolumn (np. nazwa kontrahenta w kolumnie
daty), oznacza to, że profil nie pasuje do wariantu formularza — patrz
„Ograniczenia MVP".

### Eksport mimo kilku wadliwych wierszy

Polityka `strict` blokuje **cały** eksport, gdy choć jeden wybrany wiersz ma
otwarty problem krytyczny — także wtedy, gdy jest to 1 wiersz na 400. Nie trzeba
przez to rezygnować z reszty danych; są trzy wyjścia, wszystkie niedestrukcyjne:

1. **Odznacz wadliwe wiersze** w konfiguratorze eksportu. Filtry i ręczny wybór
   działają przed sprawdzeniem polityki, więc `strict` przestaje blokować, a
   podgląd pokaże `blockingIssueCount: 0`.
2. **Popraw wartość** w trybie pełnej kontroli. Korekta jest osobną warstwą —
   oryginalny PDF pozostaje nietknięty.
3. **Zmień politykę na `reviewed`**, gdy problemy są przejrzane i świadomie
   zaakceptowane. Eksport obejmie wszystkie wiersze.

Liczbę blokujących wierszy pokazuje `blockingIssueCount` w podglądzie eksportu,
a `scripts/diagnose.py` wskaże, których kolumn dotyczą.

### Nowy wariant formularza

Jeśli diagnostyka pokaże, że wartości trafiają do złych kolumn, dokument ma
prawdopodobnie inny układ niż profil. Geometrię sprawdzisz bez ujawniania danych:

```bash
python scripts/inspect_layout.py --doc 1812             # dokument wczytany do aplikacji
python scripts/inspect_layout.py twoj-plik.pdf          # tryb bezpieczny
python scripts/inspect_layout.py twoj-plik.pdf --show-text   # z treścią
python scripts/inspect_layout.py twoj-plik.pdf --suggest-profile > profiles/moj.yaml
python scripts/inspect_layout.py twoj-plik.pdf --compare-layout  # układ kolumn strona po stronie
python scripts/inspect_layout.py --doc 1812 --ruler-debug        # czemu odrzucono wiersz numeracji
```

Tryb domyślny wypisuje wyłącznie współrzędne, liczbę znaków i klasę tekstu
(`int`, `data`, `kwota`, `tekst`), więc wynik można bezpiecznie załączyć
w zgłoszeniu błędu. Kluczowa jest linia „→ sugeruje N kolumn".

`--ruler-debug` wyjaśnia wybór detektora kolumn: ile komórek zwrócił wiersz
numeracji, których numerów zabrakło i jakimi znakami jest narysowany. Wydruki
używają różnych wypełniaczy — `|_1_|__2__|` albo `|--1--|---2---|` — a tego
samego formularza mogą dotyczyć oba warianty.

## Usuwanie danych

```bash
python scripts/clean_local_data.py
```

Usunięcie dokumentu w UI kasuje oryginał, wyrenderowane strony, dane pośrednie,
korekty i powiązane eksporty.

## Bezpieczeństwo

- weryfikacja sygnatury `%PDF-`, limity rozmiaru i liczby stron;
- pliki zapisywane pod wewnętrznym UUID, nazwa użytkownika tylko jako metadana;
- ścisła lista dozwolonych `Host`/`Origin`, token CSRF dla operacji zmieniających dane;
- CSP bez połączeń zewnętrznych (`default-src 'self'; connect-src 'self'`);
- ochrona przed formułami w XLSX (`=`, `+`, `-`, `@`);
- logi zawierają kody i identyfikatory, nigdy treści księgowej ani pełnych ścieżek.

## Struktura projektu

```text
backend/src/kpir_converter/
├── domain/           # encje, typy wartości, profile, walidacja, logika eksportu
├── application/      # pipeline ekstrakcji i przypadki użycia
├── infrastructure/   # SQLite, PyMuPDF, pliki, OpenPyXL
├── api/              # FastAPI: DTO, routery, bezpieczeństwo
└── worker/           # kolejka, leasing zadań, postęp, anulowanie
frontend/src/         # React + TypeScript (import, kontrola, eksport)
profiles/             # wersjonowane profile układu KPiR (YAML)
docs/adr/             # decyzje architektoniczne
```

## Ograniczenia MVP

- brak OCR — PDF bez warstwy tekstowej otrzymuje problem `OCR_REQUIRED`;
- jeden profil układu (`kpir_pl_2018@1`); geometrię kolumn należy zweryfikować
  na rzeczywistych, zanonimizowanych dokumentach z docelowego programu księgowego;
- jedna lokalna instancja i jeden użytkownik;
- brak instalatora desktopowego.
