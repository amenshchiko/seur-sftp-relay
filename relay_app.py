#!/usr/bin/env python3
# SEUR SFTP gateway for Make. Full ops: upload, list, download, delete, move.
#
# Design: ALL connection details (host, port, user, password, folder) are passed
# BY MAKE in each request body, so nothing SEUR-specific is stored here or on the
# host. The only server-side secret is RELAY_TOKEN, which authorises the caller.
#   -> GitHub holds no credentials.
#   -> Railway stores only RELAY_TOKEN.
#   -> Make holds host/user/password/folder as Make variables.

import os
import base64
import paramiko
from flask import Flask, request, jsonify

app = Flask(__name__)
RELAY_TOKEN = os.environ["RELAY_TOKEN"]        # only stored secret

def _bad(msg, code=400):
    return jsonify(error=msg), code

def _conn(d):
    """Open SFTP using params supplied by Make. Allows legacy ssh-rsa host key."""
    host = d.get("host"); user = d.get("user"); password = d.get("password")
    port = int(d.get("port", 22))
    if not (host and user and password is not None):
        raise ValueError("host, user and password are required")
    t = paramiko.Transport((host, port))
    try:
        t.get_security_options().key_types = ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")
    except Exception:
        pass
    t.connect(username=user, password=password)
    return t, paramiko.SFTPClient.from_transport(t)

def _guard(d):
    return d.get("token") == RELAY_TOKEN

@app.get("/health")
def health():
    return jsonify(status="up")

@app.post("/list")
def list_folder():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder = (d.get("folder") or "/OUT").rstrip("/") or "/"
    t, sftp = _conn(d)
    try:
        files = [{"name": a.filename, "size": a.st_size, "mtime": a.st_mtime,
                  "is_dir": bool(a.st_mode and (a.st_mode & 0o40000))}
                 for a in sftp.listdir_attr(folder)]
        return jsonify(folder=folder, count=len(files), files=files)
    finally:
        t.close()

@app.post("/upload")
def upload():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder   = (d.get("folder") or "/IN/TEST").rstrip("/")
    filename = d.get("filename"); content = d.get("content")
    encoding = d.get("encoding", "cp1252")
    if not filename or content is None:
        return _bad("filename and content are required")
    payload = content.encode(encoding, errors="replace")
    t, sftp = _conn(d)
    try:
        remote = f"{folder}/{filename}"
        with sftp.open(remote, "wb") as fh:
            fh.write(payload)
        return jsonify(status="ok", path=remote, bytes=len(payload))
    finally:
        t.close()

@app.post("/download")
def download():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder   = (d.get("folder") or "/OUT").rstrip("/")
    filename = d.get("filename"); encoding = d.get("encoding", "cp1252")
    if not filename: return _bad("filename is required")
    t, sftp = _conn(d)
    try:
        remote = f"{folder}/{filename}"
        with sftp.open(remote, "rb") as fh:
            raw = fh.read()
        return jsonify(status="ok", path=remote, bytes=len(raw),
                       content=raw.decode(encoding, errors="replace"),
                       content_b64=base64.b64encode(raw).decode("ascii"))
    finally:
        t.close()

@app.post("/delete")
def delete():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder = (d.get("folder") or "").rstrip("/"); filename = d.get("filename")
    if not filename: return _bad("filename is required")
    t, sftp = _conn(d)
    try:
        remote = f"{folder}/{filename}"; sftp.remove(remote)
        return jsonify(status="ok", deleted=remote)
    finally:
        t.close()

@app.post("/move")
def move():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    src = d.get("src"); dst = d.get("dst")
    if not (src and dst): return _bad("src and dst are required")
    t, sftp = _conn(d)
    try:
        sftp.rename(src, dst)
        return jsonify(status="ok", moved_from=src, moved_to=dst)
    finally:
        t.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
