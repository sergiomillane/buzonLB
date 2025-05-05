from flask import Flask, render_template, request, redirect, flash, send_file
from flask_sqlalchemy import SQLAlchemy
import pandas as pd
from datetime import datetime, timedelta
import os
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'clave_super_secreta_123'

# CAMBIO AQUÍ: usar SQLite en lugar de SQL Server
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///respuestas.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Respuesta(db.Model):
    __tablename__ = 'respuestas'
    id = db.Column(db.Integer, primary_key=True)
    orden = db.Column(db.String(50), nullable=False)
    clasificacion = db.Column(db.String(50), nullable=False)
    justificacion = db.Column(db.String(100), nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.now)

with app.app_context():
    db.create_all()

def cargar_alertas():
    archivo_excel = os.path.join('data', 'Forza.xlsx')
    df = pd.read_excel(archivo_excel, sheet_name="Hoja1")
    df["FechaCast"] = pd.to_datetime(df["FechaCast"], errors='coerce')
    df = df[~df["canal"].isin(["LIVERPOOL", "COPPEL", "WALMART"])]

    resultados = []
    for carrier, grupo_carrier in df.groupby('carrier'):
        for sku, grupo_sku in grupo_carrier.groupby('sku'):
            valores = grupo_sku['costoEnvio']
            moda = valores.mode()
            moda_valor = moda.iloc[0] if not moda.empty else None
            resultados.append({
                'Carrier': carrier,
                'SKU': sku,
                'CostoEnvioMasRepetido': moda_valor,
                'CoincidenConModa': (valores == moda_valor).sum() if moda_valor is not None else 0,
                'DifierenDeModa': (valores != moda_valor).sum() if moda_valor is not None else len(valores),
                'ValoresDiferentes': valores.nunique(),
                'Total_Registros': len(valores)
            })

    modo_por_carrier_sku = pd.DataFrame(resultados)

    tres_dias_atras = datetime.now() - timedelta(days=25)
    df_3d = df[df["FechaCast"] >= tres_dias_atras]
    df_3d = df_3d[["FechaCast", "orden", "carrier", "guia", "tracking", "canal", "sku", "costoEnvio"]]

    base = pd.merge(df_3d, modo_por_carrier_sku,
                    left_on=['sku', 'carrier'],
                    right_on=['SKU', 'Carrier'], how='left')

    base["Coincidencia"] = base.apply(
        lambda row: 1 if row['costoEnvio'] == row["CostoEnvioMasRepetido"] else 0,
        axis=1
    )

    base = base[["FechaCast", "orden", "carrier", "guia", "canal", "sku", "costoEnvio",
                 "CostoEnvioMasRepetido", "ValoresDiferentes", "Total_Registros", "Coincidencia"]]

    alertas = base[
        (base["Coincidencia"] == 0) & 
        (base["costoEnvio"] > base["CostoEnvioMasRepetido"])
    ]

    alertas = alertas.rename(columns={
        'FechaCast': 'Fecha',
        'orden': 'Orden',
        'carrier': 'Carrier',
        'guia': 'Guia',
        'canal': 'Canal',
        'sku': 'Sku',
        'costoEnvio': 'CostoActual',
        'CostoEnvioMasRepetido': 'CostoUsual'
    })

    alertas = alertas.loc[:, ~alertas.columns.duplicated()]

    respuestas = {r.orden: r for r in Respuesta.query.all()}
    alertas["Estatus"] = alertas["Orden"].astype(str).apply(lambda x: "Resuelto" if x in respuestas else "No resuelto")
    alertas["clasificacion"] = alertas["Orden"].astype(str).apply(lambda x: respuestas[x].clasificacion if x in respuestas else "")
    alertas["justificacion"] = alertas["Orden"].astype(str).apply(lambda x: respuestas[x].justificacion if x in respuestas else "")

    return alertas

@app.route('/')
def principal():
    alertas = cargar_alertas()

    fecha = request.args.get('fecha', default="")
    estatus = request.args.get('estatus', default="")

    if fecha:
        alertas = alertas[alertas['Fecha'].dt.strftime('%Y-%m-%d') == fecha]
    if estatus:
        alertas = alertas[alertas['Estatus'] == estatus]

    contador_no_resueltos = (alertas['Estatus'] == 'No resuelto').sum()
    contador_fp = ((alertas['Estatus'] == 'Resuelto') & (alertas['clasificacion'] == 'F')).sum()
    contador_vp = ((alertas['Estatus'] == 'Resuelto') & (alertas['clasificacion'] == 'V')).sum()

    filtros = {'fecha': fecha, 'estatus': estatus}

    return render_template('index.html', alertas=alertas, 
                           contador_no_resueltos=contador_no_resueltos, 
                           contador_fp=contador_fp, 
                           contador_vp=contador_vp,
                           filtros=filtros)

@app.route('/guardar', methods=['POST'])
def guardar():
    orden = request.form.get('orden')
    clasificacion = request.form.get('clasificacion')
    justificacion = request.form.get('justificacion')

    if not orden or not clasificacion or not justificacion:
        flash("Faltan datos para guardar la justificación", "danger")
        return redirect("/")

    nueva_respuesta = Respuesta(
        orden=str(orden),
        clasificacion=clasificacion,
        justificacion=justificacion
    )
    db.session.add(nueva_respuesta)
    db.session.commit()

    flash(f"Justificación guardada para orden {orden}", "success")
    return redirect("/")

@app.route('/exportar')
def exportar():
    alertas = cargar_alertas()

    fecha = request.args.get('fecha', default="")
    estatus = request.args.get('estatus', default="")

    if fecha:
        alertas = alertas[alertas['Fecha'].dt.strftime('%Y-%m-%d') == fecha]
    if estatus:
        alertas = alertas[alertas['Estatus'] == estatus]

    output = BytesIO()
    alertas.to_excel(output, index=False)
    output.seek(0)

    return send_file(output, download_name="alertas_filtradas.xlsx", as_attachment=True)

@app.route('/subir', methods=['GET', 'POST'])
def subir():
    df1 = pd.read_excel('data/Forza.xlsx')
    tablas_resultado = []
    resumenes = []

    columnas = {
        'fedex': {
            'left': 'tracking',
            'right': 'AWB',
            'columns': [
                "orden", "guia", "tracking", "nombreCliente", "canal", "costoEnvio", "Billed Total Amount",
                "precioSkuCanal", "sku","carrier", "FechaCast", "nombreBodega", "codigoPostalCliente"
            ]
        },
        'dhl': {
            'left': 'tracking',
            'right': 'Rastreo',
            'columns': [
                "orden", "guia", "tracking", "nombreCliente", "canal", "costoEnvio", "Total",
                "precioSkuCanal", "sku","carrier", "FechaCast", "nombreBodega", "codigoPostalCliente"
            ]
        },
        'estafeta': {
            'left': 'guia',
            'right': 'Guía',
            'columns': [
                "orden", "guia", "tracking", "nombreCliente", "canal", "costoEnvio", "TOTAL",
                "precioSkuCanal", "sku","carrier", "FechaCast", "nombreBodega", "codigoPostalCliente"
            ]
        }
    }

    simbolos = {
        'coincide': '<i class="bi bi-check-circle-fill text-success"></i>',
        'no_coincide': '<i class="bi bi-x-circle-fill text-danger"></i>'
    }

    if request.method == 'POST':
        archivos = {
            'fedex': request.files.get('archivo_fedex'),
            'dhl': request.files.get('archivo_dhl'),
            'estafeta': request.files.get('archivo_estafeta')
        }

        for paqueteria, archivo in archivos.items():
            if archivo:
                try:
                    df2 = pd.read_excel(archivo)
                    config = columnas[paqueteria]
                    resultado = pd.merge(df1, df2, left_on=config['left'], right_on=config['right'], how='inner')
                    resultado = resultado[config['columns']]

                    if paqueteria == 'fedex':
                        resultado['Coincidencia'] = resultado.apply(lambda row: simbolos['coincide'] if row['costoEnvio'] == row['Billed Total Amount'] else simbolos['no_coincide'], axis=1)
                        resultado['match'] = resultado.apply(lambda row: row['costoEnvio'] == row['Billed Total Amount'], axis=1)
                    elif paqueteria == 'estafeta':
                        resultado['Coincidencia'] = resultado.apply(lambda row: simbolos['coincide'] if row['costoEnvio'] == row['TOTAL'] else simbolos['no_coincide'], axis=1)
                        resultado['match'] = resultado.apply(lambda row: row['costoEnvio'] == row['TOTAL'], axis=1)
                    elif paqueteria == 'dhl':
                        resultado['Coincidencia'] = resultado.apply(lambda row: simbolos['coincide'] if row['costoEnvio'] == row['Total'] else simbolos['no_coincide'], axis=1)
                        resultado['match'] = resultado.apply(lambda row: row['costoEnvio'] == row['Total'], axis=1)

                    total = len(resultado)
                    coinciden = resultado['match'].sum()
                    no_coinciden = total - coinciden
                    porcentaje = round(coinciden / total * 100, 2) if total > 0 else 0

                    resumenes.append({
                        'paqueteria': paqueteria.upper(),
                        'coinciden': coinciden,
                        'no_coinciden': no_coinciden,
                        'porcentaje': porcentaje
                    })

                    tabla_html = resultado.to_html(classes='table table-bordered table-hover', index=False, escape=False)
                    tablas_resultado.append((paqueteria.upper(), tabla_html))
                    flash(f"[{paqueteria.upper()}] Archivo procesado correctamente.", "success")
                except Exception as e:
                    flash(f"[{paqueteria.upper()}] Error al procesar el archivo: {e}", "danger")

    return render_template('subida.html', tablas=tablas_resultado, resumenes=resumenes)





@app.route('/skus')
def skus():
    archivo_excel = os.path.join('data', 'Forza.xlsx')
    df = pd.read_excel(archivo_excel, sheet_name="Hoja1")

    df["carrier"] = df["carrier"].str.upper()
    df = df[df["carrier"].isin(["FEDEX", "PMM", "ESTAFETA", "DHL"])]

    resumen = df.groupby(["sku", "carrier"]).agg(
        PromedioCosto=("costoEnvio", "mean"),
        Conteo=("costoEnvio", "count")
    ).reset_index()
    resumen["PromedioCosto"] = resumen["PromedioCosto"].round(2)
    resumen["Conteo"] = resumen["Conteo"].astype(int)

    pivot_promedios = resumen.pivot(index=["sku"], columns="carrier", values="PromedioCosto").reset_index()
    pivot_conteos = resumen.pivot(index=["sku"], columns="carrier", values="Conteo").reset_index()
    pivot_conteos.columns = [col if col == "sku" else f"Conteo_{col}" for col in pivot_conteos.columns]

    tabla_merge = pd.merge(pivot_promedios, pivot_conteos, on="sku").fillna("-")

    # Reordenar columnas
    carriers = ["FEDEX", "PMM", "ESTAFETA", "DHL"]
    columnas_finales = ["sku"]
    for c in carriers:
        columnas_finales.append(c)
        columnas_finales.append(f"Conteo_{c}")

    tabla_final = tabla_merge[columnas_finales]

    # Convertir a HTML
    tabla_html = tabla_final.to_html(index=False, classes="table table-bordered table-striped", escape=False)

    # Aplicar estilos grises a columnas de conteo
    for c in carriers:
        conteo_col = f"Conteo_{c}"
        tabla_html = tabla_html.replace(f"<th>{conteo_col}</th>", f"<th style='background-color:#f0f0f0'>{conteo_col}</th>")
        col_index = tabla_final.columns.get_loc(conteo_col)
        for row in tabla_final.itertuples(index=False):
            valor = getattr(row, conteo_col)
            tabla_html = tabla_html.replace(f"<td>{valor}</td>", f"<td style='background-color:#f0f0f0'>{valor}</td>", 1)

    return render_template("skus.html", tabla=tabla_html)



if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

