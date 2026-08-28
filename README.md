# Cinema City Odyssea watch

Malý automatický watcher aktuálně vypsaných projekcí filmu **Odyssea** v Cinema City Praha Flora, které současně splňují **IMAX** a **70 mm**.

Projekt nescrapuje HTML. Při každém spuštění provede nové HTTP požadavky na stejné veřejné JSON API Cinema City CZ, které používá web `cinemacity.cz`, a z odpovědí vytvoří čitelný snapshot.

## Aktuální stav

- [Aktuální snapshot (`data/latest.json`)](data/latest.json)
- [RAW `data/latest.json`](https://raw.githubusercontent.com/adamrabi1234/cinemacity-odyssea-watch/main/data/latest.json)
- [Historie změn (`data/history.json`)](data/history.json)
- [GitHub Actions](https://github.com/adamrabi1234/cinemacity-odyssea-watch/actions/workflows/watch.yml)

`checked_at` je čas dokončení posledního **úspěšného živého API dotazu** v časové zóně Europe/Prague. `checked_at_utc` je stejný okamžik v UTC. `latest.json` není historická cache: po každém úspěšném běhu je kompletně nahrazen stavem získaným ze živého API, včetně volatilních polí `sold_out` a `availability_ratio`.

Pokud API korektně vrátí žádné odpovídající projekce, jde o platný stav: `matching_showings_count` bude `0` a `latest_showing` bude `null`. Pokud některý požadavek selže, program skončí s nenulovým kódem a poslední správný `latest.json` nepřepíše.

## Ověřená API struktura

K 28. srpnu 2026 byly živými požadavky ověřeny tyto hodnoty:

- Cinema City Praha Flora: API cinema ID `1052`, název `Praha Flora, OC FLORA`
- Odyssea: film ID `7268s2r` (watcher ale film vybírá podle aktuálního názvu z API, ne pouze podle tohoto ID)
- 70 mm: atribut `70-mm`
- IMAX: `auditorium` = `IMAX VOLVO`, `auditoriumTinyName` = `IMAX`

Použité endpointy pod base URL `https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/10101`:

```text
/cinemas/with-event/until/{date}
/dates/in-cinema/{cinema_id}/until/{date}
/film-events/in-cinema/{cinema_id}/at-date/{date}
```

API vyžaduje parametr `until`. Ověřená hodnota `9999-12-31` vrací všechna data, která Cinema City právě nabízí, takže watcher nepoužívá umělý sedmi- nebo čtrnáctidenní limit. Samotné Cinema City zpravidla zveřejňuje program jen na omezenou dobu dopředu.

## Lokální spuštění

Vyžaduje Python 3.12 nebo novější.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python src/watch.py
```

Watcher zapisuje `data/latest.json` při každém úspěšném běhu. `data/history.json` mění jen tehdy, když přibude nebo zmizí event ID či se změní nejpozdější dostupná projekce. Historie se nikdy nepoužívá jako zdroj aktuálního stavu.

## GitHub Actions

Workflow `.github/workflows/watch.yml` běží přibližně každých 15 minut (`:07`, `:22`, `:37`, `:52`) a podporuje i ruční spuštění:

1. otevřít záložku **Actions**,
2. vybrat **Watch Cinema City showings**,
3. zvolit **Run workflow**.

Po úspěšném dotazu workflow commitne změněné JSON soubory zpět do větve. `concurrency` brání překryvu dvou běhů a `contents: write` dává vestavěnému `GITHUB_TOKEN` pouze oprávnění potřebné k zápisu obsahu.

## Změna sledovaného filmu, kina nebo formátu

Všechny běžně měněné hodnoty jsou v [`config.json`](config.json):

```json
{
  "film_name": "Odyssea",
  "cinema_name_contains": "Praha Flora",
  "cinema_id_fallback": "1052",
  "required_attributes": ["70-mm"],
  "require_imax": true
}
```

Kino se při každém běhu primárně znovu dohledává podle názvu; `cinema_id_fallback` umožňuje vrátit platný prázdný snapshot i v okamžiku, kdy endpoint `with-event` kino neuvádí, protože nemá žádný program. Film se mapuje z pole `films` každé denní odpovědi podle názvu a jeho ID se tedy může změnit.

Při změně konfigurace je vhodné archivovat nebo smazat dosavadní `data/history.json`, protože historie jinak bude obsahovat také eventy předchozího cíle.

## Omezení

- Jde o nezdokumentované veřejné API Cinema City; změna endpointů nebo JSON struktury způsobí bezpečné selhání běhu, dokud se watcher neupraví.
- GitHub plánované workflow může být při vysokém zatížení spuštěno se zpožděním.
- Přímé `booking_url` se bezpečně odvozuje z `presentationCode` jako `https://tickets.cinemacity.cz/order/{presentationCode}`; původní odkazy z API zůstávají ve snapshotu v `api_booking_links`.
