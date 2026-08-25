#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard local para pruebas de carga del chatbot de Academia Prefortia.

Uso:
    pip install flask requests
    python app.py
    -> Abre http://127.0.0.1:5000 en tu navegador

No requiere conocer el código del chatbot: envia peticiones POST directamente
al endpoint del MinichatBotHandler, igual que hace la web real.
"""

import csv
import io
import json
import os
import time
import uuid
import threading
from statistics import mean
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Estado global de la prueba (en memoria)
# ---------------------------------------------------------------------------
STATE = {
    "running": False,
    "stop": False,
    "config": {},
    "results": [],          # lista de dicts por peticion
    "start_time": None,
    "end_time": None,
    "total_expected": 0,
    "sample_response": "",   # primera respuesta OK, para verificar que el bot contesta
    "probes": [],            # registro de consultas de observabilidad al agente
}
LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "url": "https://www.academiaprefortia.com/gescon/alumnos/minichat/MinichatBotHandler.aspx",
    "num_users": 10,
    "loops": 1,                 # mensajes que envia cada usuario
    "ramp_up": 0.15,            # segundos de separacion entre el arranque de cada usuario
    "timeout": 30,              # timeout por peticion (s)
    "mensaje": "¿Qué puedes hacer?",
    "nombreAlumno": "Pablo",
    "migasJson": '["curso pre-ingreso 133º","aula-virtual","escritorio"]',
    "chat_mode": "unique",      # unique = un chatId por usuario | shared = todos el mismo
    "chatId_fijo": "2fff5830-eb9f-4086-b9fc-6a07a1ac6164",
}


# ---------------------------------------------------------------------------
# Motor de la prueba de carga
# ---------------------------------------------------------------------------
def enviar_peticion(user_id, iteracion, cfg, chat_id):
    """Envia una peticion POST al chatbot y registra el resultado."""
    payload = {
        "mensaje": f"{cfg['mensaje']} [u{user_id}-i{iteracion}]",
        "nombreAlumno": f"{cfg['nombreAlumno']}{user_id}",
        "migasJson": cfg["migasJson"],
        "chatId": chat_id,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (LoadTest) PruebaCarga/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }

    t0 = time.time()
    resultado = {
        "user": user_id,
        "iteracion": iteracion,
        "chatId": chat_id,
        "timestamp": t0,
    }
    try:
        r = requests.post(cfg["url"], data=payload, headers=headers,
                          timeout=cfg["timeout"])
        latencia = time.time() - t0
        resultado.update({
            "status": r.status_code,
            "latencia": latencia,
            "ok": 200 <= r.status_code < 300,
            "bytes": len(r.content),
            "error": None,
        })
        # Guarda una respuesta de ejemplo para verificar que el bot responde
        if resultado["ok"]:
            with LOCK:
                if not STATE["sample_response"]:
                    STATE["sample_response"] = r.text[:1500]
    except requests.exceptions.Timeout:
        resultado.update({"status": 0, "latencia": time.time() - t0,
                          "ok": False, "bytes": 0, "error": "TIMEOUT"})
    except Exception as e:
        resultado.update({"status": 0, "latencia": time.time() - t0,
                          "ok": False, "bytes": 0,
                          "error": f"{type(e).__name__}: {str(e)[:120]}"})

    with LOCK:
        STATE["results"].append(resultado)
    return resultado


def worker_usuario(user_id, cfg):
    """Simula un usuario: espera su turno de arranque y envia N mensajes."""
    # Ramp-up: escalona el arranque de cada usuario
    time.sleep(user_id * cfg["ramp_up"])

    if cfg["chat_mode"] == "shared":
        chat_id = cfg["chatId_fijo"] or str(uuid.uuid4())
    else:
        chat_id = str(uuid.uuid4())

    for it in range(1, cfg["loops"] + 1):
        with LOCK:
            if STATE["stop"]:
                return
        enviar_peticion(user_id, it, cfg, chat_id)


def ejecutar_prueba(cfg):
    """Lanza todos los usuarios en paralelo."""
    with LOCK:
        STATE["running"] = True
        STATE["stop"] = False
        STATE["results"] = []
        STATE["sample_response"] = ""
        STATE["config"] = cfg
        STATE["start_time"] = time.time()
        STATE["end_time"] = None
        STATE["total_expected"] = cfg["num_users"] * cfg["loops"]

    try:
        with ThreadPoolExecutor(max_workers=cfg["num_users"]) as pool:
            futuros = [pool.submit(worker_usuario, u, cfg)
                       for u in range(1, cfg["num_users"] + 1)]
            for f in futuros:
                f.result()
    finally:
        with LOCK:
            STATE["running"] = False
            STATE["end_time"] = time.time()


def calcular_stats():
    """Calcula metricas agregadas del estado actual."""
    with LOCK:
        results = list(STATE["results"])
        running = STATE["running"]
        start = STATE["start_time"]
        end = STATE["end_time"]
        total_expected = STATE["total_expected"]
        sample = STATE["sample_response"]

    ok = [r for r in results if r["ok"]]
    fail = [r for r in results if not r["ok"]]
    lat = sorted(r["latencia"] for r in ok)

    def pct(p):
        if not lat:
            return 0
        k = int(round((p / 100) * (len(lat) - 1)))
        return lat[k]

    if start:
        wall = (end or time.time()) - start
    else:
        wall = 0
    throughput = (len(results) / wall) if wall > 0 else 0

    return {
        "running": running,
        "completadas": len(results),
        "total_expected": total_expected,
        "exitosas": len(ok),
        "errores": len(fail),
        "tasa_exito": round(100 * len(ok) / len(results), 1) if results else 0,
        "wall": round(wall, 2),
        "throughput": round(throughput, 2),
        "lat_avg": round(mean(lat), 3) if lat else 0,
        "lat_min": round(min(lat), 3) if lat else 0,
        "lat_max": round(max(lat), 3) if lat else 0,
        "lat_p50": round(pct(50), 3),
        "lat_p90": round(pct(90), 3),
        "lat_p95": round(pct(95), 3),
        "sample_response": sample,
        # ultimas 60 filas para la tabla
        "ultimas": [
            {
                "user": r["user"],
                "iteracion": r["iteracion"],
                "status": r["status"],
                "latencia": round(r["latencia"], 3),
                "ok": r["ok"],
                "error": r["error"],
                "chatId": r["chatId"][:8],
            }
            for r in results[-60:]
        ],
    }


# ---------------------------------------------------------------------------
# Rutas HTTP
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.route("/config")
def get_config():
    return jsonify(DEFAULT_CONFIG)


@app.route("/run", methods=["POST"])
def run():
    with LOCK:
        if STATE["running"]:
            return jsonify({"error": "Ya hay una prueba en curso"}), 409

    body = request.get_json(force=True)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({
        "url": body.get("url", DEFAULT_CONFIG["url"]).strip(),
        "num_users": int(body.get("num_users", 10)),
        "loops": int(body.get("loops", 1)),
        "ramp_up": float(body.get("ramp_up", 0.15)),
        "timeout": float(body.get("timeout", 30)),
        "mensaje": body.get("mensaje", DEFAULT_CONFIG["mensaje"]),
        "nombreAlumno": body.get("nombreAlumno", DEFAULT_CONFIG["nombreAlumno"]),
        "migasJson": body.get("migasJson", DEFAULT_CONFIG["migasJson"]),
        "chat_mode": body.get("chat_mode", "unique"),
        "chatId_fijo": body.get("chatId_fijo", DEFAULT_CONFIG["chatId_fijo"]),
    })

    threading.Thread(target=ejecutar_prueba, args=(cfg,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    resp = jsonify(calcular_stats())
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/stop", methods=["POST"])
def stop():
    with LOCK:
        STATE["stop"] = True
    return jsonify({"ok": True})


@app.route("/export")
def export():
    with LOCK:
        data = json.dumps(STATE["results"], indent=2, default=str)
    return Response(
        data, mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=resultados.json"},
    )


# ---------------------------------------------------------------------------
# Observabilidad del agente (Serenity Star) - 1 consulta CONTROLADA
# ---------------------------------------------------------------------------
# Contrato verificado (repo VersusGroup/serenity-benchmark): endpoint /execute,
# body = array {Key,Value}, auth X-API-KEY. Aqui mandamos stream=false para
# recibir UNA respuesta JSON unica (una peticion por pulsacion, sin bucles).
SERENITY_BASE_URL = "https://api.serenitystar.ai/api"
DEFAULT_AGENT_CODE = "AgenteOrquestaPNEB"

# Registro persistente de consultas (sobrevive a reinicios del servidor).
PROBES_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registro_consultas.jsonl")

# Columnas del export, en orden.
PROBE_FIELDS = [
    "ts", "label", "agent_code", "agent_version", "model", "status",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "cost_total", "currency", "latency_s", "executors",
    "instance_id", "question", "response", "error",
]


def _pick(d, *names):
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return None


def _find_model(data):
    """Busca el nombre del modelo en la respuesta (best-effort: campos con 'model')."""
    if not isinstance(data, dict):
        return ""
    for k, v in data.items():
        if "model" in k.lower() and isinstance(v, str) and v.strip():
            return v.strip()
    for v in data.values():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if "model" in k2.lower() and isinstance(v2, str) and v2.strip():
                    return v2.strip()
    return ""


def _csv_cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def _load_probes():
    """Carga el registro del disco al arrancar."""
    try:
        with open(PROBES_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    STATE["probes"].append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass


def _record_probe(rec):
    """Guarda un registro en memoria y lo anexa al fichero (append-only)."""
    with LOCK:
        STATE["probes"].append(rec)
    try:
        with open(PROBES_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


_load_probes()

# Banco de preguntas del repo (generado en preguntas_banco.json). Solo lectura.
PRESETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preguntas_banco.json")


def _load_presets():
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


PRESETS = _load_presets()


@app.route("/presets")
def presets_list():
    """Banco de preguntas para el desplegable (id, group, question, seccion)."""
    return jsonify(PRESETS)


@app.route("/agent-execute", methods=["POST"])
def agent_execute():
    """Envia UNA sola consulta real al agente, mide su consumo y la registra.

    Controlado: una peticion por pulsacion, sin concurrencia ni repeticion.
    La API key solo se usa para esta llamada; no se guarda, ni se registra.
    """
    body = request.get_json(force=True, silent=True) or {}
    api_key = (body.get("api_key") or "").strip() or os.environ.get("SERENITY_API_KEY", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Falta la API key (campo del panel o variable SERENITY_API_KEY)."}), 400

    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Escribe un mensaje para enviar al agente."}), 400

    agent_code = (body.get("agent_code") or DEFAULT_AGENT_CODE).strip()
    agent_version = (body.get("agent_version") or "").strip()
    label = (body.get("label") or "").strip()
    version_suffix = f"/{agent_version}" if agent_version else ""
    url = f"{SERENITY_BASE_URL}/v2/agent/{agent_code}/execute{version_suffix}"

    # Body en formato array {Key,Value}. stream=false -> una unica respuesta JSON.
    payload = [
        {"Key": "userIdentifier", "Value": (body.get("userIdentifier") or "observabilidad-manual@versuselearning.com").strip()},
        {"Key": "stream", "Value": False},
        {"Key": "channel", "Value": body.get("channel") or "PN EB"},
        {"Key": "message", "Value": message},
        {"Key": "usuario", "Value": (body.get("usuario") or "OBSERVABILIDAD MANUAL").strip()},
        {"Key": "seccion", "Value": body.get("seccion") or ""},
        {"Key": "meta", "Value": body.get("meta") or "1-uno"},
        {"Key": "grupo", "Value": body.get("grupo") or "2-dos"},
    ]
    chat_id = (body.get("chatId") or "").strip()
    if chat_id:
        payload.append({"Key": "chatId", "Value": chat_id})

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": api_key,
        "Accept": "application/json, text/plain, */*",
    }

    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "label": label,
        "agent_code": agent_code,
        "agent_version": agent_version or "publicada",
        "model": "",
        "status": "error",
        "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
        "cost_total": None, "currency": "",
        "latency_s": None, "executors": [],
        "instance_id": None, "question": message, "response": "", "error": None,
    }

    t0 = time.time()
    try:
        # (connect=15s, read=120s): el agente (RAG + LLM) puede tardar.
        r = requests.post(url, json=payload, headers=headers, timeout=(15, 120))
    except requests.exceptions.Timeout:
        rec["error"] = "TIMEOUT: el agente no respondió a tiempo (120 s)."
        rec["latency_s"] = round(time.time() - t0, 2)
        _record_probe(rec)
        return jsonify({"ok": False, "error": rec["error"]}), 504
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        rec["latency_s"] = round(time.time() - t0, 2)
        _record_probe(rec)
        return jsonify({"ok": False, "error": rec["error"]}), 502
    rec["latency_s"] = round(time.time() - t0, 2)

    raw = r.text or ""
    if not (200 <= r.status_code < 300):
        msg = {
            401: "API key inválida o no autorizada (401).",
            403: "Sin permiso para este agente (403).",
            404: "Agente o versión no encontrados (404) — revisa el agentCode/versión.",
        }.get(r.status_code, f"El agente devolvió HTTP {r.status_code}.")
        rec["error"] = msg
        _record_probe(rec)
        return jsonify({"ok": False, "status": r.status_code, "error": msg, "raw": raw[:500]}), 502

    # El result puede venir como {"result": {...}} o como el objeto directo.
    data = {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            data = parsed.get("result") if isinstance(parsed.get("result"), dict) else parsed
    except Exception:
        data = {}

    usage = _pick(data, "completionUsage", "completion_usage") or {}
    cost = _pick(data, "cost")
    cost = cost if isinstance(cost, dict) else {}
    actions = _pick(data, "actionResults", "action_results")
    executors = sorted(actions.keys()) if isinstance(actions, dict) else []
    content = str(_pick(data, "content", "response") or "")

    rec.update({
        "model": _find_model(data),
        "status": "ok",
        "prompt_tokens": _pick(usage, "promptTokens", "prompt_tokens"),
        "completion_tokens": _pick(usage, "completionTokens", "completion_tokens"),
        "total_tokens": _pick(usage, "totalTokens", "total_tokens"),
        "cost_total": cost.get("total"),
        "currency": cost.get("currency") or "",
        "executors": executors,
        "instance_id": _pick(data, "instanceId", "instance_id"),
        "response": content[:4000],
    })
    _record_probe(rec)

    return jsonify({
        "ok": True,
        "status": r.status_code,
        "latency_s": rec["latency_s"],
        "content": content[:4000],
        "instance_id": rec["instance_id"],
        "model": rec["model"],
        "prompt_tokens": rec["prompt_tokens"],
        "completion_tokens": rec["completion_tokens"],
        "total_tokens": rec["total_tokens"],
        "cost_total": rec["cost_total"],
        "currency": rec["currency"],
        "executors": executors,
        "raw": raw[:600],
    })


@app.route("/probes")
def probes_list():
    """Ultimas consultas registradas, para pintar la tabla del panel."""
    with LOCK:
        data = list(STATE["probes"][-200:])
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/probes/export.json")
def probes_export_json():
    with LOCK:
        data = json.dumps(STATE["probes"], indent=2, ensure_ascii=False, default=str)
    return Response(
        data, mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=registro_consultas.json"},
    )


@app.route("/probes/export.csv")
def probes_export_csv():
    with LOCK:
        rows = list(STATE["probes"])
    out = io.StringIO()
    out.write("﻿")  # BOM: Excel (es) abre bien el UTF-8
    w = csv.writer(out, delimiter=";", lineterminator="\n")
    w.writerow(PROBE_FIELDS)
    for p in rows:
        w.writerow([_csv_cell(p.get(f)) for f in PROBE_FIELDS])
    return Response(
        out.getvalue(), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=registro_consultas.csv"},
    )


@app.route("/probes/summary")
def probes_summary():
    """Comparativa agrupada por Etiqueta: percentiles de latencia + tokens/coste.

    Percentil por rango mas cercano (sin interpolar), como en la prueba de carga.
    Solo cuenta las consultas OK para latencia/tokens; los errores se cuentan aparte.
    """
    with LOCK:
        rows = list(STATE["probes"])

    def pct(vals, p):
        if not vals:
            return None
        k = int(round((p / 100) * (len(vals) - 1)))
        return vals[k]

    groups = {}
    for p in rows:
        key = (p.get("label") or "").strip() or "(sin etiqueta)"
        g = groups.setdefault(key, {"ok": [], "errors": 0, "cost_total": 0.0,
                                    "tokens_total": 0, "currency": ""})
        if p.get("status") == "ok":
            g["ok"].append(p)
            if isinstance(p.get("cost_total"), (int, float)):
                g["cost_total"] += p["cost_total"]
            if isinstance(p.get("total_tokens"), (int, float)):
                g["tokens_total"] += p["total_tokens"]
            if p.get("currency"):
                g["currency"] = p["currency"]
        else:
            g["errors"] += 1

    out = []
    for key, g in groups.items():
        oks = g["ok"]
        lats = sorted(x["latency_s"] for x in oks if isinstance(x.get("latency_s"), (int, float)))
        toks = [x["total_tokens"] for x in oks if isinstance(x.get("total_tokens"), (int, float))]
        out.append({
            "label": key,
            "count": len(oks),
            "errors": g["errors"],
            "lat_avg": round(mean(lats), 2) if lats else None,
            "lat_min": round(min(lats), 2) if lats else None,
            "lat_max": round(max(lats), 2) if lats else None,
            "lat_p50": round(pct(lats, 50), 2) if lats else None,
            "lat_p90": round(pct(lats, 90), 2) if lats else None,
            "lat_p95": round(pct(lats, 95), 2) if lats else None,
            "tokens_avg": round(mean(toks)) if toks else None,
            "tokens_total": g["tokens_total"],
            "cost_total": round(g["cost_total"], 4),
            "currency": g["currency"],
        })
    out.sort(key=lambda r: r["label"].lower())
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Frontend (una sola pagina)
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prueba de carga · Chatbot Prefortia</title>
<style>
  :root{
    --bg:#0e1116; --panel:#161b22; --panel2:#1c2230; --line:#2b3444;
    --ink:#e6edf3; --dim:#8b98a8; --acc:#4cc9f0; --ok:#3fb950; --err:#f85149;
    --warn:#e3b341; --mono:'SFMono-Regular',ui-monospace,'Cascadia Code',Consolas,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px}
  header{padding:20px 28px;border-bottom:1px solid var(--line);
    display:flex;align-items:baseline;gap:14px}
  header h1{font-size:17px;margin:0;font-weight:650;letter-spacing:.2px}
  header .tag{font-family:var(--mono);font-size:11px;color:var(--dim);
    border:1px solid var(--line);padding:2px 8px;border-radius:20px}
  .wrap{display:grid;grid-template-columns:340px 1fr;gap:0;min-height:calc(100vh - 61px)}
  .side{border-right:1px solid var(--line);padding:22px;overflow-y:auto}
  .main{padding:22px 28px;overflow-y:auto}
  label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.6px;
    color:var(--dim);margin:14px 0 5px}
  input,textarea,select{width:100%;background:var(--panel);border:1px solid var(--line);
    color:var(--ink);border-radius:7px;padding:8px 10px;font-size:13px;font-family:inherit}
  input:focus,textarea:focus,select:focus{outline:none;border-color:var(--acc)}
  textarea{resize:vertical;min-height:52px;font-family:var(--mono);font-size:12px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .btns{display:flex;gap:10px;margin-top:20px}
  button{flex:1;border:none;border-radius:8px;padding:12px;font-size:14px;
    font-weight:600;cursor:pointer;transition:.15s}
  .run{background:var(--acc);color:#04222e}
  .run:hover{filter:brightness(1.1)}
  .run:disabled{opacity:.4;cursor:not-allowed}
  .stop{background:transparent;color:var(--err);border:1px solid var(--err)}
  .stop:hover{background:var(--err);color:#fff}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .card .k{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
  .card .v{font-size:26px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
  .card .v small{font-size:13px;color:var(--dim);font-weight:400}
  .v.ok{color:var(--ok)} .v.err{color:var(--err)} .v.acc{color:var(--acc)}
  .bar{height:6px;background:var(--panel2);border-radius:4px;overflow:hidden;margin-bottom:18px}
  .bar>span{display:block;height:100%;background:var(--acc);width:0;transition:width .3s}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px}
  .box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
  .box h3{margin:0 0 12px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
  .lat{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);
    font-variant-numeric:tabular-nums}
  .lat:last-child{border:none}
  .lat b{font-family:var(--mono)}
  table{width:100%;border-collapse:collapse;font-size:12px;font-family:var(--mono)}
  th{text-align:left;color:var(--dim);font-weight:500;padding:6px 8px;
    border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel);
    text-transform:uppercase;font-size:10px;letter-spacing:.5px}
  td{padding:5px 8px;border-bottom:1px solid var(--panel2)}
  .tblwrap{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:8px 8px 4px;max-height:340px;overflow-y:auto}
  .pill{padding:1px 7px;border-radius:20px;font-size:11px;font-weight:600}
  .pill.ok{background:rgba(63,185,80,.15);color:var(--ok)}
  .pill.err{background:rgba(248,81,73,.15);color:var(--err)}
  .resp{background:#0a0d12;border:1px solid var(--line);border-radius:8px;padding:12px;
    font-family:var(--mono);font-size:11px;color:var(--dim);white-space:pre-wrap;
    word-break:break-word;max-height:200px;overflow-y:auto;margin-top:4px}
  .exp{color:var(--acc);font-size:12px;text-decoration:none;font-family:var(--mono)}
  .status-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px}
  .idle{background:var(--dim)} .live{background:var(--ok);animation:pulse 1.2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .hint{font-size:11px;color:var(--dim);margin-top:5px;line-height:1.4}
</style>
</head>
<body>
<header>
  <h1>Prueba de carga · Chatbot</h1>
  <span class="tag" id="statustag"><span class="status-dot idle"></span>en espera</span>
</header>

<div class="tabs" style="display:flex;border-bottom:1px solid var(--line);padding:0 28px;background:var(--panel)">
  <button id="tabbtn-carga" onclick="showTab('carga')" style="flex:0 0 auto;border:none;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:var(--ink);font-weight:600;padding:12px 20px;cursor:pointer">Prueba de carga</button>
  <button id="tabbtn-obs" onclick="showTab('obs')" style="flex:0 0 auto;border:none;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:var(--ink);font-weight:600;padding:12px 20px;cursor:pointer">Observabilidad del agente</button>
</div>

<div id="tab-carga" style="display:none">
<div class="wrap">
  <!-- Panel de configuracion -->
  <aside class="side">
    <label>Endpoint (URL)</label>
    <input id="url">

    <div class="row3">
      <div><label>Usuarios</label><input id="num_users" type="number" min="1" value="10"></div>
      <div><label>Mensajes c/u</label><input id="loops" type="number" min="1" value="1"></div>
      <div><label>Timeout (s)</label><input id="timeout" type="number" min="1" value="30"></div>
    </div>

    <label>Ramp-up entre usuarios (s)</label>
    <input id="ramp_up" type="number" step="0.05" min="0" value="0.15">
    <div class="hint">0 = todos a la vez (pico máximo). 0.15 = arranque escalonado más realista.</div>

    <label>Mensaje a enviar</label>
    <textarea id="mensaje">¿Qué puedes hacer?</textarea>

    <div class="row2">
      <div><label>Nombre alumno</label><input id="nombreAlumno" value="Pablo"></div>
      <div>
        <label>Modo chatId</label>
        <select id="chat_mode">
          <option value="unique">Único por usuario</option>
          <option value="shared">Compartido (uno fijo)</option>
        </select>
      </div>
    </div>

    <label>migasJson</label>
    <textarea id="migasJson">["curso pre-ingreso 133º","aula-virtual","escritorio"]</textarea>

    <div class="btns">
      <button class="run" id="runBtn" onclick="runTest()">▶ Ejecutar</button>
      <button class="stop" id="stopBtn" onclick="stopTest()" disabled>■ Parar</button>
    </div>
  </aside>

  <!-- Panel de resultados -->
  <main class="main">
    <div class="cards">
      <div class="card"><div class="k">Completadas</div>
        <div class="v acc" id="c_done">0 <small id="c_total">/ 0</small></div></div>
      <div class="card"><div class="k">Exitosas</div>
        <div class="v ok" id="c_ok">0</div></div>
      <div class="card"><div class="k">Errores</div>
        <div class="v err" id="c_err">0</div></div>
      <div class="card"><div class="k">Tasa éxito</div>
        <div class="v" id="c_rate">—</div></div>
    </div>

    <div class="bar"><span id="progbar"></span></div>

    <div class="grid2">
      <div class="box">
        <h3>Latencia (respuestas OK)</h3>
        <div class="lat"><span>Media</span><b id="l_avg">—</b></div>
        <div class="lat"><span>Mínima</span><b id="l_min">—</b></div>
        <div class="lat"><span>Máxima</span><b id="l_max">—</b></div>
        <div class="lat"><span>p50 (mediana)</span><b id="l_p50">—</b></div>
        <div class="lat"><span>p90</span><b id="l_p90">—</b></div>
        <div class="lat"><span>p95</span><b id="l_p95">—</b></div>
      </div>
      <div class="box">
        <h3>Rendimiento</h3>
        <div class="lat"><span>Duración total</span><b id="r_wall">—</b></div>
        <div class="lat"><span>Peticiones / seg</span><b id="r_tput">—</b></div>
        <div class="lat" style="margin-top:14px;border:none">
          <a class="exp" href="/export" id="expLink" style="display:none">⤓ Descargar resultados (JSON)</a>
        </div>
        <h3 style="margin-top:18px">Respuesta de ejemplo del bot</h3>
        <div class="resp" id="sample">Aún sin respuesta…</div>
      </div>
    </div>

    <div class="box" style="padding:16px 16px 8px">
      <h3>Peticiones (últimas 60) · prueba de estrés</h3>
      <div class="tblwrap">
        <table>
          <thead><tr><th>User</th><th>Iter</th><th>Status</th><th>Latencia</th><th>ChatId</th><th>Detalle</th></tr></thead>
          <tbody id="tbody"><tr><td colspan="6" style="color:var(--dim);padding:16px">Sin datos todavía.</td></tr></tbody>
        </table>
      </div>
    </div>
  </main>
</div>
</div><!-- /tab-carga -->

<div id="tab-obs">
  <main class="main">
    <!-- ==== Observabilidad del agente (Serenity Star) — 1 consulta controlada ==== -->
    <div class="box" style="margin-bottom:18px">
      <h3>Observabilidad del agente · Serenity (1 consulta)</h3>
      <div class="hint" style="margin-bottom:10px">
        Envía <b>UNA sola consulta real</b> al agente y mide su consumo: tokens, coste (€),
        latencia y sub-agentes. Una petición por pulsación — sin cargas masivas.
        ⚠️ Cada envío es una <b>llamada real y facturable</b> al agente de producción.
      </div>
      <div class="row2">
        <div>
          <label>API key (X-API-KEY)</label>
          <input id="ag_key" type="password" autocomplete="off" placeholder="Solo en memoria; no se guarda">
        </div>
        <div>
          <label>Agent code</label>
          <input id="ag_code" value="AgenteOrquestaPNEB">
        </div>
      </div>
      <label>Etiqueta (modelo / agente)</label>
      <input id="ag_label" placeholder="p. ej. Qwen 3.8 / Agente X">
      <label>Banco de preguntas (repo de tu compañero)</label>
      <select id="ag_preset" onchange="applyPreset()">
        <option value="">— Elige una pregunta del banco —</option>
      </select>
      <div class="hint" style="margin-top:5px">Al elegir se rellenan solos el Mensaje y la Sección. Agrupadas por el sub-agente al que suelen ir.</div>
      <label>Sección / contexto (opcional)</label>
      <input id="ag_seccion" placeholder="p. ej. curso… &gt; temario &gt; tema 1">
      <label>Mensaje al agente</label>
      <textarea id="ag_message" style="min-height:64px" placeholder="Escribe la pregunta…">¿Qué puedes hacer?</textarea>
      <div style="display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap">
        <button class="run" id="ag_btn" style="flex:0 0 auto;padding:10px 18px" onclick="runAgentProbe()">▶ Enviar 1 consulta y medir</button>
        <span id="ag_state" class="hint" style="margin:0"></span>
      </div>
      <div class="cards" style="margin-top:16px;margin-bottom:0;grid-template-columns:repeat(4,1fr)">
        <div class="card"><div class="k">Tokens total</div><div class="v acc" id="ag_tokens">—</div></div>
        <div class="card"><div class="k">Coste</div><div class="v" id="ag_cost">—</div></div>
        <div class="card"><div class="k">Latencia</div><div class="v" id="ag_lat">—</div></div>
        <div class="card"><div class="k">Sub-agentes</div><div class="v" id="ag_exec" style="font-size:14px">—</div></div>
      </div>
      <div class="hint" id="ag_extra" style="margin-top:8px"></div>
      <h3 style="margin-top:14px">Respuesta del agente</h3>
      <div class="resp" id="ag_resp">—</div>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:18px;flex-wrap:wrap;gap:8px">
        <h3 style="margin:0">Registro de consultas (persistente)</h3>
        <span>
          <a class="exp" href="/probes/export.json">⤓ JSON</a>
          &nbsp;·&nbsp;
          <a class="exp" href="/probes/export.csv">⤓ Excel (CSV)</a>
        </span>
      </div>
      <div class="tblwrap" style="margin-top:8px;overflow-x:auto">
        <table>
          <thead><tr>
            <th>Hora</th><th>Etiqueta</th><th>Agente</th><th>Modelo</th><th>Pregunta</th>
            <th>Sub-agentes</th><th>Tokens</th><th>Coste</th><th>Latencia</th><th>Estado</th>
          </tr></thead>
          <tbody id="pbody"><tr><td colspan="10" style="color:var(--dim);padding:16px">Sin consultas todavía.</td></tr></tbody>
        </table>
      </div>
    </div>

    <!-- ==== Comparativa por etiqueta (percentiles) ==== -->
    <div class="box" style="margin-bottom:18px">
      <h3>Comparativa por etiqueta (percentiles de latencia)</h3>
      <div class="hint" style="margin-bottom:8px">
        Agrupa las consultas del agente por <b>Etiqueta</b>. Los percentiles necesitan
        <b>varias consultas por modelo</b> para ser fiables (con 1-2 muestras solo verás los propios valores).
      </div>
      <div class="tblwrap" style="overflow-x:auto">
        <table>
          <thead><tr>
            <th>Etiqueta</th><th>nº (ok)</th><th>Errores</th>
            <th>Lat media</th><th>p50</th><th>p90</th><th>p95</th><th>Lat mín</th><th>Lat máx</th>
            <th>Tokens medios</th><th>Coste total</th>
          </tr></thead>
          <tbody id="sbody"><tr><td colspan="11" style="color:var(--dim);padding:16px">Sin datos todavía.</td></tr></tbody>
        </table>
      </div>
    </div>
  </main>
</div>

<script>
let poll = null;

async function loadDefaults(){
  const c = await (await fetch('/config')).json();
  url.value=c.url; num_users.value=c.num_users; loops.value=c.loops;
  timeout.value=c.timeout; ramp_up.value=c.ramp_up; mensaje.value=c.mensaje;
  nombreAlumno.value=c.nombreAlumno; migasJson.value=c.migasJson;
}
loadDefaults();
update();  // Al abrir/recargar, pinta el resultado de la ultima prueba si la hay
renderProbes();  // y el registro de consultas al agente
renderSummary();  // y la comparativa por etiqueta / percentiles
loadPresets();  // y el banco de preguntas en el desplegable
showTab('obs');  // vista inicial: Observabilidad del agente

async function runTest(){
  const body = {
    url:url.value, num_users:+num_users.value, loops:+loops.value,
    timeout:+timeout.value, ramp_up:+ramp_up.value, mensaje:mensaje.value,
    nombreAlumno:nombreAlumno.value, migasJson:migasJson.value,
    chat_mode:chat_mode.value
  };
  const r = await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  if(r.status===409){alert('Ya hay una prueba en curso');return;}
  runBtn.disabled=true; stopBtn.disabled=false;
  setTag(true);
  if(poll) clearInterval(poll);
  poll = setInterval(update, 500);
  update();
}

async function stopTest(){ await fetch('/stop',{method:'POST'}); }

function setTag(live){
  statustag.innerHTML = live
    ? '<span class="status-dot live"></span>ejecutando'
    : '<span class="status-dot idle"></span>en espera';
}

function fmt(s){ return s.toFixed(3)+'s'; }

async function update(){
  let s;
  try {
    s = await (await fetch('/status?t=' + Date.now(), {cache:'no-store'})).json();
  } catch(e) {
    console.warn('fallo al consultar estado, reintentando...', e);
    return;  // el siguiente ciclo del sondeo reintenta; no congela la pantalla
  }
  // Se reconstruye la tarjeta entera de una vez: escribir solo el numero
  // borraba el <small id="c_total"> anidado y hacia que el resto del refresco fallara.
  c_done.innerHTML = s.completadas + ' <small id="c_total">/ ' + s.total_expected + '</small>';
  c_ok.textContent = s.exitosas;
  c_err.textContent = s.errores;
  c_rate.textContent = s.completadas? s.tasa_exito+'%' : '—';
  c_rate.className = 'v ' + (s.tasa_exito>=99?'ok':s.tasa_exito>=80?'':'err');

  const pct = s.total_expected? Math.round(100*s.completadas/s.total_expected):0;
  progbar.style.width = pct+'%';

  l_avg.textContent=s.lat_avg?fmt(s.lat_avg):'—';
  l_min.textContent=s.lat_min?fmt(s.lat_min):'—';
  l_max.textContent=s.lat_max?fmt(s.lat_max):'—';
  l_p50.textContent=s.lat_p50?fmt(s.lat_p50):'—';
  l_p90.textContent=s.lat_p90?fmt(s.lat_p90):'—';
  l_p95.textContent=s.lat_p95?fmt(s.lat_p95):'—';
  r_wall.textContent=s.wall?s.wall+'s':'—';
  r_tput.textContent=s.throughput?s.throughput+' req/s':'—';

  if(s.sample_response){ sample.textContent = s.sample_response; }

  if(s.ultimas.length){
    tbody.innerHTML = s.ultimas.slice().reverse().map(r=>{
      const pill = r.ok ? '<span class="pill ok">'+r.status+'</span>'
                        : '<span class="pill err">'+(r.status||'ERR')+'</span>';
      return `<tr><td>${r.user}</td><td>${r.iteracion}</td><td>${pill}</td>
        <td>${r.latencia}s</td><td>${r.chatId}…</td>
        <td style="color:var(--dim)">${r.error||''}</td></tr>`;
    }).join('');
    expLink.style.display='inline';
  }

  if(!s.running){
    if(poll){ clearInterval(poll); poll=null; }
    runBtn.disabled=false; stopBtn.disabled=true;
    setTag(false);
  } else if(!poll){
    // Hay una prueba en curso pero no estabamos sondeando (p.ej. tras recargar)
    runBtn.disabled=true; stopBtn.disabled=false;
    setTag(true);
    poll = setInterval(update, 500);
  }
}

async function runAgentProbe(){
  const key = ag_key.value.trim();
  const msg = ag_message.value.trim();
  if(!msg){ ag_state.textContent = '⚠ Escribe un mensaje.'; return; }
  ag_btn.disabled = true;
  ag_state.textContent = 'Consultando al agente… (puede tardar unos segundos)';
  ag_tokens.textContent='—'; ag_cost.textContent='—'; ag_lat.textContent='—'; ag_exec.textContent='—';
  ag_extra.textContent=''; ag_resp.textContent='—';

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 130000);  // corta a los 130 s: nunca se cuelga
  try {
    const r = await fetch('/agent-execute', {
      method:'POST', headers:{'Content-Type':'application/json'}, signal: ctrl.signal,
      body: JSON.stringify({
        api_key:key, message:msg, agent_code:ag_code.value.trim(),
        seccion:ag_seccion.value, label:ag_label.value.trim()
      })
    });
    const d = await r.json();
    if(!d.ok){
      ag_state.textContent = '⚠ ' + (d.error || ('Error HTTP ' + r.status));
      if(d.raw){ ag_resp.textContent = d.raw; }
      return;
    }
    ag_state.textContent = '✓ Listo';
    ag_tokens.textContent = (d.total_tokens != null) ? d.total_tokens : '—';
    ag_cost.textContent   = (d.cost_total != null) ? (Number(d.cost_total).toFixed(4) + ' ' + (d.currency||'')) : '—';
    ag_lat.textContent    = (d.latency_s != null) ? (d.latency_s + 's') : '—';
    ag_exec.textContent   = (d.executors && d.executors.length) ? d.executors.join(', ') : '(orquestador solo)';
    ag_extra.textContent  = 'Prompt: ' + (d.prompt_tokens ?? '?') + ' · Completion: ' + (d.completion_tokens ?? '?') + ' · instance_id: ' + (d.instance_id || '—');
    ag_resp.textContent   = d.content || '(sin contenido)';
  } catch(e){
    ag_state.textContent = (e.name === 'AbortError')
      ? '⚠ Cancelado: el agente tardó más de 130 s.'
      : '⚠ Error de red: ' + e.message;
  } finally {
    clearTimeout(timer);
    ag_btn.disabled = false;
    renderProbes();   // refresca el registro (incluye la consulta recien hecha)
    renderSummary();  // y la comparativa por etiqueta / percentiles
  }
}

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function renderProbes(){
  let list;
  try { list = await (await fetch('/probes?t=' + Date.now(), {cache:'no-store'})).json(); }
  catch(e){ return; }
  if(!Array.isArray(list) || !list.length) return;
  pbody.innerHTML = list.slice().reverse().map(p => {
    const st = (p.status === 'ok') ? '<span class="pill ok">ok</span>' : '<span class="pill err">error</span>';
    const q = String(p.question || '');
    const qshort = q.length > 60 ? q.slice(0,60) + '…' : q;
    const exec = (p.executors && p.executors.length) ? p.executors.join(', ')
               : (p.status === 'ok' ? '(orquestador solo)' : '—');
    const cost = (p.cost_total != null) ? (Number(p.cost_total).toFixed(4) + ' ' + (p.currency||'')) : '—';
    const agent = esc(p.agent_code) + ((p.agent_version && p.agent_version !== 'publicada') ? (' v' + esc(p.agent_version)) : '');
    return '<tr>'
      + '<td>' + esc(p.ts) + '</td>'
      + '<td>' + esc(p.label) + '</td>'
      + '<td>' + agent + '</td>'
      + '<td>' + esc(p.model) + '</td>'
      + '<td title="' + esc(q) + '">' + esc(qshort) + '</td>'
      + '<td>' + esc(exec) + '</td>'
      + '<td>' + (p.total_tokens != null ? p.total_tokens : '—') + '</td>'
      + '<td>' + cost + '</td>'
      + '<td>' + (p.latency_s != null ? p.latency_s + 's' : '—') + '</td>'
      + '<td>' + st + '</td>'
      + '</tr>';
  }).join('');
}

async function renderSummary(){
  let list;
  try { list = await (await fetch('/probes/summary?t=' + Date.now(), {cache:'no-store'})).json(); }
  catch(e){ return; }
  const f = (v, suf) => (v == null ? '—' : (v + (suf || '')));
  if(!Array.isArray(list) || !list.length){
    sbody.innerHTML = '<tr><td colspan="11" style="color:var(--dim);padding:16px">Sin datos todavía.</td></tr>';
    return;
  }
  sbody.innerHTML = list.map(g => {
    const cost = (g.cost_total != null) ? (Number(g.cost_total).toFixed(4) + ' ' + (g.currency||'')) : '—';
    return '<tr>'
      + '<td>' + esc(g.label) + '</td>'
      + '<td>' + g.count + '</td>'
      + '<td>' + g.errors + '</td>'
      + '<td>' + f(g.lat_avg,'s') + '</td>'
      + '<td>' + f(g.lat_p50,'s') + '</td>'
      + '<td>' + f(g.lat_p90,'s') + '</td>'
      + '<td>' + f(g.lat_p95,'s') + '</td>'
      + '<td>' + f(g.lat_min,'s') + '</td>'
      + '<td>' + f(g.lat_max,'s') + '</td>'
      + '<td>' + f(g.tokens_avg) + '</td>'
      + '<td>' + cost + '</td>'
      + '</tr>';
  }).join('');
}

let PRESETS = {};
async function loadPresets(){
  let list;
  try { list = await (await fetch('/presets')).json(); }
  catch(e){ return; }
  if(!Array.isArray(list) || !list.length) return;
  const groups = {
    temario: 'Temario (→ TemarioJurispolEB)',
    test: 'Test / doctrina (→ iaTestPNEB o temario)',
    soporte: 'Soporte (→ SoportePNEB)',
    otros: 'Otros'
  };
  const byGroup = {};
  list.forEach(p => { PRESETS[p.id] = p; (byGroup[p.group] = byGroup[p.group] || []).push(p); });
  let html = '<option value="">— Elige una pregunta del banco —</option>';
  Object.keys(groups).forEach(g => {
    if(!byGroup[g]) return;
    html += '<optgroup label="' + esc(groups[g]) + '">';
    byGroup[g].forEach(p => { html += '<option value="' + esc(p.id) + '">' + esc(p.question) + '</option>'; });
    html += '</optgroup>';
  });
  ag_preset.innerHTML = html;
}

function applyPreset(){
  const p = PRESETS[ag_preset.value];
  if(!p) return;
  ag_message.value = p.question || '';
  ag_seccion.value = p.seccion || '';
}

function showTab(name){
  const carga = (name === 'carga');
  document.getElementById('tab-carga').style.display = carga ? '' : 'none';
  document.getElementById('tab-obs').style.display   = carga ? 'none' : '';
  const bc = document.getElementById('tabbtn-carga');
  const bo = document.getElementById('tabbtn-obs');
  bc.style.borderBottomColor = carga ? 'var(--acc)' : 'transparent';
  bc.style.color             = carga ? 'var(--acc)' : 'var(--ink)';
  bo.style.borderBottomColor = carga ? 'transparent' : 'var(--acc)';
  bo.style.color             = carga ? 'var(--ink)' : 'var(--acc)';
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    print("\n  Dashboard de prueba de carga")
    print("  -> Abre: http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
