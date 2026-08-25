#!/usr/bin/env python3
# SEUR SFTP gateway for Make. Ops: list, upload (raw), download, delete, move,
# and upload_sip (build a fixed-width SISLOG order file from structured JSON, then upload).
#
# All SFTP connection details are passed by Make in each request body. The only
# server-side secret is RELAY_TOKEN. GitHub holds no credentials; Railway stores
# only RELAY_TOKEN; Make holds host/user/password.

import os
import base64
import datetime
import paramiko
from flask import Flask, request, jsonify

app = Flask(__name__)
RELAY_TOKEN = os.environ["RELAY_TOKEN"]

def _bad(m, c=400): return jsonify(error=m), c
def _guard(d): return d.get("token") == RELAY_TOKEN

def _conn(d):
    host = d.get("host"); user = d.get("user"); pw = d.get("password")
    port = int(d.get("port", 22))
    if not (host and user and pw is not None):
        raise ValueError("host, user and password are required")
    t = paramiko.Transport((host, port))
    try:
        t.get_security_options().key_types = ("rsa-sha2-512", "rsa-sha2-256", "ssh-rsa")
    except Exception:
        pass
    t.connect(username=user, password=pw)
    return t, paramiko.SFTPClient.from_transport(t)

# ---------- fixed-width SISLOG interface 9 (sip) builder ----------
def _put(buf, start, length, value, ftype):
    v = "" if value is None else str(value)
    if ftype == "N":
        v = v.replace(".", "").replace(",", "")
        v = v[:length].rjust(length, "0")
    else:
        v = v[:length].ljust(length, " ")
    for i, ch in enumerate(v):
        buf[start-1+i] = ch

HEADER_ZERO = [(623,11),(636,11),(708,11),(719,3),(722,8),(730,8),
               (925,4),(929,3),(932,7),(2416,6),(2424,6)]

def _price11(p):
    # "10.74" or "10,74" -> 10740 (thousandths) -> zero-pad 11 -> 00000010740
    s = str(p).replace(",", ".")
    try: cents = round(float(s) * 1000)
    except Exception: cents = 0
    return str(cents).rjust(11, "0")

def _fecha(order):
    d = order.get("date")
    if d:
        d = str(d)[:10]
        try:
            y, m, day = d.split("-")
            return f"{day}/{m}/{y} 09:00:00"
        except Exception:
            pass
    now = datetime.datetime.utcnow()
    return now.strftime("%d/%m/%Y %H:%M:%S")

def build_sip(order):
    num = str(order.get("num", ""))
    fecha = _fecha(order)
    fname = order.get("filename") or ("sip" + datetime.datetime.utcnow().strftime("%d%m%Y%H%M") + ".2e1")

    # --- header (2450) ---
    h = [" "] * 2450
    _put(h,1,2,"RD","C"); _put(h,3,1,"A","C"); _put(h,4,15,num,"C")
    _put(h,19,3,"148","N"); _put(h,22,6,"000002","C"); _put(h,28,6,"000001","C")
    _put(h,34,3,"01","C"); _put(h,37,2,"PN","C"); _put(h,39,2,"01","C")
    _put(h,41,19,fecha,"C")
    _put(h,249,30,order.get("name",""),"C"); _put(h,279,30,order.get("surname",""),"C")
    _put(h,339,15,order.get("nif",""),"C")
    _put(h,396,2,"CL","C"); _put(h,398,70,order.get("street",""),"C")
    _put(h,486,15,order.get("phone",""),"C")
    _put(h,521,3,order.get("country","ES"),"C")
    _put(h,524,10,order.get("postal",""),"C"); _put(h,534,40,order.get("city",""),"C")
    _put(h,614,3,"01","C"); _put(h,617,5,"40","C"); _put(h,647,1,"P","C")
    _put(h,648,60,order.get("notes",""),"C")     # Observaciones (optional)
    _put(h,758,2,"N","C"); _put(h,1042,100,order.get("email",""),"C")
    _put(h,2422,1,"S","C"); _put(h,2423,1,"N","C")
    for pos,ln in HEADER_ZERO: _put(h,pos,ln,"0","N")
    header = "".join(h)

    # --- lines (924 each) ---
    lines = []
    for i, ln in enumerate(order.get("lines", []), 1):
        b = [" "] * 924
        _put(b,1,2,"RD","C"); _put(b,3,15,num,"C"); _put(b,18,3,"148","N")
        _put(b,21,6,"000002","C"); _put(b,27,6,"000001","C"); _put(b,33,3,"01","C")
        _put(b,36,3,str(i),"N"); _put(b,39,35,ln.get("sku",""),"C")
        _put(b,74,3,"0","C"); _put(b,77,2,"0","C"); _put(b,79,1,"0","N")
        _put(b,120,6,str(ln.get("qty","1")),"N")
        _put(b,126,11,_price11(ln.get("price","0")),"N")
        lines.append("".join(b))

    # --- CC/CF/FF envelope ---
    out = []
    cc=[" "]*83; _put(cc,1,2,"CC","C"); _put(cc,3,19,fname,"C"); _put(cc,22,6,"148","N")
    _put(cc,28,40,"BIOXY ENTERPRISES SL","C"); _put(cc,68,16,fecha[:16],"C"); out.append("".join(cc))
    cf1=[" "]*28; _put(cf1,1,2,"CF","C"); _put(cf1,3,19,fname,"C"); _put(cf1,22,7,"1","N"); out.append("".join(cf1))
    out.append(header)
    ff1=[" "]*21; _put(ff1,1,2,"FF","C"); _put(ff1,3,19,fname,"C"); out.append("".join(ff1))
    cf2=[" "]*28; _put(cf2,1,2,"CF","C"); _put(cf2,3,19,fname,"C"); _put(cf2,22,7,str(len(lines)),"N"); out.append("".join(cf2))
    out.extend(lines)
    ff2=[" "]*21; _put(ff2,1,2,"FF","C"); _put(ff2,3,19,fname,"C"); out.append("".join(ff2))
    ccf=[" "]*21; _put(ccf,1,2,"CC","C"); _put(ccf,3,19,fname,"C"); out.append("".join(ccf))
    content = "\r\n".join(out) + "\r\n"
    return fname, content

# ---------- endpoints ----------
@app.get("/health")
def health(): return jsonify(status="up")

@app.post("/list")
def list_folder():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder = (d.get("folder") or "/OUT").rstrip("/") or "/"
    t, sftp = _conn(d)
    try:
        files = [{"name":a.filename,"size":a.st_size,"mtime":a.st_mtime,
                  "is_dir":bool(a.st_mode and (a.st_mode & 0o40000))} for a in sftp.listdir_attr(folder)]
        return jsonify(folder=folder, count=len(files), files=files)
    finally: t.close()

@app.post("/upload")
def upload():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder=(d.get("folder") or "/IN/TEST").rstrip("/"); filename=d.get("filename"); content=d.get("content")
    if not filename or content is None: return _bad("filename and content are required")
    payload = content.encode(d.get("encoding","cp1252"), errors="replace")
    t, sftp = _conn(d)
    try:
        remote=f"{folder}/{filename}"
        with sftp.open(remote,"wb") as fh: fh.write(payload)
        return jsonify(status="ok", path=remote, bytes=len(payload))
    finally: t.close()

@app.post("/upload_sip")
def upload_sip():
    """Build a fixed-width sip from {order:{...}} and upload it. Body also needs the
    SFTP connection fields + folder (default /IN/TEST)."""
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    order = d.get("order")
    if not order or not order.get("num"): return _bad("order.num and order are required")
    folder=(d.get("folder") or "/IN/TEST").rstrip("/")
    fname, content = build_sip(order)
    payload = content.encode("cp1252", errors="replace")
    t, sftp = _conn(d)
    try:
        remote=f"{folder}/{fname}"
        with sftp.open(remote,"wb") as fh: fh.write(payload)
        return jsonify(status="ok", path=remote, filename=fname, bytes=len(payload),
                       header_len=len(content.split("\r\n")[2]) if len(content.split("\r\n"))>2 else 0)
    finally: t.close()

@app.post("/download")
def download():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder=(d.get("folder") or "/OUT").rstrip("/"); filename=d.get("filename")
    if not filename: return _bad("filename is required")
    t, sftp = _conn(d)
    try:
        remote=f"{folder}/{filename}"
        with sftp.open(remote,"rb") as fh: raw=fh.read()
        return jsonify(status="ok", path=remote, bytes=len(raw),
                       content=raw.decode(d.get("encoding","cp1252"),errors="replace"),
                       content_b64=base64.b64encode(raw).decode("ascii"))
    finally: t.close()

@app.post("/delete")
def delete():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    folder=(d.get("folder") or "").rstrip("/"); filename=d.get("filename")
    if not filename: return _bad("filename is required")
    t, sftp = _conn(d)
    try:
        remote=f"{folder}/{filename}"; sftp.remove(remote)
        return jsonify(status="ok", deleted=remote)
    finally: t.close()

@app.post("/move")
def move():
    d = request.get_json(force=True, silent=True) or {}
    if not _guard(d): return _bad("unauthorized", 401)
    src=d.get("src"); dst=d.get("dst")
    if not (src and dst): return _bad("src and dst are required")
    t, sftp = _conn(d)
    try:
        sftp.rename(src,dst); return jsonify(status="ok", moved_from=src, moved_to=dst)
    finally: t.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
