# Web UI and HA Ingress

The Web UI uses **relative paths** for all HTMX redirects to support both direct
access and Home Assistant ingress:

## Path Rules

- **Always use relative paths** in `HX-Redirect` headers
- **Never use absolute paths** starting with `/`
- **Use the `_get_detail_redirect()` helper** for redirects to integration detail pages — it detects the source page from the Referer header and returns the appropriate path:
  - From index page: returns `./integrations/{domain}`
  - From detail page: returns `.` (current page)

## Examples

| Endpoint | Source Page | Redirect Path | Result |
|----------|-------------|---------------|--------|
| `/integrations/{domain}/enable` | Index | `./integrations/{domain}` | `/integrations/dreo` |
| `/integrations/{domain}/enable` | Detail | `.` | `/integrations/dreo` (stays) |
| `/integrations/{domain}/remove` | Detail | `..` | `/` (index) |

## Why Context-Aware Redirects?

HTMX resolves `HX-Redirect` relative to the **page URL where the request originated**,
not the request URL. This means:

- From index `/`: `./integrations/{domain}` → `/integrations/{domain}` ✓
- From detail `/integrations/{domain}`: `./integrations/{domain}` → `/integrations/integrations/{domain}` ✗
