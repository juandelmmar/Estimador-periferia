"""
Backend - Estimador de Proyectos con IA (v2.0)
===============================================
Cliente: Periferia IT Group
Tecnologías: Flask + Groq (GPT-OSS 120B) + OpenPyXL
Autor: Juan Aragón

Cambios v2.0:
  - Modelo actualizado a openai/gpt-oss-120b (llama-3.3-70b fue deprecado)
  - Modelo configurable vía env var GROQ_MODEL (protege contra futuras deprecaciones)
  - Prompt de sistema mejorado con few-shot example y reglas más claras
  - JSON mode activado (response_format) para eliminar limpiezas manuales
  - Validación de campos robusta en el endpoint /api/estimar
  - Retry con fallback a modelo secundario si el primario falla
  - Logging estructurado
  - CORS habilitado para despliegues con frontend separado
"""

import os
import json
import logging
import tempfile
from datetime import datetime

import openpyxl
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from groq import Groq
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── App ──────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# ── Configuración ────────────────────────────────────────────────────
API_KEY = os.environ.get("GROQ_API_KEY")
MODELO_PRIMARIO = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MODELO_FALLBACK = os.environ.get("GROQ_MODEL_FALLBACK", "qwen/qwen3.6-27b")

client = Groq(api_key=API_KEY) if API_KEY else None

# ── System Prompt ────────────────────────────────────────────────────
PROMPT_SISTEMA = """Eres un líder técnico senior especializado en estimación de proyectos de software.
Tu objetivo es generar una estimación de horas justa, realista y profesional.

REGLAS:
1. Horas por actividad: mínimo 4, máximo 80. La mayoría caen entre 8 y 40.
2. Desglosa en actividades granulares y concretas (no genéricas como "desarrollo backend").
3. Los porcentajes de pruebas, entendimiento y riesgo deben reflejar la complejidad real del proyecto.
4. Infiere las tecnologías si no se mencionan explícitamente.
5. Responde ÚNICAMENTE con un objeto JSON válido. Sin markdown, sin texto adicional, sin explicaciones.

FORMATO JSON EXACTO:
{
  "cliente": "Nombre del cliente",
  "ingeniero": "Nombre del ingeniero",
  "backend": "Tecnologías backend",
  "frontend": "Tecnologías frontend",
  "base_datos": "Motor de base de datos",
  "cloud": "Proveedor cloud",
  "actividades": [
    {
      "actividad": "Nombre corto de la actividad",
      "descripcion": "Qué se hace en esta actividad (1-2 oraciones)",
      "funcionalidades": "Funcionalidades específicas que cubre",
      "horas": 16
    }
  ],
  "pruebas_pct": 15,
  "entendimiento_pct": 10,
  "riesgo_pct": 5,
  "notas": ["Nota relevante sobre supuestos o riesgos"]
}

EJEMPLO — Para un proyecto de API REST con autenticación y CRUD de productos:
{
  "cliente": "TechCorp",
  "ingeniero": "Ana López",
  "backend": "Node.js, Express",
  "frontend": "React",
  "base_datos": "PostgreSQL",
  "cloud": "AWS",
  "actividades": [
    {"actividad": "Diseño de arquitectura", "descripcion": "Definición de capas, esquema de BD y contratos de API", "funcionalidades": "ERD, OpenAPI spec, diagrama de componentes", "horas": 12},
    {"actividad": "Autenticación y autorización", "descripcion": "Implementación de registro, login y middleware JWT", "funcionalidades": "Registro, login, refresh token, roles", "horas": 20},
    {"actividad": "CRUD de productos", "descripcion": "Endpoints REST con validación y paginación", "funcionalidades": "Crear, leer, actualizar, eliminar, filtros, paginación", "horas": 16},
    {"actividad": "Frontend - UI de productos", "descripcion": "Componentes React con formularios y tabla de datos", "funcionalidades": "Listado, formulario, búsqueda, detalle", "horas": 24},
    {"actividad": "Despliegue e infraestructura", "descripcion": "Configuración de CI/CD y entorno cloud", "funcionalidades": "Docker, pipeline CI/CD, variables de entorno", "horas": 10}
  ],
  "pruebas_pct": 15,
  "entendimiento_pct": 10,
  "riesgo_pct": 5,
  "notas": ["Se asume base de datos nueva sin migración de datos legacy", "No incluye diseño UX/UI"]
}"""


def llamar_ia(prompt_usuario: str, modelo: str) -> dict:
    """Llama a Groq con el modelo indicado y retorna el JSON parseado."""
    completion = client.chat.completions.create(
        model=modelo,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt_usuario},
        ],
        temperature=0.2,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    raw = completion.choices[0].message.content
    return json.loads(raw)


# ── Rutas ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/estimar", methods=["POST"])
def estimar():
    if not client:
        return jsonify({"error": "GROQ_API_KEY no configurada en el servidor"}), 500

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    descripcion = (data.get("descripcion") or "").strip()
    cliente = (data.get("cliente") or "").strip()
    ingeniero = (data.get("ingeniero") or "").strip()

    if len(descripcion) < 20:
        return jsonify({"error": "La descripción debe tener al menos 20 caracteres"}), 400

    # Armar prompt de usuario
    partes = []
    if cliente:
        partes.append(f"Cliente: {cliente}")
    if ingeniero:
        partes.append(f"Ingeniero a cargo: {ingeniero}")
    partes.append(f"Descripción del proyecto:\n{descripcion}")
    prompt_usuario = "\n".join(partes)

    # Intentar con modelo primario, fallback al secundario
    for modelo in [MODELO_PRIMARIO, MODELO_FALLBACK]:
        try:
            log.info("Estimando con modelo: %s", modelo)
            parsed = llamar_ia(prompt_usuario, modelo)

            # Garantizar que los campos del usuario prevalecen
            if cliente:
                parsed["cliente"] = cliente
            if ingeniero:
                parsed["ingeniero"] = ingeniero

            # Validación mínima de la respuesta
            if "actividades" not in parsed or not parsed["actividades"]:
                log.warning("Respuesta sin actividades del modelo %s, intentando fallback", modelo)
                continue

            log.info(
                "Estimación OK — modelo=%s, actividades=%d",
                modelo,
                len(parsed["actividades"]),
            )
            return jsonify(parsed)

        except Exception as exc:
            log.error("Error con modelo %s: %s", modelo, exc)
            continue

    return jsonify({"error": "No se pudo generar la estimación. Intenta de nuevo."}), 500


@app.route("/api/descargar-excel", methods=["POST"])
def descargar_excel():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estimación"

    # ── Paleta de colores (Periferia brand) ──────────────────────────
    GREEN_DARK = "15601D"
    GREEN_MED = "1E7A28"
    GREEN_NEON = "6DFD8C"
    GREEN_LIGHT = "CCFFD6"
    GREEN_PALE = "F0FFF3"
    DARK = "212121"
    WHITE = "FFFFFF"
    GRAY_MID = "E0E0E0"
    ACCENT_TEAL = "1B5E20"

    def _fill(hex_color):
        return PatternFill("solid", fgColor=hex_color)

    def _font(bold=False, color="212121", size=10, italic=False):
        return Font(bold=bold, color=color, size=size, italic=italic, name="Calibri")

    def _border_thin(sides="all"):
        thin = Side(style="thin", color="CCCCCC")
        if sides == "all":
            return Border(left=thin, right=thin, top=thin, bottom=thin)
        if sides == "bottom":
            return Border(bottom=thin)
        return Border()

    def _align(h="left", v="center", wrap=False):
        return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

    # ── Anchos de columna ────────────────────────────────────────────
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 38
    ws.column_dimensions["D"].width = 38
    ws.column_dimensions["E"].width = 12

    def row_h(row, h):
        ws.row_dimensions[row].height = h

    # ═══════════════════════════════════════════
    # HEADER BANNER  (rows 1-5)
    # ═══════════════════════════════════════════
    for r in (1, 2, 3, 4, 5):
        row_h(r, {1: 8, 2: 48, 3: 22, 4: 22, 5: 10}[r])

    for r in range(1, 6):
        for c in range(1, 6):
            ws.cell(r, c).fill = _fill(GREEN_DARK)

    ws.merge_cells("B2:D2")
    c = ws["B2"]
    c.value = "PERIFERIA IT GROUP"
    c.font = Font(bold=True, color=GREEN_NEON, size=22, name="Calibri")
    c.alignment = _align("left", "center")
    c.fill = _fill(GREEN_DARK)
    ws["E2"].fill = _fill(GREEN_DARK)

    ws.merge_cells("B3:D3")
    c = ws["B3"]
    c.value = "Estimación Profesional de Proyectos de Software"
    c.font = Font(bold=False, color=GREEN_LIGHT, size=11, name="Calibri", italic=True)
    c.alignment = _align("left", "center")
    c.fill = _fill(GREEN_DARK)

    ws.merge_cells("B4:D4")
    c = ws["B4"]
    c.value = f"Generado el {datetime.now().strftime('%d/%m/%Y  %H:%M')}"
    c.font = Font(color="88BB99", size=9, name="Calibri")
    c.alignment = _align("left", "center")
    c.fill = _fill(GREEN_DARK)

    # ═══════════════════════════════════════════
    # INFORMACIÓN DEL PROYECTO  (rows 6-13)
    # ═══════════════════════════════════════════
    for r in range(6, 14):
        row_h(r, {6: 8, 7: 28, 13: 10}.get(r, 24))

    ws.merge_cells("B7:E7")
    c = ws["B7"]
    c.value = "  INFORMACIÓN DEL PROYECTO"
    c.font = Font(bold=True, color=WHITE, size=11, name="Calibri")
    c.fill = _fill(GREEN_MED)
    c.alignment = _align("left", "center")

    info_fields = [
        ("CLIENTE", data.get("cliente", "—")),
        ("INGENIERO", data.get("ingeniero", "—")),
        ("BACKEND", data.get("backend", "—")),
        ("FRONTEND", data.get("frontend", "—")),
        ("BASE DATOS", data.get("base_datos", "—")),
        ("CLOUD", data.get("cloud", "—")),
    ]

    for i, (label, value) in enumerate(info_fields):
        r = 8 + i
        lc = ws.cell(r, 2, value=f"  {label}")
        lc.font = Font(bold=True, color=WHITE, size=10, name="Calibri")
        lc.fill = _fill(ACCENT_TEAL)
        lc.alignment = _align("left", "center")
        lc.border = Border(bottom=Side(style="thin", color="2E8B40"))

        ws.merge_cells(f"C{r}:E{r}")
        vc = ws.cell(r, 3, value=f"  {value}")
        vc.font = Font(color=DARK, size=10, name="Calibri")
        vc.fill = _fill(GREEN_PALE if i % 2 == 0 else WHITE)
        vc.alignment = _align("left", "center")
        vc.border = Border(bottom=Side(style="thin", color=GRAY_MID))

    # ═══════════════════════════════════════════
    # STATS BOXES  (rows 14-18)
    # ═══════════════════════════════════════════
    for r, h in [(14, 8), (15, 32), (16, 20), (17, 20), (18, 8)]:
        row_h(r, h)

    actividades = data.get("actividades", [])
    total_dev = sum(a.get("horas", 0) for a in actividades)
    p_pruebas = data.get("pruebas_pct", 0) / 100
    p_ent = data.get("entendimiento_pct", 0) / 100
    p_riesgo = data.get("riesgo_pct", 0) / 100
    total_final = round(total_dev * (1 + p_pruebas + p_ent + p_riesgo), 1)

    # Stat box 1 - Horas Dev
    ws.merge_cells("B15:C15")
    c = ws["B15"]
    c.value = total_dev
    c.font = Font(bold=True, color=GREEN_NEON, size=28, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("center", "center")

    ws.merge_cells("B16:C16")
    c = ws["B16"]
    c.value = "HORAS DESARROLLO"
    c.font = Font(bold=True, color=GREEN_LIGHT, size=9, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("center", "center")

    ws.merge_cells("B17:C17")
    ws["B17"].fill = _fill(DARK)

    # Stat box 2 - Horas Total
    ws.merge_cells("D15:E15")
    c = ws["D15"]
    c.value = total_final
    c.font = Font(bold=True, color=GREEN_NEON, size=28, name="Calibri")
    c.fill = _fill(GREEN_DARK)
    c.alignment = _align("center", "center")

    ws.merge_cells("D16:E16")
    c = ws["D16"]
    c.value = "HORAS TOTAL PROYECTO"
    c.font = Font(bold=True, color=GREEN_LIGHT, size=9, name="Calibri")
    c.fill = _fill(GREEN_DARK)
    c.alignment = _align("center", "center")

    ws.merge_cells("D17:E17")
    ws["D17"].fill = _fill(GREEN_DARK)

    # ═══════════════════════════════════════════
    # ACTIVITIES TABLE
    # ═══════════════════════════════════════════
    row_h(19, 8)
    tbl_start = 20
    row_h(tbl_start, 28)

    ws.merge_cells(f"B{tbl_start}:E{tbl_start}")
    c = ws.cell(tbl_start, 2, value="  DESGLOSE DE ACTIVIDADES")
    c.font = Font(bold=True, color=WHITE, size=11, name="Calibri")
    c.fill = _fill(GREEN_MED)
    c.alignment = _align("left", "center")

    header_row = tbl_start + 1
    row_h(header_row, 26)
    headers = ["ACTIVIDAD", "DESCRIPCIÓN", "FUNCIONALIDADES", "HORAS"]
    for ci, h in enumerate(headers, 2):
        c = ws.cell(header_row, ci, value=h)
        c.font = Font(bold=True, color=WHITE, size=10, name="Calibri")
        c.fill = _fill(GREEN_DARK)
        c.alignment = _align("center", "center", wrap=True)
        c.border = _border_thin("all")

    data_start = header_row + 1
    for i, act in enumerate(actividades):
        r = data_start + i
        row_h(r, 48)
        bg = GREEN_PALE if i % 2 == 0 else WHITE

        c = ws.cell(r, 2, value=act.get("actividad", ""))
        c.font = Font(bold=True, color=GREEN_DARK, size=10, name="Calibri")
        c.fill = _fill(bg)
        c.alignment = _align("left", "center", wrap=True)
        c.border = _border_thin("all")

        c = ws.cell(r, 3, value=act.get("descripcion", ""))
        c.font = Font(color="444444", size=9, name="Calibri")
        c.fill = _fill(bg)
        c.alignment = _align("left", "center", wrap=True)
        c.border = _border_thin("all")

        c = ws.cell(r, 4, value=act.get("funcionalidades", ""))
        c.font = Font(color="444444", size=9, name="Calibri")
        c.fill = _fill(bg)
        c.alignment = _align("left", "center", wrap=True)
        c.border = _border_thin("all")

        c = ws.cell(r, 5, value=act.get("horas", 0))
        c.font = Font(bold=True, color=WHITE, size=13, name="Calibri")
        c.fill = _fill(GREEN_DARK)
        c.alignment = _align("center", "center")
        c.border = _border_thin("all")

    # Total row
    total_row = data_start + len(actividades)
    row_h(total_row, 30)
    ws.merge_cells(f"B{total_row}:D{total_row}")
    c = ws.cell(total_row, 2, value="TOTAL DESARROLLO")
    c.font = Font(bold=True, color=WHITE, size=11, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("right", "center")
    c.border = _border_thin("all")

    c = ws.cell(total_row, 5, value=total_dev)
    c.font = Font(bold=True, color=GREEN_NEON, size=16, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("center", "center")
    c.border = _border_thin("all")

    # ═══════════════════════════════════════════
    # FACTORES ADICIONALES + TOTAL FINAL
    # ═══════════════════════════════════════════
    pct_start = total_row + 2

    row_h(pct_start, 26)
    ws.merge_cells(f"B{pct_start}:E{pct_start}")
    c = ws.cell(pct_start, 2, value="  FACTORES ADICIONALES")
    c.font = Font(bold=True, color=WHITE, size=11, name="Calibri")
    c.fill = _fill(GREEN_MED)
    c.alignment = _align("left", "center")

    factors = [
        ("Pruebas Unitarias", data.get("pruebas_pct", 0)),
        ("Entendimiento", data.get("entendimiento_pct", 0)),
        ("Riesgo", data.get("riesgo_pct", 0)),
    ]
    for j, (fname, fval) in enumerate(factors):
        r = pct_start + 1 + j
        row_h(r, 22)
        bg = GREEN_PALE if j % 2 == 0 else WHITE
        ws.merge_cells(f"B{r}:D{r}")
        c = ws.cell(r, 2, value=f"  {fname}")
        c.font = Font(color=DARK, size=10, name="Calibri")
        c.fill = _fill(bg)
        c.alignment = _align("left", "center")
        c.border = _border_thin("all")
        c = ws.cell(r, 5, value=f"{fval}%")
        c.font = Font(bold=True, color=GREEN_DARK, size=10, name="Calibri")
        c.fill = _fill(bg)
        c.alignment = _align("center", "center")
        c.border = _border_thin("all")

    gt_row = pct_start + 4
    row_h(gt_row, 34)
    ws.merge_cells(f"B{gt_row}:D{gt_row}")
    c = ws.cell(gt_row, 2, value="  TOTAL FINAL DEL PROYECTO")
    c.font = Font(bold=True, color=WHITE, size=12, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("left", "center")
    c.border = _border_thin("all")

    c = ws.cell(gt_row, 5, value=total_final)
    c.font = Font(bold=True, color=GREEN_NEON, size=16, name="Calibri")
    c.fill = _fill(DARK)
    c.alignment = _align("center", "center")
    c.border = _border_thin("all")

    # Notas
    notas = data.get("notas", [])
    if notas:
        notes_start = gt_row + 2
        row_h(notes_start, 26)
        ws.merge_cells(f"B{notes_start}:E{notes_start}")
        c = ws.cell(notes_start, 2, value="  NOTAS IMPORTANTES")
        c.font = Font(bold=True, color=WHITE, size=11, name="Calibri")
        c.fill = _fill(GREEN_MED)
        c.alignment = _align("left", "center")
        for k, nota in enumerate(notas):
            r = notes_start + 1 + k
            row_h(r, 28)
            ws.merge_cells(f"B{r}:E{r}")
            c = ws.cell(r, 2, value=f"  • {nota}")
            c.font = Font(color="444444", size=9, italic=True, name="Calibri")
            c.fill = _fill(GREEN_PALE)
            c.alignment = _align("left", "center", wrap=True)
            c.border = Border(bottom=Side(style="thin", color=GRAY_MID))

    # ── Footer ───────────────────────────────────────────────────────
    footer_row = ws.max_row + 2
    row_h(footer_row, 20)
    ws.merge_cells(f"B{footer_row}:E{footer_row}")
    c = ws.cell(
        footer_row,
        2,
        value="Periferia IT Group  •  Desarrollado por Juan Aragón  •  📞 314 674 7578",
    )
    c.font = Font(color="888888", size=9, italic=True, name="Calibri")
    c.alignment = _align("center", "center")
    c.fill = _fill(GREEN_PALE)

    # ── Config final ─────────────────────────────────────────────────
    ws.freeze_panes = "B1"
    ws.sheet_view.showGridLines = False

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()

    nombre_archivo = f"Estimacion_{data.get('cliente', 'Proyecto').replace(' ', '_')}.xlsx"
    return send_file(tmp.name, as_attachment=True, download_name=nombre_archivo)


# ── Health check (útil en Render) ────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "modelo": MODELO_PRIMARIO,
        "fallback": MODELO_FALLBACK,
        "api_key_set": bool(API_KEY),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info("Iniciando en puerto %d — modelo: %s", port, MODELO_PRIMARIO)
    app.run(host="0.0.0.0", port=port)
