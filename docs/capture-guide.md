# Capturing the AdrenoX app's traffic

The endpoints and field names in this integration were recovered by statically
decompiling the app (see [reverse-engineering.md](reverse-engineering.md)), but
they have **not been confirmed against live traffic**. A capture from an owner's
own phone is how we (a) confirm the `vehicleStatus` fields, (b) map the live SSE
stream for ignition / lamps / doors / roof, and (c) map remote commands. This
guide shows an owner how to do that **on their own phone, with their own
account**, and how to share the result safely.

For most users the easier path is to run the integration with debug logging on
(README → "Validating / fixing field names") and read the redacted diagnostics —
a full packet capture is usually not needed.

> **Before you start**
> - Do this only with your own car and account. Using the app this way may go
>   against Mahindra's terms of use; you decide whether to proceed.
> - **Never share passwords, OTPs, live tokens, cookies, your phone number, VIN
>   or home location.** Run the redaction script below and read its output
>   before you share anything.
> - Don't press remote commands (unlock, AC, engine) while capturing unless the
>   car is somewhere safe and you are next to it.

## What we need

Do each of these once in the app while capturing:

1. **Sign out, then sign in**: enter your mobile number and password.
2. **Open the car's dashboard** and pull to refresh, so the app reads fuel,
   range, odometer, doors, tyres and so on.
3. **Open the map / "find my car"** screen.
4. **Leave the app for an hour or more**, then open it again. This usually
   makes it renew its token, which shows the refresh call.
5. *Optional, only if safe:* lock, unlock, start AC, stop AC, one at a time,
   with a few seconds between them.

## Option A: Android emulator (recommended, no root on your phone)

Android apps ignore user-installed certificates, so the interception
certificate has to be a *system* one. An emulator makes that easy.

1. Install [Android Studio](https://developer.android.com/studio) and create a
   virtual device with a **Google APIs** system image (not "Google Play"; the
   Play images can't be rooted). Pick Android 13 or 14, arm64 on Apple
   silicon.
2. Install [HTTP Toolkit](https://httptoolkit.com) on your computer and start
   the emulator.
3. Copy the AdrenoX app from **your own phone** onto the emulator, instead of
   downloading the APK from a mirror site. With USB debugging on your phone:
   ```sh
   adb -d shell pm list packages | grep -i -E "mahindra|adrenox"   # find the package name
   adb -d shell pm path <package>        # lists one or more .apk paths
   adb -d pull <each path it printed>
   adb -e install-multiple base.apk split_*.apk
   ```
4. In HTTP Toolkit choose **Android device via ADB**. It installs its system
   certificate on the emulator and starts capturing.
5. Open AdrenoX on the emulator and do the steps in *What we need*.

**If the app shows no network or "something went wrong"**, it is probably
pinning its certificate. In HTTP Toolkit use **Android app via Frida** (it
runs the open-source
[frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning)
scripts for you), choose AdrenoX, and try again.

**If the app refuses to run on an emulator or rooted device**, try Option B,
or stop here and open an issue saying so. That is useful information too.

## Option B: your own rooted Android phone

Same as Option A, but connect your phone instead of the emulator and choose
**Android device via ADB** in HTTP Toolkit. It needs root to add the system
certificate. If your phone isn't rooted, use Option A; don't root a phone just
for this.

## Option C: iPhone

[mitmproxy](https://mitmproxy.org) or HTTP Toolkit can proxy an iPhone after you
install and trust their certificate (Settings → General → About → Certificate
Trust Settings). This only works if the app doesn't pin its certificate;
getting around pinning on iOS needs a jailbreak, which this guide doesn't cover.

## Export and redact

1. In HTTP Toolkit, filter to the requests whose host looks like Mahindra or
   AdrenoX (ignore Google, Firebase, analytics and map tiles), select them, and
   **Export → HAR**.
2. Shrink and redact it with the script in this repository (Python 3 only):
   ```sh
   python3 tools/redact_har.py adrenox.har > adrenox-endpoints.json
   ```
3. **Read `adrenox-endpoints.json` yourself.** Search it for your phone number,
   VIN, registration number, name, email and the first few digits of your home
   latitude. Replace anything the script missed with `<redacted>`.
4. Delete the original `.har` file when you're done; it contains live tokens.

The script keeps what's needed to write the integration (hosts, paths, methods,
header names, JSON field names, value types, fuel/door/tyre values) and
replaces tokens, JWTs, cookies, auth headers, phone numbers, emails, VINs,
coordinates, OTPs and personal-looking fields.

## Share it

Attach `adrenox-endpoints.json` to a GitHub issue on this repository, or hand
the file to whoever is helping you. Don't paste tokens or OTPs into a chat or
issue, even "expired" ones.

## What happens next (for maintainers)

With a redacted capture:

1. Fill in `custom_components/mahindra_adrenox/endpoints.py`: `API_BASE`, the
   login/refresh/vehicle/status calls, `AUTH_HEADER`, any constant
   `EXTRA_HEADERS`, and the request-body functions.
2. Update the candidate paths in `parser.py` so the real field names come
   first, and fix units (for example tyre pressure in `sensor.py`).
3. Add the redacted payloads to `tests/fixtures/` and a test that parses them.
4. Only fill in `COMMANDS` for commands seen in the capture, exactly as sent.
