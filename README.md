# Mahindra AdrenoX for Home Assistant (unofficial)

A Home Assistant integration for cars with Mahindra's **AdrenoX Connect** service
(XUV700 and other AdrenoX models). It signs in the way the AdrenoX app does —
with your registered **mobile number and password** — and adds each car as a
device.

> **Unofficial.** Not made, endorsed or supported by Mahindra & Mahindra or
> Bosch. Mahindra publishes no public API for AdrenoX; this integration talks to
> the same private backend the app uses and may stop working at any time. Using
> it may go against AdrenoX's terms of use. Use it at your own risk.

## Status: ready to test

The AdrenoX API addresses, the login handshake and the status field names were
recovered by statically reverse-engineering the official app
(`com.mahindra.adrenox` 2.0.68) — see
[docs/reverse-engineering.md](docs/reverse-engineering.md). They have **not yet
been confirmed against live traffic**, so when you first run it, turn on debug
logging and expect to adjust a field name or two
([docs/capture-guide.md](docs/capture-guide.md) explains how).

**Working now (from the passive `vehicleStatus` poll):**

| Entity | Notes |
|---|---|
| Odometer | `odoMeter` |
| Fuel range | distance-to-empty (`dte`); ICE vehicles report range, not litres |
| Last reported | when the car last sent data |

**Next feature — ignition, lamps, doors, roof** (`ign`, `lampStatus`,
`doorLock`/`stsDoor`, `sunRoof`): these are **not** in the cheap status poll.
The app receives them over a live Server-Sent-Events channel
(`{service host}/location-services/webapi/sse/register/{vin}`). The field
mappings are already in [`parser.py`](custom_components/mahindra_adrenox/parser.py)
and the host is in [`endpoints.py`](custom_components/mahindra_adrenox/endpoints.py);
wiring that stream into the coordinator is the planned next step (see Roadmap).

The parser also already maps fuel level, tyre pressures, battery voltage,
per-door/window states, climate and GPS for when a capture confirms they are
available — entities appear only for values your car actually returns.

## How it talks to AdrenoX (and avoids account lockout)

Mahindra appears to enforce a **single active session per account** and pushes
back on anything that looks like a scraping proxy. This integration is built to
look like **one ordinary instance of the app**:

- **Same headers as the app** on every request: `Source: MMCApp`,
  `AppLogin: W601App`, a stable `AppUniqueId`, `User-Agent: Android`, and the raw
  JWT in `Authorization` (no `Bearer `).
- **One stable device identity.** A single `AppUniqueId` is generated when you
  add the integration and reused forever — never rotated.
- **It never signs in by itself.** After the first password sign-in the session
  is renewed with the refresh token at most once per request. If that fails,
  polling stops and Home Assistant shows a **Reconfigure** prompt; it only signs
  in again when you enter your password there. No repeated logins.
- **Gentle polling:** every **15 minutes** by default (10–120 in options),
  doubling up to 2 hours after failures, honouring `Retry-After` on 429, and
  spacing requests ≥2 s apart.
- Your password is used only to sign in and is **never stored**; only the tokens
  and your mobile number are kept in the config entry.

> ⚠️ **Running this and the phone app on the same account may log one of them
> out**, because the backend allows one active session and each client uses a
> different `AppUniqueId`. If your phone keeps getting signed out, use a
> **secondary AdrenoX account** that has been granted access to the vehicle
> (the app's share-access flow) and run Home Assistant on that.

### Remote commands

Lock/unlock and climate controls act on the real car, so they are **off by
default** and only appear when you enable **Remote commands** in options *and*
the exact command has been confirmed from a capture. None are confirmed yet, so
they stay hidden — this release is read-only.

## Install with HACS

1. In Home Assistant, open **HACS** → **⋮** → **Custom repositories**.
2. Add `https://github.com/rkarthikn/ha-mahindra-adrenox`, type **Integration**.
3. Search HACS for **Mahindra AdrenoX**, download it, restart Home Assistant.
4. **Settings → Devices & services → Add integration → Mahindra AdrenoX**.
5. Enter the mobile number and password registered in the AdrenoX app.
6. **First time only — verify the device:** AdrenoX treats Home Assistant as a
   new device and texts a one-time code to your number (error `AE1500`,
   "App and User Mapping Failed"). Enter it to link HA. Your phone's AdrenoX app
   may be signed out when you do this — that's the single-session behaviour; see
   the warning above, and consider a secondary account.

To install by hand, copy `custom_components/mahindra_adrenox` into your Home
Assistant `config/custom_components/` folder and restart.

## Validating / fixing field names

Because the field names come from static analysis, verify them once against your
car:

```yaml
logger:
  logs:
    custom_components.mahindra_adrenox: debug
```

Then **Settings → Devices & services → Mahindra AdrenoX → ⋮ → Download
diagnostics** gives the raw AdrenoX responses with tokens, phone number, VIN,
registration, names and coordinates removed. If a value is missing or wrong,
add the real JSON key to the candidate lists in
[`parser.py`](custom_components/mahindra_adrenox/parser.py) — the entities don't
need to change. See [docs/capture-guide.md](docs/capture-guide.md).

### Live channel (ignition, lamps, doors, sunroof)

These are **not** in the status poll. Turn on **Enable live channel** in the
integration's options to open the app's SSE stream and get them. It's
experimental (field names from static analysis) and richest while the car is
awake; raw events are logged at debug so mappings can be confirmed.

## Roadmap

- [x] Confirm `vehicleStatus` auth: the session must switch to the vehicle
      (`auth/switch/vehicle/{encryptedVehicleId}`) for a vehicle-scoped token.
- [x] Wire the live SSE channel for ignition, lamps, doors and sunroof (opt-in).
- [ ] Add a `device_tracker` from the live GPS feed (`gnssInfo`).
- [ ] Confirm and enable remote commands (lock/unlock, climate) from a capture.

## Development

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

`api.py`, `parser.py`, `const.py` and `endpoints.py` don't import Home Assistant.
Tests run against synthetic payloads in `tests/fixtures/`.

## License

MIT
