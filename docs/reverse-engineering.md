# Where these endpoints and fields come from

Everything in [`endpoints.py`](../custom_components/mahindra_adrenox/endpoints.py)
and the field maps in [`parser.py`](../custom_components/mahindra_adrenox/parser.py)
was recovered by **statically** reverse-engineering the official **AdrenoX
Connect** Android app (`com.mahindra.adrenox`, version 2.0.68, `w601_production`
release) with a JADX decompile. No live traffic was captured and nothing was run
on a device, so field names are *best-known* and may need small corrections
against a real account (see [capture-guide.md](capture-guide.md)).

## Backend hosts (res/values/strings.xml)

| Purpose | Value |
|---|---|
| Main REST API (`base_url`) | `https://interface.mahindramobilitysolution.com/V1/` |
| Location/live host (`base_url_location_services`) | `https://service.mahindramobilitysolution.com` |
| WebSocket (`base_url_ws`) | `wss://service.mahindramobilitysolution.com` |

## Login (verified)

- `GET auth/login` with credentials as **request headers**
  (`ApiClient.logInApi` → `MahindraApi.signin` / `signInOnClearSession`):
  - `X-Auth-Username` = registered mobile number (`LoginSignActivity` passes the
    number field straight through; no hashing/encryption)
  - `X-Auth-Password` = password
  - `AppLogin` = `Util.getAppName()` → **`W601App`**
  - `AppUniqueId` = `Util.fetchAndroidID()` (a stable per-install id)
  - fixed headers `Content-Type: application/json`, `Source: MMCApp`,
    `User-Agent: Android`
- The JWT and refresh token are returned in **response headers**
  `jwttoken` / `refreshToken` (`TokenVerifier`, `Util.saveToken`), *not* the body.
- `GET auth/refreshToken` with the refresh token in the `refreshToken` header
  renews both (`TokenVerifier.TokenAuthenticator`, fired on HTTP 401).
- Every authorized call sends the **raw JWT** in `Authorization` (no `Bearer `) —
  369 endpoints take `@Header("Authorization")`.
- **No OTP in normal sign-in** once a device is registered. But a **new/unknown
  `AppUniqueId`** gets rejected with `{"Code":"AE1500","message":"App and User
  Mapping Failed"}` plus a temporary token in the `x-amzn-remapped-authorization`
  response header (`Constants.APP_UNIQUE_ID_VALIDATION_ERROR_CODE = "AE1500"`,
  `ApiClient.logInApi`, `Util.saveTempJwtTokenInPref`). The app then maps the
  device with a one-time OTP:
  - `GET requestaccess/generateOtpAppIdMapping` with `Authorization: <temp token>`
    → OTP texted to the registered mobile (`generateOTPForAppIdMapping`).
  - `POST auth/validate/otp` with headers `Authorization: <temp token>`,
    `OTP: <code>`, `AppUniqueId: <new id>` → device mapped (`validateAppIDMapping`).
  - After that, normal `auth/login` with the same `AppUniqueId` returns real
    tokens. Mapping a new device can sign the existing phone out
    (`new_device_login` dialog → "verify and login").

## Vehicle list

- `GET requestaccess/getMyVehicleInfo` (a.k.a. `myInfoRefresh`), called right
  after login (`ApiClient.userInfoApi`). Vehicle fields seen: `vin`,
  `vehicleName`, `modelName`, `registrationNumber`.

## Vehicle status

- **Passive (cached, no vehicle wakeup):** `GET vehicleservice/vehicleStatus?vin=`
  → `VehicleInfoResponse { code, data: { odoMeter, dte, vSpd, timeStamp } }`
  (`MahindraApi.getVehilceSpeedAndDte`, `common/models/Data`).
- **Live snapshot (ignition, lamps, doors, roof):** the Remote screen's
  `ReadModel` (`remote/model/ReadModel.java`) carries `ign`, `odoMeter`,
  `vehRange`, `doorLock`, `stsWin`, `sunRoof`, `vSpd`, `tyrePressure`, and a
  nested `lampStatus` (`remote/model/LampStatus.java`:
  `hazardLmp`, `highBeam`, `lowBeam`, `stsParkLmp`/`stsPrkLmp`). It arrives over
  the SSE channel the app opens at
  `{service host}/location-services/webapi/sse/register/{vin}`
  (`ServerSentEventOkHttpWrapper`, headers `Authorization`, `Accept:
  text/event-stream`, `User-Agent: Android`). The live-track event envelope is
  `LiveVehicleTrackEventData { din, vin, timeStamp, gnssInfo{lat,lon,heading},
  canData{speed,vSpd,vehRange,gearMt,hvBattSOC,...}, data{current{ign}} }`.

## Security notes observed (context for "avoid lockout")

- Single-active-session behaviour: the app has a clear-session / "logged in on
  another device" login variant (`signInOnClearSession`, header
  `Cleartoken: clearToken`).
- Main clients pin to a bundled CA (`res/raw/mmc_solution_com.crt`); some
  auxiliary map clients use a trust-all manager. The HA integration just uses
  normal TLS to the real host.
- The app's on-SD-card request/response logging (`Util.saveTextInSdCard`) is a
  no-op stub in the release build.

## Correction (v2.0.68, re-verified): the dashboard status endpoint

The home-screen vehicle data does **not** come from `vehicleservice/vehicleStatus`
(that returns `{"status":"Error","message":"UNAUTHORIZED"}` here). It comes from a
single REST call on the same `RetrofitClient.getMahindraApi()` client (same base
and `Authorization: <jwt>` header as login — no vehicle switch):

- `GET location-services/fetchStateVin/{vin}` → `FetchState { dataModel: DataModel,
  statusCode }` (`MahindraApi.getFetchState`, `common/models/FetchState`,
  `common/models/DataModel`).
- `DataModel` fields: `odoMeter` (Int), `dte` (Int, fuel range), `vehRange`,
  `cngDte`, `fuelMode`, `ign` (boolean = ignition), `latitude`/`longitude`
  (String), `heading`, `vspd`, `hvBattSOC`, `chrgSts`, `timeToCharge`,
  `tripEndTime`, `connected`, `serviceMode`, `dndStatus`, `criticalAlerts`,
  `time` (last update), `wkpSrc`.

Lamps/doors/roof (`lampStatus`, `doorLock`/`stsDoor`, `sunRoof`) are not in this
snapshot. The app POSTs `location-services/initLiveTracking` (`LiveTrackRequest {
vin }`, must return `statusCode 200`) to arm the backend, then opens the SSE
stream `location-services/webapi/sse/register/{vin}`
(`LiveTrackingRepository.initLiveTracking` + `ServerSentEventOkHttpWrapper`). The
first arg to both `getFetchState` and `initLiveTracking` is the raw JWT read from
`MahindraPreference "JWTTOKEN"`.
