import streamlit as st

st.session_state.update(st.session_state)
for k, v in st.session_state.items():
    st.session_state[k] = v

from PIL import Image
import os

path = os.path.dirname(__file__)
my_file = path + '/pages/images/mechub_logo.png'
img = Image.open(my_file)

st.set_page_config(
    page_title='HX Modeler',
    layout="wide",
    page_icon=img
)

st.sidebar.image(img)
st.sidebar.markdown(
    "[![YouTube](https://img.shields.io/badge/YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@MecHub?sub_confirmation=1) [![GitHub](https://img.shields.io/badge/GitHub-100000?style=for-the-badge&logo=github&logoColor=white)](https://github.com/GitMecHub)")

hide_menu = '''
        <style>
        #MainMenu {visibility: hidden; }
        footer {visibility: hidden;}
        </style>
        '''
st.markdown(hide_menu, unsafe_allow_html=True)

import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.path import Path as mplPath  # Para testar se um ponto está dentro de um contorno (interferência)

import cadquery as cq
from cadquery import exporters

from stl import mesh  # numpy-stl (display do CAD, no lugar do pyvista)
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import os  # Required to check if the file exists
from os import listdir
from os.path import isfile, join

import io
import time
import tempfile


################# FUNCTIONS #################

def scale_xy_airfoil(points, h_c, chord):
    # Separar coordenadas x e y
    # x_coords, y_coords = zip(*points)

    # Criar o gráfico
    # plt.figure(figsize=(10, 5))
    # plt.plot(x_coords, y_coords, marker='o', markersize=2, linewidth=1)
    # plt.title("Curva gerada pelos pontos fornecidos")
    # plt.xlabel("x")
    # plt.ylabel("y")
    # plt.grid(True)
    # plt.axis("equal")
    # plt.show()

    height = h_c * chord

    max_y = max(points, key=lambda point: point[1])[1]
    min_y = min(points, key=lambda point: point[1])[1]
    max_height = max_y - min_y

    pontos = [(x * chord, y * (height / max_height)) for x, y in points]

    # Separar coordenadas x e y
    # x_coords, y_coords = zip(*pontos)

    # Criar o gráfico
    # plt.figure(figsize=(10, 5))
    # plt.plot(x_coords, y_coords, marker='o', markersize=2, linewidth=1)
    # plt.title("Curva gerada pelos pontos fornecidos")
    # plt.xlabel("x")
    # plt.ylabel("y")
    # plt.grid(True)
    # plt.axis("equal")
    # plt.show()

    return pontos


def calcular_dimensoes(c, h_c, p_v_h, p_h_c):
    espessura_h = h_c * c
    pitch_vertical = p_v_h * espessura_h
    pitch_horizontal = p_h_c * c
    print(f"Espessura: {espessura_h}, Pitch vertical: {pitch_vertical}, Pitch horizontal: {pitch_horizontal}")
    return espessura_h, pitch_vertical, pitch_horizontal


# Função para calcular coordenadas com deslocamentos alternados
def calcular_coordenadas_aerofolios(points, num_rows, num_cols, chord_length, h_c, p_v_h, p_h_c, staggered=True):
    # Calcular dimensões reais
    espessura_h, pitch_v, pitch_h = calcular_dimensoes(chord_length, h_c, p_v_h, p_h_c)

    # Separar as coordenadas dos pontos fornecidos
    x_coords, y_coords = zip(*points)

    all_contours = []

    # Calcular cada aerofólio com o deslocamento necessário
    for row in range(num_rows):
        for col in range(num_cols):
            # Deslocamentos horizontais e verticais
            x_offset = col * pitch_h
            y_offset = row * pitch_v * 2 + (pitch_v if col % 2 == 1 and staggered else 0)  # alinhado: sem deslocamento

            # Aplicar os deslocamentos aos pontos
            x_new = [x + x_offset for x in x_coords]
            y_new = [y + y_offset for y in y_coords]

            contour = [(x, y) for x, y in zip(x_new, y_new)]
            all_contours.append(contour)

    return all_contours, espessura_h, pitch_v, pitch_h


# Função apenas para plotar os aerofólios (mat plt lib)
def plot_airfoils_alternados(contours):
    plt.figure(figsize=(12, 6))

    # Plotar cada contorno fornecido
    for contour in contours:
        x_coords, y_coords = zip(*contour)
        plt.plot(x_coords, y_coords, marker='o', markersize=2, linewidth=1)

    plt.title("Tube Arrangement")
    plt.xlabel("x (mm)")
    plt.ylabel("y (mm)")
    plt.grid(True)
    plt.axis("equal")
    plt.show()


def process_coord_files(file, file_name):
    df, pontos = None, None

    if file_name.endswith('.xlsx'):
        try:
            excel_data = pd.read_excel(file, sheet_name=None)  # Load all sheets
            _sheet = list(excel_data.keys())[0]
            df = excel_data[_sheet]

            # Process columns
            if isinstance(list(df.columns)[0], str):
                df = df.rename(columns={list(df.columns)[0]: 'x', list(df.columns)[1]: 'y'})
            elif isinstance(list(df.columns)[0], (int, float)):
                df_part1 = pd.DataFrame({'x': [list(df.columns)[0]], 'y': [list(df.columns)[1]]})
                df_part2 = df.iloc[0:]
                df_part2 = df_part2.rename(columns={list(df.columns)[0]: 'x', list(df.columns)[1]: 'y'})
                df = pd.concat([pd.DataFrame(df_part1), df_part2], ignore_index=True)

            df = df.dropna().reset_index(drop=True)
            pontos = [(round(df['x'][i], 6), round(df['y'][i], 6)) for i in range(len(df['x']))]

        except Exception as e:
            st.error(f"Failed to read the Excel file: {str(e)}")

    elif file_name.endswith('.csv'):
        try:
            df = pd.read_csv(file)
            column_data = list(df.columns)[0]

            try:
                column_data = float(column_data)
            except ValueError:
                pass

            # Process columns
            if isinstance(column_data, str):
                df = df.rename(columns={list(df.columns)[0]: 'x', list(df.columns)[1]: 'y'})
            elif isinstance(column_data, (int, float)):
                df_part1 = pd.DataFrame({'x': [list(df.columns)[0]], 'y': [list(df.columns)[1]]})
                df_part2 = df.iloc[0:]
                df_part2 = df_part2.rename(columns={list(df.columns)[0]: 'x', list(df.columns)[1]: 'y'})
                df = pd.concat([pd.DataFrame(df_part1), df_part2], ignore_index=True)

            df = df.dropna().reset_index(drop=True)
            pontos = [(round(float(df['x'][i]), 6), round(float(df['y'][i]), 6)) for i in range(len(df['x']))]

        except Exception as e:
            st.error(f"Failed to read the CSV file: {str(e)}")

    else:
        st.warning("Please upload a valid file in .xlsx or .csv format.")

    return df


def centralizar_pontos_na_origem(pontos):
    if not pontos:
        return []

    # Separando as coordenadas X e Y
    x_coords, y_coords = zip(*pontos)

    # Calculando os valores máximos e mínimos
    x_min, x_max = min(x_coords), max(x_coords)
    y_min, y_max = min(y_coords), max(y_coords)

    # Calculando os centros baseados nas distâncias máximas
    x_centro = (x_max + x_min) / 2
    y_centro = (y_max + y_min) / 2

    # Centralizando os pontos subtraindo o centro calculado
    pontos_centralizados = [(x - x_centro, y - y_centro) for x, y in pontos]

    return pontos_centralizados


def detectar_simetria(pontos):
    pontos_set = set(pontos)

    simetria_x = True
    simetria_y = True

    for x, y in pontos:
        if (x, -y) not in pontos_set:
            simetria_x = False
        if (-x, y) not in pontos_set:
            simetria_y = False

    return {
        'x': simetria_x,
        'y': simetria_y
    }


def raio_eq_poligono(pontos):  # Para modificar perfil assimétrico nas curvas
    n = len(pontos)
    area = 0
    for i in range(n):
        x1, y1 = pontos[i]
        x2, y2 = pontos[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)

    area = abs(area) / 2
    raio_circ = (area / math.pi) ** 0.5

    return raio_circ


# Coordenadas de uma elipse "unitária" (diâmetro 1 nos dois eixos, centrada na origem)
# Depois o scale_xy_airfoil escala x pela corda (2 * raio x) e y pela espessura (2 * raio y),
# exatamente como é feito com as coordenadas do aerofólio
def coordenadas_elipse_unitaria(n=200):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return pd.DataFrame({'x': 0.5 * np.cos(t), 'y': 0.5 * np.sin(t)})


# Normaliza, limpa e opcionalmente fecha um contorno 2D (mesma função do código de CFD validado)
def tratar_contorno(pts, fechar=True, remover_loop=True, tol=1e-7):
    # fechar: garante ultimo ponto == primeiro (polygon)
    # remover_loop: remove o ultimo ponto se igual ao primeiro (spline periodica)
    if not pts:
        return pts

    pts = [(float(x), float(y)) for x, y in pts]

    if remover_loop and len(pts) > 2:
        x0, y0 = pts[0]
        x1, y1 = pts[-1]
        if abs(x0 - x1) < tol and abs(y0 - y1) < tol:
            pts = pts[:-1]

    limpos = []
    for x, y in pts:
        if not limpos:
            limpos.append((x, y))
        else:
            px, py = limpos[-1]
            if abs(x - px) >= tol or abs(y - py) >= tol:
                limpos.append((x, y))

    if fechar and limpos:
        x0, y0 = limpos[0]
        x1, y1 = limpos[-1]
        if abs(x0 - x1) >= tol or abs(y0 - y1) >= tol:
            limpos.append(limpos[0])

    return limpos


# Face de spline EXATAMENTE simétrica em relação a y = 0 (para perfis simétricos): spline pela metade de cima (todos os
# pontos com y >= 0, das duas passagens pelo eixo) com tangentes perpendiculares ao eixo nas pontas, mais o espelho dela.
# A spline periódica, mesmo por pontos simétricos, sai ~1 micrometro assimétrica, e isso deixa frestas nas junções do
# tubo com as curvas em U e com os fittings (o perfil girado 180 graus não coincide com o do tubo seguinte)
def face_spline_simetrica(pts):
    pts = tratar_contorno(pts, fechar=False)
    n = len(pts)
    cadeia = _cadeia_superior(pts)
    if cadeia is None or len(cadeia) < 3:
        raise ValueError("profile does not cross the symmetry axis twice")

    ## Pontas da metade de cima: no eixo (ou cruzando o eixo entre dois pontos) -> ponto no eixo com tangente
    ## perpendicular ao eixo (bordo arredondado); longe do eixo (bordo de fuga rombudo) -> fechado com uma reta,
    ## como no spline(pts).close()
    i_ini = pts.index(cadeia[0])
    i_fim = (i_ini + len(cadeia) - 1) % n

    def cruzamento(a, b):
        (xa, ya), (xb, yb) = a, b
        if ya == yb:
            return (xa, 0.)
        t = ya / (ya - yb)
        return (xa + t * (xb - xa), 0.)

    def rombudo(ponta, vizinho):
        return ponta[1] > math.hypot(vizinho[0] - ponta[0], vizinho[1] - ponta[1])

    cima = list(cadeia)
    ini_rombudo = rombudo(cadeia[0], cadeia[1])
    fim_rombudo = rombudo(cadeia[-1], cadeia[-2])
    if not ini_rombudo:
        cima = [cruzamento(pts[i_ini - 1], pts[i_ini])] + cima
    if not fim_rombudo:
        cima = cima + [cruzamento(pts[i_fim], pts[(i_fim + 1) % n])]
    cima = tratar_contorno(cima, fechar=False, remover_loop=False)
    if not ini_rombudo:
        cima[0] = (cima[0][0], 0.)
    if not fim_rombudo:
        cima[-1] = (cima[-1][0], 0.)

    def unitario(a, b):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return cq.Vector((b[0] - a[0]) / d, (b[1] - a[1]) / d, 0)

    tangente_ini = unitario(cima[0], cima[1]) if ini_rombudo else cq.Vector(0, 1, 0)
    tangente_fim = unitario(cima[-2], cima[-1]) if fim_rombudo else cq.Vector(0, -1, 0)

    # parâmetros = comprimento de corda acumulado e tangentes unitárias sem reescala (evita "laço" perto das pontas)
    parametros = [0.]
    for (xa, ya), (xb, yb) in zip(cima[:-1], cima[1:]):
        parametros.append(parametros[-1] + math.hypot(xb - xa, yb - ya))
    borda_cima = cq.Edge.makeSpline([cq.Vector(x, y, 0) for x, y in cima], tangents=[tangente_ini, tangente_fim],
                                    parameters=parametros, scale=False)
    bordas = [borda_cima, borda_cima.mirror("XZ")]
    if ini_rombudo:
        bordas.append(cq.Edge.makeLine(cq.Vector(cima[0][0], -cima[0][1], 0), cq.Vector(cima[0][0], cima[0][1], 0)))
    if fim_rombudo:
        bordas.append(cq.Edge.makeLine(cq.Vector(cima[-1][0], cima[-1][1], 0), cq.Vector(cima[-1][0], -cima[-1][1], 0)))
    wire = cq.Wire.assembleEdges(bordas)
    return cq.Face.makeFromWires(wire)


# Sketch (face 2D) de um contorno do banco: polígono (suavizado), spline por todos os pontos, ou elipse/círculo exato
def criar_sketch_perfil(contour, forma_tubo='Aerofólio', spline_mode=False):
    if forma_tubo == 'Circular/Elíptico':
        # Elipse exata (sem pontos): centro e semi-eixos a partir do retângulo envolvente do contorno
        x_coords, y_coords = zip(*contour)
        x_c, y_c = (max(x_coords) + min(x_coords)) / 2, (max(y_coords) + min(y_coords)) / 2
        r_x, r_y = (max(x_coords) - min(x_coords)) / 2, (max(y_coords) - min(y_coords)) / 2

        if abs(r_x - r_y) <= 1e-9 * max(r_x, r_y):
            return cq.Sketch().push([(x_c, y_c)]).circle(r_x).reset()
        return cq.Sketch().push([(x_c, y_c)]).ellipse(r_x, r_y).reset()

    if spline_mode:
        # 1. Cria a curva usando spline (por TODOS os pontos do contorno), fecha o contorno e extrai o Wire
        # 2. Converte o Wire fechado diretamente em uma face 2D
        # Primeiro tenta a spline periódica (validada no código de CFD, bordo de fuga suave); se falhar,
        # tenta a spline aberta + close() (fecha o bordo de fuga com uma reta)
        pts = tratar_contorno(contour, fechar=False, remover_loop=True)
        if detectar_simetria([(round(x, 9), round(y, 9)) for x, y in pts])['x']:
            try:
                return cq.Sketch().face(face_spline_simetrica(pts)).reset()
            except Exception:
                pass
        try:
            wire = cq.Workplane("XY").spline(pts, periodic=True).close().wires().val()
            face = cq.Face.makeFromWires(wire)
            if not face.isValid():
                raise ValueError("invalid periodic spline face")
        except Exception:
            wire = cq.Workplane("XY").spline(pts).close().val()
            face = cq.Face.makeFromWires(wire)

        return cq.Sketch().face(face).reset()

    return cq.Sketch().polygon(contour).reset()


# Offset interno (espessura) de um perfil em spline, alternativo: o offset 2D do OpenCASCADE direto na spline falha
# para algumas espessuras (bordo de fuga arredondado com raio muito pequeno). Aqui o offset é feito no polígono dos
# pontos originais (robusto) e a spline é refeita pelos pontos do contorno interno
def offset_spline_por_poligono(contour, espessura, n=300):
    w_off = cq.Sketch().polygon(contour).reset().faces().val().outerWire().offset2D(-espessura, "arc")[0]
    amostras = [w_off.positionAt(t, 'length') for t in np.linspace(0, 1, n, endpoint=False)]
    pts_off = tratar_contorno([(p.x, p.y) for p in amostras], fechar=False)
    if detectar_simetria([(round(x, 9), round(y, 9)) for x, y in contour])['x']:
        # Contorno interno simétrico como o externo
        return cq.Sketch().face(face_spline_simetrica(simetrizar_contorno(pts_off))).reset()
    wire = cq.Workplane("XY").spline(pts_off, periodic=True).close().wires().val()
    return cq.Sketch().face(cq.Face.makeFromWires(wire)).reset()


@st.dialog("Uploading a Coordinates File")
def show_uploading_instructions():
    st.markdown("""
    - The file must be saved in either **.xlsx** or **.csv** format.
    - For **.xlsx** files, column A represents the x-axis, while column B represents the y-axis.
    - For **.xlsx** files, decimal values should use a `.` as the separator, not a `,`.
    - For **.csv** files, the first value represents the x-axis, and the second value represents the y-axis.
    - **Coordinates in both formats must be in order, with each (x, y) point located between the previous and next points.**
    """)
    try:
        st.image(path + '/pages/images/example_mhcad.png')
    except Exception:
        pass


def scale_contour_df(df, scale=1):
    df = df.copy().astype(float)
    # center = df[['x', 'y']].mean(axis=0)
    # scaled_df = (df[['x', 'y']] - center) * scale + center
    scaled_df = (df[['x', 'y']]) * scale
    df[['x', 'y']] = scaled_df
    return df


@st.dialog("Sketch Preview ")
def plot_airfoils_alternados_plotly(contours):
    # Lista para armazenar os gráficos dos contornos
    fig = make_subplots(rows=1, cols=1)

    for i, contour in enumerate(contours):
        x_coords, y_coords = zip(*contour)

        # Adicionar uma linha para cada contorno
        fig.add_trace(go.Scatter(
            x=x_coords,
            y=y_coords,
            mode='lines+markers',
            marker=dict(size=3),
            name=f'Tube {i + 1}'
        ))

    # Ajustar layout
    fig.update_layout(
        title='Tube Arrangement',
        xaxis_title='x (mm)',
        yaxis_title='y (mm)',
        showlegend=False,
        width=800,
        height=600,
        template='plotly_white'  #
    )

    fig.update_yaxes(scaleanchor="x", scaleratio=1)  # Manter escala igual nos eixos
    # fig.show()
    st.plotly_chart(fig, width="stretch")


def displayCAD_streamlit(file_path: str, title: str, opacity: float = 0.75):
    # Visualiza malha (STL) com numpy-stl (mesmo princípio do displayCAD do rocket nozzle sizing)
    stl_mesh = mesh.Mesh.from_file(file_path)

    # Extrair todos os vértices da malha (cada face tem 3 vértices de 3 coordenadas [x, y, z])
    # stl_mesh.vectors tem o formato (N_triangulos, 3, 3)
    vertices = stl_mesh.vectors.reshape(-1, 3)

    # Identificar vértices únicos e reorganizar os índices das faces
    unique_vertices, indices = np.unique(vertices, axis=0, return_inverse=True)

    # pontos
    x, y, z = unique_vertices.T

    # a gente vai fazer isso para dividir a malha do objeto em vários triangulos
    # (as faces são formadas por trios sequenciais de índices)
    faces = indices.reshape(-1, 3)

    # aqui a gente viai criar uma figura do plotly como um objeto 3d passando os pontos do objeto (x,y,z)
    # e os triangulos feitos com esses pontos (i, j, k)
    # estou seguindo o mesmo princípio da função de displaycad que coloquei no colab
    # vc também pode controlar a opacidade, eu deixei 0.75 pois acho suficiente pra ver um pouco o interior
    # sem ficar tão transparente

    fig = go.Figure(data=[go.Mesh3d(
        x=x, y=y, z=z,
        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
        opacity=opacity
    )])

    # e aqui o layout a ser exibido
    fig.update_layout(
        width=1200,
        height=800,
        scene=dict(aspectmode="data"),
        title=title,
        margin=dict(l=0, r=0, t=40, b=0)
    )

    # aqui que muda, a gente vai usar essa função do streamlit pra gráficos plotly
    st.plotly_chart(fig, width="stretch")

def manter_extrude_button_ativo():
    st.session_state.extrude_button = True


def desativ_extrude_button():
    st.session_state.extrude_button = False


# Douglas-Peucker (mesmo algoritmo do cv2.approxPolyDP), em numpy: sem depender do OpenCV
def _douglas_peucker(pts, epsilon):
    n = len(pts)
    manter = np.zeros(n, dtype=bool)
    manter[0] = manter[-1] = True
    pilha = [(0, n - 1)]
    while pilha:
        i, j = pilha.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        L = np.hypot(seg[0], seg[1])
        rel = pts[i + 1:j] - pts[i]
        if L > 0:
            d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / L  # distância ao segmento i-j
        else:
            d = np.hypot(rel[:, 0], rel[:, 1])
        k = int(np.argmax(d))
        if d[k] > epsilon:
            idx = i + 1 + k
            manter[idx] = True
            pilha += [(i, idx), (idx, j)]
    return pts[manter]


# Metade de cima (y >= 0) de um contorno fechado, na ordem do contorno (cíclica)
def _cadeia_superior(pts):
    n = len(pts)
    inicio = None
    for i in range(n):
        if pts[i][1] >= 0 and pts[i - 1][1] < 0:
            inicio = i
            break
    if inicio is None:
        return None
    cadeia = []
    k = inicio
    while pts[k % n][1] >= 0 and len(cadeia) < n:
        cadeia.append(pts[k % n])
        k += 1
    return cadeia


# Força a simetria em relação a y = 0 (metade de cima espelhada), para contornos de perfis simétricos
def simetrizar_contorno(pts):
    cadeia = _cadeia_superior(pts)
    if cadeia is None or len(cadeia) < 3:
        return pts
    return list(cadeia) + [(x, -y) for x, y in reversed(cadeia) if y > 0]


def suavizar_contorno(coordinates, suavizar, suavizacao):
    # Suavização **
    if suavizar is True:
        # Converter a lista de pontos para um array NumPy
        contour = np.array(coordinates, dtype=np.float64)

        # Calcular o perímetro do contorno (fechado), como o cv2.arcLength(contour, True)
        peri = float(np.sum(np.linalg.norm(np.roll(contour, -1, axis=0) - contour, axis=1)))

        # Suavizar o contorno (contorno fechado, como o cv2.approxPolyDP):
        # divide no ponto mais distante do primeiro e simplifica as duas metades
        epsilon = suavizacao * peri  # ajuste esse valor conforme necessário

        # Perfil simétrico em relação à corda: suaviza só a metade de cima e espelha. Assim o perfil suavizado continua
        # exatamente simétrico, e a curva em U (perfil girado 180 graus) encaixa sem frestas no tubo seguinte
        if detectar_simetria(coordinates)['x']:
            cadeia = _cadeia_superior(coordinates)
            if cadeia is not None and len(cadeia) >= 3:
                cima = _douglas_peucker(np.array(cadeia, dtype=np.float64), epsilon)
                smoothed_points = [(float(x), float(y)) for x, y in cima]
                smoothed_points += [(x, -y) for x, y in reversed(smoothed_points) if y > 0]
                if len(smoothed_points) >= 3:
                    return smoothed_points

        k = int(np.argmax(np.linalg.norm(contour - contour[0], axis=1)))
        if k == 0 or len(contour) < 4:
            return coordinates
        metade_1 = _douglas_peucker(contour[:k + 1], epsilon)
        metade_2 = _douglas_peucker(np.vstack([contour[k:], contour[:1]]), epsilon)
        smoothed_contour = np.vstack([metade_1, metade_2[1:-1]])

        # Converter o contorno suavizado para uma lista de tuplas (x, y)
        smoothed_points = [(float(x), float(y)) for x, y in smoothed_contour]

        if len(smoothed_points) >= 3:
            coordinates = smoothed_points

    return coordinates


################# INTERFERÊNCIA ENTRE TUBOS (PASSO MÍNIMO) #################

# Menor distância dos pontos P aos segmentos do contorno fechado A
def _dist_pontos_segmentos(P, A):
    B = np.roll(A, -1, axis=0)
    AB = B - A
    AP = P[:, None, :] - A[None, :, :]
    L2 = np.sum(AB * AB, axis=1)
    L2[L2 == 0] = 1e-30
    t = np.clip(np.sum(AP * AB[None, :, :], axis=2) / L2[None, :], 0, 1)
    proj = A[None, :, :] + t[:, :, None] * AB[None, :, :]
    return float(np.min(np.linalg.norm(P[:, None, :] - proj, axis=2)))


# Folga entre dois contornos (0 se encostam / se sobrepõem)
def _folga_contornos(A, B, apenas_sinal=False):
    # Retângulos envolventes separados -> sem interferência
    sep_x = max(A[:, 0].min() - B[:, 0].max(), B[:, 0].min() - A[:, 0].max())
    sep_y = max(A[:, 1].min() - B[:, 1].max(), B[:, 1].min() - A[:, 1].max())
    if apenas_sinal and max(sep_x, sep_y) > 0:
        return max(sep_x, sep_y)

    if mplPath(B).contains_points(A).any() or mplPath(A).contains_points(B).any():
        return 0.0

    return min(_dist_pontos_segmentos(A, B), _dist_pontos_segmentos(B, A))


# Deslocamentos relativos entre tubos vizinhos no arranjo alternado (bloco de até 3 x 3 tubos)
def _deslocamentos_vizinhos(num_rows, num_cols, pitch_v, pitch_h, staggered=True):
    centros = [(col * pitch_h, row * pitch_v * 2 + (pitch_v if col % 2 == 1 and staggered else 0))
               for row in range(min(num_rows, 3)) for col in range(min(num_cols, 3))]
    desloc = set()
    for i in range(len(centros)):
        for j in range(i + 1, len(centros)):
            dx = round(centros[j][0] - centros[i][0], 12)
            dy = round(centros[j][1] - centros[i][1], 12)
            if (dx, dy) < (0, 0):
                dx, dy = -dx, -dy
            desloc.add((dx, dy))
    return list(desloc)


def _folga_arranjo(P, num_rows, num_cols, chord, h_c, p_v_h, p_h_c, apenas_sinal=False, staggered=True):
    espessura_h = h_c * chord
    pitch_v = p_v_h * espessura_h
    pitch_h = p_h_c * chord
    folga = math.inf
    for dx, dy in _deslocamentos_vizinhos(num_rows, num_cols, pitch_v, pitch_h, staggered):
        folga = min(folga, _folga_contornos(P, P + np.array([dx, dy]), apenas_sinal))
        if apenas_sinal and folga <= 0:
            break
    return folga


# Verifica interferência entre os tubos e calcula o passo mínimo (vertical e horizontal) para o setup atual
@st.cache_data(show_spinner=False)
def verificar_interferencia(pontos_perfil, num_rows, num_cols, chord, h_c, p_v_h, p_h_c, staggered=True):
    P = np.array(tratar_contorno(list(pontos_perfil), fechar=False), dtype=float)
    if len(P) > 300:  # amostra o contorno (estimativa rápida)
        P = P[np.linspace(0, len(P) - 1, 300).astype(int)]

    folga = _folga_arranjo(P, num_rows, num_cols, chord, h_c, p_v_h, p_h_c, staggered=staggered)

    def interfere(pv, ph):
        return _folga_arranjo(P, num_rows, num_cols, chord, h_c, pv, ph, apenas_sinal=True, staggered=staggered) <= 1e-3 * chord

    def passo_minimo(funcao, atual):
        # Bisseção: menor passo sem interferência (mantendo o outro passo fixo)
        lo, hi = 1e-3, max(atual, 1e-3)
        while funcao(hi) and hi < 1e3:
            hi *= 2
        if funcao(hi):
            return None
        if not funcao(lo):
            return 0.0
        for _ in range(30):
            meio = 0.5 * (lo + hi)
            if funcao(meio):
                lo = meio
            else:
                hi = meio
        return hi

    p_v_h_min = passo_minimo(lambda p: interfere(p, p_h_c), p_v_h) if (num_rows > 1 or num_cols > 1) else None
    p_h_c_min = passo_minimo(lambda p: interfere(p_v_h, p), p_h_c) if num_cols > 1 else None

    return folga, p_v_h_min, p_h_c_min


################# ALETAS #################

def criar_aletas(
        modelo,
        perfis_ext,
        scale_,
        n_aletas,
        espessura_aleta,
        margem_aleta,
        z_ini,
        z_fim,
        unir_aletas=False,
        passo_aleta=None
):  # ALETAS PLANAS (placas perpendiculares aos tubos, furadas pelo perfil externo dos tubos)
    # perfis_ext: sketch com a união dos perfis externos (sem escala)
    # passo_aleta None: n_aletas igualmente espaçadas (passo = comprimento / (n + 1))
    # passo_aleta dado: o máximo de aletas que cabe com esse passo, centralizadas no comprimento disponível
    if passo_aleta is None:
        passo_aleta = (z_fim - z_ini) / (n_aletas + 1)
        z_primeira = z_ini + passo_aleta
        if espessura_aleta >= passo_aleta:
            raise ValueError(f"the fin thickness ({espessura_aleta:.3f} mm) must be smaller than the fin pitch "
                             f"({passo_aleta:.3f} mm). Reduce the number of fins or the fin thickness.")
    else:
        if espessura_aleta >= passo_aleta:
            raise ValueError(f"the fin thickness ({espessura_aleta:.3f} mm) must be smaller than the fin pitch "
                             f"({passo_aleta:.3f} mm). Increase the fin pitch or reduce the fin thickness.")
        n_aletas = int(math.floor((z_fim - z_ini - espessura_aleta) / passo_aleta)) + 1
        if n_aletas < 1:
            raise ValueError("the fin thickness is larger than the available tube length.")
        z_primeira = z_ini + ((z_fim - z_ini) - (n_aletas - 1) * passo_aleta) / 2

    ## Placa retangular (perfis externos + margem) furada pelos perfis: boolean 2D (rápido), depois extrusão
    bb = cq.Workplane("XY").placeSketch(perfis_ext).extrude(1).val().BoundingBox()
    placa = (
        cq.Sketch()
        .push([(bb.center.x, bb.center.y)])
        .rect(bb.xlen + 2 * margem_aleta / scale_, bb.ylen + 2 * margem_aleta / scale_)
        .reset()
    )
    sketch_aleta = placa - perfis_ext
    sketch_aleta = sketch_aleta.val().scale(scale_) if scale_ != 1 else sketch_aleta

    aleta = cq.Workplane("XY").placeSketch(sketch_aleta).extrude(espessura_aleta)

    aletas = []
    for k in range(n_aletas):
        aletas.append(aleta.translate((0, 0, z_primeira + k * passo_aleta - espessura_aleta / 2)).val())

    if unir_aletas:
        # Sólido único: as aletas encostam nos tubos (faces coincidentes) -> união "glue" (mais rápida que a booleana comum)
        try:
            modelo = modelo.union(cq.Workplane("XY").add(cq.Compound.makeCompound(aletas)), glue=True)
        except Exception:
            st.warning("The fins could not be merged into a single solid; they were kept as separate solids.")
            modelo = cq.Workplane("XY").add(cq.Compound.makeCompound(modelo.vals() + aletas))
    else:
        # Aletas como sólidos separados encostados nos tubos (como em um trocador real de tubos e aletas)
        modelo = cq.Workplane("XY").add(cq.Compound.makeCompound(modelo.vals() + aletas))

    return modelo, passo_aleta, n_aletas


# Exporta STL com tolerâncias da triangulação ajustadas: linear absoluta (0.2% da corda) e angular 0.3 rad.
# Os padrões do exporters (0.1 relativa / 0.1 rad) geram STLs enormes e lentos com perfis em spline
# (ex.: 130 MB e 4 min para 12 tubos), sem ganho visível
def exportar_stl(modelo, arquivo, chord_):
    shape = modelo if isinstance(modelo, cq.Shape) else cq.Compound.makeCompound(
        [v for v in modelo.vals() if isinstance(v, cq.Shape)])
    shape.exportStl(arquivo, 0.002 * chord_, 0.3, False, False)


# União booleana (+). Se falhar, tenta o modo "glue" do OpenCASCADE, próprio para sólidos que só se tocam por faces
# coincidentes (ex.: curvas e cabeçotes encostados nas pontas dos tubos em spline)
# glue_primeiro: usado nas curvas com perfil em spline (encostam nas pontas dos tubos), onde o "+" costuma falhar
# depois de muito processamento (o "glue" direto é ~2.5x mais rápido)
def unir_solidos(a, b, glue_primeiro=False):
    if glue_primeiro:
        try:
            return a.union(b, glue=True)
        except Exception:
            return a + b
    try:
        return a + b
    except Exception:
        return a.union(b, glue=True)


def hx_lin(
        modelo_combinado,
        s1,
        pitch_h_,
        pitch_v_,
        length_,
        num_cols,
        num_rows,
        set_scale,
        simetry,
        staggered=True
):
    if simetry['x']:
        #   CONEXÕES ENTRE LINHAS (PERFIL SIMÉTRICO)

        # Curva de cima = curva de baixo girada 180 graus no próprio eixo (mesmo sólido do revolve no eixo invertido).
        # O revolve no eixo invertido gera o toro com parâmetro de 180 a 360 graus, que o Ansys (SpaceClaim/Discovery)
        # lê como a outra metade do toro ao importar o STEP (curvas invertidas e pontas abertas no lado z = L)
        result_cima = (
            cq.Workplane("XY")
            .placeSketch(s1)
            .revolve(
                angleDegrees=180,
                axisStart=(0, pitch_v_, 0),  # mesmo X e Z
                axisEnd=(1, pitch_v_, 0)  # variação apenas em Y
            )
            .rotate((0, pitch_v_, 0), (1, pitch_v_, 0), 180)
        )

        # exporters.export(result_cima, "revolve_cima.stl")

        result_baixo = (
            cq.Workplane("XY")
            .placeSketch(s1)
            .revolve(
                angleDegrees=180,
                axisStart=(0, pitch_v_, 0),  # mesmo X e Z
                axisEnd=(1, pitch_v_, 0)  # variação apenas em Y
            )
        )

    else:
        # Fittings
        r_circ_loft = raio_eq_poligono(pontos) * scale if set_scale else raio_eq_poligono(pontos)

        L_curva_col = 3 * r_circ_loft

        sketch_tubo_curva_col = (
            cq.Sketch()
            .circle(r_circ_loft)
            .circle(r_circ_loft - espessura_offset, mode="s")
            .reset()
        )

        sketch_tubo_interno_curva_col = (
            cq.Sketch()
            .circle(r_circ_loft - espessura_offset)
            .reset()
        )

        ## Fitting cima
        curva_col = cq.Workplane("XY").placeSketch(s1, sketch_tubo_curva_col.moved(z=L_curva_col)).loft(combine=True)

        if offset_:
            curva_col_int = cq.Workplane("XY").placeSketch(s1_offset_list,
                                                           sketch_tubo_interno_curva_col.moved(z=L_curva_col)).loft(
                combine=True)
            curva_col_cima = curva_col - curva_col_int
        else:
            curva_col_cima = curva_col

        ## Fitting baixo
        curva_col = cq.Workplane("XY").placeSketch(s1, sketch_tubo_curva_col.moved(z=-L_curva_col)).loft(combine=True)

        if offset_:
            curva_col_int = cq.Workplane("XY").placeSketch(s1_offset_list,
                                                           sketch_tubo_interno_curva_col.moved(z=-L_curva_col)).loft(
                combine=True)
            curva_col_baixo = curva_col - curva_col_int
        else:
            curva_col_baixo = curva_col

        ## Curva cima
        result_cima = (
            cq.Workplane("XY")
            .placeSketch(sketch_tubo_curva_col)
            .revolve(
                angleDegrees=180,
                axisStart=(0, pitch_v_, 0),  # mesmo X e Z
                axisEnd=(1, pitch_v_, 0)  # variação apenas em Y
            )
            .rotate((0, pitch_v_, 0), (1, pitch_v_, 0), 180)  # Curva de baixo girada (ver curva de cima simétrica)
            .translate((0, 0, L_curva_col))
        )

        result_cima = result_cima + curva_col_cima + curva_col_cima.translate((0, pitch_v_ * 2, 0))

        ## Curva baixo
        result_baixo = (
            cq.Workplane("XY")
            .placeSketch(sketch_tubo_curva_col)
            .revolve(
                angleDegrees=180,
                axisStart=(0, pitch_v_, 0),  # mesmo X e Z
                axisEnd=(1, pitch_v_, 0)  # variação apenas em Y
            )
            .translate((0, 0, -L_curva_col))
        )

        result_baixo = result_baixo + curva_col_baixo + curva_col_baixo.translate((0, pitch_v_ * 2, 0))

    # exporters.export(result_baixo, "revolve_baixo.stl")

    modelo_final = modelo_combinado

    for i in range(num_cols):

        for j in range(num_rows - 1):  # Aqui muda de staggered para alligned

            if j % 2 == 0 and i % 2 == 0:
                add_curva = result_baixo.translate((pitch_h_ * i, pitch_v_ * 2 * j, 0))
            elif j % 2 != 0 and i % 2 == 0:
                add_curva = result_cima.translate(
                    (pitch_h_ * i, pitch_v_ * 2 * j, length_ if set_scale is True else length))

            elif j % 2 == 0 and i % 2 != 0:
                add_curva = result_baixo.translate((pitch_h_ * i, pitch_v_ * 2 * j + (pitch_v_ if staggered else 0), 0))
            elif j % 2 != 0 and i % 2 != 0:
                add_curva = result_cima.translate((pitch_h_ * i, pitch_v_ * 2 * j + (pitch_v_ if staggered else 0),
                                                   length_ if set_scale is True else length))

            modelo_final = unir_solidos(modelo_final, add_curva, spline_mode)

    return modelo_final


def hx_col(
        pontos,
        scale,
        set_scale,
        espessura_offset,
        s1,
        s1_offset_list,
        offset_,
        pitch_h_,
        pitch_v_,
        length_,
        num_cols,
        num_rows,
        modelo_final,
        simetry,
        staggered=True
):
    if simetry['y'] and not staggered:

        # Simétrico

        # Curvas

        theta_rad_curva = math.atan(pitch_v_ / pitch_h_)  # Para alternados

        dist_curva = pitch_v_ / math.sin(theta_rad_curva) if staggered else pitch_h_

        ## Curva cima
        curva_col_ = (
            cq.Workplane("XY")
            .placeSketch(s1)
            .revolve(
                angleDegrees=180,
                axisStart=(dist_curva / 2, 1, 0),  # mesmo X e Z
                axisEnd=(dist_curva / 2, 0, 0)  # variação apenas em Y
            )
            .rotate((dist_curva / 2, 0, 0), (dist_curva / 2, 1, 0), 180)  # Curva de baixo girada (ver hx_lin)
        )
        curva_col_ = curva_col_.rotate((0, 0, 0), (0, 0, 1),
                                       math.degrees(-theta_rad_curva)) if staggered else curva_col_

        curva_col_cima = curva_col_

        ## Curva baixo
        curva_col_ = (
            cq.Workplane("XY")
            .placeSketch(s1)
            .revolve(
                angleDegrees=180,
                axisStart=(dist_curva / 2, 1, 0),  # mesmo X e Z
                axisEnd=(dist_curva / 2, 0, 0)  # variação apenas em Y
            )
        )
        curva_col_ = curva_col_.rotate((0, 0, 0), (0, 0, 1), math.degrees(theta_rad_curva)) if staggered else curva_col_

        curva_col_baixo = curva_col_

        curva_col_cima2 = curva_col_cima  # Alinhado: as curvas entre colunas são retas (sem rotação)


    else:

        # Assimétrico

        # Fittings
        r_circ_loft = raio_eq_poligono(pontos) * scale if set_scale else raio_eq_poligono(pontos)

        L_curva_col = 3 * r_circ_loft

        sketch_tubo_curva_col = (
            cq.Sketch()
            .circle(r_circ_loft)
            .circle(r_circ_loft - espessura_offset, mode="s")
            .reset()
        )

        sketch_tubo_interno_curva_col = (
            cq.Sketch()
            .circle(r_circ_loft - espessura_offset)
            .reset()
        )

        ## Fitting cima
        curva_col = cq.Workplane("XY").placeSketch(s1, sketch_tubo_curva_col.moved(z=L_curva_col)).loft(combine=True)

        if offset_:
            curva_col_int = cq.Workplane("XY").placeSketch(s1_offset_list,
                                                           sketch_tubo_interno_curva_col.moved(z=L_curva_col)).loft(
                combine=True)
            curva_col_cima = curva_col - curva_col_int
        else:
            curva_col_cima = curva_col

        ## Fitting baixo
        curva_col = cq.Workplane("XY").placeSketch(s1, sketch_tubo_curva_col.moved(z=-L_curva_col)).loft(combine=True)

        if offset_:
            curva_col_int = cq.Workplane("XY").placeSketch(s1_offset_list,
                                                           sketch_tubo_interno_curva_col.moved(z=-L_curva_col)).loft(
                combine=True)
            curva_col_baixo = curva_col - curva_col_int
        else:
            curva_col_baixo = curva_col

        # Curvas

        theta_rad_curva = math.atan(pitch_v_ / pitch_h_)  # Para alternados

        dist_curva = pitch_v_ / math.sin(theta_rad_curva) if staggered else pitch_h_

        ## Curva cima
        curva_col_ = (
            cq.Workplane("XY")
            .placeSketch(sketch_tubo_curva_col)
            .revolve(
                angleDegrees=180,
                axisStart=(dist_curva / 2, 1, 0),  # mesmo X e Z
                axisEnd=(dist_curva / 2, 0, 0)  # variação apenas em Y
            )
            .rotate((dist_curva / 2, 0, 0), (dist_curva / 2, 1, 0), 180)  # Curva de baixo girada (ver hx_lin)
        )

        curva_col_2 = curva_col_.rotate((0, 0, 0), (0, 0, 1), math.degrees(theta_rad_curva)).translate(
            (0, 0, L_curva_col)) if staggered else curva_col_.translate(
            (0, 0, L_curva_col))  # Apenas para número de fileiras par

        curva_col_cima2 = curva_col_cima + curva_col_2 + curva_col_cima.translate(
            (pitch_h_, pitch_v_ if staggered else 0, 0))  # Apenas para número de fileiras par

        curva_col_ = curva_col_.rotate((0, 0, 0), (0, 0, 1), math.degrees(-theta_rad_curva)).translate(
            (0, 0, L_curva_col)) if staggered else curva_col_.translate((0, 0, L_curva_col))

        curva_col_cima = curva_col_cima + curva_col_ + curva_col_cima.translate((pitch_h_, -pitch_v_ if staggered else 0, 0))

        ## Curva baixo
        curva_col_ = (
            cq.Workplane("XY")
            .placeSketch(sketch_tubo_curva_col)
            .revolve(
                angleDegrees=180,
                axisStart=(dist_curva / 2, 1, 0),  # mesmo X e Z
                axisEnd=(dist_curva / 2, 0, 0)  # variação apenas em Y
            )
        )
        curva_col_ = curva_col_.rotate((0, 0, 0), (0, 0, 1), math.degrees(theta_rad_curva)).translate(
            (0, 0, -L_curva_col)) if staggered else curva_col_.translate((0, 0, -L_curva_col))

        curva_col_baixo = curva_col_baixo + curva_col_ + curva_col_baixo.translate((pitch_h_, pitch_v_ if staggered else 0, 0))

        # exporters.export(curva_col_cima, "curva_col_cima.stl")
        # displayCAD("/content/curva_col_cima.stl", "Sketch: sketch_TESTE.stl")

        # exporters.export(curva_col_baixo, "curva_col_baixo.stl")
        # displayCAD("/content/curva_col_baixo.stl", "Sketch: sketch_TESTE.stl")

    modelo_final2 = modelo_final

    if num_rows % 2 != 0:  # *************
        for i in range(num_cols - 1):
            if i % 2 == 0:
                ## Translação para o ramo com rotação
                translacao_pipeline1 = cq.Vector(pitch_h_ * i, (pitch_v_ * (num_rows - 1)) * 2, 0)

                ## Use o objeto já rotacionado e apenas aplique a translação diferente
                pipeline1 = curva_col_baixo.translate(translacao_pipeline1)

                modelo_final2 = unir_solidos(modelo_final2, pipeline1, spline_mode)

            else:
                ## Translação para o ramo sem rotação
                translacao_pipeline2 = cq.Vector(pitch_h_ * i, pitch_v_ if staggered else 0, length_)

                pipeline2 = curva_col_cima.translate(translacao_pipeline2)

                modelo_final2 = unir_solidos(modelo_final2, pipeline2, spline_mode)

    else:  # Caso "*"
        for i in range(num_cols - 1):
            if i % 2 == 0:
                translacao_pipeline1 = cq.Vector(pitch_h_ * i, (pitch_v_ * (num_rows - 1)) * 2, length_)

                pipeline1 = curva_col_cima2.translate(translacao_pipeline1)

                modelo_final2 = unir_solidos(modelo_final2, pipeline1, spline_mode)

            else:
                translacao_pipeline2 = cq.Vector(pitch_h_ * i, pitch_v_ if staggered else 0, length_)

                pipeline2 = curva_col_cima.translate(translacao_pipeline2)

                modelo_final2 = unir_solidos(modelo_final2, pipeline2, spline_mode)



    return modelo_final2


def criar_header_hx(
        connectype,
        pitch_h_,
        pitch_v_,
        num_cols,
        num_rows,
        espessura_offset,
        espessura_h_,
        chord_,
        offset_,
        sketch_ext_list,
        scale,
        set_scale,
        L_header,
        set_fitting,
        diam_interno_fitting,
        espessura_fitting,
        L_fitting,
        modelo_combinado,
        length_
):
    if connectype != 'Cabeçote':
        return None

    # Cálculo das dimensões da base inferior do cabeçote
    L_cab_b = pitch_h_ * num_cols + chord_
    H_cab_b = pitch_v_ * num_rows * 2 + chord_
    S_cab_b = espessura_offset * 1.5
    # L_cab_b = pitch_h_ * num_cols * 1.1
    # H_cab_b = pitch_v_ * num_rows * 2.5
    # S_cab_b = espessura_offset * 1.5

    if offset_ is True:
        # Cria a base inferior do cabeçote com arredondamento nos cantos verticais
        cab_baixo = (
            cq.Workplane("XY")
            .box(L_cab_b, H_cab_b, S_cab_b)
            #.edges("|Z").fillet(chord_ / 3)
            .edges("|Z").fillet(chord_/10)
        ).translate((pitch_h_ * (num_cols - 1) / 2, pitch_v_ * num_rows - espessura_h_, S_cab_b / 2))

        # Subtrai os perfis (furos) do cabeçote
        for profile in sketch_ext_list:
            s1_header = profile
            s1_header = s1_header.val().scale(scale) if set_scale else s1_header

            header_hole = (
                cq.Workplane("XY")
                .placeSketch(s1_header)
                .consolidateWires()
                .extrude(S_cab_b)
            )

            cab_baixo = cab_baixo - header_hole
    else:
        # Cria a base inferior do cabeçote sem furos
        cab_baixo = (
            cq.Workplane("XY")
            .box(L_cab_b, H_cab_b, S_cab_b)
            #.edges("|Z").fillet(chord_ / 3)
            .edges("|Z").fillet(chord_/10)
        ).translate((pitch_h_ * (num_cols - 1) / 2, pitch_v_ * num_rows - espessura_h_, S_cab_b / 2))


    # Cria o corpo superior do cabeçote
    header_body = (
        cq.Workplane("XY")
        .box(L_cab_b, H_cab_b, L_header)
    ).translate((pitch_h_ * (num_cols - 1) / 2, pitch_v_ * num_rows - espessura_h_, -L_header / 2))

    ## Se offset_ for True, aplica shell interno antes do fillet
    #header_body = header_body.faces("+Z").shell(-espessura_offset * 2).edges("|Z").fillet(chord_ / 3) if offset_ else header_body.edges("|Z").fillet(chord_ / 3)
    header_body = header_body.faces("+Z").shell(-espessura_offset * 2).edges("|Z").fillet(
        chord_/10) if offset_ else header_body.edges("|Z").fillet(chord_/10)

    ## Junta base inferior ao corpo do cabeçote
    header_body = header_body + cab_baixo

    ## Pega o centro da bounding box do cabeçote
    center_header = header_body.val().BoundingBox().center  # .BoundingBox() retorna a caixa delimitadora de um objeto, o menor paralelepípedo retangular alinhado aos eixos que envolve completamente o sólido

    # Cria corpo inferior do cabeçote (espelhado)
    header_body2 = (
        cq.Workplane("XY")
        .box(L_cab_b, H_cab_b, L_header)
    ).translate((pitch_h_ * (num_cols - 1) / 2, pitch_v_ * num_rows - espessura_h_, L_header / 2))

    ## Aplica shell se necessário
    #header_body2 = header_body2.faces("-Z").shell(-espessura_offset * 2).edges("|Z").fillet(chord_ / 3) if offset_ else header_body2.edges("|Z").fillet(chord_ / 3)
    header_body2 = header_body2.faces("-Z").shell(-espessura_offset * 2).edges("|Z").fillet(
        chord_/10) if offset_ else header_body2.edges("|Z").fillet(chord_/10)

    ## Junta base inferior
    header_body2 = header_body2 + cab_baixo

    ## Pega o centro da bounding box do segundo cabeçote
    center_header2 = header_body2.val().BoundingBox().center

    if set_fitting is True:
        # Cálculo do diâmetro externo do tubo
        diam_externo_fitting = diam_interno_fitting + espessura_fitting * 2

        # Cria sketch do tubo com espessura (anular)
        sketch_tubo = (
            cq.Sketch()
            .circle(diam_externo_fitting / 2)
            .circle(diam_interno_fitting / 2, mode="s")
            .reset()
        )

        # Sketch interno para corte
        sketch_tubo_interno = (
            cq.Sketch()
            .circle(diam_interno_fitting / 2)
            .reset()
        )

        wp = cq.Workplane("XY")

        # Cria tubos e rota para ficar perpendicular ao cabeçote
        conexao_tubo_externa = wp.placeSketch(sketch_tubo).extrude(L_fitting).rotate((0, 0, 0), (0, 1, 0), 90)
        cut_conexao_tubo_externa = wp.placeSketch(sketch_tubo_interno).extrude(L_fitting).rotate((0, 0, 0), (0, 1, 0),
                                                                                                 90)

        # Adiciona tubo ao primeiro cabeçote e subtrai interno
        header_body = header_body + conexao_tubo_externa.translate(
            (center_header.x + L_cab_b / 2 - espessura_offset * 2, center_header.y, center_header.z)) \
                      - cut_conexao_tubo_externa.translate(
            (center_header.x + L_cab_b / 2 - espessura_offset * 2, center_header.y, center_header.z))

        # Adiciona tubo ao segundo cabeçote e subtrai interno
        # (virado para o mesmo lado (+x) do fitting do primeiro cabeçote)
        header_body2 = header_body2 + conexao_tubo_externa.translate(
            (center_header2.x + L_cab_b / 2 - espessura_offset * 2, center_header2.y, center_header2.z)) \
                       - cut_conexao_tubo_externa.translate(
            (center_header2.x + L_cab_b / 2 - espessura_offset * 2, center_header2.y, center_header2.z))

    # Junta os dois cabeçotes e o modelo central
    modelo_final2 = unir_solidos(unir_solidos(header_body, modelo_combinado), header_body2.translate((0, 0, length_ - S_cab_b)))

    # Exporta arquivos STL e STEP
    exporters.export(header_body, cad_dir + "/header_body.step")

    return modelo_final2


def adicionar_fittings(
        modelo_final2,
        diam_interno_fitting,
        espessura_fitting,
        L_fitting,
        s1,
        s1_offset_list,
        set_fitting,
        num_cols,
        num_rows,
        pitch_h_,
        pitch_v_,
        length_,
        staggered=True
):  # CONEXÕES DE ENTRADA E SAÍDA (FITTINGS)
    if not set_fitting:
        return modelo_final2

    diam_externo_fitting = diam_interno_fitting + espessura_fitting * 2

    sketch_tubo = (
        cq.Sketch()
        .circle(diam_externo_fitting / 2)
        .circle(diam_interno_fitting / 2, mode="s")
        .reset()
    )

    sketch_tubo_interno = (
        cq.Sketch()
        .circle(diam_interno_fitting / 2)
        .reset()
    )

    if connectype == 'Curvas':
        wp = cq.Workplane("XY")

        conexao_tubo_externa = wp.placeSketch(s1, sketch_tubo.moved(z=L_fitting)).loft(combine=True)

        conexao_tubo = conexao_tubo_externa + wp.placeSketch(
            sketch_tubo.moved(z=L_fitting)).extrude(L_fitting)

        try:
            conexao_tubo_interna = wp.placeSketch(sketch_tubo_interno.moved(z=L_fitting), s1_offset_list).loft(
                combine=True)

            conexao_tubo = conexao_tubo - conexao_tubo_interna
        except:
            pass

        conexao_tubo_rot = conexao_tubo.mirror("XY")  # espelho em z (girar 180 graus em x espelhava o perfil em y)

        modelo_final3 = modelo_final2

        i = 0 # Fitting de entrada
        translacao_pipeline1 = cq.Vector(pitch_h_ * i, 0, length_)
        conexao_tubo1 = conexao_tubo.translate(translacao_pipeline1)
        modelo_final3 = unir_solidos(modelo_final3, conexao_tubo1)

        i = num_cols - 1   #   Fitting de saída
        if i % 2 != 0:
            translacao_pipeline2 = cq.Vector(pitch_h_ * i, pitch_v_ if staggered else 0, length_)
            conexao_tubo2 = conexao_tubo.translate(translacao_pipeline2)
            modelo_final3 = unir_solidos(modelo_final3, conexao_tubo2)
        elif i % 2 == 0 and num_rows % 2 != 0:
            translacao_pipeline2 = cq.Vector(pitch_h_ * i, (pitch_v_ * (num_rows - 1)) * 2, 0)
            conexao_tubo2 = conexao_tubo_rot.translate(translacao_pipeline2)
            modelo_final3 = unir_solidos(modelo_final3, conexao_tubo2)
        else:
            translacao_pipeline2 = cq.Vector(pitch_h_ * i, (pitch_v_ * (num_rows - 1)) * 2, length_)
            conexao_tubo2 = conexao_tubo.translate(translacao_pipeline2)
            modelo_final3 = unir_solidos(modelo_final3, conexao_tubo2)

        return modelo_final3
    else:
        return modelo_final2


############################################################


# st.title("AEROHX", anchor=False)

## Pasta temporária (por sessão) para os arquivos CAD e de malha (vários usuários não sobrescrevem os arquivos uns dos outros)
if 'cad_dir' not in st.session_state:
    st.session_state['cad_dir'] = tempfile.mkdtemp()
cad_dir = st.session_state['cad_dir']

col1, col2 = st.columns([1, 2])

# SETUP

if 'active_page_2' not in st.session_state:
    st.session_state.active_page_2 = '2_Tubos_com_Conexoes'
    st.session_state.st_suavizar = True
    st.session_state.st_suavizacao = 0.0005

    # if 'active_page' not in st.session_state:

    st.session_state.st_coordinates_x = []
    st.session_state.st_coordinates_y = []
    st.session_state.st_coordinates = []
    st.session_state.st_upload_coordinates = False

    st.session_state.st_chord = 15.
    st.session_state.st_h_c = 0.25
    st.session_state.st_p_v_h = 2.
    st.session_state.st_p_h_c = 0.8
    st.session_state.st_num_rows = 4
    st.session_state.st_num_cols = 3
    st.session_state.st_length = 125.
    st.session_state.st_espessura_offset = 1.5
    st.session_state.st_L_header = 12.

    st.session_state.extrude_button = False

    st.session_state.st_sketch = None
    st.session_state.st_solid = None

    st.session_state.st_set_scale = False
    st.session_state.st_scale = 2.

    st.session_state.st_diam_interno_fitting = 6.35
    st.session_state.st_espessura_fitting = 1.5
    st.session_state.st_L_fitting = 20.

## Valores padrão das novas opções (forma do tubo, aletas e malha)
st.session_state.setdefault('st_forma_tubo', 'Aerofólio')
st.session_state.setdefault('st_arranjo', 'Alternado')
st.session_state.setdefault('st_offset_', True)  # Chaves dos toggles (mantêm o estado ao trocar de página)
st.session_state.setdefault('st_set_fitting', False)
st.session_state.setdefault('st_set_aletas', False)
st.session_state.setdefault('st_connectype', 'N/A')
st.session_state.setdefault('st_raio_x', 5.)
st.session_state.setdefault('st_raio_y', 2.5)
st.session_state.setdefault('st_simetria_circular', True)
st.session_state.setdefault('st_n_aletas', 10)
st.session_state.setdefault('st_espessura_aleta', 0.5)
st.session_state.setdefault('st_margem_aleta', 2.)
st.session_state.setdefault('st_unir_aletas', False)
st.session_state.setdefault('st_modo_aletas', 'Quantidade')
st.session_state.setdefault('st_passo_aleta', 5.)

col1.subheader('Heat Exchanger Modeler v1.0.0'
               , anchor=False)

## Dimensões dos tubos
with col1.expander("Tube dimensions", expanded=True):

    ## Forma do tubo
    nomes_forma = {'Aerofólio': 'Airfoil', 'Circular/Elíptico': 'Circular / Elliptical'}
    forma_tubo = st.selectbox("Tube shape", ('Aerofólio', 'Circular/Elíptico'), format_func=lambda x: nomes_forma[x],
                              key='st_forma_tubo', on_change=desativ_extrude_button,
                              help='Airfoil: default profile or uploaded coordinates. Circular / Elliptical: exact circle or ellipse.')

    df__coord_input = None

    if forma_tubo == 'Aerofólio':
        upload_coordinates = st.toggle("Upload Coordinates File",
                                       help='For any other specific tube contour. The points must be ordered.', key='st_upload_coordinates',
                                       on_change=desativ_extrude_button)

        if not upload_coordinates:
            df__coord_input = process_coord_files(path + '/coord_originais_.xlsx', 'coord_originais_.xlsx')
            # st.dataframe(df__coord_input)

        else:
            if st.button('Instructions', width="stretch"):
                show_uploading_instructions()

            uploaded_file = st.file_uploader('File in **.xlsx** or **.csv** format.', type=["xlsx", "csv"])
            if uploaded_file is not None:
                with st.spinner('Loading...'):
                    df__coord_input = process_coord_files(uploaded_file, str(uploaded_file.name))
                    st.dataframe(df__coord_input)

                # Arquivo precisa ter colunas x e y com pelo menos 3 pontos numéricos
                try:
                    df__coord_input = df__coord_input[['x', 'y']].apply(pd.to_numeric, errors='coerce').dropna().reset_index(drop=True)
                    if len(df__coord_input) < 3:
                        raise ValueError
                except Exception:
                    st.error("The file must have at least 3 points with numeric x and y coordinates (see Instructions).")
                    df__coord_input = None

        ## Entradas - Aerofólios

        chord = st.number_input("Chord length", format='%f', step=1., min_value=0.01, key='st_chord',
                                help='Chord length (mm)')

        h_c = st.number_input("Dimensionless thickness", format='%f', step=0.01, min_value=0.001, key='st_h_c',
                              help='Maximum thickness / chord')

    else:
        ## Entradas - Tubo circular / elíptico
        # O raio x faz o papel da corda (corda = 2 * raio x) e o raio y o da espessura (espessura = 2 * raio y)
        raio_x = st.number_input("Radius x", format='%f', step=0.5, min_value=0.01, key='st_raio_x',
                                 help='Semi-axis along the flow direction (mm). Plays the role of the chord: chord = 2 x radius x.')

        simetria_circular = st.checkbox("Symmetric (circular tube)", key='st_simetria_circular',
                                        help='Uncheck to set a different radius y (elliptical tube).')

        if simetria_circular:
            raio_y = raio_x
        else:
            raio_y = st.number_input("Radius y", format='%f', step=0.5, min_value=0.01, key='st_raio_y',
                                     help='Semi-axis normal to the flow (mm). Plays the role of the thickness: thickness = 2 x radius y.')

        chord = 2 * raio_x
        h_c = raio_y / raio_x
        df__coord_input = coordenadas_elipse_unitaria()

    length = st.number_input("Tube length", format='%f', step=1., min_value=0.1, key='st_length',
                             help='Tube length (mm)')

##

## Banco de tubos
with col1.expander("Tube bank", expanded=True):

    ## Arranjo do banco de tubos
    nomes_arranjo = {'Alternado': 'Staggered', 'Alinhado': 'Aligned'}
    arranjo = st.selectbox("Tube arrangement", ('Alternado', 'Alinhado'), format_func=lambda x: nomes_arranjo[x],
                           key='st_arranjo',
                           help='Staggered: the odd columns are shifted by the vertical pitch. Aligned: all the columns at the same height.')
    staggered = arranjo == 'Alternado'

    p_v_h = st.number_input("Dimensionless vertical pitch", format='%f', step=0.1, min_value=0.01, max_value=100.,
                            key='st_p_v_h', help='Vertical pitch / thickness. The tubes of a column are 2 x vertical pitch apart; '
                                                 'in the staggered arrangement the odd columns are shifted by this pitch.')

    p_h_c = st.number_input("Dimensionless horizontal pitch", format='%f', step=0.1, min_value=0.01, max_value=100.,
                            key='st_p_h_c', help='Horizontal pitch / chord.')

    status_passo = st.empty()  # Resultado da verificação de interferência (preenchido mais abaixo)

    num_rows = st.number_input("Number of tube rows", step=1, min_value=1, key='st_num_rows',
                               help='Number of tube rows')

    num_cols = st.number_input("Number of tube columns", step=1, min_value=1, key='st_num_cols',
                               help='Number of tube columns')

## Verificação de interferência entre tubos (passo mínimo para o setup atual)
interferencia = False

## Curvas em U: as curvas de cada coluna ficam no plano vertical da própria coluna, então as de colunas vizinhas se
## atravessam se o passo horizontal for menor que a largura do tubo (corda), no alternado ou no alinhado
## (passo exatamente 1: as curvas se tocam num ponto, o que gera geometria não-manifold e erro ao importar o STEP em CAD)
passo_min_curvas = 1.02
interferencia_curvas = (st.session_state.get('st_connectype') == 'Curvas' and int(num_cols) > 1 and p_h_c < passo_min_curvas)
bloqueio_curvas = False
try:
    pontos_check = scale_xy_airfoil(centralizar_pontos_na_origem(
        [(float(df__coord_input['x'][i]), float(df__coord_input['y'][i])) for i in range(len(df__coord_input['x']))]), h_c, chord)
    folga, p_v_h_min, p_h_c_min = verificar_interferencia(tuple(pontos_check), int(num_rows), int(num_cols), float(chord),
                                                          float(h_c), float(p_v_h), float(p_h_c), staggered)
    txt_min = []
    if p_v_h_min is not None:
        txt_min.append(f"vertical >= **{p_v_h_min:.3f}**" if p_v_h_min > 1e-3 else "vertical: no limit")
    elif num_rows > 1 or num_cols > 1:
        txt_min.append("vertical: interference for any value with the current horizontal pitch")
    if p_h_c_min is not None:
        txt_min.append(f"horizontal >= **{p_h_c_min:.3f}**" if p_h_c_min > 1e-3 else "horizontal: no limit")
    elif num_cols > 1:
        txt_min.append("horizontal: interference for any value with the current vertical pitch")
    txt_min = ("Minimum dimensionless pitch for this setup (keeping the other pitch): " + ", ".join(txt_min) + ".") if txt_min else ""

    if folga <= 1e-3 * chord:  # encostando (folga ~0) também conta: geometria não-manifold no STEP
        interferencia = True
        status_passo.error("Tubes interfere with (or touch) each other with this pitch. " + txt_min)
    elif math.isinf(folga):
        status_passo.caption("Single tube: no interference check needed.")
    elif interferencia_curvas:
        bloqueio_curvas = True  # só bloqueia o modelo 3D (o 2D da página 2 não tem curvas)
        status_passo.error("With U-bends, the bends of neighbouring columns overlap (or touch) when the dimensionless "
                           "horizontal pitch is not larger than 1 (the tube width): each column has its bends in its own "
                           f"vertical plane. Use a horizontal pitch >= {passo_min_curvas}, or headers.")
    else:
        status_passo.caption(f"No interference between tubes (smallest gap: {folga:.3f} mm, before scaling). " + txt_min)
except Exception:
    pass

##


## Offset
with col1.expander("Wall thickness"):
    offset_ = st.toggle("Optional: Create a wall thickness (hollow tubes).",
                        help='Enable to create a hollow volume. Note: the dimensions selected above refer to the outer profile.'
                        , key='st_offset_')
    if not offset_:
        espessura_offset = st.session_state['st_espessura_offset']
    else:
        espessura_offset = st.number_input("Profile wall thickness", format='%f', step=0.5, min_value=0.01,
                                           key='st_espessura_offset', help='Wall thickness of the model profile (mm).')

##


## Set Scale Input
with col1.expander("Scale"):
    set_scale = st.toggle("Optional: Scale (does not affect the wall thickness)",
                          help='Scales the generated model proportionally in x, y and z.'
                          , key='st_set_scale')

    if not set_scale:
        set_scale = st.session_state['st_set_scale']
        scale = 1
    else:
        scale = st.number_input("Scale factor", format='%f', step=0.5, min_value=0.01, key='st_scale')

##

## Selecting HX Tube Connections
with col1.expander("Tube connections"):
    nomes_conexao = {"N/A": "N/A", "Curvas": "U-bends", "Cabeçote": "Headers"}
    connectype = st.selectbox(
        "Connection type between tubes",
        ("N/A", "Curvas", "Cabeçote"), key='st_connectype', format_func=lambda x: nomes_conexao[x],
        help='Select how the tubes should be connected to each other'
    )

    if connectype != 'Cabeçote':
        L_header = st.session_state['st_L_header']
    else:
        L_header = st.number_input("Header length", format='%f', step=1., key='st_L_header',
                                   help='Must be larger than the fitting diameter, if there is one.')

##

## Set Smooth Input

if forma_tubo == 'Aerofólio':
    with col1.expander("Smoothing"):
        suavizar = st.toggle("Optional: Smooth",
                             help='Slightly smooths the profile curve (fewer points) and speeds up the geometry generation. '
                                  'When disabled, the profile is built as a spline through all the contour points '
                                  '(the more points, the better the spline).'
                             , key='st_suavizar')

        if not suavizar:
            spline_mode = True  # Sem suavização: spline por todos os pontos do contorno
            suavizacao = st.session_state['st_suavizacao']
        else:
            spline_mode = False
            suavizacao = st.number_input("Smoothing factor", format='%f', step=0.0005, min_value=0.0, max_value=0.001,
                                         key='st_suavizacao')
else:
    # Circular / elíptico: perfil exato (sem pontos), não precisa de suavização
    suavizar = False
    spline_mode = False
    suavizacao = st.session_state['st_suavizacao']

##

## Fittings Entrada e Saída

with col1.expander("Inlet and outlet fittings"):
    set_fitting = st.toggle("Optional: Add fittings",
                            help='Adds a circular pipe at the inlet and at the outlet of the heat exchanger.'
                            , key='st_set_fitting')

    if not set_fitting:
        diam_interno_fitting = st.session_state['st_diam_interno_fitting']
        espessura_fitting = st.session_state['st_espessura_fitting']
        L_fitting = st.session_state['st_L_fitting']
    else:
        diam_interno_fitting = st.number_input("Fitting inner diameter", format='%f', step=1.,
                                               key='st_diam_interno_fitting')
        espessura_fitting = st.number_input("Fitting wall thickness", format='%f', step=1., key='st_espessura_fitting')
        L_fitting = st.number_input("Fitting length", format='%f', step=1., key='st_L_fitting')

##

## Aletas

with col1.expander("Fins"):
    set_aletas = st.toggle("Optional: Add plate fins",
                           help='Adds flat plate fins perpendicular to the tubes (plate fin-and-tube heat exchanger), '
                                'evenly spaced along the tube length and pierced by the outer tube profiles.'
                           , key='st_set_aletas')

    if not set_aletas:
        modo_aletas = st.session_state['st_modo_aletas']
        n_aletas = st.session_state['st_n_aletas']
        passo_aleta_in = st.session_state['st_passo_aleta']
        espessura_aleta = st.session_state['st_espessura_aleta']
        margem_aleta = st.session_state['st_margem_aleta']
        unir_aletas = st.session_state['st_unir_aletas']
    else:
        nomes_modo_aletas = {'Quantidade': 'Number of fins', 'Passo': 'Fin pitch'}
        modo_aletas = st.radio("Fin spacing", ('Quantidade', 'Passo'), format_func=lambda x: nomes_modo_aletas[x],
                               key='st_modo_aletas', horizontal=True,
                               help='Number of fins: the fins are evenly spaced along the tube length. '
                                    'Fin pitch: the maximum number of fins that fit with this pitch, centred on the tube length.')
        if modo_aletas == 'Quantidade':
            n_aletas = st.number_input("Number of fins", step=1, min_value=1, key='st_n_aletas')
            passo_aleta_in = st.session_state['st_passo_aleta']
        else:
            passo_aleta_in = st.number_input("Fin pitch", format='%f', step=0.5, min_value=0.01, key='st_passo_aleta',
                                             help='Distance between the centres of two neighbouring fins (mm). Not affected by the scale.')
            n_aletas = st.session_state['st_n_aletas']
        espessura_aleta = st.number_input("Fin thickness", format='%f', step=0.1, min_value=0.01, key='st_espessura_aleta',
                                          help='Fin thickness (mm). Not affected by the scale.')
        margem_aleta = st.number_input("Fin margin", format='%f', step=0.5, min_value=0.0, key='st_margem_aleta',
                                       help='Distance from the outermost tubes to the fin edges (mm). Not affected by the scale.')
        unir_aletas = st.checkbox("Merge fins and tubes into a single solid", key='st_unir_aletas',
                                  help='Unchecked: the fins are separate solids touching the tubes (as the separate parts of a real '
                                       'fin-and-tube heat exchanger), which is much faster. Checked: one single solid (slower boolean union).')

##

## Sketch Preview

preview_button = col1.button("Sketch Preview", width="stretch", on_click=desativ_extrude_button)

if preview_button:
    try:
        scale_ = 1 if not set_scale else scale
        coordinates = [(round(float(df__coord_input['x'][i]), 8), round(float(df__coord_input['y'][i]), 8)) for i in
                       range(len(df__coord_input['x']))]

        coordinates = centralizar_pontos_na_origem(coordinates)

        pontos = scale_xy_airfoil(coordinates, h_c, chord)

        contours, espessura_h, pitch_v, pitch_h = calcular_coordenadas_aerofolios(pontos, num_rows, num_cols, chord,
                                                                                  h_c, p_v_h, p_h_c, staggered)
        plot_airfoils_alternados_plotly(contours)
    except Exception as e:
        col1.error(f"Error generating the contours: {e}")

##

## Setup do modelo 2D e da malha (salvo para a página 2)

try:
    coordinates_2d = [(float(df__coord_input['x'][i]), float(df__coord_input['y'][i])) for i in range(len(df__coord_input['x']))]
    coordinates_2d = centralizar_pontos_na_origem(coordinates_2d)
    simetria_x_2d = True if forma_tubo == 'Circular/Elíptico' else detectar_simetria(coordinates_2d)['x']
    coordinates_2d = suavizar_contorno(coordinates_2d, suavizar, suavizacao)
    pontos_2d = scale_xy_airfoil(coordinates_2d, h_c, chord)
    contours_2d, espessura_h_2d, pitch_v_2d, pitch_h_2d = calcular_coordenadas_aerofolios(pontos_2d, num_rows, num_cols,
                                                                                          chord, h_c, p_v_h, p_h_c,
                                                                                          staggered)
except Exception:
    contours_2d, simetria_x_2d, pitch_v_2d = None, False, 0.

st.session_state['setup_2d'] = {
    'forma_tubo': forma_tubo,
    'spline_mode': spline_mode,
    'contours': contours_2d,
    'chord': chord,
    'h_c': h_c,
    'pitch_v': pitch_v_2d,
    'num_rows': int(num_rows),
    'num_cols': int(num_cols),
    'set_scale': set_scale,
    'scale': scale,
    'simetria_x': simetria_x_2d,
    'staggered': staggered,
    'interferencia': interferencia
}

##


run_button = col2.button("Generate Model", width="stretch")

if run_button:
    st.session_state.extrude_button = True

if st.session_state.extrude_button and (interferencia or bloqueio_curvas):
    col2.error("The tubes (or the U-bends) interfere with each other with the current pitch. Increase the vertical "
               "and/or horizontal pitch (see the message below the pitch inputs) and generate the model again.")
    st.session_state.extrude_button = False

if st.session_state.extrude_button and df__coord_input is None:
    col2.warning("Upload a coordinates file (.xlsx or .csv) to generate the model.")
    st.session_state.extrude_button = False

if st.session_state.extrude_button:
    # Saving the inputs
    ## For coord's df
    try:
        st.session_state['st_coordinates_x'] = df__coord_input['x']
        st.session_state['st_coordinates_y'] = df__coord_input['y']
    except:
        col2.error('Error: contour coordinates. Check the uploaded file.')
        pass

    with col2:

        with st.spinner('Loading...'):

            try:
                my_bar = st.progress(0, text='Tubes')

                ################# RUNNING #################

                coordinates = [(float(df__coord_input['x'][i]), float(df__coord_input['y'][i])) for i in range(len(df__coord_input['x']))]

                coordinates = centralizar_pontos_na_origem(coordinates)  # *

                simetry = detectar_simetria(coordinates)  # +
                if forma_tubo == 'Circular/Elíptico':
                    simetry = {'x': True, 'y': True}  # Elipse centrada é simétrica nos dois eixos

                coordinates = suavizar_contorno(coordinates, suavizar, suavizacao)

                pontos = scale_xy_airfoil(coordinates, h_c, chord)

                contours, espessura_h, pitch_v, pitch_h = calcular_coordenadas_aerofolios(pontos, num_rows, num_cols,
                                                                                          chord,
                                                                                          h_c, p_v_h, p_h_c, staggered)


                contour_coordinates_ = []

                result = None
                result_ = None
                result_externo = None
                modelo_combinado = None
                ajuste_espessura = False
                s1_offset_list = None

                sketch_list = []
                sketch_offset_list = []
                sketch_ext_list = []

                ## Perfil de referência na origem: polígono (suavizado), spline por todos os pontos (sem suavização)
                ## ou elipse/círculo exato. Os perfis do banco são cópias deslocadas dele (mesma geometria em todos os
                ## tubos: o offset da spline não falha em um tubo e funciona em outro por diferença numérica)
                try:
                    sketch_base = criar_sketch_perfil(pontos, forma_tubo, spline_mode)
                except Exception as e:
                    raise RuntimeError(f"The tube profile could not be created: {e}. "
                                       + ("Try enabling smoothing or check that the coordinates are ordered and do not cross each other."
                                          if forma_tubo == 'Aerofólio' else "Check the tube radii."))
                sketch_base_offset = None

                for contour in contours:

                    if contour is None:
                        continue

                    ## Criar o esboço inicial
                    sketch1 = cq.Sketch()

                    try:

                        dx_perfil, dy_perfil = contour[0][0] - pontos[0][0], contour[0][1] - pontos[0][1]
                        sketch1 = sketch_base.moved(x=dx_perfil, y=dy_perfil)

                        sketch1 = sketch1.reset()  # * RESET Limpa o estado interno do esboço, mantendo apenas o resultado final (a face)

                        if set_scale:
                            contour_coordinates_.append(
                                [(v.X, v.Y) for v in sketch1.copy().wires().val().scale(scale).Vertices()])
                        else:
                            contour_coordinates_.append([(v.X, v.Y) for v in sketch1.copy().wires().val().Vertices()])  # Coordenadas ajustadas com escala

                        sketch_ext_list.append(sketch1.copy())  # external

                        try:
                            result = result + sketch1
                        except:
                            result = sketch1

                        if offset_ is True:

                            if set_scale is True and 2 * espessura_offset / scale > espessura_h:  # Detectar se foi necessário ajuste na espessura
                                espessura_offset = espessura_h * scale / 2.5
                                ajuste_espessura = True

                            elif set_scale is False and 2 * espessura_offset > espessura_h:  # Detectar se foi necessário ajuste na espessura
                                espessura_offset = espessura_h / 2.5
                                ajuste_espessura = True

                            if sketch_base_offset is None and spline_mode and simetry['x']:
                                # Spline de perfil simétrico: contorno interno simetrizado (o offset direto da spline
                                # não sai exatamente simétrico e deixa frestas nas junções com as curvas em U e fittings)
                                try:
                                    sketch_base_offset = offset_spline_por_poligono(
                                        pontos, espessura_offset / scale if set_scale else espessura_offset)
                                except Exception:
                                    sketch_base_offset = None

                            if sketch_base_offset is None:  # offset calculado uma vez no perfil de referência
                                try:
                                    sketch_base_offset = sketch_base.copy().wires().offset(-(espessura_offset / scale),
                                                                                           mode='r').reset() if set_scale else sketch_base.copy().wires().offset(
                                        -(espessura_offset),
                                        mode='r').reset()  # * * RESET Limpa o estado interno do esboço, mantendo apenas o resultado final (a face)
                                except Exception as e:
                                    try:
                                        if not spline_mode:
                                            raise
                                        sketch_base_offset = offset_spline_por_poligono(
                                            pontos, espessura_offset / scale if set_scale else espessura_offset)
                                    except Exception:
                                        raise RuntimeError(f"Wall thickness (offset) failed: {e}. Reduce the wall thickness "
                                                           + ("or enable smoothing." if spline_mode else "or increase the tube thickness."))

                            sketch1_offset = sketch_base_offset.moved(x=dx_perfil, y=dy_perfil)

                            sketch_offset_list.append(sketch1_offset.copy())  # internal

                            sketch_list.append(sketch1 - sketch1_offset)  # external - internal profile

                            try:
                                result_ = result_ + sketch1_offset
                            except:
                                result_ = sketch1_offset


                        else:
                            sketch_list.append(sketch1)  # external profile only

                    except RuntimeError:
                        raise
                    except Exception as e:
                        col2.error(f"Error generating a contour sketch: {e}")
                        continue

                if not sketch_list:
                    raise RuntimeError("No tube profile could be generated. "
                                       + ("Try enabling smoothing or check that the coordinates are ordered and do not cross each other."
                                          if forma_tubo == 'Aerofólio' else "Check the tube radii."))

                result_externo = result  # Perfis externos (para os furos das aletas)

                if offset_ is True:
                    try:
                        result = result - result_
                    except Exception as e:
                        raise RuntimeError(f"Wall thickness (offset) failed: {e}. Reduce the wall thickness or enable smoothing.")
                    if ajuste_espessura is True:
                        col2.error(f"Wall thickness adjusted to the maximum = {espessura_offset:.3f} mm")


                if set_scale is True:
                    try:
                        result = result.val().scale(scale)  # .translate(((-chord/2)*scale, 0, 0))
                    except:
                        col2.error("Error during scaling")
                        length = length / scale
                        # pass

                # 3D

                ##   SCALE

                ### Pegar os sketches de referência
                s1 = sketch_list[0]
                try:
                    s1_offset_list = sketch_offset_list[0]
                except:
                    pass

                ### Copiar os valores iniciais de pitch
                pitch_v_ = pitch_v
                pitch_h_ = pitch_h
                chord_ = chord
                espessura_h_ = espessura_h
                length_ = length

                if set_scale:
                    try:
                        s1 = s1.val().scale(scale)
                        s1_offset_list = s1_offset_list.val().scale(scale) if offset_ else 0
                        pitch_v_ = pitch_v_ * scale
                        pitch_h_ = pitch_h_ * scale
                        chord_ = chord_ * scale
                        espessura_h_ = espessura_h_ * scale
                        length_ = length * scale
                    except Exception as e:
                        st.error(f"Scaling error: {e}")

                try:
                    ## Exportar o modelo como STL

                    exporters.export(result, cad_dir + '/sketch_hxairfoils.stl')

                    sketch = cq.Workplane("XY").placeSketch(result)

                    modelo_combinado = sketch.extrude(length_)

                    tube_profile = cq.Workplane("XY").placeSketch(s1).extrude(length_)
                    exportar_stl(tube_profile, cad_dir + '/hx_profile.stl', chord_)
                    if connectype == 'N/A':
                        exporters.export(tube_profile, cad_dir + '/hx_profile.step')

                except Exception as e:
                    raise RuntimeError(f"Error generating the 3D model (tube extrusion): {e}. "
                                       "Check the tube length and the wall thickness.")

                ################# CONEXÕES #################

                ################# CURVAS

                if connectype == 'Curvas':
                    my_bar.progress(50, text='Connections between rows')

                    #   CONEXÕES ENTRE LINHAS

                    try:
                        modelo_final = hx_lin(
                            modelo_combinado,
                            s1,
                            pitch_h_,
                            pitch_v_,
                            length_,
                            num_cols,
                            num_rows,
                            set_scale,
                            simetry,
                            staggered
                        )
                    except Exception as e:
                        raise RuntimeError(f"U-bends between rows failed: {e}. The vertical pitch may be too small for "
                                           "the tube thickness: increase the vertical pitch, reduce the wall thickness "
                                           "or enable smoothing.")

                    #   CONEXÕES ENTRE COLUNAS

                    my_bar.progress(70, text='Connections between columns')

                    try:
                        modelo_final2 = hx_col(
                            pontos=pontos,
                            scale=scale,
                            set_scale=set_scale,
                            espessura_offset=espessura_offset,
                            s1=s1,
                            s1_offset_list=s1_offset_list,
                            offset_=offset_,
                            pitch_h_=pitch_h_,
                            pitch_v_=pitch_v_,
                            length_=length_,
                            num_cols=num_cols,
                            num_rows=num_rows,
                            modelo_final=modelo_final,
                            simetry=simetry,
                            staggered=staggered
                        )
                    except Exception as e:
                        raise RuntimeError(f"U-bends between columns failed: {e}. Increase the horizontal pitch, reduce "
                                           "the wall thickness or enable smoothing.")

                    #   CONEXÕES DE ENTRADA E SAÍDA (FITTINGS)

                    if set_fitting:
                        my_bar.progress(85, text='Adding inlet and outlet fittings')

                    try:
                        modelo_final2 = adicionar_fittings(
                            modelo_final2,
                            diam_interno_fitting,
                            espessura_fitting,
                            L_fitting,
                            s1,
                            s1_offset_list,
                            set_fitting,
                            num_cols,
                            num_rows,
                            pitch_h_,
                            pitch_v_,
                            length_,
                            staggered
                        )
                    except Exception as e:
                        raise RuntimeError(f"Inlet/outlet fittings failed: {e}. Check the fitting diameter, wall "
                                           "thickness and length.")


                ################# CABEÇOTE

                elif connectype == 'Cabeçote':
                    my_bar.progress(50, text='Creating headers')

                    try:
                        modelo_final2 = criar_header_hx(
                            connectype,
                            pitch_h_,
                            pitch_v_,
                            num_cols,
                            num_rows,
                            espessura_offset,
                            espessura_h_,
                            chord_,
                            offset_,
                            sketch_ext_list,
                            scale,
                            set_scale,
                            L_header,
                            set_fitting,
                            diam_interno_fitting,
                            espessura_fitting,
                            L_fitting,
                            modelo_combinado,
                            length_
                        )
                    except Exception as e:
                        raise RuntimeError(f"Headers failed: {e}. The header length must be larger than the fitting "
                                           "outer diameter and larger than twice the wall thickness.")

                ################# N/A

                else:
                    modelo_final2 = modelo_combinado

                ################# ALETAS

                if set_aletas:
                    my_bar.progress(88, text='Adding fins')

                    try:
                        # Com cabeçote, as aletas ficam entre as bases dos cabeçotes
                        z_ini_aletas = espessura_offset * 1.5 if connectype == 'Cabeçote' else 0
                        z_fim_aletas = length_ - espessura_offset * 1.5 if connectype == 'Cabeçote' else length_

                        modelo_final2, passo_aleta, n_aletas_final = criar_aletas(
                            modelo_final2,
                            result_externo,
                            scale if set_scale else 1,
                            n_aletas,
                            espessura_aleta,
                            margem_aleta,
                            z_ini_aletas,
                            z_fim_aletas,
                            unir_aletas,
                            passo_aleta_in if modo_aletas == 'Passo' else None
                        )
                        st.caption(f"Fins: {n_aletas_final}, fin pitch: {passo_aleta:.3f} mm (gap between fins: "
                                   f"{passo_aleta - espessura_aleta:.3f} mm).")
                    except Exception as e:
                        raise RuntimeError(f"Fins failed: {e}")

                ############################### RESULTS

                try:
                    exportar_stl(modelo_final2, cad_dir + '/hx_final.stl', chord_)
                    exporters.export(modelo_final2, cad_dir + '/hx_final.step')
                except Exception as e:
                    raise RuntimeError(f"Export of the final model failed: {e}. The geometry may be invalid: try "
                                       "enabling smoothing, increasing the pitch or reducing the wall thickness.")

                my_bar.progress(90, text='Display')

                try:
                    displayCAD_streamlit(cad_dir + '/hx_final.stl', f"Preview: hx_final.step")

                    st.success("Model generated successfully.")
                    st.session_state.st_sketch = result
                    st.session_state.st_solid = modelo_final2

                except Exception as e:
                    st.warning(f"The model was generated, but the 3D preview failed: {e}")

                my_bar.progress(95, text='.STL files')

                ############################### DOWNLOAD

                st.divider()
                st.subheader("⬇️ Download", divider='gray', anchor=False)

                col21, col22 = col2.columns([1, 1])

                solid_name = 'hx_final'
                sketch_name = 'sketch_hxairfoils.stl'
                profile_name = 'hx_profile'
                stl_file_sketch = cad_dir + "/" + sketch_name
                stl_file_solid = cad_dir + "/" + solid_name + '.stl'
                step_file_solid = cad_dir + "/" + solid_name + '.step'
                stl_file_profile = cad_dir + "/" + profile_name + '.stl'
                step_file_profile = cad_dir + "/" + profile_name + '.step'

                # Create a download button for STL
                ## Sketch
                col21.download_button(
                    label="Sketch Surface .stl",
                    data=open(stl_file_sketch, "rb").read(),
                    file_name=sketch_name,
                    mime="application/stl",
                    on_click="ignore",  # download sem rerun (não regera o modelo)
                    width="stretch"
                )
                ## Solid
                col22.download_button(
                    label="Solid .stl",
                    data=open(stl_file_solid, "rb").read(),
                    file_name=solid_name + '.stl',
                    mime="application/stl",
                    on_click="ignore",  # download sem rerun (não regera o modelo)
                    width="stretch"
                )

                col2.download_button(
                    label="Solid .step",
                    data=open(step_file_solid, "rb").read(),
                    file_name=solid_name + '.step',
                    mime="application/step",
                    on_click="ignore",  # download sem rerun (não regera o modelo)
                    width="stretch"
                )

                if connectype == 'Cabeçote':
                    step_file_header = cad_dir + "/" + 'header_body' + '.step'
                    col2.download_button(
                        label="Header (Solid) .step",
                        data=open(step_file_header, "rb").read(),
                        file_name='header_body' + '.step',
                        mime="application/step",
                        on_click="ignore",  # download sem rerun (não regera o modelo)
                        width="stretch"
                    )

                if connectype == 'N/A':
                    col21.download_button(
                        label="Tube Profile (Solid) .stl",
                        data=open(stl_file_profile, "rb").read(),
                        file_name=profile_name + '.stl',
                        mime="application/stl",
                        on_click="ignore",  # download sem rerun (não regera o modelo)
                        width="stretch"
                    )
                    col22.download_button(
                        label="Tube Profile (Solid) .step",
                        data=open(step_file_profile, "rb").read(),
                        file_name=profile_name + '.step',
                        mime="application/step",
                        on_click="ignore",  # download sem rerun (não regera o modelo)
                        width="stretch"
                    )
                else:
                    col2.download_button(
                        label="Tube Profile (Solid) .stl",
                        data=open(stl_file_profile, "rb").read(),
                        file_name=profile_name + '.stl',
                        mime="application/stl",
                        on_click="ignore",  # download sem rerun (não regera o modelo)
                        width="stretch"
                    )

                my_bar.progress(100, text='Loading')

                time.sleep(1)
                my_bar.empty()

            except Exception as e:
                st.error(f"Error generating the model: {e}")
                try:
                    my_bar.empty()
                except Exception:
                    pass
else:
    col2.markdown("")

    # https://aerohx-xbgevddyrwrida74b4hvnx.streamlit.app/

