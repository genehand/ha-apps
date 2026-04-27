"""Constants for the Web UI.

CDN URLs, error message translations, and other static configuration.
"""

# CDN URLs for CSS and JS libraries
PICO_CSS_URL = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"
PICO_COLORS_URL = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.colors.min.css"

# https://htmx.org/docs/#installing
HTMX_URL = "https://cdn.jsdelivr.net/npm/htmx.org@2.0.10/dist/htmx.min.js"
HTMX_SRI = "sha384-H5SrcfygHmAuTDZphMHqBJLc3FhssKjG7w/CeCpFReSfwBWDTKpkzPP8c+cLsK+V"

# Error message translations for config flow errors
ERROR_TRANSLATIONS = {
    "invalid_auth": "Invalid credentials. Please check your username/password.",
    "cannot_connect": "Cannot connect to device. Please check the IP address and ensure the device is online.",
    "cannot_find": "Cannot find device. Please check your network connection.",
    "invalid_host": "Invalid host address. Please check the IP address.",
    "unknown": "An unknown error occurred. Please try again.",
    "cannot_parse_wifi_info": "Failed to parse WiFi information. Please check your SSID and password.",
}
