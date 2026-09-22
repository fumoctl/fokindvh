import argparse
import os
import re
import sys
import pandas as pd


def clasificar_vidrio(codigo: str) -> str:
    """Clasifica el código del vidrio según las 4 familias del Reporte de Producción DVH:
    Float, Laminado, Templado, Texturado."""
    if not isinstance(codigo, str):
        return 'Float'

    c = codigo.strip().upper()

    # 1. Laminado (Lam3+3, Lam4+4, Lam5+5, reflectivos laminados, antirrobo)
    if c.startswith('LAM') or 'LAMINADO' in c:
        return 'Laminado'

    # 2. Templado (Temp6, Temp8, Temp10, puertas standard)
    if c.startswith('TEMP') or 'TEMPLAD' in c or 'PUERTA STANDARD' in c:
        return 'Templado'

    # 3. Texturado / Impreso (Stipolite, Jakare, Mosaico, Cuadrillé, etc.)
    texturados = [
        'STIPOLITE',
        'JAKARE',
        'MOSAICO',
        'CUADRILL',
        'PACIFICO',
        'PACÍFICO',
        'ROMBO',
        'DREAMLINE',
        'FANTASIA',
    ]
    if re.match(r'^I\d', c) or 'IMPRESO' in c or any(t in c for t in texturados):
        return 'Texturado'

    # 4. Float (monolítico incoloro, color gris/bronce/verde, reflectivo, Low-E y derivados)
    return 'Float'


def analizar_produccion(ruta_archivo: str, anio: int = None, mes: int = None):
    if not os.path.exists(ruta_archivo):
        print(f"Error: No se encontró el archivo '{ruta_archivo}'")
        sys.exit(1)

    df = pd.read_csv(ruta_archivo)
    df.columns = [c.strip().lower() for c in df.columns]

    # Normalización de textos
    df['tipo_producto'] = (
        df['tipo_producto'].astype(str).str.strip().str.upper()
    )
    df['codigo'] = df['codigo'].astype(str).str.strip()

    # Filtros temporales opcionales
    if anio is not None and 'fechafacanio' in df.columns:
        df = df[df['fechafacanio'] == anio]
    if mes is not None and 'fechafacmes' in df.columns:
        df = df[df['fechafacmes'] == mes]

    # 1. Identificar presupuestos que contienen servicio de armado de DVH (Cámara)
    filas_dvh = df[
        (df['tipo_producto'] == 'ANEXO')
        & (df['codigo'].str.contains('Cámara|Camara|DVH', na=False, case=False))
        & (~df['codigo'].str.contains('Desmontaje', na=False, case=False))
        & (~df['codigo'].str.contains('Silicona', na=False, case=False))
        & (~df['codigo'].str.contains('PerfCam', na=False, case=False))
    ]
    presupuestos_con_dvh = filas_dvh['nropresupuesto'].unique()

    # Total m² de servicio de cámara facturados
    filas_camara_m2 = filas_dvh[
        filas_dvh['codigo'].str.contains('Cámara|Camara', na=False, case=False)
        & (~filas_dvh['codigo'].str.contains('unidades pequeñas', na=False, case=False))
    ]
    total_m2_dvh_facturado = filas_camara_m2['cantidad_anexo'].sum()

    # 2. Vidrios pertenecientes a presupuestos con DVH
    vidrios_en_dvh = df[
        (df['nropresupuesto'].isin(presupuestos_con_dvh))
        & (df['tipo_producto'] == 'VIDRIO')
    ].copy()

    # Comprobar si el CSV cuenta con las dimensiones físicas (ancho, alto, cantidad)
    tiene_dimensiones = (
        'ancho' in df.columns
        and 'alto' in df.columns
        and 'cantidad' in df.columns
        and (vidrios_en_dvh['ancho'] > 0).any()
    )

    if tiene_dimensiones:
        # CÁLCULO FÍSICO EXACTO POR MEDIDAS DE PANEL
        vidrios_validos = vidrios_en_dvh[
            (vidrios_en_dvh['ancho'] > 0) & (vidrios_en_dvh['alto'] > 0)
        ].copy()

        paneles_agrupados = (
            vidrios_validos.groupby(['nropresupuesto', 'ancho', 'alto'])
            .agg(
                total_hojas=('cantidad', 'sum'),
                m2_grupo=('m2', 'sum'),
            )
            .reset_index()
        )

        paneles_agrupados['cant_paneles'] = paneles_agrupados['total_hojas'] / 2.0
        paneles_agrupados['perimetro'] = 2.0 * (
            paneles_agrupados['ancho'] + paneles_agrupados['alto']
        )
        paneles_agrupados['mtl_espaciador'] = (
            paneles_agrupados['cant_paneles'] * paneles_agrupados['perimetro']
        )
        paneles_agrupados['m2_dvh'] = (
            paneles_agrupados['cant_paneles']
            * paneles_agrupados['ancho']
            * paneles_agrupados['alto']
        )

        total_paneles = int(round(paneles_agrupados['cant_paneles'].sum()))
        total_mtl_espaciador = paneles_agrupados['mtl_espaciador'].sum()
        total_m2_dvh = (
            total_m2_dvh_facturado
            if total_m2_dvh_facturado > 0
            else paneles_agrupados['m2_dvh'].sum()
        )

    else:
        # CÁLCULO DESDE CSV LEGACY (pulido perimetral de las 2 caras)
        filas_pulido_dvh = df[
            (df['nropresupuesto'].isin(presupuestos_con_dvh))
            & (df['tipo_producto'] == 'ANEXO')
            & (df['codigo'].str.contains('Pulido', na=False, case=False))
        ]
        total_mtl_espaciador = filas_pulido_dvh['cantidad_anexo'].sum() / 2.0
        total_m2_vidrio = vidrios_en_dvh['m2'].sum()
        total_m2_dvh = (
            total_m2_dvh_facturado
            if total_m2_dvh_facturado > 0
            else (total_m2_vidrio / 2.0)
        )

        # En caso de export legacy sin ancho/alto, estimar con el perímetro medio verificado (3.82 mtl)
        if total_mtl_espaciador > 0:
            total_paneles = int(round(total_mtl_espaciador / 3.8213))
        else:
            total_paneles = max(1, int(round(len(vidrios_en_dvh) / 2.0)))

    promedio_espaciador = (
        (total_mtl_espaciador / total_paneles) if total_paneles > 0 else 0.0
    )

    # 3. Clasificación de vidrios en las 4 familias oficiales
    vidrios_en_dvh['familia_vidrio'] = vidrios_en_dvh['codigo'].apply(
        clasificar_vidrio
    )

    consumo_familias = (
        vidrios_en_dvh.groupby('familia_vidrio')['m2']
        .sum()
        .reindex(['Float', 'Laminado', 'Templado', 'Texturado'], fill_value=0.0)
    )

    # 4. Ranking de tipos específicos de vidrio usados en DVH
    ranking_especifico = (
        vidrios_en_dvh.groupby(['codigo', 'familia_vidrio'])['m2']
        .agg(['sum', 'count'])
        .rename(columns={'sum': 'm2_total', 'count': 'cant_lineas'})
        .sort_values(by='m2_total', ascending=False)
        .reset_index()
    )

    total_m2_vidrio_dvh = vidrios_en_dvh['m2'].sum()
    if total_m2_vidrio_dvh > 0:
        ranking_especifico['porcentaje'] = (
            ranking_especifico['m2_total'] / total_m2_vidrio_dvh
        ) * 100.0
    else:
        ranking_especifico['porcentaje'] = 0.0

    # 5. Formateo y presentación de resultados
    periodo_txt = (
        f'{mes:02d}/{anio}'
        if mes and anio
        else ('Año ' + str(anio) if anio else 'TOTAL REPORTE')
    )
    print('=' * 75)
    print(
        f' REPORTE DVH: {os.path.basename(ruta_archivo)} | PERIODO:'
        f' {periodo_txt}'
    )
    print('=' * 75)
    print(
        f'• Total m² de DVH fabricados:            {total_m2_dvh:>10.2f} m²'
    )
    print(
        f'• Total paneles de DVH armados:           {total_paneles:>10} unidades'
    )
    print(
        '• Metros lineales perfil espaciador:     '
        f' {total_mtl_espaciador:>10.2f} mtl'
    )
    print(
        '• Promedio espaciador por panel:         '
        f' {promedio_espaciador:>10.2f} mtl/panel'
    )
    print('-' * 75)
    print(' DATOS PARA "Reporte de Producción DVH.xlsx":')
    print(
        f"  - Float:     {consumo_familias['Float']:>8.2f} m²"
        f"  |  Laminado:  {consumo_familias['Laminado']:>8.2f} m²"
    )
    print(
        f"  - Templado:  {consumo_familias['Templado']:>8.2f} m²"
        f"  |  Texturado: {consumo_familias['Texturado']:>8.2f} m²"
    )
    print('-' * 75)
    print(' RANKING DE VIDRIOS UTILIZADOS EN DVH:')
    print(
        f"  {'#':<3} {'CÓDIGO VIDRIO':<18} {'FAMILIA':<16} {'M2 TOTAL':>10}"
        f" {'PARTICIPACIÓN':>15}"
    )
    print('  ' + '-' * 67)
    if not ranking_especifico.empty:
        for i, row in ranking_especifico.iterrows():
            print(
                f"  {i+1:<3} {row['codigo']:<18} {row['familia_vidrio']:<16}"
                f" {row['m2_total']:>8.2f} m² {row['porcentaje']:>13.1f}%"
            )
    else:
        print('  (Sin datos)')
    print('=' * 75)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Análisis de producción de DVH y ranking de vidrios.'
    )
    parser.add_argument(
        'archivo',
        help='Ruta al archivo CSV (ej. movimientos.csv)',
    )
    parser.add_argument(
        '--anio',
        type=int,
        default=None,
        help='Filtrar por año (columna fechafacanio)',
    )
    parser.add_argument(
        '--mes',
        type=int,
        default=None,
        help='Filtrar por mes (columna fechafacmes)',
    )

    args = parser.parse_args()
    analizar_produccion(args.archivo, anio=args.anio, mes=args.mes)