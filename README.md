# Cinema City Odyssea watch

Watcher sleduje veřejné Cinema City API a hledá představení filmu **Odyssea** v Praze. Aplikace je připravená jako samostatná Docker Compose služba pro Coolify: kontrolu provede hned po startu a potom ji opakuje podle adaptivního rozvrhu. GitHub slouží pouze jako zdroj kódu; GitHub Actions nejsou potřeba.

## Veřejné endpointy

Po přiřazení domény v Coolify jsou data dostupná na:

- `https://VAŠE_DOMÉNA/latest.json` – poslední úspěšný snapshot, ideální pro pravidelné čtení
- `https://VAŠE_DOMÉNA/history.json` – historie změn
- `https://VAŠE_DOMÉNA/healthz` – krátká kontrola stavu
- `https://VAŠE_DOMÉNA/` – přehled endpointů
- `https://VAŠE_DOMÉNA/discord/interactions` – podepsaný endpoint pro Discord příkazy `/newdates`, `/alldates` a `/checkdates`

Veřejné datové endpointy jsou pouze pro čtení. Discord endpoint přijímá jen podepsané interakce od Discordu. Odpovědi mají `Cache-Control: no-store`, aby čtenář nedostal starou kopii.

## Nasazení v Coolify

1. V Coolify zvolte **New Resource → Public Repository**.
2. Použijte repozitář `https://github.com/adamrabi1234/cinemacity-odyssea-watch` a větev `main`.
3. Jako build pack zvolte **Docker Compose** a soubor `compose.yaml`.
4. U služby `cinema-watch` v poli **Domains** použijte **Generate Domain**, případně zadejte vlastní subdoménu.
5. Protože aplikace uvnitř kontejneru poslouchá na portu 8000, musí mít hodnota v Coolify tvar `https://VAŠE_DOMÉNA:8000`. Číslo zde pouze říká proxy, na který interní port má požadavky směrovat; návštěvník ho ve výsledné URL nepoužívá.
6. Proměnnou `WATCH_FIXED_INTERVAL_SECONDS` nechte prázdnou pro doporučený adaptivní rozvrh. Kladné celé číslo vynutí pevný interval v sekundách.
7. Proveďte deploy a zvenku ověřte `https://VAŠE_DOMÉNA/healthz`.

Coolify Scheduled Tasks nejsou potřeba. Smyčka kontrol je součástí kontejneru a služba se po pádu nebo restartu serveru automaticky znovu spustí.

### Adaptivní rozvrh kontrol

Časy se vždy vyhodnocují v časové zóně `Europe/Prague`:

| Období | Interval |
| --- | ---: |
| Pondělí 18:00–24:00 | 10 minut |
| Úterý 06:00–22:00 | 10 minut |
| Středa–neděle 07:00–23:00 | 30 minut |
| Mimo uvedená publikační okna | 1 hodina |

Cinema City uvádí, že nový program na období čtvrtek–středa zveřejňuje v úterý; v nápovědě také zmiňuje pondělí večer nebo úterý ráno. Rozvrh proto kontroluje nejčastěji v tomto publikačním okně a mimo něj omezuje zbytečné API požadavky. Kontroly jsou zarovnané jednu minutu po časovém slotu: hodinové běží v `HH:01`, desetiminutové v `:01`, `:11`, `:21` atd. a půlhodinové v `:01` a `:31`. Cinema City tak dostane krátký čas na dokončení aktualizace programu.

## Discord oznámení

Volitelná proměnná `DISCORD_WEBHOOK_URL` zapne oznámení do jednoho Discord kanálu. URL ukládejte pouze jako secret v Coolify, nikdy do repozitáře. Watcher při prvním zapnutí vytvoří výchozí stav bez rozeslání všech existujících projekcí. Při dalších kontrolách pošle jednu zprávu se všemi novými termíny, jejich datem, časem, sálem a přímým odkazem na rezervaci.

1. V nastavení cílového Discord kanálu otevřete **Integrace → Webhooky**, vytvořte nový webhook a zkopírujte jeho URL.
2. V Coolify otevřete aplikaci a v **Environment Variables** přidejte `DISCORD_WEBHOOK_URL`. Hodnotu označte jako secret a neukládejte ji do Compose ani do Gitu.
3. Uložte nastavení a aplikaci znovu nasaďte. URL musí mířit na oficiální HTTPS webhook Discordu; jinou adresu aplikace bezpečnostně odmítne.

Stav oznámení je uložen v `data/notification-state.json` na trvalém volume. Aktualizuje se až po úspěšném odeslání; při dočasném selhání Discordu se stejné nové termíny zkusí poslat při následující kontrole.

Watcher také pošle jedno upozornění při selhání živé kontroly Cinema City a jedno potvrzení po jejím obnovení. Další chyby během stejného výpadku se zapisují pouze do logu, takže Discord nedostává opakované zprávy každých několik minut.

Pád celého kontejneru nemůže oznámit proces uvnitř něj. Pro tyto případy zapněte v globálním **Coolify → Notifications → Discord** události **Deployment Failure**, **Container Status Changes** a **Server Unreachable**. Tato externí kontrola doplňuje upozornění, která posílá samotný watcher.

### Discord příkazy `/newdates`, `/alldates` a `/checkdates`

Volitelná Discord aplikace umí spustit živou kontrolu mimo rozvrh. `/newdates` vypíše pouze termíny přidané od předchozí kontroly, případně oznámí, že žádné nové nejsou. `/alldates` vypíše všechny aktuální termíny s rezervačními odkazy. `/checkdates` bez volání Cinema City API okamžitě ukáže aktuální interval, čas poslední úspěšné kontroly a přesně naplánovanou další automatickou kontrolu. Všechny příkazy odpovídají soukromě pouze uživateli, který je vyvolal. Endpoint přijímá jen požadavky s platným Ed25519 podpisem Discordu, povoleným ID serveru a povoleným ID uživatele. Současně může běžet jen jedna živá kontrola a mezi ručními kontrolami je 60sekundová ochranná prodleva; stavový `/checkdates` ji nepoužívá.

V Coolify nastavte jako secrets nebo runtime proměnné:

- `DISCORD_APPLICATION_ID` – Application ID z Discord Developer Portal
- `DISCORD_PUBLIC_KEY` – Public Key z Discord Developer Portal
- `DISCORD_ALLOWED_GUILD_ID` – ID jediného povoleného Discord serveru
- `DISCORD_ALLOWED_USER_IDS` – jedno nebo více povolených uživatelských ID oddělených čárkou

V Discord Developer Portal nastavte **Interactions Endpoint URL** na:

```text
https://VAŠE_DOMÉNA/discord/interactions
```

Discord při uložení odešle podepsaný `PING`, který endpoint ověří a potvrdí. Pro jednorázovou registraci příkazu přidejte do Coolify jako secret `DISCORD_BOT_TOKEN` a uvnitř kontejneru spusťte:

```bash
python src/register_discord_command.py
```

`DISCORD_BOT_TOKEN` lze po registraci odstranit, nebo jej pro pohodlnější budoucí změny ponechat jako zamknutý secret dostupný pouze za běhu. Vypněte u něj Build Variable, protože aplikace token při sestavení nepotřebuje. Token ani ostatní tajné hodnoty nikdy nepatří do Git repozitáře.

Automatická upozornění nadále používají `DISCORD_WEBHOOK_URL`, zatímco slash příkazy odpovídají pod identitou Discord aplikace. Webhook lze pojmenovat a vizuálně nastavit stejně jako aplikaci, takže v kanálu působí jednotně.

### Proč nehrozí konflikt portů

Compose soubor nepublikuje port 8000 přímo na hostitelském serveru. Port je dostupný jen v interní Docker síti a Coolify proxy rozlišuje aplikace podle domén. Více služeb proto může současně používat interní port 8000 bez vzájemného konfliktu. Ve firewallu není potřeba otevírat žádný nový port; veřejný provoz jde přes standardní HTTPS port 443.

## Trvalá data

Docker volume `cinema-watch-data` je připojený do `/app/data`. Snapshoty tedy zůstanou zachované při novém deployi nebo výměně kontejneru. Soubory v Git repozitáři slouží jen jako počáteční data při prvním vytvoření volume.

Pokud kontrola Cinema City API dočasně selže, HTTP server dál poskytuje poslední platný snapshot a další kontrolu zkusí po uplynutí intervalu.

## Lokální spuštění

```bash
docker compose up --build -d
docker compose exec cinema-watch python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
docker compose logs -f cinema-watch
```

Zastavení bez smazání uložených dat:

```bash
docker compose down
```

## Konfigurace vyhledávání

Soubor `config.json` určuje kino, hledaný název filmu a požadovaný formát. Současné nastavení:

- Cinema City API pro ČR (`quickbook/10101`)
- kino, jehož název obsahuje `Praha Flora` (fallback ID `1052`)
- titul, jehož název obsahuje `Odyssea`
- povinný atribut `70-mm`
- pouze sál IMAX

## Kontrola bez Dockeru

```bash
python -m pip install -r requirements.txt
python src/watch.py
python -m unittest discover -s tests -v
```

## Přístup z ChatGPT

Pro scheduler použijte `https://VAŠE_DOMÉNA/latest.json`. HTTPS doména přes Coolify proxy je vhodnější než přímá IP adresa a nestandardní veřejný port.

Repozitář neposílá notifikace a nic necommituje automaticky zpět na GitHub.
