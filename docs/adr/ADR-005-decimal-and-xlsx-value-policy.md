# ADR-005: Polityka wartości pieniężnych i typów w XLSX

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
`float` nie reprezentuje dokładnie kwot dziesiętnych (`0.1 + 0.2 != 0.3`), a błąd
w kolumnie kwotowej księgi jest błędem księgowym.

## Decyzja
- parsowanie i porównania: `Decimal` ze skalą maks. 2;
- baza: kanoniczny tekst dziesiętny (`"81.30"`);
- JSON: string, także granice filtrów;
- XLSX: liczba z formatem `0.00`, data jako typ Excel Date;
- pusto ≠ zero: pusta wartość to pusta komórka, nigdy `0.00`;
- format niejednoznaczny (np. `1.234`) tworzy problem `INVALID_MONEY`, zamiast
  zgadywać, czy kropka to separator tysięcy czy dziesiętny;
- teksty zaczynające się od `=`, `+`, `-`, `@` są neutralizowane przed zapisem.

## Konsekwencje
- (+) brak cichej utraty precyzji na całej trasie PDF → XLSX;
- (−) użytkownik musi rozstrzygnąć niejednoznaczne zapisy ręcznie — świadomy koszt.
