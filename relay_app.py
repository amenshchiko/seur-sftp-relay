#!/usr/bin/env python3
# SEUR SFTP gateway for Make. Ops: list, upload (raw), download, delete, move,
# build_sip (build fixed-width and RETURN it, no upload), upload_sip (build + upload),
# parse (download + decode a SEUR return file: cps shipping conf, sto stock snapshot).
# All SFTP connection details are passed by Make per request. Only server-side
# secret is RELAY_TOKEN.

import os
import base64
import datetime
import paramiko
from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
RELAY_TOKEN = os.environ["RELAY_TOKEN"]

def _bad(m, c=400): return jsonify(status="error", error=m), c
def _guard(d): return d.get("token") == RELAY_TOKEN

# Any unhandled error returns JSON (never an HTML 500) so Make can read and route it.
@app.errorhandler(Exception)
def _handle_any(e):
    code = e.code if isinstance(e, HTTPException) else 500
    return jsonify(status="error", error=str(e), type=type(e).__name__), code

def _conn(d):
    host=d.get("host"); user=d.get("user"); pw=d.get("password"); port=int(d.get("port",22))
    if not (host and user and pw is not None): raise ValueError("host, user and password are required")
    t=paramiko.Transport((host,port))
    try: t.get_security_options().key_types=("rsa-sha2-512","rsa-sha2-256","ssh-rsa")
    except Exception: pass
    t.connect(username=user,password=pw)
    return t, paramiko.SFTPClient.from_transport(t)

# ---------- fixed-width SISLOG interface 9 (sip) builder ----------
def _put(buf,start,length,value,ftype):
    v="" if value is None else str(value)
    if ftype=="N":
        v=v.replace(".","").replace(",",""); v=v[:length].rjust(length,"0")
    else:
        v=v[:length].ljust(length," ")
    for i,ch in enumerate(v): buf[start-1+i]=ch

HEADER_ZERO=[(623,11),(636,11),(708,11),(719,3),(722,8),(730,8),(925,4),(929,3),(932,7),(2416,6),(2424,6)]

def _price11(p):
    s=str(p).replace(",",".")
    try: cents=round(float(s)*1000)
    except Exception: cents=0
    return str(cents).rjust(11,"0")

def _fecha(order):
    d=order.get("date")
    if d:
        d=str(d)[:10]
        try:
            y,m,day=d.split("-"); return f"{day}/{m}/{y} 09:00:00"
        except Exception: pass
    return datetime.datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S")

def build_sip(order):
    num=str(order.get("num","")); fecha=_fecha(order)
    fname=order.get("filename") or ("sip"+datetime.datetime.utcnow().strftime("%d%m%Y%H%M")+".2e1")
    h=[" "]*2450
    _put(h,1,2,"RD","C"); _put(h,3,1,"A","C"); _put(h,4,15,num,"C")
    _put(h,19,3,"148","N"); _put(h,22,6,"000002","C"); _put(h,28,6,"000001","C")
    _put(h,34,3,"01","C"); _put(h,37,2,"PN","C"); _put(h,39,2,"01","C"); _put(h,41,19,fecha,"C")
    _put(h,249,30,order.get("name",""),"C"); _put(h,279,30,order.get("surname",""),"C")
    _put(h,339,15,order.get("nif",""),"C")
    _put(h,396,2,"CL","C"); _put(h,398,70,order.get("street",""),"C")
    _put(h,486,15,order.get("phone",""),"C"); _put(h,521,3,order.get("country","ES"),"C")
    _put(h,524,10,order.get("postal",""),"C"); _put(h,534,40,order.get("city",""),"C")
    _put(h,614,3,"01","C"); _put(h,617,5,"40","C"); _put(h,647,1,"P","C")
    _put(h,648,60,order.get("notes",""),"C")
    _put(h,758,2,"N","C"); _put(h,1042,100,order.get("email",""),"C")
    # Canal Mail (2422) and Canal SMS (2423): left BLANK on purpose. SEUR activates
    # the customer email/SMS notifications automatically at the action level in their
    # own system (confirmed by SIL / Ana, 2026-10-06), so these must NOT be marked in
    # the sip. Marking them with S/N previously caused file rejections.
    for pos,ln in HEADER_ZERO: _put(h,pos,ln,"0","N")
    header="".join(h)
    lines=[]
    for i,ln in enumerate(order.get("lines",[]),1):
        b=[" "]*924
        _put(b,1,2,"RD","C"); _put(b,3,15,num,"C"); _put(b,18,3,"148","N")
        _put(b,21,6,"000002","C"); _put(b,27,6,"000001","C"); _put(b,33,3,"01","C")
        _put(b,36,3,str(i),"N"); _put(b,39,35,ln.get("sku",""),"C")
        _put(b,74,3,"0","C"); _put(b,77,2,"0","C"); _put(b,79,1,"0","N")
        _put(b,120,6,str(ln.get("qty","1")),"N"); _put(b,126,11,_price11(ln.get("price","0")),"N")
        lines.append("".join(b))
    out=[]
    cc=[" "]*83; _put(cc,1,2,"CC","C"); _put(cc,3,19,fname,"C"); _put(cc,22,6,"148","N")
    _put(cc,28,40,"BIOXY ENTERPRISES SL","C"); _put(cc,68,16,fecha[:16],"C"); out.append("".join(cc))
    cf1=[" "]*28; _put(cf1,1,2,"CF","C"); _put(cf1,3,19,fname,"C"); _put(cf1,22,7,"1","N"); out.append("".join(cf1))
    out.append(header)
    ff1=[" "]*21; _put(ff1,1,2,"FF","C"); _put(ff1,3,19,fname,"C"); out.append("".join(ff1))
    cf2=[" "]*28; _put(cf2,1,2,"CF","C"); _put(cf2,3,19,fname,"C"); _put(cf2,22,7,str(len(lines)),"N"); out.append("".join(cf2))
    out.extend(lines)
    ff2=[" "]*21; _put(ff2,1,2,"FF","C"); _put(ff2,3,19,fname,"C"); out.append("".join(ff2))
    ccf=[" "]*21; _put(ccf,1,2,"CC","C"); _put(ccf,3,19,fname,"C"); out.append("".join(ccf))
    return fname, "\r\n".join(out)+"\r\n"

# ---------- SEUR return-file parsers (inbound, from /OUT) ----------
# SEUR return files are fixed-width, cp1252, RD data records only (NO CC/CF/FF
# envelope on SEUR->BIOXY files). Field positions below are 0-based slices aligned
# to "SISLOG - Manual de Integracion v11.00" (sheets "10 C. Salidas" and "7.1 Stock").
# NOTE: no real SEUR return file has been received yet (OLD/ holds only our outbound
# sip). These offsets are per the manual and MUST be validated byte-for-byte against
# the first real cps/sto file during hypercare. /parse always returns `content` and
# `content_b64`, so raw bytes are available to re-tune offsets without data loss.

def _int_or_none(s):
    s=(s or "").strip()
    return int(s) if s.isdigit() else None

def _tracking_url(order_ref):
    # SEUR does NOT send a tracking URL in cps. Per the SISLOG KEY RULE the customer
    # tracking link is derived from our order number.
    return f"https://www.seur.com/miseur/mis-envios/?code={order_ref}" if order_ref else ""

def _parse_cps(text):
    # cps = Confirmacion lineas pedidos salida (SEUR -> BIOXY), file cps*.2m9,
    # protocol 209/22. Manual sheet "10 C. Salidas". RD records only; LINE-LEVEL
    # (one RD per order line, Reg 1 len 174) plus optional Reg 2 lote records
    # (shorter, ~len 108). We aggregate the Reg 1 lines into ONE shipment per order.
    # cps carries NO bultos/peso/servicio and NO tracking URL (derived below).
    orders={}; seq=[]
    for l in text.replace("\r\n","\n").split("\n"):
        if l[0:2]!="RD": continue
        if len(l)<124: continue            # skip Reg 2 lote / short lines
        order_ref=l[2:37].strip()
        if not order_ref: continue
        ref_cli=l[124:174].strip()         # Referencia Cliente = expedition ref
        servida=_int_or_none(l[115:124])   # Cantidad Servida
        if order_ref not in orders:
            orders[order_ref]={
                "order_ref":        order_ref,
                "almacen":          l[40:43].strip(),
                "fecha_salida":     l[87:106].strip(),   # DD-MM-YYYY HH:MM:SS
                "expedicion":       ref_cli,
                "bultos":           None,
                "peso":             None,
                "servicio":         None,
                "tracking_url":     _tracking_url(order_ref),
                "cantidad_servida": 0,
                "lines":            [],
            }
            seq.append(order_ref)
        o=orders[order_ref]
        if not o["expedicion"] and ref_cli: o["expedicion"]=ref_cli
        if servida: o["cantidad_servida"]+=servida
        o["lines"].append({
            "linea":            l[43:46].strip(),
            "sku":              l[46:81].strip(),
            "cantidad_pedida":  _int_or_none(l[106:115]),
            "cantidad_servida": servida,
        })
    return [orders[k] for k in seq]

def _parse_sto(text):
    # sto = Stock (SEUR -> BIOXY), file sto*.2c3 (203/10 art+lote) or *.2p3 (203/16
    # consolidated). Manual sheet "7.1 Stock", Reg len 180, RD records only.
    # Available = Fisico - Pendiente - Reservado - Bloqueado.
    # In .2c3 there is one article-level record (lote blank) plus per-lote records;
    # consumers should use article-level rows (lote == "") to avoid double counting.
    out=[]
    for l in text.replace("\r\n","\n").split("\n"):
        if l[0:2]!="RD": continue
        if len(l)<172: continue
        fisico   =_int_or_none(l[109:118])
        pendiente=_int_or_none(l[118:127])
        reservado=_int_or_none(l[127:136])
        bloqueado=_int_or_none(l[163:172])
        precio=l[172:180].strip()
        disp=None
        if fisico is not None:
            disp=fisico-(pendiente or 0)-(reservado or 0)-(bloqueado or 0)
        out.append({
            "record_type": l[0:2],
            "cliente":     l[2:5].strip(),
            "almacen":     l[5:8].strip(),
            "sku":         l[8:43].strip(),
            "descripcion": l[49:89].strip(),
            "lote":        l[89:109].strip(),
            "fisico":      fisico,
            "pendiente":   pendiente,
            "reservado":   reservado,
            "bloqueado":   bloqueado,
            "disponible":  disp,
            "stock":       disp,
            "precio":      (int(precio)/1000 if precio.isdigit() else None),
        })
    return out

def _parse_sps(text):
    # sps = Situacion de pedidos (SEUR -> BIOXY), file sps*.2k2, 230-char RD records.
    # One record per order per status snapshot. Offsets validated against real SEUR
    # files (2026-10). Carries the transport EXPEDITION number (pos 77, len 10),
    # the status code (e.g. T028), and the full customer tracking URL.
    out=[]
    for l in text.replace("\r\n","\n").split("\n"):
        if l[:2]!="RD": continue
        if len(l)<120: continue
        ui=l.find("https")
        out.append({
            "record_type":  l[0:2],
            "order_ref":    l[2:37].strip(),
            "cliente":      l[37:40].strip(),
            "expedicion":   l[76:86].strip(),
            "fecha_estado": l[86:105].strip(),
            "status_code":  l[116:120].strip(),
            "tracking_url": (l[ui:].strip() if ui!=-1 else ""),
        })
    return out

# Route by filename prefix. cps -> units confirmed, sps -> status/expedition/tracking,
# sto -> stock snapshot.
_PARSERS={"cps":("shipping_confirmation",_parse_cps),
          "sps":("order_status",_parse_sps),
          "sto":("stock_snapshot",_parse_sto)}

def _detect(filename):
    return _PARSERS.get((filename or "")[:3].lower())

# ---------- endpoints ----------
@app.get("/health")
def health(): return jsonify(status="up")

@app.post("/build_sip")
def build_sip_endpoint():
    """Build the fixed-width sip and RETURN it (no upload). Body: {token, order:{...}}.
    Returns filename, content (text), content_b64 (exact cp1252 bytes)."""
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    order=d.get("order")
    if not order or not order.get("num"): return _bad("order.num and order are required")
    fname,content=build_sip(order)
    raw=content.encode("cp1252",errors="replace")
    rows=content.split("\r\n")
    return jsonify(status="ok", filename=fname, content=content,
                   content_b64=base64.b64encode(raw).decode("ascii"), bytes=len(raw),
                   header_len=len(rows[2]) if len(rows)>2 else 0,
                   line_count=sum(1 for r in rows if r[:2]=="RD" and len(r)==924))

@app.post("/list")
def list_folder():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    folder=(d.get("folder") or "/OUT").rstrip("/") or "/"
    t,sftp=_conn(d)
    try:
        files=[{"name":a.filename,"size":a.st_size,"mtime":a.st_mtime,
                "is_dir":bool(a.st_mode and (a.st_mode & 0o40000))} for a in sftp.listdir_attr(folder)]
        return jsonify(folder=folder,count=len(files),files=files)
    finally: t.close()

@app.post("/upload")
def upload():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    folder=(d.get("folder") or "/IN/TEST").rstrip("/"); filename=d.get("filename"); content=d.get("content")
    if not filename or content is None: return _bad("filename and content are required")
    payload=content.encode(d.get("encoding","cp1252"),errors="replace")
    t,sftp=_conn(d)
    try:
        remote=f"{folder}/{filename}"
        with sftp.open(remote,"wb") as fh: fh.write(payload)
        return jsonify(status="ok",path=remote,bytes=len(payload))
    finally: t.close()

@app.post("/upload_sip")
def upload_sip():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    order=d.get("order")
    if not order or not order.get("num"): return _bad("order.num and order are required")
    folder=(d.get("folder") or "/IN/TEST").rstrip("/")
    fname,content=build_sip(order); payload=content.encode("cp1252",errors="replace")
    t,sftp=_conn(d)
    try:
        remote=f"{folder}/{fname}"
        with sftp.open(remote,"wb") as fh: fh.write(payload)
        return jsonify(status="ok",path=remote,filename=fname,bytes=len(payload))
    finally: t.close()

@app.post("/download")
def download():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    folder=(d.get("folder") or "/OUT").rstrip("/"); filename=d.get("filename")
    if not filename: return _bad("filename is required")
    t,sftp=_conn(d)
    try:
        remote=f"{folder}/{filename}"
        with sftp.open(remote,"rb") as fh: raw=fh.read()
        return jsonify(status="ok",path=remote,bytes=len(raw),
                       content=raw.decode(d.get("encoding","cp1252"),errors="replace"),
                       content_b64=base64.b64encode(raw).decode("ascii"))
    finally: t.close()

@app.post("/delete")
def delete():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    folder=(d.get("folder") or "").rstrip("/"); filename=d.get("filename")
    if not filename: return _bad("filename is required")
    t,sftp=_conn(d)
    try:
        remote=f"{folder}/{filename}"; sftp.remove(remote); return jsonify(status="ok",deleted=remote)
    finally: t.close()

@app.post("/move")
def move():
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    src=d.get("src"); dst=d.get("dst")
    if not (src and dst): return _bad("src and dst are required")
    t,sftp=_conn(d)
    try:
        sftp.rename(src,dst); return jsonify(status="ok",moved_from=src,moved_to=dst)
    finally: t.close()

@app.post("/parse")
def parse_file():
    """Decode a SEUR return file into structured records.
    Body: {token, ...sftp..., folder, filename}  -> downloads then parses.
      or: {token, filename, content}             -> parses provided text (no SFTP).
    Type is detected from the filename prefix (cps, sto). Returns type + records.
    Unknown types still return raw_lines with parsed=false so nothing is lost."""
    d=request.get_json(force=True,silent=True) or {}
    if not _guard(d): return _bad("unauthorized",401)
    filename=d.get("filename")
    if not filename: return _bad("filename is required")
    enc=d.get("encoding","cp1252")
    content=d.get("content")
    if content is None:
        folder=(d.get("folder") or "/OUT").rstrip("/")
        t,sftp=_conn(d)
        try:
            with sftp.open(f"{folder}/{filename}","rb") as fh: raw=fh.read()
        finally: t.close()
        content=raw.decode(enc,errors="replace")
    else:
        raw=content.encode(enc,errors="replace")
    b64=base64.b64encode(raw).decode("ascii")
    hit=_detect(filename)
    lines=[l for l in content.replace("\r\n","\n").split("\n") if l.strip()]
    if not hit:
        return jsonify(status="ok", filename=filename, parsed=False, type="unknown",
                       count=len(lines), raw_lines=lines,
                       content=content, content_b64=b64, bytes=len(raw))
    type_name, fn = hit
    records=fn(content)
    return jsonify(status="ok", filename=filename, parsed=True, type=type_name,
                   count=len(records), records=records,
                   content=content, content_b64=b64, bytes=len(raw))

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
