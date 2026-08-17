# ADR-001: Modularny monolit z workerem i kolejką w SQLite

**Status:** zaakceptowany · **Data:** 2026-08-17

## Kontekst
Aplikacja działa na jednym komputerze użytkownika, offline, ale musi przetwarzać
paczki liczące tysiące stron przez wiele minut, z postępem, anulowaniem
i odzyskiwaniem po restarcie.

## Decyzja
Modularny monolit: proces API (FastAPI), osobny proces workera i SQLite w trybie
WAL jako trwała kolejka. Zadanie przejmowane jest warunkowym `UPDATE`, chronione
leasingiem i heartbeatem; wygasły lease wraca do kolejki lub kończy się `FAILED`.

## Uzasadnienie
Broker (Redis/RabbitMQ) i Celery wymagałyby dodatkowych usług na trzech systemach
operacyjnych, co jest nieproporcjonalne do jednej lokalnej instancji. WAL pozwala
API czytać, gdy worker pisze. Granice modułów pozwolą później wydzielić usługi.

## Konsekwencje
- (+) prosty start, brak zależności zewnętrznych, trwałość zadań po restarcie;
- (−) współbieżność ograniczona przez jeden plik bazy — akceptowalne dla jednego hosta;
- transakcje muszą być krótkie (checkpoint po stronie), nigdy na cały dokument.
