# Konfiguracja połączeń z bazą

Ten dokument opisuje konfigurację źródeł danych używanych przez GAARD do
generowania i wykonywania zapytań SQL.

Źródła danych konfiguruje się w panelu administracyjnym:

```text
/admin -> Datasource connector
```

Konfiguracja jest zapisywana w bazie metadanych GAARD, w tabeli
`datasource_connectors`. Zbuforowany schemat źródła danych, ustawienia tabel i
widoków oraz opis schematu dla promptów są zapisywane w
`datasource_schema_caches`.

## Zalecany model uprawnień

Użytkownik bazy danych używany przez GAARD powinien być użytkownikiem
techniczno-odczytowym. Nie powinien mieć uprawnień do zmiany danych ani
struktury bazy.

Wymagane minimum:

- możliwość połączenia się z bazą,
- możliwość odczytu metadanych schematu,
- podgląd listy tabel,
- podgląd listy widoków,
- podgląd kolumn tabel i widoków,
- podgląd kluczy obcych tabel, jeśli baza je udostępnia,
- `SELECT` na tabelach i widokach, z których GAARD ma korzystać.

Nie nadawaj użytkownikowi GAARD:

- `INSERT`,
- `UPDATE`,
- `DELETE`,
- `CREATE`,
- `ALTER`,
- `DROP`,
- `TRUNCATE`,
- uprawnień administracyjnych,
- praw właściciela schematu lub bazy, jeśli nie są potrzebne.

GAARD waliduje zapytania jako odczytowe, ale uprawnienia w bazie powinny być
drugą warstwą bezpieczeństwa.

## Introspekcja schematu

Po zapisaniu datasource w panelu admina użyj przycisku `Schema introspection`.
GAARD odczytuje wtedy schemat przez SQLAlchemy i zapisuje go w metadanych.

Schemat obejmuje:

- tabele,
- widoki,
- kolumny,
- typy kolumn,
- informację o `primary key`, jeśli jest dostępna,
- informację o `nullable`, jeśli jest dostępna,
- klucze obce dla tabel, jeśli są dostępne.

Widoki są oznaczane w schemacie jako:

```json
{
  "name": "active_patients",
  "object_type": "view",
  "columns": []
}
```

Tabele są oznaczane jako:

```json
{
  "name": "patients",
  "object_type": "table",
  "columns": []
}
```

Jeśli widok nie pojawia się w schemacie, sprawdź najpierw, czy użytkownik bazy
ma prawo zobaczyć definicję widoku i wykonać z niego `SELECT`.

## MySQL

W panelu admina ustaw:

```text
Database type: mysql
SQL dialect: mysql
Database URL: mysql+pymysql://gaard_reader:password@db.example.com:3306/app_db
```

Akceptowane prefiksy URL:

- `mysql://`
- `mysql+pymysql://`

Przykładowy użytkownik tylko do odczytu:

```sql
CREATE USER 'gaard_reader'@'%' IDENTIFIED BY 'strong-password';

GRANT SELECT, SHOW VIEW
ON app_db.*
TO 'gaard_reader'@'%';

FLUSH PRIVILEGES;
```

`SELECT` pozwala wykonywać zapytania na tabelach i widokach. `SHOW VIEW` jest
potrzebne, żeby użytkownik mógł widzieć definicje widoków podczas introspekcji.

Jeśli chcesz ograniczyć dostęp tylko do wybranych obiektów:

```sql
GRANT SELECT ON app_db.orders TO 'gaard_reader'@'%';
GRANT SELECT, SHOW VIEW ON app_db.monthly_sales_view TO 'gaard_reader'@'%';
```

## Oracle Database

W panelu admina ustaw:

```text
Database type: oracle
SQL dialect: oracle
Database URL: oracle+oracledb://gaard_reader:password@db.example.com:1521?service_name=appdb
```

Akceptowane prefiksy URL:

- `oracle://`
- `oracle+oracledb://`
- `oracle+cx_oracle://`

Domyślny URL generowany z pól formularza używa sterownika `oracledb`.
Zainstaluj opcjonalne zależności `gaard-api[oracle]` albo odpowiedni sterownik
Oracle w środowisku uruchomieniowym.

Przykładowy użytkownik tylko do odczytu:

```sql
CREATE USER gaard_reader IDENTIFIED BY "strong-password";
GRANT CREATE SESSION TO gaard_reader;
GRANT SELECT ON app_schema.orders TO gaard_reader;
GRANT SELECT ON app_schema.monthly_sales_view TO gaard_reader;
```

## Microsoft SQL Server

W panelu admina ustaw:

```text
Database type: mssql
SQL dialect: tsql
Database URL: mssql+pyodbc://gaard_reader:password@db.example.com:1433/app_db?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
```

Akceptowane prefiksy URL:

- `mssql://`
- `mssql+pyodbc://`
- `mssql+pymssql://`

Domyślny URL generowany z pól formularza używa sterownika `pyodbc`.
Zainstaluj opcjonalne zależności `gaard-api[mssql]` i właściwy sterownik ODBC
SQL Server w systemie.

Przykładowy użytkownik tylko do odczytu:

```sql
CREATE LOGIN gaard_reader WITH PASSWORD = 'strong-password';
CREATE USER gaard_reader FOR LOGIN gaard_reader;
GRANT SELECT TO gaard_reader;
```

Jeśli chcesz ograniczyć dostęp tylko do wybranych schematów lub obiektów:

```sql
GRANT SELECT ON SCHEMA::reporting TO gaard_reader;
GRANT SELECT ON OBJECT::dbo.orders TO gaard_reader;
```

## PostgreSQL

W panelu admina ustaw:

```text
Database type: postgresql
SQL dialect: postgres
Database URL: postgresql+psycopg://gaard_reader:password@db.example.com:5432/app_db
```

Akceptowane prefiksy URL:

- `postgresql://`
- `postgresql+psycopg://`

Przykładowy użytkownik tylko do odczytu:

```sql
CREATE ROLE gaard_reader LOGIN PASSWORD 'strong-password';

GRANT CONNECT ON DATABASE app_db TO gaard_reader;
GRANT USAGE ON SCHEMA public TO gaard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO gaard_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO gaard_reader;
```

W PostgreSQL widoki korzystają z uprawnień `SELECT` podobnie jak tabele.
Użytkownik powinien mieć `USAGE` na schemacie oraz `SELECT` na widokach, które
mają być dostępne dla GAARD.

Jeśli używasz wielu schematów, nadaj uprawnienia osobno dla każdego z nich:

```sql
GRANT USAGE ON SCHEMA reporting TO gaard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO gaard_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA reporting
GRANT SELECT ON TABLES TO gaard_reader;
```

Jeśli nie chcesz dawać dostępu do całego schematu, nadaj `SELECT` tylko na
konkretne tabele i widoki:

```sql
GRANT SELECT ON TABLE public.orders TO gaard_reader;
GRANT SELECT ON TABLE reporting.monthly_sales_view TO gaard_reader;
```

## SQLite

W panelu admina ustaw:

```text
Database type: sqlite
SQL dialect: sqlite
Database URL: sqlite:///./examples/medical-poc/demo.db
```

Akceptowany prefiks URL:

- `sqlite://`

Przykłady:

```text
sqlite:///./examples/medical-poc/demo.db
sqlite:////absolute/path/to/demo.db
```

Dla SQLite nie konfiguruje się użytkowników i grantów w samej bazie. Dostęp jest
kontrolowany przez uprawnienia systemu plików. Proces API musi mieć prawo
odczytu pliku `.db`. Jeśli chcesz zachować zasadę minimalnych uprawnień, ustaw
plik bazy jako tylko do odczytu dla użytkownika uruchamiającego GAARD.

SQLite przechowuje definicje tabel i widoków w `sqlite_master`; introspekcja
widoków będzie działać, jeśli proces ma dostęp do pliku bazy.

## IBM Db2

W panelu admina ustaw:

```text
Database type: ibm_db2
SQL dialect: db2
Database URL: db2+ibm_db://gaard_reader:password@db.example.com:50000/app_db
```

Akceptowane prefiksy URL:

- `db2+ibm_db://`
- `ibm_db_sa://`

Domyślny URL generowany z pól formularza używa dialektu `ibm-db-sa`.
Zainstaluj opcjonalne zależności `gaard-api[ibm_db2]` w środowisku
uruchomieniowym. SQLGlot nie ma obecnie natywnego parsera `db2`, więc GAARD
używa dialektu `db2` w promptach, a walidację składni wykonuje parserem
ogólnym.

Przykładowy użytkownik tylko do odczytu:

```sql
CREATE USER gaard_reader USING PASSWORD 'strong-password';
GRANT CONNECT ON DATABASE TO USER gaard_reader;
GRANT SELECT ON TABLE app_schema.orders TO USER gaard_reader;
GRANT SELECT ON TABLE app_schema.monthly_sales_view TO USER gaard_reader;
```

## Teradata

W panelu admina ustaw:

```text
Database type: teradata
SQL dialect: teradata
Database URL: teradatasql://gaard_reader:password@db.example.com?dbs_port=1025&database=app_db
```

Akceptowane prefiksy URL:

- `teradatasql://`
- `teradata://`

Domyślny URL generowany z pól formularza używa sterownika
`teradatasqlalchemy`. Zainstaluj opcjonalne zależności `gaard-api[teradata]` w
środowisku uruchomieniowym.

Przykładowy użytkownik tylko do odczytu:

```sql
GRANT LOGON ON ALL TO gaard_reader;
GRANT SELECT ON app_db TO gaard_reader;
```

## Konfiguracja w panelu admina

1. Wejdź do `/admin`.
2. Otwórz `Datasource connector`.
3. Utwórz lub wybierz połączenie.
4. Ustaw `Connector key`, `Name`, `Database type`, `SQL dialect` i
   `Database URL`.
5. Kliknij `Test`, żeby sprawdzić połączenie.
6. Kliknij `Schema introspection`, żeby zapisać schemat tabel i widoków w
   metadanych.
7. Zaznacz `Active datasource`, jeśli to źródło ma być używane przez endpoint
   zapytań.
8. Zapisz konfigurację.

Aktywne może być jedno użytkowe źródło danych. Systemowy datasource
`metadata-db` jest zarządzany przez GAARD i nie powinien być aktywowany jako
źródło zapytań użytkownika.

## Logika biznesowa tabel i widoków

Logikę biznesową można opisywać w dwóch miejscach:

- w ustawieniach schematu przy datasource, gdy reguła dotyczy konkretnej tabeli
  lub widoku,
- w sekcji `Business logic suggestions`, gdy reguła jest ogólniejszą zasadą
  generowania SQL dla aktywnego datasource.

### Ustawienia schematu przy datasource

Po introspekcji schematu w panelu admina można doprecyzować znaczenie tabel i
widoków. Te ustawienia są zapisywane w metadanych i dołączane do promptu SQL.

Dla każdego obiektu można ustawić:

- czy obiekt jest używany w schemacie dla LLM,
- opis biznesowy,
- wskazówki dotyczące klucza głównego,
- wskazówki dotyczące kluczy obcych,
- reguły joinów.

Przykłady opisów:

```text
patients:
Tabela pacjentów. Jeden rekord odpowiada jednemu pacjentowi.
```

```text
active_patients:
Widok zawierający wyłącznie pacjentów aktywnych. Używaj go zamiast tabeli
patients, gdy pytanie dotyczy tylko aktywnych pacjentów.
```

Przykłady logiki joinów:

```text
appointments.patient_id łączy się z patients.id.
```

```text
monthly_sales_view jest widokiem zagregowanym po miesiącu i regionie. Nie łącz
go ponownie z orders, jeśli pytanie dotyczy miesięcznej sprzedaży.
```

### Business logic suggestions

Sekcja `Business logic suggestions` służy do przechowywania reguł, które mają
wpływać na przyszłe generowanie SQL dla aktywnego datasource.

Reguła może opisywać na przykład:

- kiedy używać konkretnego widoku zamiast tabel źródłowych,
- jak rozumieć biznesowe pojęcie, takie jak aktywny pacjent, przychód netto
  albo anulowana wizyta,
- jakich joinów unikać,
- które statusy lub wartości słownikowe odpowiadają pojęciom z języka
  naturalnego,
- kiedy dana tabela jest techniczna i nie powinna być używana w odpowiedziach.

Przykłady reguł:

```text
Gdy użytkownik pyta o aktywnych pacjentów, używaj widoku active_patients zamiast
tabeli patients z ręcznym filtrem status = 'active'.
```

```text
Widok monthly_sales_view jest już zagregowany. Nie łącz go z orders ani
order_items, jeśli pytanie dotyczy sprzedaży miesięcznej.
```

```text
Kolumna appointments.status = 'cancelled' oznacza wizytę anulowaną. Nie traktuj
jej jako odbytej wizyty.
```

Sugestie mogą powstawać automatycznie po błędach SQL lub analizie brakujących
informacji, ale przed użyciem powinny zostać przejrzane i włączone w panelu
admina.

## Widoki jako warstwa semantyczna

Widoki są dobrym miejscem do ukrywania złożonej logiki domenowej przed modelem.
Warto używać widoków, gdy:

- metryka wymaga kilku joinów,
- trzeba odfiltrować rekordy techniczne lub nieaktywne,
- nazwy kolumn w tabelach źródłowych są trudne dla użytkowników biznesowych,
- chcesz ograniczyć zakres danych widocznych dla GAARD,
- chcesz wystawić bezpieczny, odczytowy model raportowy.

Przykład:

```sql
CREATE VIEW active_patients AS
SELECT id, first_name, last_name, city, insurance_provider
FROM patients
WHERE status = 'active';
```

Po utworzeniu lub zmianie widoku uruchom ponownie `Schema introspection`, żeby
GAARD odświeżył cache schematu.

## Zmiana schematu źródła danych

Po zmianach w bazie, na przykład po dodaniu tabeli, widoku albo kolumny:

1. Wejdź do `/admin`.
2. Otwórz `Datasource connector`.
3. Wybierz datasource.
4. Kliknij `Schema introspection`.
5. Przejrzyj ustawienia tabel i widoków.
6. Zapisz ustawienia schematu.

Jeśli cache schematu jest używany przez zapytania, możesz też odświeżyć go z
sekcji `Schema cache`.
