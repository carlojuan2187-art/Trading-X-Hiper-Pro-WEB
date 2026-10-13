# Market terminal (KAELEON-style)

This frontend is a Vite ESM library using **klinecharts 10.0.3** (same version as the provided KAELEON reference). It mounts in the existing server-rendered FastAPI web UI without replacing `app/web/static/app.js`.

```bash
npm ci
npm run build
```

Output is written to `../app/web/static/market/` and served by FastAPI. `npm ci` needs Internet/package-registry access during deployment build. No API keys or trading commands are sent from the market frontend.

See `../FASE_3_GRAFICO_KAELEON.md` for production checklist and limitations.
