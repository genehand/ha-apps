# Web Layer Architecture

The web UI (`shim/web/`) is built with FastAPI and Jinja2 templates using a modular
route structure:

```
shim/web/
├── app.py              # WebUI class, FastAPI setup, route registration
├── const.py            # Web-specific constants
├── schema.py           # Pydantic models for API request/response validation
├── renderers.py        # Template rendering helpers
├── supervisor.py       # Supervisor API client wrapper
├── translations.py     # Translation string utilities
└── routes/             # Route handlers by domain
    ├── __init__.py
    ├── api.py          # REST API endpoints (states, services, events)
    ├── auth.py         # Authentication routes
    ├── config_flows.py # Config flow UI (wizard steps, forms)
    ├── credentials.py  # Application credentials management
    ├── fragments.py    # HTMX fragment endpoints
    └── integrations.py # Integration list, detail, enable/disable/remove
```

## Adding New Routes

When adding new web endpoints:

1. **Pick the right module**: Add to an existing route file by domain, or create a new one under `routes/`
2. **Use `register_routes(app, shim_manager, template_dir)`**: Each module exposes this function signature
3. **Register in `WebUI._register_routes()`**: Import and call the module's `register_routes` in `app.py`
4. **Keep `app.py` thin**: Only FastAPI setup and route registration belong in `app.py`

## Related

- Redirect paths and HA ingress → [`web-ingress.md`](web-ingress.md)
- Config flow wizard steps → [`config-flows.md`](config-flows.md)
- Schema markers (`vol.Required` / `vol.UNDEFINED`) → [`probatio.md`](probatio.md)
