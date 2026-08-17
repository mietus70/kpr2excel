# Polityka danych testowych

## Zasada
W repozytorium **nie mogą** znaleźć się prawdziwe dane księgowe ani osobowe:
nazwy kontrahentów, adresy, numery dowodów, NIP-y, kwoty z realnych ksiąg.

## Fixtures syntetyczne
Korpus testowy jest generowany programowo przez
`backend/tests/fixtures/synthetic_kpir.py`. Generator odtwarza cechy strukturalne,
które faktycznie utrudniają parsowanie:

- dwupoziomowy nagłówek z numerami kolumn 1–16,
- linie wektorowe wokół komórek,
- powtarzalny nagłówek i stopka na każdej stronie,
- rozstrzelony literowo tytuł dokumentu,
- wieloliniowe opisy zdarzeń,
- puste pola oznaczone myślnikiem,
- polskie formatowanie kwot (`1 234,56`) ze spacją nierozdzielającą,
- warianty rotacji strony i dokumenty bez linii tabeli.

Nazwy w fixture'ach są jawnie fikcyjne (Alfa Testowa, Beta Fikcyjna). Generator
czyści metadane dokumentu (`set_metadata({})`).

## Gdyby użyto rzeczywistego PDF-a
Sama podmiana widocznego tekstu **nie wystarcza**. Trzeba dodatkowo:

1. usunąć metadane dokumentu (autor, tytuł, producent),
2. usunąć załączniki, warstwy opcjonalne i zakładki,
3. sprawdzić tekst niewidoczny (białe czcionki, obiekty poza obszarem strony),
4. sprawdzić miniatury i podglądy osadzone w pliku,
5. potwierdzić brak danych w strumieniach XMP.

Dopiero zanonimizowany plik, po przeglądzie, może trafić do `backend/tests/fixtures/`.

## Golden files
Oczekiwane wyniki (`expected_records`) są zatwierdzane ręcznie. **Nie wolno**
regenerować ich automatycznie bez obejrzenia różnicy: przesunięcie kwoty do
sąsiedniej kolumny wygląda w diffie niewinnie, a jest błędem krytycznym.
