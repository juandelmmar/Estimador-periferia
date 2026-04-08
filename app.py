"""
Backend - Estimador de Proyectos con IA (Edición Producción)
==========================================================
Cliente: Periferia IT Group
Tecnologías: Flask + Groq (Llama 3.3) + OpenPyXL
"""

import os
import json
import tempfile
import openpyxl
from flask import Flask, request, jsonify, send_file, send_from_directory
from groq import Groq
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

app = Flask(__name__, static_folder=".", static_url_path="")

# ============ CONFIGURACIÓN DE SEGURIDAD ============
# En local: Puedes setear esto en tu terminal con $env:GROQ_API_KEY="tu_llave"
# En Render: Agrégala en la pestaña 'Environment'
api_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=api_key)
MODELO = "llama-3.3-70b-versatile"

PROMPT_SISTEMA = """Actúa como un líder técnico senior con experiencia en estimación de proyectos de software.
Tu objetivo es generar una estimación de horas justa y realista.

REGLAS CRÍTICAS:
1. Las horas deben ser JUSTAS: ni infladas ni demasiado bajas.
2. El rango típico por actividad es entre 4 y 40 horas.
3. Responde SOLO en formato JSON exacto, sin markdown ni texto extra.

ESTRUCTURA JSON:
{
  "cliente": "Nombre",
  "backend": "Tecnologías",
  "frontend": "Tecnologías",
  "base_datos": "DB",
  "cloud": "Proveedor Cloud (AWS, Azure, GCP, etc.)",
  "actividades": [
    {"actividad": "Nombre", "descripcion": "Detalle", "funcionalidades": "Funciones", "horas": 8}
  ],
  "pruebas_pct": 15,
  "entendimiento_pct": 10,
  "riesgo_pct": 5,
  "notas": ["nota1"]
}"""

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/estimar", methods=["POST"])
def estimar():
    data = request.get_json()
    descripcion = data.get("descripcion", "")
    cliente = data.get("cliente", "")

    if not descripcion or len(descripcion) < 20:
        return jsonify({"error": "La descripción es muy corta"}), 400

    if not api_key:
        return jsonify({"error": "API Key no configurada en el servidor"}), 500

    try:
        prompt_usuario = f"Cliente: {cliente}\n\nEstima esto: {descripcion}" if cliente else f"Estima esto: {descripcion}"

        # Uso del cliente oficial de Groq
        completion = client.chat.completions.create(
            model=MODELO,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": prompt_usuario}
            ],
            temperature=0.2,
            max_tokens=2048
        )

        respuesta_ia = completion.choices[0].message.content
        # Limpieza por si la IA devuelve markdown
        clean_json = respuesta_ia.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        
        return jsonify(parsed)

    except Exception as e:
        return jsonify({"error": f"Error al procesar con IA: {str(e)}"}), 500

@app.route("/api/descargar-excel", methods=["POST"])
def descargar_excel():
    data = request.get_json()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Estimación Periferia"

    # --- Estilos ---
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    thin_border = Border(left=Side(style="thin"), right=Side(style="thin"), 
                        top=Side(style="thin"), bottom=Side(style="thin"))
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # --- Encabezados de Proyecto ---
    ws["A1"] = "RESUMEN DE ESTIMACIÓN"
    ws["A1"].font = Font(size=14, bold=True)
    
    info_labels = [
        ("CLIENTE:", data.get("cliente")), 
        ("BACKEND:", data.get("backend")),
        ("FRONTEND:", data.get("frontend")),
        ("DB:", data.get("base_datos")),
        ("CLOUD:", data.get("cloud", "N/A"))
    ]
    
    for i, (label, value) in enumerate(info_labels, start=3):
        ws.cell(row=i, column=1, value=label).font = Font(bold=True)
        ws.cell(row=i, column=2, value=value)

    # --- Tabla de Actividades ---
    headers = ["Actividad", "Descripción", "Funcionalidades", "Horas"]
    for col, text in enumerate(headers, 1):
        cell = ws.cell(row=9, column=col, value=text)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = center_align

    actividades = data.get("actividades", [])
    current_row = 10
    for act in actividades:
        ws.cell(row=current_row, column=1, value=act.get("actividad")).border = thin_border
        ws.cell(row=current_row, column=2, value=act.get("descripcion")).border = thin_border
        ws.cell(row=current_row, column=3, value=act.get("funcionalidades")).border = thin_border
        ws.cell(row=current_row, column=4, value=act.get("horas")).border = thin_border
        current_row += 1

    # --- Totales y Porcentajes ---
    total_dev = sum(a.get("horas", 0) for a in actividades)
    ws.cell(row=current_row + 1, column=3, value="SUBTOTAL DESARROLLO:").font = Font(bold=True)
    ws.cell(row=current_row + 1, column=4, value=total_dev).font = Font(bold=True)

    # Cálculo de impacto de adicionales
    p_pruebas = data.get("pruebas_pct", 0) / 100
    p_ent = data.get("entendimiento_pct", 0) / 100
    p_riesgo = data.get("riesgo_pct", 0) / 100
    
    total_final = total_dev * (1 + p_pruebas + p_ent + p_riesgo)
    
    ws.cell(row=current_row + 3, column=3, value="TOTAL FINAL (Inc. Factores):").font = Font(size=12, bold=True)
    ws.cell(row=current_row + 3, column=4, value=round(total_final, 1)).font = Font(size=12, bold=True)

    # Ajuste de columnas
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 50
    ws.column_dimensions["C"].width = 50
    ws.column_dimensions["D"].width = 10

    # Guardado temporal
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()

    return send_file(tmp.name, as_attachment=True, download_name="Estimacion_Periferia.xlsx")

if __name__ == "__main__":
    # CONFIGURACIÓN PARA RENDER
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)