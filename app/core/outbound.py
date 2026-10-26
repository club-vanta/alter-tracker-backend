"""Shared identity of the HTTP clients that call public providers (Photon, Open-Meteo)."""

# Both providers ask API users to identify the app.
OUTBOUND_USER_AGENT = "alter-tracker/1.0 (club-vanta.com)"
OUTBOUND_HEADERS: dict[str, str] = {
    "User-Agent": OUTBOUND_USER_AGENT,
    "Accept": "application/json",
}
