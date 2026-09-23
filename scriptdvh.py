#!/usr/bin/env python3
"""
Cálculo de producción de vidrios DVH (Doble Vidriado Hermético).
Agrupa piezas de vidrio en pares de igual medida física y valida
las áreas y perímetros totales contra los conceptos facturados en anexos.
"""

import argparse
import json
import os
import sys
import warnings
from collections import Counter, defaultdict
import pandas as pd

# Suprimir advertencias de openpyxl sobre estilos por defecto
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


def normalizar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza nombres de columnas y tipos de datos básicos."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    if 'tipo_producto' in df.columns:
        df['tipo_producto'] = df['tipo_producto'].astype(str).str.strip().str.upper()
    if 'codigo' in df.columns:
        df['codigo'] = df['codigo'].astype(str).str.strip()
    if 'cantidad' in df.columns:
        df['cantidad'] = pd.to_numeric(df['cantidad'], errors='coerce').fillna(0).astype(int)
    if 'ancho' in df.columns:
        df['ancho'] = pd.to_numeric(df['ancho'], errors='coerce').fillna(0.0)
    if 'alto' in df.columns:
        df['alto'] = pd.to_numeric(df['alto'], errors='coerce').fillna(0.0)
    if 'cantidad_anexo' in df.columns:
        df['cantidad_anexo'] = pd.to_numeric(df['cantidad_anexo'], errors='coerce').fillna(0.0)
    if 'ordennro' in df.columns:
        df['ordennro'] = df['ordennro'].astype(str).str.strip()
    if 'nropresupuesto' in df.columns:
        df['nropresupuesto'] = df['nropresupuesto'].astype(str).str.strip()

    return df


def emparejar_piezas_dimension(codigos: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Agrupa en pares de a 2 las piezas de vidrio con igual medida.
    Prioriza emparejar códigos distintos (composición típica de DVH como Laminado + Float),
    y luego códigos idénticos.
    Retorna: (lista de pares (c1, c2), lista de piezas sobrantes sin par).
    """
    counts = Counter(codigos)
    pares = []

    # 1. Emparejar piezas con códigos distintos primero
    while len([c for c, cnt in counts.items() if cnt > 0]) >= 2:
        top2 = counts.most_common(2)
        c1, c2 = top2[0][0], top2[1][0]
        pares.append((c1, c2))
        counts[c1] -= 1
        counts[c2] -= 1

    # 2. Emparejar piezas restantes con el mismo código
    for c, cnt in list(counts.items()):
        while cnt >= 2:
            pares.append((c, c))
            cnt -= 2
            counts[c] -= 2

    sobrantes = [c for c, cnt in counts.items() for _ in range(cnt)]
    return pares, sobrantes


def calcular_produccion_dvh(
    df: pd.DataFrame,
    orden_filtro: str = None,
    tol_area: float = 0.05,
    tol_perim: float = 0.10
) -> dict:
    """
    Calcula la producción de DVH en base a las piezas de vidrio del DataFrame.
    """
    df = normalizar_dataframe(df)

    if orden_filtro:
        orden_filtro_clean = orden_filtro.strip().upper()
        mask = (
            df['ordennro'].str.upper().str.contains(orden_filtro_clean, na=False) |
            df['nropresupuesto'].str.upper().str.contains(orden_filtro_clean, na=False)
        )
        df_trabajo = df[mask].copy()
        if df_trabajo.empty:
            raise ValueError(f"No se encontraron registros que coincidan con la orden/presupuesto '{orden_filtro}'")
    else:
        df_trabajo = df.copy()

    presupuestos = [p for p in df_trabajo['nropresupuesto'].unique() if p and p != '-']
    ordenes = [o for o in df_trabajo['ordennro'].unique() if o and o != '-']

    # Filtrar únicamente piezas de vidrio válidas
    df_vidrios = df_trabajo[
        (df_trabajo['tipo_producto'] == 'VIDRIO') &
        (df_trabajo['ancho'] > 0) &
        (df_trabajo['alto'] > 0) &
        (df_trabajo['cantidad'] > 0)
    ].copy()

    # Agrupar piezas por dimensión física (ancho, alto en metros con 4 decimales)
    grupos_dimensiones = defaultdict(list)
    for _, row in df_vidrios.iterrows():
        w = round(float(row['ancho']), 4)
        h = round(float(row['alto']), 4)
        cant = int(row['cantidad'])
        codigo = str(row['codigo'])
        grupos_dimensiones[(w, h)].extend([codigo] * cant)

    paneles = []
    vidrios_usados = Counter()
    vidrios_descartados = Counter()

    for (w, h), codigos in sorted(grupos_dimensiones.items()):
        pares, sobrantes = emparejar_piezas_dimension(codigos)

        # Cada par representa 1 panel de DVH
        # Área y perímetro de cada par dividido entre dos (equivale al de 1 panel)
        area_panel = w * h
        perimetro_panel = 2.0 * (w + h)

        for c1, c2 in pares:
            paneles.append({
                'ancho': w,
                'alto': h,
                'vidrio_1': c1,
                'vidrio_2': c2,
                'area': area_panel,
                'perimetro': perimetro_panel
            })
            vidrios_usados[c1] += 1
            vidrios_usados[c2] += 1

        for s in sobrantes:
            vidrios_descartados[s] += 1

    cant_paneles = len(paneles)
    area_total = sum(p['area'] for p in paneles)
    perimetro_total = sum(p['perimetro'] for p in paneles)
    promedio_perimetro = (perimetro_total / cant_paneles) if cant_paneles > 0 else 0.0

    # --- VERIFICACIONES CONTRA ANEXOS ---
    # 1. Verificación de Área: DVH-M2 DVH para perfilería Cámara *
    filas_camara = df_trabajo[
        (df_trabajo['tipo_producto'] == 'ANEXO') &
        (df_trabajo['codigo'].str.contains(r'DVH-M2.*perfiler[ií]a.*C[aá]mara', case=False, regex=True))
    ]
    suma_camara = float(filas_camara['cantidad_anexo'].sum())
    tiene_camara = not filas_camara.empty and suma_camara > 0

    # 2. Verificación de Perímetro: * pulido recto *
    filas_pulido = df_trabajo[
        (df_trabajo['tipo_producto'] == 'ANEXO') &
        (df_trabajo['codigo'].str.contains('pulido recto', case=False, regex=False))
    ]
    suma_pulido = float(filas_pulido['cantidad_anexo'].sum())
    tiene_pulido = not filas_pulido.empty and suma_pulido > 0

    # Análisis de desvío de área
    diff_area = abs(area_total - suma_camara)
    desvio_area = False
    if tiene_camara:
        rel_diff_area = diff_area / suma_camara
        if diff_area > tol_area and rel_diff_area > 0.01:
            desvio_area = True

    # Análisis de desvío de perímetro
    diff_perim_1x = abs(perimetro_total - suma_pulido)
    diff_perim_2x = abs(2 * perimetro_total - suma_pulido)
    coincide_pulido_1x = tiene_pulido and (diff_perim_1x <= tol_perim or (diff_perim_1x / suma_pulido <= 0.02))
    coincide_pulido_2x = tiene_pulido and (diff_perim_2x <= (tol_perim * 2) or (diff_perim_2x / suma_pulido <= 0.02))

    desvio_perimetro = False
    if tiene_pulido:
        # Se verifica si el cálculo coincide con el pulido de 1 cara o 2 caras
        if not (coincide_pulido_1x or coincide_pulido_2x):
            desvio_perimetro = True

    alertas = []
    if desvio_area:
        alertas.append(
            f"El área total calculada ({area_total:.2f} m²) difiere significativamente del anexo "
            f"de Cámara DVH facturado ({suma_camara:.2f} m²). Diferencia: {diff_area:.2f} m²."
        )
    if desvio_perimetro:
        alertas.append(
            f"El perímetro total calculado ({perimetro_total:.2f} m) difiere significativamente del anexo "
            f"de Pulido recto facturado ({suma_pulido:.2f} m). Diferencia directa: {diff_perim_1x:.2f} m."
        )

    return {
        'presupuestos': presupuestos,
        'ordenes': ordenes,
        'cantidad_paneles': cant_paneles,
        'area_total': area_total,
        'perimetro_total': perimetro_total,
        'promedio_perimetro': promedio_perimetro,
        'tipos_vidrio_usados': dict(sorted(vidrios_usados.items())),
        'total_vidrios_usados': sum(vidrios_usados.values()),
        'vidrios_descartados': dict(sorted(vidrios_descartados.items())),
        'total_vidrios_descartados': sum(vidrios_descartados.values()),
        'paneles_detalle': paneles,
        'verificacion_area': {
            'area_calculada': area_total,
            'area_anexo_camara': suma_camara,
            'tiene_registro': tiene_camara,
            'diferencia': diff_area,
            'desvio_significativo': desvio_area
        },
        'verificacion_perimetro': {
            'perimetro_calculado': perimetro_total,
            'perimetro_anexo_pulido': suma_pulido,
            'tiene_registro': tiene_pulido,
            'diferencia_1x': diff_perim_1x,
            'diferencia_2x': diff_perim_2x,
            'coincide_1x': coincide_pulido_1x,
            'coincide_2x': coincide_pulido_2x,
            'desvio_significativo': desvio_perimetro
        },
        'alertas': alertas,
        'requiere_revision_manual': len(alertas) > 0
    }


def formatear_reporte(resultado: dict, archivo: str = "", mostrar_detalle: bool = False) -> str:
    """Genera un reporte en texto con formato limpio y legible."""
    lineas = []
    lineas.append("=" * 64)
    lineas.append("         REPORTE DE PRODUCCIÓN DE VIDRIOS DVH")
    lineas.append("=" * 64)

    if archivo:
        lineas.append(f"Archivo: {archivo}")
    if resultado['presupuestos']:
        lineas.append(f"Presupuesto(s): {', '.join(resultado['presupuestos'])}")
    if resultado['ordenes']:
        lineas.append(f"Orden(es): {', '.join(resultado['ordenes'][:4])}{'...' if len(resultado['ordenes']) > 4 else ''}")

    lineas.append("-" * 64)
    lineas.append("RESULTADOS DE PRODUCCIÓN:")
    lineas.append(f"  • Cantidad de paneles DVH:      {resultado['cantidad_paneles']} unidades")
    lineas.append(f"  • Área total de paneles DVH:    {resultado['area_total']:.4f} m² ({resultado['area_total']:.2f} m²)")
    lineas.append(f"  • Perímetro total de paneles:   {resultado['perimetro_total']:.4f} m ({resultado['perimetro_total']:.2f} m)")
    lineas.append(f"  • Promedio de perímetro/panel:  {resultado['promedio_perimetro']:.4f} m ({resultado['promedio_perimetro']:.2f} m)")

    lineas.append("\nTIPOS DE VIDRIO USADOS (EN PANELES DVH):")
    if resultado['tipos_vidrio_usados']:
        for tipo, cant in resultado['tipos_vidrio_usados'].items():
            lineas.append(f"  • {tipo}: {cant} unidades")
        lineas.append(f"  Total piezas de vidrio ensambladas: {resultado['total_vidrios_usados']} unidades")
    else:
        lineas.append("  (Ninguna pieza emparejada)")

    if resultado['total_vidrios_descartados'] > 0:
        lineas.append("\nPIEZAS DE VIDRIO DESCARTADAS (SIN PAR COMPATIBLE):")
        for tipo, cant in resultado['vidrios_descartados'].items():
            lineas.append(f"  • {tipo}: {cant} unidades")

    lineas.append("-" * 64)
    lineas.append("VERIFICACIONES CONTRA ANEXOS:")

    # 1. Área
    v_area = resultado['verificacion_area']
    lineas.append("1. Área total DVH vs Anexo Cámara (DVH-M2 DVH para perfilería Cámara *):")
    lineas.append(f"   - Área calculada:         {v_area['area_calculada']:.2f} m²")
    lineas.append(f"   - Anexo Cámara facturado: {v_area['area_anexo_camara']:.2f} m²")
    if not v_area['tiene_registro']:
        lineas.append("   - Estado: [SIN ANEXO] No se encontraron registros de Cámara DVH.")
    elif v_area['desvio_significativo']:
        lineas.append("   - Estado: [DESVIACIÓN SIGNIFICATIVA] Requiere revisión.")
        lineas.append(f"             Diferencia: {v_area['diferencia']:.2f} m²")
    else:
        lineas.append("   - Estado: [CORRECTO] Coincide con la perfilería facturada.")

    # 2. Perímetro
    v_perim = resultado['verificacion_perimetro']
    lineas.append("\n2. Perímetro total DVH vs Anexo Pulido recto (* pulido recto *):")
    lineas.append(f"   - Perímetro calculado:     {v_perim['perimetro_calculado']:.2f} m")
    lineas.append(f"   - Anexo Pulido facturado:   {v_perim['perimetro_anexo_pulido']:.2f} m")
    if not v_perim['tiene_registro']:
        lineas.append("   - Estado: [SIN ANEXO] No se encontraron registros de Pulido recto.")
    elif v_perim['coincide_1x']:
        lineas.append("   - Estado: [CORRECTO] Coincide con el pulido perimetral (1 cara).")
    elif v_perim['coincide_2x']:
        lineas.append("   - Estado: [CORRECTO / 2 CARAS] Coincide exactamente con el pulido")
        lineas.append(f"             de ambas caras del DVH (2 x {v_perim['perimetro_calculado']:.2f} m = {2*v_perim['perimetro_calculado']:.2f} m).")
    else:
        lineas.append("   - Estado: [DESVIACIÓN SIGNIFICATIVA] Requiere revisión.")
        lineas.append(f"             Diferencia directa: {v_perim['diferencia_1x']:.2f} m")

    if resultado['requiere_revision_manual']:
        lineas.append("-" * 64)
        lineas.append("[!] ALERTA DE REVISIÓN MANUAL:")
        for alerta in resultado['alertas']:
            lineas.append(f"    • {alerta}")
        lineas.append("    Nota: Los paneles calculados se mantienen como válidos según")
        lineas.append("    especificaciones, pero se sugiere inspección manual.")

    if mostrar_detalle and resultado['paneles_detalle']:
        lineas.append("-" * 64)
        lineas.append("DETALLE DE PANELES DVH FORMADOS:")
        for idx, p in enumerate(resultado['paneles_detalle'], 1):
            lineas.append(
                f"  Panel #{idx:02d}: {p['ancho']:.3f}m x {p['alto']:.3f}m | "
                f"Vidrios: {p['vidrio_1']} + {p['vidrio_2']} | "
                f"Área: {p['area']:.4f}m² | Perím: {p['perimetro']:.4f}m"
            )

    lineas.append("=" * 64)
    return "\n".join(lineas)


def main():
    parser = argparse.ArgumentParser(
        description="Cálculo de producción de vidrios DVH a partir de un archivo Excel."
    )
    parser.add_argument(
        'archivo',
        help="Ruta al archivo Excel (ej: excels/pre184420.xlsx)"
    )
    parser.add_argument(
        '--orden',
        help="Filtrar por orden específica o presupuesto",
        default=None
    )
    parser.add_argument(
        '--detalle',
        action='store_true',
        help="Mostrar desglose individual de cada panel DVH formado"
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help="Exportar resultado en formato JSON"
    )
    parser.add_argument(
        '--tol-area',
        type=float,
        default=0.05,
        help="Tolerancia máxima para diferencia de área en m² (default: 0.05)"
    )
    parser.add_argument(
        '--tol-perimetro',
        type=float,
        default=0.10,
        help="Tolerancia máxima para diferencia de perímetro en metros (default: 0.10)"
    )

    args = parser.parse_args()

    if not os.path.exists(args.archivo):
        print(f"Error: El archivo '{args.archivo}' no existe.", file=sys.stderr)
        sys.exit(1)

    try:
        df = pd.read_excel(args.archivo)
    except Exception as e:
        print(f"Error al leer el archivo Excel '{args.archivo}': {e}", file=sys.stderr)
        sys.exit(1)

    try:
        resultado = calcular_produccion_dvh(
            df,
            orden_filtro=args.orden,
            tol_area=args.tol_area,
            tol_perim=args.tol_perimetro
        )
    except Exception as e:
        print(f"Error en el cálculo: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
    else:
        print(formatear_reporte(resultado, archivo=args.archivo, mostrar_detalle=args.detalle))


if __name__ == '__main__':
    main()

