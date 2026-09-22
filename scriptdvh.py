import argparse
import os
import re
import sys
import pandas as pd


def clasificar_vidrio(codigo: str) -> str:
    if not isinstance(codigo, str):
        return 'No Definido'

    c = codigo.strip()
    if c.startswith('Lam') or 'laminado' in c.lower():
        return 'Laminado'
    if (
        c.startswith('Temp')
        or 'templad' in c.lower()
        or 'puerta standard' in c.lower()
    ):
        return 'Templado'

    texturados = [
        'stipolite',
        'jakare',
        'mosaico',
        'cuadrill',
        'pacifico',
        'pacífico',
        'rombo',
        'dreamline',
    ]
    if re.match(r'^I\d', c) or any(t in c.lower() for t in texturados):
        return 'Texturado / Impreso'

    if (
        re.match(r'^E\d', c)
        or 'espejo' in c.lower()
        or 'optiglass' in c.lower()
    ):
        return 'Espejos'

    floats_derivados = ['coverglass', 'opacid', 'profilit', 'low-e', 'antir']
    if (
        re.match(r'^V\d', c)
        or 'float' in c.lower()
        or any(f in c.lower() for f in floats_derivados)
    ):
        return 'Float y Derivados'

    return 'Otros Vidrios'


def analizar_produccion(ruta_archivo: str, anio: int = None, mes: int = None):
    if not os.path.exists(ruta_archivo):
        print(f"Error: No se encontró el archivo '{ruta_archivo}'")
        sys.exit(1)

    df = pd.read_csv(ruta_archivo)

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

    # Total m² DVH
    filas_dvh = df[
        (df['tipo_producto'] == 'ANEXO')
        & (df['codigo'].str.contains('DVH', na=False, case=False))
    ]
    total_m2_dvh = filas_dvh['cantidad_anexo'].sum()
    presupuestos_con_dvh = filas_dvh['nropresupuesto'].unique()

    # Metros lineales espaciador (perímetro dividido entre 2 caras)
    filas_pulido_dvh = df[
        (df['nropresupuesto'].isin(presupuestos_con_dvh))
        & (df['tipo_producto'] == 'ANEXO')
        & (df['codigo'].str.contains('Pulido', na=False, case=False))
    ]
    mtl_pulido_total = filas_pulido_dvh['cantidad_anexo'].sum()
    total_mtl_espaciador = mtl_pulido_total / 2.0

    # Estimación de paneles
    total_paneles = 0
    for presu in presupuestos_con_dvh:
        pulidos_presu = df[
            (df['nropresupuesto'] == presu)
            & (df['tipo_producto'] == 'ANEXO')
            & (df['codigo'].str.contains('Pulido', na=False, case=False))
        ]
        perimetros_lotes = (
            pulidos_presu['cantidad_anexo'].drop_duplicates().tolist()
        )

        for perimetro in perimetros_lotes:
            vidrios_presu = df[
                (df['nropresupuesto'] == presu)
                & (df['tipo_producto'] == 'VIDRIO')
            ]
            sub_m2 = vidrios_presu[
                vidrios_presu['m2'] > (perimetro / 4) ** 2 * 0.5
            ]['m2'].max()

            if pd.notna(sub_m2) and perimetro > 0:
                if perimetro < 8.0:
                    total_paneles += 1
                else:
                    cortes = df[
                        (df['nropresupuesto'] == presu)
                        & (df['tipo_producto'] == 'VIDRIO')
                        & (df['m2'] == sub_m2)
                    ]['ncorte'].max()
                    total_paneles += (
                        int(cortes) if pd.notna(cortes) and cortes > 0 else 3
                    )
            else:
                total_paneles += 1

    total_paneles = max(1, total_paneles) if len(presupuestos_con_dvh) > 0 else 0
    promedio_espaciador = (
        (total_mtl_espaciador / total_paneles) if total_paneles > 0 else 0.0
    )

    # Clasificación de vidrios
    df['familia_vidrio'] = df.apply(
        lambda r: (
            clasificar_vidrio(r['codigo'])
            if r['tipo_producto'] == 'VIDRIO'
            else '-'
        ),
        axis=1,
    )

    vidrios_en_dvh = df[
        (df['nropresupuesto'].isin(presupuestos_con_dvh))
        & (df['tipo_producto'] == 'VIDRIO')
    ]
    resumen_dvh = (
        vidrios_en_dvh.groupby('familia_vidrio')['m2']
        .agg(['sum', 'count'])
        .rename(columns={'sum': 'm2_total', 'count': 'cant_lineas'})
    )

    resumen_general = (
        df[df['tipo_producto'] == 'VIDRIO']
        .groupby('familia_vidrio')['m2']
        .agg(['sum', 'count'])
        .rename(columns={'sum': 'm2_total', 'count': 'cant_lineas'})
    )

    # Salida por consola
    periodo_txt = (
        f'{mes}/{anio}'
        if mes and anio
        else ('Año ' + str(anio) if anio else 'TOTAL REPORTE')
    )
    print('=' * 68)
    print(f' REPORTE DVH: {os.path.basename(ruta_archivo)} | PERIODO: {periodo_txt}')
    print('=' * 68)
    print(f'• Total m² de DVH fabricados:           {total_m2_dvh:>10.2f} m²')
    print(f'• Total paneles de DVH armados:          {total_paneles:>10} unidades')
    print(f'• Metros lineales perfil espaciador:     {total_mtl_espaciador:>10.2f} mtl')
    print(f'• Promedio espaciador por panel:         {promedio_espaciador:>10.2f} mtl/panel')
    print('-' * 68)
    print(' VIDRIOS UTILIZADOS EN DVH:')
    if not resumen_dvh.empty:
        for fam, f in resumen_dvh.iterrows():
            print(f"  - {fam:<25}: {f['m2_total']:>8.2f} m² ({int(f['cant_lineas'])} líneas)")
    else:
        print('  (No se encontraron registros de DVH)')
    print('-' * 68)
    print(' TOTAL VIDRIO CONSUMIDO POR FAMILIA (GLOBAL):')
    for fam, f in resumen_general.iterrows():
        print(f"  - {fam:<25}: {f['m2_total']:>8.2f} m² ({int(f['cant_lineas'])} líneas)")
    print('=' * 68)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Análisis de producción de DVH desde CSV.'
    )
    parser.add_argument(
        'archivo',
        help='Ruta al archivo CSV (ej. movimientos_diciembre.csv)',
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