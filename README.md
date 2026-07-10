# SmartGridready Dynamic Tariff for Home Assistant

Home Assistant integration for **dynamic electricity tariffs following the
Swiss VSE / SmartGridready OpenAPI specification** (TariffDto schema, VSE
handbook "Dynamische Netznutzungstarife im Verteilnetz", valid from 2026).

Works out of the box with **BKW's dynamic feed-in remuneration
(Abnahmevergütung)** and is designed to work with any provider publishing a
conform API. Find providers on the SmartGridready tariff map:
https://smartgridready.ch/loesungen/dynamischetarife

## Features

- **GUI setup** (config flow) — no YAML needed. Add multiple tariff sources
  (e.g. BKW feed-in today, a dynamic consumption tariff later) as separate
  entries.
- One sensor per source: state = the price valid **right now**, updated
  exactly on every quarter hour (15-min slot resolution).
- Nordpool-style attributes for automations and charts:
  `today`, `tomorrow`, `tomorrow_valid`, `min_today`, `max_today`,
  `average_today`, `publication_timestamp`.
- Handles provider behavior where the evening publication returns **only
  the next day**: slots are merged into a cache so today's prices stay
  available.
- Unit taken from the API (`CHF_kWh` → `CHF/kWh` etc.).
- Optional VAT factor and fixed surcharge per source (changeable later via
  the entry's *Configure* dialog).

## Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → *Custom repositories* → add this repo
   (category: Integration).
2. Install **SmartGridready Dynamic Tariff**, restart Home Assistant.

### Manual
Copy `custom_components/sgr_dyntariff/` into your `config/custom_components/`
folder and restart.

## Setup

Settings → Devices & Services → **Add integration** →
*SmartGridready Dynamic Tariff*.

| Field | Notes |
|---|---|
| Name | Sensor/device name, e.g. "BKW Feed-in" |
| API URL | Default: BKW Abnahmevergütung (`.../Tariffs/energyreturn`) |
| Price component | `feed_in` for remuneration; `electricity`/`grid`/`integrated` for consumption tariffs |
| VAT factor | e.g. `1.081` for 8.1% Swiss VAT (default `1.0`) |
| Surcharge | fixed amount per kWh added after VAT (default `0`) |

The URL is validated during setup; a typo fails in the dialog.

## Known provider URLs

| Provider | Tariff | URL |
|---|---|---|
| BKW | Feed-in remuneration (dynamic Abnahmevergütung) | `https://api.bkw.ch/api/dyntariffs/v1/Tariffs/energyreturn` |

Contributions with additional conform provider URLs are very welcome —
please open a PR against this table.

## Example automation

```yaml
automation:
  - alias: "Discharge battery to grid at feed-in peak"
    trigger:
      - platform: state
        entity_id: sensor.bkw_feed_in
    condition:
      - condition: template
        value_template: >
          {{ states('sensor.bkw_feed_in') | float(0) >=
             state_attr('sensor.bkw_feed_in', 'max_today') | float(999) - 0.005 }}
    action:
      - service: huawei_solar.forcible_discharge_soc
        data:
          device_id: YOUR_BATTERY_DEVICE
          power: 2500
          target_soc: 40
```

## Notes / limitations

- The slot cache is persisted to disk after every successful fetch and
  restored on startup, so a restart between the evening publication
  (~18:00) and midnight still has the remainder of *today* available even
  though the API itself no longer serves it at that point.
- Version 2 of the SmartGridready specification (valid for 2027) was
  published in June 2026; this integration targets V1 and will be updated.

## License

MIT
