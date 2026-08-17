# ADR-003: Wersjonowane profile KPiR

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
Formularz KPiR zmienia się w czasie, a różne programy księgowe generują warianty
tego samego układu. Zaszycie kolumn w kodzie UI lub w algorytmie uniemożliwiłoby
obsługę wielu wersji i migrację danych historycznych.

## Decyzja
Profil to deklaratywny plik YAML (`kpir_pl_2018@1`) walidowany schematem przy
starcie. Zawiera klucze i typy kolumn, frazy nagłówka, progi dopasowania, strefy
nagłówka/stopki, markery pustki, reguły sum, progi pewności i formaty XLSX.
Profil **nie wykonuje kodu**. Każdy wynik ekstrakcji zapisuje `profile_id`,
`profile_version` i `extractor_version`.

## Konsekwencje
- (+) nowy wariant formularza to nowy plik, nie zmiana kodu;
- (+) UI pobiera kolumny i etykiety z API, nie zna prawa podatkowego;
- geometria kolumn w profilu jest wzorcem *relatywnym* — rzeczywiste granice
  wyznaczają linie wektorowe i kotwice nagłówka.
