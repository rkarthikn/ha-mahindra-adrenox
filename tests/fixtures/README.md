# Fixtures

These payloads are **synthetic**: they are shaped like a plausible connected-car
API, not copied from AdrenoX. They test the parser's tolerance (envelopes,
units glued onto numbers, string states). When a real, redacted capture is
available, add it here as `status_real.json` / `vehicles_real.json` and add a
test that parses it; then trim the guessed paths in `parser.py` to the real ones.
