# SEUR SFTP Gateway (Railway) — for Make

A tiny always-on service that performs SFTP operations against SEUR on behalf of Make.
Make orchestrates and calls this over HTTPS. It exists because Make's own network path
cannot reach SEUR's SFTP, while a normal host can.

## Design (maximally Make-managed)

**All SFTP connection details are passed by Make in each request** — host, port, user,
password, folder, filename, content. The only thing stored on the server is `RELAY_TOKEN`,
a shared secret that authorises the caller.

- GitHub: **no credentials** in the repo.
- Railway: stores **only** `RELAY_TOKEN`.
- Make: holds host / port / user / password / folder as Make variables.

## Files
- `relay_app.py` — the gateway (health, list, upload, download, delete, move)
- `requirements.txt` — flask, paramiko (<5 keeps ssh-rsa), gunicorn
- `Procfile` — `web: gunicorn relay_app:app --bind 0.0.0.0:$PORT`

## Deploy on Railway
1. Push these files to a GitHub repo.
2. Railway → New Project → Deploy from GitHub repo → select it.
3. Variables tab → add `RELAY_TOKEN` = a long random string (the ONLY variable needed).
4. Settings → Networking → Generate Domain → note the `https://...up.railway.app` URL.
5. Test: `curl https://YOUR-APP.up.railway.app/health` → `{"status":"up"}`

## Endpoints (all POST JSON; every call needs `token` = RELAY_TOKEN)

Common connection fields in every body: `host`, `port`, `user`, `password`.

- **/list** `{token, host, port, user, password, folder}` → file list
- **/upload** `{token, host, port, user, password, folder, filename, content, encoding?}`
- **/download** `{token, host, port, user, password, folder, filename, encoding?}` → content + base64
- **/move** `{token, host, port, user, password, src, dst}`
- **/delete** `{token, host, port, user, password, folder, filename}`

`encoding` defaults to `cp1252` (Windows-1252) — correct for SISLOG files.

## Make setup (store these as Make variables / a data store)

| Make variable | Value |
|---|---|
| RELAY_URL | https://YOUR-APP.up.railway.app |
| RELAY_TOKEN | (same long random string as in Railway) |
| SFTP_HOST | ftp-a.seur.com |
| SFTP_PORT | 22 |
| SFTP_USER | bio06514 |
| SFTP_PASS | (SEUR password) |

Then each SFTP step is an **HTTP → Make a request** (POST JSON) to `{RELAY_URL}/upload`
(etc.), mapping the variables above plus `folder`, `filename`, `content`.

Example upload body:
```json
{
  "token": "{{RELAY_TOKEN}}",
  "host": "{{SFTP_HOST}}", "port": "{{SFTP_PORT}}",
  "user": "{{SFTP_USER}}", "password": "{{SFTP_PASS}}",
  "folder": "/IN/TEST",
  "filename": "sip190820260001.2e1",
  "content": "{{ fixed-width file content }}"
}
```

## Why it works
Everything except Make's network reaches SEUR (your PC, a Dublin VPN, and Make's SFTP
module against a public server all succeed). This host sits on a normal path, so its
packets reach SEUR. Make still runs the whole scenario and just calls this for the SFTP hop.
