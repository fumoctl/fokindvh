#!/usr/bin/env python3
"""
================================================================================
SISTEMA DE CÁLCULO Y CONCILIACIÓN DE PRODUCCIÓN DE VIDRIOS DVH
================================================================================

1. DESCRIPCIÓN GENERAL Y CONTEXTO
---------------------------------
Un DVH (Doble Vidriado Hermético) es una unidad compuesta por dos hojas de
vidrio de idénticas dimensiones perimetrales unidas por un perfil espaciador
de aluminio perimetral con sales deshidratantes y sellado hermético.

En los sistemas ERP del rubro (ej. vidrioluzdb / presupuestos facturados),
un presupuesto o pedido puede contener tanto piezas destinadas a DVH como
piezas sueltas (cortes monolíticos, mamparas, reposiciones). Además, la
facturación incluye ítems de servicio en las líneas de 'ANEXO':
  - Armado / Cámara: 'DVH-M2 DVH para perfilería Cámara *' (en m²).
  - Pulido perimetral: '* pulido recto *' (en metros lineales).

Este script automatiza el cálculo de producción física real, valida los
resultados contra los conceptos facturados y, ante inconsistencias o alertas,
ejecuta un motor de inferencia combinatoria para discernir cuáles piezas
pertenecen al DVH y cuáles son vidrios monolíticos sueltos.


2. METODOLOGÍA DE CÁLCULOS
--------------------------
a) Supuesto de Unidades:
   - Todas las medidas de 'ancho' y 'alto' se interpretan en metros (m).
   - Las áreas resultantes se expresan en metros cuadrados (m²).
   - Los perímetros resultantes se expresan en metros lineales (m).

b) Agrupamiento y Emparejamiento en Pares de 2:
   - Se seleccionan únicamente registros con tipo_producto == 'VIDRIO',
     dimensiones positivas (ancho > 0, alto > 0) y cantidad > 0.
   - Las piezas se agrupan por sus dimensiones físicas redondeadas a 4 decimales:
     grupo_dim = (round(ancho, 4), round(alto, 4)).
   - Dentro de cada grupo de dimensiones idénticas, las piezas se emparejan
     estrictamente en pares de a 2.
   - Prioridad de emparejamiento: Se prioriza emparejar vidrios de distinto tipo
     o código (ej. Lam4+4I con V6G, combinación estándar en DVH), y luego vidrios
     del mismo tipo (ej. V6I con V6I).
   - Regla de descarte: Toda pieza de vidrio que no tenga un par compatible
     (número impar de piezas en la medida) se descarta del cálculo de DVH.

c) Cálculo Físico de Panel:
   - Cada par de vidrios forma exactamente 1 panel de DVH:
     * Área del panel = ancho × alto  (se toma el área de una cara).
     * Perímetro del panel = 2 × (ancho + alto)  (perímetro de espaciador).
   - Área Total = Suma de áreas de todos los paneles formados.
   - Perímetro Total = Suma de perímetros de todos los paneles formados.
   - Promedio de Perímetro = Perímetro Total / Cantidad de Paneles.

d) Verificaciones contra Anexos Facturados:
   - Verificación 1 (Área): Se compara el Área Total calculada contra la suma de
     'cantidad_anexo' donde codigo contiene 'DVH-M2 ... perfilería Cámara *'.
   - Verificación 2 (Perímetro): Se compara el Perímetro Total calculado contra
     la suma de 'cantidad_anexo' donde codigo contiene '* pulido recto *'.
     * El pulido puede facturarse sobre 1 cara (1× perímetro) o sobre ambas
       caras de cada hoja de vidrio (2× perímetro). Ambas condiciones se verifican.
   - Detección de Desviación: Si la diferencia relativa supera el 5% (o tol_area /
     tol_perimetro), se dispara una alerta de revisión manual.

e) Inferencia Combinatoria en caso de Alerta:
   - Si se dispara una alerta y el anexo facturado es menor a la suma total de
     paneles emparejados, significa que en la orden conviven piezas de DVH con
     vidrios monolíticos comunes que casualmente tenían medidas idénticas.
   - El script ejecuta una exploración combinatoria con poda (Branch and Bound):
     evalúa los subconjuntos de paneles candidatos y encuentra la combinación
     óptima que concilia con exactitud el área de cámara y los metros de pulido.
   - Los paneles seleccionados se reportan como DVH confirmado, y las piezas
     restantes se desglosan como vidrios monolíticos / no DVH.


3. GUÍA DE FLAGS Y PARÁMETROS CLI
---------------------------------
Uso básico:
  python scriptdvh.py <archivo.xlsx> [opciones]

Argumento posicional:
  archivo                 Ruta al archivo Excel (ej: excels/pre184420.xlsx).

Opciones y Flags:
  --orden <ORDEN/PRESU>   Filtra el análisis a una orden de pedido específica
                          (ej: ORD-114678-5-2-2) o número de presupuesto.
  --detalle               Imprime el listado desglosado de cada panel DVH
                          formado (dimensiones, vidrios componentes, área y perímetro).
  --json                  Exporta todo el resultado (cálculo bruto, inferencia,
                          alertas y métricas) en formato JSON estructurado.
  --tol-area <FLOAT>      Tolerancia máxima admisible para desviación de área en m²
                          (por defecto: 0.05 m²).
  --tol-perimetro <FLOAT> Tolerancia máxima admisible para desviación de perímetro
                          en metros lineales (por defecto: 0.10 m).
  --no-inferir            Desactiva la inferencia combinatoria automática, mostrando
                          únicamente el emparejamiento bruto y la alerta si la hubiera.
  --forzar-inferir        Fuerza la ejecución de la inferencia combinatoria incluso
                          si el cálculo preliminar no arrojó alerta.

Ejemplos de Ejecución:
  1) Ejecución estándar en entorno Nix Flake:
     nix develop --command python3 scriptdvh.py excels/pre184420.xlsx

  2) Ejecución con shell.nix:
     nix-shell --run "python3 scriptdvh.py excels/pre143306.xlsx"

  3) Ver desglose detallado de paneles:
     python3 scriptdvh.py excels/pre184420.xlsx --detalle

  4) Salida JSON para integración con APIs / pipelines:
     python3 scriptdvh.py excels/pre143306.xlsx --json
================================================================================
"""

import argparse
import json
import os
import sys
import warnings
from collections import Counter, defaultdict
import pandas as pd

# Suprimir advertencias de openpyxl sobre estilos por defecto en libros de Excel
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')


def normalizar_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza y sanea las columnas y tipos de datos del DataFrame de entrada.

    Parámetros:
      df: DataFrame cargado directamente desde el archivo Excel.

    Retorna:
      Copia del DataFrame con nombres de columnas en minúsculas sin espacios
      y columnas críticas convertidas a sus tipos numéricos o de texto correctos.
    """
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
    Agrupa en pares de 2 las piezas de vidrio que comparten exactamente la misma medida física.

    Criterio de emparejamiento:
      1. Se prioriza el ensamble de códigos heterogéneos (distintos entre sí), ya que
         en la industria del DVH la cara exterior suele ser reflectiva/laminada y la
         interior monolítica float (o de espesores disímiles).
      2. Cuando no quedan tipos dispares disponibles, se emparejan piezas con el
         mismo código (DVH homogéneo).
      3. Si el total de piezas de esa dimensión es impar, la pieza sobrante queda sin
         par y se descarta del cálculo de DVH según las especificaciones.

    Parámetros:
      codigos: Lista de códigos de vidrio (strings) con igual dimensión (ancho, alto).

    Retorna:
      tuple:
        - lista de pares [(codigo1, codigo2), ...]
        - lista de códigos sobrantes sin par [codigo_huerfano, ...]
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

    # 3. Piezas sin par compatible
    sobrantes = [c for c, cnt in counts.items() for _ in range(cnt)]
    return pares, sobrantes


def inferir_mejor_combinacion(
    paneles_candidatos: list[dict],
    suma_camara: float,
    suma_pulido: float,
    tol_area: float = 0.05,
    tol_perim: float = 0.10,
    max_evaluaciones: int = 50000
) -> dict | None:
    """
    Infiere el subconjunto óptimo de paneles DVH en caso de alerta por discrepancia.

    CONTEXTO Y MOTIVACIÓN:
      En la práctica comercial del ERP, es muy común que un mismo presupuesto facture:
        1. Paneles de DVH propiamente dichos (que llevan servicio de armado 'Cámara N').
        2. Vidrios comunes / monolíticos sueltos (ej. estantes, hojas de reposición).
      Cuando esos vidrios monolíticos tienen casualmente medidas idénticas entre sí,
      el emparejamiento bruto inicial los agrupa en supuestos paneles de DVH. Al sumar
      sus áreas, el total supera ampliamente la cantidad de m² de armado de Cámara facturado,
      disparando una alerta de revisión manual.

    MÉTODO DE RESOLUCIÓN (Branch and Bound combinatorio):
      1. Agrupa los paneles candidatos por signatura única (ancho, alto, vidrio_1, vidrio_2).
      2. Ordena los grupos dando prioridad a composiciones heterogéneas (vidrios diferentes,
         típico de DVH) y áreas mayores para recortar ramas inviables rápidamente.
      3. Aplica exploración en profundidad (DFS) con poda:
         - Si el área acumulada excede la cuota de cámara más la tolerancia (suma_camara + tol_area),
           la rama se poda inmediatamente.
      4. Función de Evaluación (Score):
         - Minimiza la desviación relativa de Área frente a suma_camara.
         - Minimiza la desviación de Perímetro frente a suma_pulido (evaluando tanto pulido
           de 1 cara como de ambas caras 2x).
         - Otorga una pequeña recompensa a composiciones heterogéneas.

    Parámetros:
      paneles_candidatos: Lista de diccionarios con todos los paneles candidatos emparejados.
      suma_camara: Suma total de m² facturados en 'DVH-M2 ... Cámara *'.
      suma_pulido: Suma total de metros lineales facturados en '* pulido recto *'.
      tol_area: Margen de tolerancia para discrepancia de área en m² (default: 0.05).
      tol_perim: Margen de tolerancia para discrepancia de perímetro en m (default: 0.10).
      max_evaluaciones: Límite de nodos a explorar en el DFS para evitar tiempos de espera.

    Retorna:
      dict con la solución óptima encontrada:
        - 'exitosa': True si concilia dentro de las tolerancias.
        - 'cantidad_paneles': Cantidad de paneles DVH confirmados.
        - 'area_total': Área total de los paneles inferidos.
        - 'perimetro_total': Perímetro total de los paneles inferidos.
        - 'promedio_perimetro': Perímetro promedio por panel.
        - 'tipos_vidrio_usados': Conteo de vidrios asignados a los paneles DVH.
        - 'vidrios_no_dvh': Conteo de vidrios excluidos (cortes sueltos / monolíticos).
        - 'paneles_detalle': Lista de paneles confirmados.
        - 'paneles_excluidos': Lista de paneles descartados.
        - 'verificacion_area': Detalle de la validación de área de la combinación.
        - 'verificacion_perimetro': Detalle de la validación de perímetro de la combinación.
      O None si no hay candidatos o no hay metas de anexos a conciliar.
    """
    if not paneles_candidatos:
        return None
    if suma_camara <= 0 and suma_pulido <= 0:
        return None

    # Agrupar paneles candidatos por firma idéntica (dimensiones y composición)
    group_map = defaultdict(lambda: {'count': 0, 'data': None})
    for p in paneles_candidatos:
        sig = (round(p['ancho'], 4), round(p['alto'], 4), p['vidrio_1'], p['vidrio_2'])
        group_map[sig]['count'] += 1
        group_map[sig]['data'] = p

    cand_groups = []
    for sig, val in group_map.items():
        p = val['data']
        es_hetero = p.get('es_heterogeneo', p['vidrio_1'] != p['vidrio_2'])
        cand_groups.append({
            'sig': sig,
            'count': val['count'],
            'ancho': p['ancho'],
            'alto': p['alto'],
            'vidrio_1': p['vidrio_1'],
            'vidrio_2': p['vidrio_2'],
            'area': p['area'],
            'perimetro': p['perimetro'],
            'es_heterogeneo': es_hetero
        })

    # Priorizar pares heterogéneos y áreas mayores para poda rápida
    cand_groups.sort(key=lambda g: (not g['es_heterogeneo'], -g['area']))

    best = {
        'score': float('inf'),
        'combo': None,
        'diff_area': float('inf'),
        'diff_perim': float('inf'),
        'area_total': 0.0,
        'perimetro_total': 0.0
    }
    evaluaciones = 0

    def dfs(idx: int, curr_area: float, curr_perim: float, curr_combo: list[int]):
        nonlocal evaluaciones
        evaluaciones += 1
        if evaluaciones > max_evaluaciones:
            return

        if idx == len(cand_groups):
            if curr_area <= 0:
                return

            diff_a = abs(curr_area - suma_camara) if suma_camara > 0 else 0.0
            if suma_pulido > 0:
                diff_p = min(abs(curr_perim - suma_pulido), abs(2.0 * curr_perim - suma_pulido))
            else:
                diff_p = 0.0

            hetero_pts = sum(k for k, g in zip(curr_combo, cand_groups) if g['es_heterogeneo'])

            if suma_camara > 0 and suma_pulido > 0:
                rel_a = diff_a / suma_camara
                rel_p = diff_p / (suma_pulido / 2.0)
                score = (rel_a * 100.0) + (rel_p * 10.0) - (hetero_pts * 0.001)
            elif suma_camara > 0:
                score = (diff_a * 100.0) - (hetero_pts * 0.001)
            else:
                score = (diff_p * 100.0) - (hetero_pts * 0.001)

            if score < best['score']:
                best['score'] = score
                best['combo'] = list(curr_combo)
                best['diff_area'] = diff_a
                best['diff_perim'] = diff_p
                best['area_total'] = curr_area
                best['perimetro_total'] = curr_perim
            return

        g = cand_groups[idx]
        for k in range(g['count'], -1, -1):
            if k > 0 and suma_camara > 0 and (curr_area + k * g['area'] > suma_camara + tol_area + 0.05):
                continue
            curr_combo.append(k)
            dfs(idx + 1, curr_area + k * g['area'], curr_perim + k * g['perimetro'], curr_combo)
            curr_combo.pop()

    dfs(0, 0.0, 0.0, [])

    if best['combo'] is None:
        return None

    # Reconstruir paneles seleccionados y no seleccionados
    paneles_inferidos = []
    paneles_excluidos = []
    vidrios_usados_inf = Counter()
    vidrios_no_dvh = Counter()

    for k, g in zip(best['combo'], cand_groups):
        # Paneles confirmados en la inferencia
        for _ in range(k):
            paneles_inferidos.append({
                'ancho': g['ancho'],
                'alto': g['alto'],
                'vidrio_1': g['vidrio_1'],
                'vidrio_2': g['vidrio_2'],
                'area': g['area'],
                'perimetro': g['perimetro'],
                'es_heterogeneo': g['es_heterogeneo']
            })
            vidrios_usados_inf[g['vidrio_1']] += 1
            vidrios_usados_inf[g['vidrio_2']] += 1

        # Paneles excluidos (cortes no DVH / sueltos)
        restantes = g['count'] - k
        for _ in range(restantes):
            paneles_excluidos.append({
                'ancho': g['ancho'],
                'alto': g['alto'],
                'vidrio_1': g['vidrio_1'],
                'vidrio_2': g['vidrio_2'],
                'area': g['area'],
                'perimetro': g['perimetro'],
                'es_heterogeneo': g['es_heterogeneo']
            })
            vidrios_no_dvh[g['vidrio_1']] += 1
            vidrios_no_dvh[g['vidrio_2']] += 1

    cant_paneles_inf = len(paneles_inferidos)
    area_total_inf = best['area_total']
    perimetro_total_inf = best['perimetro_total']
    promedio_perimetro_inf = (perimetro_total_inf / cant_paneles_inf) if cant_paneles_inf > 0 else 0.0

    # Comprobar si la combinación concilia adecuadamente con las tolerancias
    desvio_area_inf = False
    if suma_camara > 0:
        rel_diff_a = best['diff_area'] / suma_camara
        if best['diff_area'] > tol_area and rel_diff_a > 0.01:
            desvio_area_inf = True

    diff_p_1x = abs(perimetro_total_inf - suma_pulido)
    diff_p_2x = abs(2.0 * perimetro_total_inf - suma_pulido)
    coincide_1x = suma_pulido > 0 and (diff_p_1x <= tol_perim or (diff_p_1x / suma_pulido <= 0.02))
    coincide_2x = suma_pulido > 0 and (diff_p_2x <= (tol_perim * 2) or (diff_p_2x / suma_pulido <= 0.02))
    desvio_perim_inf = (suma_pulido > 0) and not (coincide_1x or coincide_2x)

    exitosa = (not desvio_area_inf) and (not desvio_perim_inf)

    return {
        'exitosa': exitosa,
        'cantidad_paneles': cant_paneles_inf,
        'area_total': area_total_inf,
        'perimetro_total': perimetro_total_inf,
        'promedio_perimetro': promedio_perimetro_inf,
        'tipos_vidrio_usados': dict(sorted(vidrios_usados_inf.items())),
        'total_vidrios_usados': sum(vidrios_usados_inf.values()),
        'vidrios_no_dvh': dict(sorted(vidrios_no_dvh.items())),
        'total_vidrios_no_dvh': sum(vidrios_no_dvh.values()),
        'paneles_detalle': paneles_inferidos,
        'paneles_excluidos': paneles_excluidos,
        'verificacion_area': {
            'area_calculada': area_total_inf,
            'area_anexo_camara': suma_camara,
            'diferencia': best['diff_area'],
            'desvio_significativo': desvio_area_inf
        },
        'verificacion_perimetro': {
            'perimetro_calculado': perimetro_total_inf,
            'perimetro_anexo_pulido': suma_pulido,
            'diferencia_1x': diff_p_1x,
            'diferencia_2x': diff_p_2x,
            'coincide_1x': coincide_1x,
            'coincide_2x': coincide_2x,
            'desvio_significativo': desvio_perim_inf
        }
    }


def calcular_produccion_dvh(
    df: pd.DataFrame,
    orden_filtro: str = None,
    tol_area: float = 0.05,
    tol_perim: float = 0.10,
    auto_inferir: bool = True,
    forzar_inferir: bool = False
) -> dict:
    """
    Función principal de procesamiento: calcula la producción de paneles DVH.

    Flujo de ejecución:
      1. Normalización: Limpia encabezados y convierte columnas a tipos numéricos estándar.
      2. Filtrado opcional: Si se provee `orden_filtro`, aísla los registros de esa orden o presupuesto.
      3. Filtrado de vidrios: Conserva únicamente filas con tipo_producto == 'VIDRIO',
         ancho > 0, alto > 0 y cantidad > 0.
      4. Agrupamiento dimensional: Agrupa piezas con ancho y alto idénticos (4 decimales).
      5. Emparejamiento en pares de 2: Llama a `emparejar_piezas_dimension()` para formar paneles.
         Las piezas impares sin par compatible se descartan del cálculo de DVH.
      6. Cálculo físico de paneles:
         - Área de cada panel = ancho × alto  (área de una cara del DVH).
         - Perímetro de cada panel = 2 × (ancho + alto)  (perímetro lineal de espaciador).
      7. Verificaciones cruzadas:
         - Se suma 'cantidad_anexo' de 'DVH-M2 ... Cámara *' (Área facturada).
         - Se suma 'cantidad_anexo' de '* pulido recto *' (Metros lineales de pulido).
         - Se evalúan las diferencias contra tol_area y tol_perim (alerta si desvío > 5%).
      8. Inferencia inteligente:
         - Si se dispara alerta y auto_inferir=True (o forzar_inferir=True), se invoca
           `inferir_mejor_combinacion()` para identificar qué piezas formaban verdaderamente
           el DVH y cuáles eran vidrios monolíticos o sueltos.
         - Si la inferencia concilia con éxito, los valores finales adoptan la combinación
           correcta y las piezas excluidas se transparentan en el reporte.

    Parámetros:
      df: DataFrame con la información del Excel.
      orden_filtro: Identificador de orden o presupuesto para filtrar (opcional).
      tol_area: Tolerancia máxima admisible para diferencia de área en m² (default: 0.05).
      tol_perim: Tolerancia máxima admisible para diferencia de perímetro en m (default: 0.10).
      auto_inferir: Si True, activa la inferencia combinatoria ante alertas (default: True).
      forzar_inferir: Si True, ejecuta la inferencia incluso si no hubo alerta previa (default: False).

    Retorna:
      dict consolidado con métricas de producción, verificaciones, alertas, detalle de paneles,
      y los bloques 'calculo_bruto' e 'inferencia'.
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
                'perimetro': perimetro_panel,
                'es_heterogeneo': c1 != c2
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

    # Análisis de desvío de área (cálculo bruto)
    diff_area = abs(area_total - suma_camara)
    desvio_area = False
    if tiene_camara:
        rel_diff_area = diff_area / suma_camara
        if diff_area > tol_area and rel_diff_area > 0.01:
            desvio_area = True

    # Análisis de desvío de perímetro (cálculo bruto)
    diff_perim_1x = abs(perimetro_total - suma_pulido)
    diff_perim_2x = abs(2 * perimetro_total - suma_pulido)
    coincide_pulido_1x = tiene_pulido and (diff_perim_1x <= tol_perim or (diff_perim_1x / suma_pulido <= 0.02))
    coincide_pulido_2x = tiene_pulido and (diff_perim_2x <= (tol_perim * 2) or (diff_perim_2x / suma_pulido <= 0.02))

    desvio_perimetro = False
    if tiene_pulido:
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

    # Registro del cálculo bruto antes de cualquier inferencia
    calculo_bruto = {
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
        'alertas': list(alertas),
        'requiere_revision_manual': len(alertas) > 0
    }

    # --- INFERENCIA EN CASO DE ALERTA O DISCREPANCIA ---
    debe_intentar_inferir = (
        (forzar_inferir or (auto_inferir and (desvio_area or desvio_perimetro)))
        and (tiene_camara or tiene_pulido)
    )

    inferencia_info = None
    inferencia_aplicada = False

    if debe_intentar_inferir:
        inferencia_res = inferir_mejor_combinacion(
            paneles_candidatos=paneles,
            suma_camara=suma_camara,
            suma_pulido=suma_pulido,
            tol_area=tol_area,
            tol_perim=tol_perim
        )

        if inferencia_res and (inferencia_res['exitosa'] or forzar_inferir):
            inferencia_aplicada = True
            inferencia_info = inferencia_res

            # Actualizar los valores principales con los datos inferidos que concilian
            cant_paneles = inferencia_res['cantidad_paneles']
            area_total = inferencia_res['area_total']
            perimetro_total = inferencia_res['perimetro_total']
            promedio_perimetro = inferencia_res['promedio_perimetro']
            vidrios_usados = Counter(inferencia_res['tipos_vidrio_usados'])
            paneles = inferencia_res['paneles_detalle']
            v_area_inf = inferencia_res['verificacion_area']
            v_perim_inf = inferencia_res['verificacion_perimetro']

            verificacion_area = {
                'area_calculada': area_total,
                'area_anexo_camara': suma_camara,
                'tiene_registro': tiene_camara,
                'diferencia': v_area_inf['diferencia'],
                'desvio_significativo': v_area_inf['desvio_significativo']
            }
            verificacion_perimetro = {
                'perimetro_calculado': perimetro_total,
                'perimetro_anexo_pulido': suma_pulido,
                'tiene_registro': tiene_pulido,
                'diferencia_1x': v_perim_inf['diferencia_1x'],
                'diferencia_2x': v_perim_inf['diferencia_2x'],
                'coincide_1x': v_perim_inf['coincide_1x'],
                'coincide_2x': v_perim_inf['coincide_2x'],
                'desvio_significativo': v_perim_inf['desvio_significativo']
            }
        else:
            verificacion_area = calculo_bruto['verificacion_area']
            verificacion_perimetro = calculo_bruto['verificacion_perimetro']
    else:
        verificacion_area = calculo_bruto['verificacion_area']
        verificacion_perimetro = calculo_bruto['verificacion_perimetro']

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
        'verificacion_area': verificacion_area,
        'verificacion_perimetro': verificacion_perimetro,
        'alertas': alertas,
        'requiere_revision_manual': (len(alertas) > 0 and not inferencia_aplicada),
        'inferencia_aplicada': inferencia_aplicada,
        'inferencia': inferencia_info,
        'calculo_bruto': calculo_bruto
    }


def formatear_reporte(resultado: dict, archivo: str = "", mostrar_detalle: bool = False) -> str:
    """
    Construye un reporte en texto enriquecido y estructurado para consola.

    Secciones del reporte:
      1. Encabezado: Archivo, presupuesto(s) y órdenes involucradas.
      2. Alerta preliminar (si aplica): Explica la discrepancia detectada en el
         emparejamiento bruto y el motivo por el cual se activó la inferencia combinatoria.
      3. Resultados de producción:
         - Cantidad total de paneles DVH (en unidades enteras).
         - Área total de paneles (en m² con 4 y 2 decimales).
         - Perímetro total de paneles (en m lineales).
         - Promedio de perímetro por panel.
      4. Tipos de vidrio usados en DVH: Desglose por código y unidades enteras.
      5. Piezas no DVH (si hubo inferencia): Vidrios monolíticos o sueltos que fueron
         excluidos de la producción de DVH tras conciliar con la facturación.
      6. Piezas sin par compatible: Vidrios que sobraron por número impar en su medida.
      7. Verificaciones contra anexos:
         - Comparativa de Área calculada vs Anexo Cámara facturado.
         - Comparativa de Perímetro calculado vs Anexo Pulido recto facturado (1 cara o 2 caras).
      8. Alerta de revisión manual (si persiste discrepancia no resuelta).
      9. Desglose individual de paneles (opcional con --detalle).

    Parámetros:
      resultado: Diccionario retornado por `calcular_produccion_dvh()`.
      archivo: Ruta del archivo analizado para mostrar en el encabezado.
      mostrar_detalle: Si True, imprime cada panel individual con su medida y vidrios.

    Retorna:
      String formateado listo para impresión en terminal.
    """
    lineas = []
    lineas.append("=" * 66)
    lineas.append("           REPORTE DE PRODUCCIÓN DE VIDRIOS DVH")
    lineas.append("=" * 66)

    if archivo:
        lineas.append(f"Archivo: {archivo}")
    if resultado['presupuestos']:
        lineas.append(f"Presupuesto(s): {', '.join(resultado['presupuestos'])}")
    if resultado['ordenes']:
        lineas.append(f"Orden(es): {', '.join(resultado['ordenes'][:4])}{'...' if len(resultado['ordenes']) > 4 else ''}")

    # Si se aplicó inferencia tras una alerta, se informa el contexto
    if resultado.get('inferencia_aplicada'):
        cb = resultado['calculo_bruto']
        lineas.append("-" * 66)
        lineas.append("[!] ALERTA EN CÁLCULO PRELIMINAR (EMPAREJAMIENTO TOTAL):")
        for alerta in cb['alertas']:
            lineas.append(f"    • {alerta}")
        lineas.append(
            f"    -> Se emparejaron inicialmente {cb['cantidad_paneles']} paneles ({cb['area_total']:.2f} m²), "
            f"pero el anexo facturado es menor."
        )
        lineas.append("    -> Se ejecutó inferencia combinatoria para hallar el subconjunto DVH exacto.")
        lineas.append("-" * 66)
        lineas.append("RESULTADOS DE PRODUCCIÓN DVH (INFERENCIA POR AJUSTE CON ANEXOS):")
    else:
        lineas.append("-" * 66)
        lineas.append("RESULTADOS DE PRODUCCIÓN DVH:")

    lineas.append(f"  • Cantidad de paneles DVH:      {resultado['cantidad_paneles']} unidades")
    lineas.append(f"  • Área total de paneles DVH:    {resultado['area_total']:.4f} m² ({resultado['area_total']:.2f} m²)")
    lineas.append(f"  • Perímetro total de paneles:   {resultado['perimetro_total']:.4f} m ({resultado['perimetro_total']:.2f} m)")
    lineas.append(f"  • Promedio de perímetro/panel:  {resultado['promedio_perimetro']:.4f} m ({resultado['promedio_perimetro']:.2f} m)")

    lineas.append("\nTIPOS DE VIDRIO USADOS EN DVH:")
    if resultado['tipos_vidrio_usados']:
        for tipo, cant in resultado['tipos_vidrio_usados'].items():
            lineas.append(f"  • {tipo}: {cant} unidades")
        lineas.append(f"  Total piezas de vidrio ensambladas: {resultado['total_vidrios_usados']} unidades")
    else:
        lineas.append("  (Ninguna pieza emparejada)")

    # Si hubo inferencia, mostrar vidrios restantes que eran cortes sueltos / no DVH
    if resultado.get('inferencia_aplicada') and resultado['inferencia']['vidrios_no_dvh']:
        lineas.append("\nPIEZAS NO DVH (CORTES SUELTOS / EXCLUIDOS TRAS CONCILIACIÓN):")
        for tipo, cant in resultado['inferencia']['vidrios_no_dvh'].items():
            lineas.append(f"  • {tipo}: {cant} unidades")
        lineas.append(f"  Total piezas descartadas de DVH: {resultado['inferencia']['total_vidrios_no_dvh']} unidades")

    if resultado['total_vidrios_descartados'] > 0:
        lineas.append("\nPIEZAS DE VIDRIO SIN PAR COMPATIBLE:")
        for tipo, cant in resultado['vidrios_descartados'].items():
            lineas.append(f"  • {tipo}: {cant} unidades")

    lineas.append("-" * 66)
    if resultado.get('inferencia_aplicada'):
        lineas.append("VERIFICACIONES DE LA COMBINACIÓN INFERIDA CONTRA ANEXOS:")
    else:
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
        lineas.append("-" * 66)
        lineas.append("[!] ALERTA DE REVISIÓN MANUAL:")
        for alerta in resultado['alertas']:
            lineas.append(f"    • {alerta}")
        lineas.append("    Nota: Los paneles calculados se mantienen como válidos según")
        lineas.append("    especificaciones, pero se sugiere inspección manual.")

    if mostrar_detalle and resultado['paneles_detalle']:
        lineas.append("-" * 66)
        lineas.append("DETALLE DE PANELES DVH FORMADOS:")
        for idx, p in enumerate(resultado['paneles_detalle'], 1):
            lineas.append(
                f"  Panel #{idx:02d}: {p['ancho']:.3f}m x {p['alto']:.3f}m | "
                f"Vidrios: {p['vidrio_1']} + {p['vidrio_2']} | "
                f"Área: {p['area']:.4f}m² | Perím: {p['perimetro']:.4f}m"
            )

    lineas.append("=" * 66)
    return "\n".join(lineas)


def main():
    """
    Punto de entrada CLI del script. Procesa argumentos de línea de comandos,
    gestiona la lectura del archivo Excel, ejecuta el cálculo y formatea la salida.
    """
    parser = argparse.ArgumentParser(
        description=(
            "================================================================================\n"
            "CÁLCULO DE PRODUCCIÓN DE VIDRIOS DVH (Doble Vidriado Hermético)\n"
            "================================================================================\n"
            "Procesa archivos Excel exportados del sistema ERP, agrupa piezas de vidrio en\n"
            "pares compatibles de igual medida, calcula el área y perímetro de cada panel DVH,\n"
            "y realiza verificaciones cruzadas contra los anexos facturados (Cámara y Pulido).\n"
            "En caso de alerta por discrepancia, ejecuta inferencia combinatoria para conciliar\n"
            "la producción real separando cortes monolíticos sueltos."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--------------------------------------------------------------------------------\n"
            "RESUMEN DE CÁLCULOS Y REGLAS:\n"
            "  1. Medidas: ancho y alto en metros lineales (m), áreas en m².\n"
            "  2. Emparejamiento: Estrictamente de a 2 piezas con igual ancho y alto.\n"
            "     Las piezas impares sin par compatible se descartan del cálculo de DVH.\n"
            "  3. Métricas por panel: Área = ancho × alto | Perímetro = 2 × (ancho + alto).\n"
            "  4. Validación de Área: Suma de áreas vs 'DVH-M2 DVH para perfilería Cámara *'.\n"
            "  5. Validación de Perímetro: Suma de perímetros vs '* pulido recto *' (1x o 2x).\n"
            "  6. Alertas e Inferencia: Si el cálculo difiere significativamente del anexo,\n"
            "     se activa una búsqueda combinatoria Branch-and-Bound para hallar el subconjunto\n"
            "     exacto de paneles que concilia con la facturación.\n"
            "\n"
            "EJEMPLOS DE USO:\n"
            "  • Ejecución básica:\n"
            "      python scriptdvh.py excels/pre184420.xlsx\n"
            "  • Con entorno Nix:\n"
            "      nix develop --command python3 scriptdvh.py excels/pre184420.xlsx\n"
            "      nix-shell --run \"python3 scriptdvh.py excels/pre143306.xlsx\"\n"
            "  • Mostrar detalle de cada panel:\n"
            "      python scriptdvh.py excels/pre184420.xlsx --detalle\n"
            "  • Filtrar una orden de pedido específica:\n"
            "      python scriptdvh.py excels/pre143306.xlsx --orden ORD-089176-2-1-3\n"
            "  • Exportar en formato JSON:\n"
            "      python scriptdvh.py excels/pre184420.xlsx --json\n"
            "--------------------------------------------------------------------------------\n"
        )
    )
    parser.add_argument(
        'archivo',
        help="Ruta al archivo Excel con los datos de producción (ej: excels/pre184420.xlsx)"
    )
    parser.add_argument(
        '--orden',
        help="Filtrar por orden específica (ej: ORD-114678-5-2-2) o número de presupuesto",
        default=None
    )
    parser.add_argument(
        '--detalle',
        action='store_true',
        help="Mostrar desglose individual de cada panel DVH formado (medidas y vidrios)"
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help="Exportar el resultado completo en formato JSON estructurado"
    )
    parser.add_argument(
        '--tol-area',
        type=float,
        default=0.05,
        help="Tolerancia máxima admisible para diferencia de área en m² (default: 0.05)"
    )
    parser.add_argument(
        '--tol-perimetro',
        type=float,
        default=0.10,
        help="Tolerancia máxima admisible para diferencia de perímetro en metros (default: 0.10)"
    )
    parser.add_argument(
        '--no-inferir',
        action='store_true',
        help="Desactivar la inferencia combinatoria automática en caso de alerta"
    )
    parser.add_argument(
        '--forzar-inferir',
        action='store_true',
        help="Forzar la revisión de combinaciones incluso si no se detectó alerta previa"
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
            tol_perim=args.tol_perimetro,
            auto_inferir=not args.no_inferir,
            forzar_inferir=args.forzar_inferir
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

