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

    # 4. Espejo (E2P, E3B, E4G, E4P, E5P, E6P...): entra en DVH esmerilado.
    #    El patrón real del catálogo es E<digito><variante>; se excluyen
    #    códigos como Esquinero8C o EcircularBr52 que empiezan con E pero
    #    no son espejos.
    if re.match(r'^E\d', c):
        return 'Espejo'

    # 5. Float (monolítico incoloro, color gris/bronce/verde, reflectivo, Low-E y derivados)
    return 'Float'


def puntuar_candidato_dvh(row, vidrios_presu) -> int:
    """Asigna un puntaje de prioridad para determinar qué hoja pertenece legítimamente al DVH."""
    score = 0
    orden = str(row.get('ordennro', '')).strip()

    # Semántica verificada en vidrioluzdb (2026-09): ordennro = ORD-<nro>-<pos>-<cara>-<era>
    #   <cara> (seg. 3): 1 = cara interior, 2 = cara exterior del MISMO panel
    #     (ej. ORD-114678-5-1-2 V6G <-> ORD-114678-5-2-2 Lam4+4I en PRE-184420)
    #   <era> (seg. 4): 2 = orden vigente, 3 = orden histórica
    # Presupuestos con DVH: 1 orden (ambas caras) o 2 órdenes (una por cara).
    score = 0
    orden = str(row.get('ordennro', '')).strip()

    m = re.match(r'^ORD-\d+-\d+-(\d+)-(\d+)$', orden)
    if m:
        cara, era = m.group(1), m.group(2)
        if era != '2':
            pass
        elif cara == '2':
            base_prefix = orden.rsplit('-', 2)[0]
            orden_hermana_esperada = f'{base_prefix}-1-2'
            if (
                'ordennro' in vidrios_presu.columns
                and orden_hermana_esperada in set(vidrios_presu['ordennro'])
            ):
                score += 50
            else:
                score += 10
        else:
            score += 40
    elif re.match(r'^ORD-\d+-\d+$', orden):
        score += 5

    # Frecuencia del código en el presupuesto (la composición base del DVH se repite)
    if 'codigo' in vidrios_presu.columns:
        frecuencia_codigo = (vidrios_presu['codigo'] == row['codigo']).sum()
        score += min(frecuencia_codigo, 4) * 2

    # Los espejos sí forman DVH esmerilado (verificado en la base: PRE-146071,
    # PRE-160740): ya no se bloquean, solo leve preferencia por vidrios comunes.
    if str(row.get('codigo', '')).startswith('E'):
        score -= 2

    return score


def analizar_produccion(ruta_archivo: str = None, anio: int = None, mes: int = None):
    # Detección flexible de archivo o directorio
    if ruta_archivo is None or ruta_archivo.strip() == '':
        if os.path.isdir('csvs'):
            ruta_archivo = 'csvs'
        elif os.path.exists('csvs/pre184420.csv'):
            ruta_archivo = 'csvs/pre184420.csv'
        else:
            print("Error: No se especificó archivo y no se encontró carpeta 'csvs'")
            sys.exit(1)

    if not os.path.exists(ruta_archivo):
        print(f"Error: No se encontró la ruta '{ruta_archivo}'")
        sys.exit(1)

    if os.path.isdir(ruta_archivo):
        import glob
        archivos = sorted(glob.glob(os.path.join(ruta_archivo, '*.csv')))
        if not archivos:
            print(f"Error: No se encontraron archivos .csv en el directorio '{ruta_archivo}'")
            sys.exit(1)
        df = pd.concat([pd.read_csv(f) for f in archivos], ignore_index=True)
        nombre_reporte = f"Directorio '{ruta_archivo}' ({len(archivos)} archivo{'s' if len(archivos)>1 else ''})"
    else:
        df = pd.read_csv(ruta_archivo)
        nombre_reporte = os.path.basename(ruta_archivo)

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

    # 1. Identificar presupuestos que contienen servicio de armado de DVH (Cámara).
    # Códigos ANEXO reales del ERP (vidrioluzdb) que matchean 'DVH/Cámara':
    #   - 'DVH-M2 DVH para perfilería Cámara N'  -> servicio de armado (m²)
    #   - '01-DVH Camara 6 9 12 unidades pequeñas' -> servicio de piezas chicas
    #   - '01-Desmontaje de unidades DVH' -> NO es armado
    #   - 'PerfCam6-Perfil separador DVH Cam 6mm' -> material, NO es armado
    #   - '3-0117-SILICONA ESTRUCTURAL DVH' -> material, NO es armado
    patrones_dvh = r'Cámara|Camara|DVH'
    exclusiones_dvh = (
        r'Desmontaje|Silicona|PerfCam|unidades pequeñas|unidades pequenas'
    )
    filas_dvh = df[
        (df['tipo_producto'] == 'ANEXO')
        & (df['codigo'].str.contains(patrones_dvh, na=False, case=False))
        & (~df['codigo'].str.contains(exclusiones_dvh, na=False, case=False))
    ]
    presupuestos_con_dvh = filas_dvh['nropresupuesto'].unique()

    # Total m² de servicio de cámara facturados (idéntico criterio: sin unidades
    # pequeñas ni desmontajes; el código de cámara es 'DVH-M2 ... Cámara N')
    filas_camara_m2 = filas_dvh[
        filas_dvh['codigo'].str.contains('Cámara|Camara', na=False, case=False)
    ]
    total_m2_dvh_facturado = filas_camara_m2['cantidad_anexo'].sum()

    # 2. Separar Vidrios Componentes de DVH vs Vidrios Sueltos
    vidrios_totales = df[df['tipo_producto'] == 'VIDRIO'].copy()

    # Comprobar si el CSV cuenta con las dimensiones físicas (ancho, alto, cantidad)
    tiene_dimensiones = (
        'ancho' in df.columns
        and 'alto' in df.columns
        and 'cantidad' in df.columns
        and (vidrios_totales['ancho'] > 0).any()
    )

    paneles_confirmados = []
    vidrios_dvh_list = []
    vidrios_sueltos_list = []

    if tiene_dimensiones:
        # CÁLCULO FÍSICO EXACTO: RECONCILIACIÓN POR PRESUPUESTO
        for presu, group in df.groupby('nropresupuesto'):
            # Cuota de DVH contratada en este presupuesto (mismo criterio estricto)
            dvh_lines = group[
                (group['tipo_producto'] == 'ANEXO')
                & (group['codigo'].str.contains(patrones_dvh, na=False, case=False))
                & (~group['codigo'].str.contains(exclusiones_dvh, na=False, case=False))
            ]
            cuota_m2_dvh = dvh_lines['cantidad_anexo'].sum()

            vidrios_presu = group[group['tipo_producto'] == 'VIDRIO'].copy()
            if cuota_m2_dvh <= 0 or vidrios_presu.empty:
                # Si el presupuesto no tiene DVH, todos sus vidrios son sueltos
                vidrios_sueltos_list.append(vidrios_presu)
                continue

            vidrios_validos = vidrios_presu[
                (vidrios_presu['ancho'] > 0) & (vidrios_presu['alto'] > 0)
            ].copy()
            vidrios_sin_medida = vidrios_presu[
                (vidrios_presu['ancho'] <= 0) | (vidrios_presu['alto'] <= 0)
            ].copy()
            if not vidrios_sin_medida.empty:
                vidrios_sueltos_list.append(vidrios_sin_medida)

            # Pulidos del presupuesto. En el ERP cada hoja lleva UNA línea de
            # pulido perimetral (verificado en vidrioluzdb), por lo que la suma
            # total de pulidos es aproximadamente 2x la suma de perímetros.
            # Se dejan como lista cruda; el matching es por perímetro individual.
            pulidos_presu = group[
                (group['tipo_producto'] == 'ANEXO')
                & (group['codigo'].str.contains('Pulido', na=False, case=False))
            ]['cantidad_anexo'].tolist()

            # Estructurar grupos candidatos de dimensiones (ancho, alto)
            grupos_candidatos = []
            for (w, h), dim_group in vidrios_validos.groupby(['ancho', 'alto']):
                total_hojas = dim_group['cantidad'].sum()
                # Los espejos SÍ forman DVH esmerilado (verificado en la base:
                # PRE-146071, PRE-160740): ya no se excluyen del cálculo físico.
                max_paneles = int(total_hojas // 2)
                area_panel = w * h
                perim = 2.0 * (w + h)
                has_pulido_match = any(abs(perim - p) < 0.05 for p in pulidos_presu)
                es_heterogeneo = len(dim_group['codigo'].unique()) > 1

                grupos_candidatos.append({
                    'ancho': w,
                    'alto': h,
                    'max_paneles': max_paneles,
                    'area': area_panel,
                    'perim': perim,
                    'has_pulido_match': has_pulido_match,
                    'es_heterogeneo': es_heterogeneo,
                    'dim_group': dim_group
                })

            # Optimización: encontrar la combinación exacta de paneles que mejor concilia la cuota DVH
            # Guard de explosión combinatoria: el DFS ramifica (max_paneles+1) por
            # grupo; con muchos grupos el producto llega a cientos de miles de ramas
            # (31k en PRE-185787). Se acota manteniendo el grupo más prometedor.
            if len(grupos_candidatos) > 12:
                grupos_candidatos = sorted(
                    grupos_candidatos,
                    key=lambda g: (
                        g['max_paneles'],
                        2 if g['has_pulido_match'] else 0,
                        1 if g['es_heterogeneo'] else 0,
                    ),
                    reverse=True,
                )[:12]

            best_match = {'diff': float('inf'), 'score': -1, 'combo': [0] * len(grupos_candidatos)}

            def buscar_combinacion(idx, area_actual, score_actual, combo_actual):
                if idx == len(grupos_candidatos):
                    diff = abs(area_actual - cuota_m2_dvh)
                    if diff < best_match['diff'] - 1e-4 or (
                        abs(diff - best_match['diff']) <= 1e-4 and score_actual > best_match['score']
                    ):
                        best_match['diff'] = diff
                        best_match['score'] = score_actual
                        best_match['combo'] = list(combo_actual)
                    return

                g = grupos_candidatos[idx]
                for c in range(g['max_paneles'], -1, -1):
                    next_area = area_actual + c * g['area']
                    if c > 0 and next_area > cuota_m2_dvh + 0.15 and area_actual > 0:
                        continue
                    bonus = (2 if g['has_pulido_match'] else 0) + (1 if g['es_heterogeneo'] else 0)
                    next_score = score_actual + c * bonus
                    combo_actual.append(c)
                    buscar_combinacion(idx + 1, next_area, next_score, combo_actual)
                    combo_actual.pop()

            buscar_combinacion(0, 0.0, 0, [])
            combo_optimo = best_match['combo']

            # Distribuir paneles confirmados y vidrios sueltos
            for c_paneles, g in zip(combo_optimo, grupos_candidatos):
                dim_group = g['dim_group'].copy()
                w = g['ancho']
                h = g['alto']

                if c_paneles > 0:
                    perim = g['perim']
                    paneles_confirmados.append({
                        'nropresupuesto': presu,
                        'ancho': w,
                        'alto': h,
                        'cant_paneles': c_paneles,
                        'perimetro': perim,
                        'mtl_espaciador': c_paneles * perim,
                        'm2_dvh': c_paneles * g['area']
                    })

                    hojas_usadas = c_paneles * 2
                    usadas_restantes = hojas_usadas

                    # --- ORDENAMIENTO POR PRIORIDAD ---
                    dim_group['dvh_score'] = dim_group.apply(
                        lambda r: puntuar_candidato_dvh(r, vidrios_presu), axis=1
                    )
                    dim_group_ordenado = dim_group.sort_values(
                        by='dvh_score', ascending=False
                    )

                    for idx, row in dim_group_ordenado.iterrows():
                        q = row['cantidad']
                        if usadas_restantes >= q:
                            vidrios_dvh_list.append(row.to_frame().T)
                            usadas_restantes -= q
                        elif usadas_restantes > 0:
                            r_dvh = row.copy()
                            r_dvh['cantidad'] = usadas_restantes
                            r_dvh['m2'] = usadas_restantes * w * h
                            vidrios_dvh_list.append(r_dvh.to_frame().T)

                            r_suelto = row.copy()
                            r_suelto['cantidad'] = q - usadas_restantes
                            r_suelto['m2'] = (q - usadas_restantes) * w * h
                            vidrios_sueltos_list.append(r_suelto.to_frame().T)
                            usadas_restantes = 0
                        else:
                            vidrios_sueltos_list.append(row.to_frame().T)
                else:
                    vidrios_sueltos_list.append(dim_group)

        df_paneles = pd.DataFrame(paneles_confirmados)
        if not df_paneles.empty:
            total_paneles = int(round(df_paneles['cant_paneles'].sum()))
            total_mtl_espaciador = df_paneles['mtl_espaciador'].sum()
            total_m2_dvh = (
                total_m2_dvh_facturado
                if total_m2_dvh_facturado > 0
                else df_paneles['m2_dvh'].sum()
            )
        else:
            total_paneles = 0
            total_mtl_espaciador = 0.0
            total_m2_dvh = total_m2_dvh_facturado

        vidrios_en_dvh = pd.concat(vidrios_dvh_list, ignore_index=True) if vidrios_dvh_list else pd.DataFrame(columns=df.columns)
        vidrios_sueltos = pd.concat(vidrios_sueltos_list, ignore_index=True) if vidrios_sueltos_list else pd.DataFrame(columns=df.columns)

        # Eliminar columna temporal si existe
        if 'dvh_score' in vidrios_en_dvh.columns:
            vidrios_en_dvh = vidrios_en_dvh.drop(columns=['dvh_score'])
        if 'dvh_score' in vidrios_sueltos.columns:
            vidrios_sueltos = vidrios_sueltos.drop(columns=['dvh_score'])

    else:
        # CÁLCULO DESDE CSV LEGACY (pulido perimetral de las 2 caras)
        vidrios_en_dvh = df[
            (df['nropresupuesto'].isin(presupuestos_con_dvh))
            & (df['tipo_producto'] == 'VIDRIO')
        ].copy()
        vidrios_sueltos = df[
            (~df['nropresupuesto'].isin(presupuestos_con_dvh))
            & (df['tipo_producto'] == 'VIDRIO')
        ].copy()

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

        if total_mtl_espaciador > 0:
            total_paneles = int(round(total_mtl_espaciador / 3.8213))
        elif len(vidrios_en_dvh) > 0:
            total_paneles = max(1, int(round(len(vidrios_en_dvh) / 2.0)))
        else:
            total_paneles = 0

    promedio_espaciador = (
        (total_mtl_espaciador / total_paneles) if total_paneles > 0 else 0.0
    )

    # 3. Clasificación de vidrios en las 4 familias oficiales
    if not vidrios_en_dvh.empty:
        vidrios_en_dvh['familia_vidrio'] = vidrios_en_dvh['codigo'].apply(
            clasificar_vidrio
        )
        consumo_familias = (
            vidrios_en_dvh.groupby('familia_vidrio')['m2']
            .sum()
            .reindex(['Float', 'Laminado', 'Templado', 'Texturado', 'Espejo'], fill_value=0.0)
        )
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
    else:
        consumo_familias = pd.Series({'Float': 0.0, 'Laminado': 0.0, 'Templado': 0.0, 'Texturado': 0.0, 'Espejo': 0.0})
        ranking_especifico = pd.DataFrame()

    # 4. Formateo y presentación de resultados
    periodo_txt = (
        f'{mes:02d}/{anio}'
        if mes and anio
        else ('Año ' + str(anio) if anio else 'TOTAL REPORTE')
    )
    print('=' * 75)
    print(f' REPORTE DVH: {nombre_reporte} | PERIODO: {periodo_txt}')
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
    print(' CONSUMO DE VIDRIO EN DVH (PARA "Reporte de Producción DVH.xlsx"):')
    print(
        f"  - Float:     {consumo_familias['Float']:>8.2f} m²"
        f"  |  Laminado:  {consumo_familias['Laminado']:>8.2f} m²"
    )
    print(
        f"  - Templado:  {consumo_familias['Templado']:>8.2f} m²"
        f"  |  Texturado: {consumo_familias['Texturado']:>8.2f} m²"
    )
    if consumo_familias.get('Espejo', 0.0) > 0:
        print(f"  - Espejo:    {consumo_familias['Espejo']:>8.2f} m²")
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

    # 5. Sección de Vidrios Sueltos Detectados (si existen en el archivo)
    if not vidrios_sueltos.empty:
        vidrios_sueltos['familia_vidrio'] = vidrios_sueltos['codigo'].apply(
            clasificar_vidrio
        )
        total_m2_sueltos = vidrios_sueltos['m2'].sum()
        total_piezas_sueltas = int(round(vidrios_sueltos['cantidad'].sum())) if 'cantidad' in vidrios_sueltos.columns else len(vidrios_sueltos)
        familias_sueltas = vidrios_sueltos.groupby('familia_vidrio')['m2'].sum()

        print('-' * 75)
        print(' VIDRIOS SUELTOS DETECTADOS EN EL ARCHIVO (EXCLUIDOS DE DVH):')
        print(f'• Total superficie vidrios sueltos:      {total_m2_sueltos:>10.2f} m² ({total_piezas_sueltas} piezas / {len(vidrios_sueltos)} líneas)')
        for fam, m2_fam in familias_sueltas.items():
            print(f'  - {fam:<15}: {m2_fam:>8.2f} m²')
    print('=' * 75)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Análisis de producción de DVH y ranking de vidrios.'
    )
    parser.add_argument(
        'archivo',
        nargs='?',
        default='csvs',
        help='Ruta al archivo CSV o directorio (por defecto: carpeta csvs)',
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