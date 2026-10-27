import streamlit as st

st.session_state.update(st.session_state)
for k, v in st.session_state.items():
    st.session_state[k] = v

from PIL import Image
import os

path = os.path.dirname(__file__)
my_file = path + '/images/mechub_logo.png'
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

import cadquery as cq
from cadquery import exporters

import plotly.graph_objects as go
from plotly.subplots import make_subplots

import tempfile
import hashlib
import threading
import importlib.util
from types import SimpleNamespace

## Verifica se Gmsh está instalado. Ele é importado só ao gerar a malha (biblioteca pesada)
gmsh_ok = importlib.util.find_spec('gmsh') is not None
gmsh_erro = '' if gmsh_ok else "No module named 'gmsh'"


################# FUNCTIONS #################

# Mesmas funções da página 1 (Home), usadas para gerar o STEP 2D
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


################# MODELO 2D E MALHA (CFD) #################

# Gera o STEP 2D do banco (faces dos perfis externos, em mm), como no código de CFD validado.
# Ficam de fora os recursos que só fazem sentido no modelo 3D de fabricação
# (perfil oco/offset, conexões, fittings, aletas, extrusão)
def gerar_step_2d(contours, forma_tubo, spline_mode, set_scale, scale, file_):
    # Perfil de referência (primeiro contorno) e cópias deslocadas: mesma geometria em todos os tubos
    face_base = criar_sketch_perfil(contours[0], forma_tubo, spline_mode).faces().val()
    faces = []
    for contour in contours:
        dx, dy = contour[0][0] - contours[0][0][0], contour[0][1] - contours[0][0][1]
        faces.append(face_base.moved(cq.Location(cq.Vector(dx, dy, 0))))

    result_shape = cq.Compound.makeCompound(faces)
    if set_scale:
        result_shape = result_shape.scale(scale)

    exporters.export(result_shape, file_)
    return file_


# Ajuste aplicado quando a malha reprova no controle de qualidade (Gmsh)
def relax_mesh_params(cfg):
    cfg.size_bank = max(cfg.size_bank * cfg.refine_shrink_factor, cfg.size_airfoil)
    cfg.refine_dist_max *= cfg.refine_grow_factor
    cfg.bl_max_layers = max(cfg.bl_min_layers, cfg.bl_max_layers - 1)


# Classifica as entidades de contorno (curvas no 2D, faces laterais no extrudado) em Inlet / Outlet / Walls / Airfoil
# Tudo que não está na caixa externa (todos os recortes internos) vai para Airfoil
def _classify_boundary(entidades, xmin, xmax, ymin, ymax, tol):
    import gmsh

    groups = {"Inlet": [], "Outlet": [], "Walls": [], "Airfoil": []}
    for dim, tag in entidades:
        bx0, by0, _, bx1, by1, _ = gmsh.model.getBoundingBox(dim, tag)
        if abs(bx0 - xmin) < tol and abs(bx1 - xmin) < tol:
            groups["Inlet"].append(tag)
        elif abs(bx0 - xmax) < tol and abs(bx1 - xmax) < tol:
            groups["Outlet"].append(tag)
        elif (abs(by0 - ymin) < tol and abs(by1 - ymin) < tol) or (abs(by0 - ymax) < tol and abs(by1 - ymax) < tol):
            groups["Walls"].append(tag)
        else:
            groups["Airfoil"].append(tag)
    return groups


# Amostra pontos (x, y) ao longo de cada curva
def _sample_curves(curve_tags, n=400):
    import gmsh

    samples = {}
    for tag in curve_tags:
        t0, t1 = gmsh.model.getParametrizationBounds(1, tag)
        params = np.linspace(t0[0], t1[0], n)
        xyz = np.array(gmsh.model.getValue(1, tag, params.tolist())).reshape(-1, 3)
        samples[tag] = xyz[:, :2]
    return samples


# Junta as amostras das curvas por perfil (o perfil com o contorno mais próximo do ponto médio da curva).
# No modo polígono (suavizado) cada perfil tem várias curvas; no spline/elipse, uma só
def _agrupar_por_perfil(samples, contornos_m):
    grupos = {}
    for tag, pts in samples.items():
        meio = pts[len(pts) // 2]
        dists = [float(np.min(np.sum((c - meio) ** 2, axis=1))) for c in contornos_m]
        grupos.setdefault(int(np.argmin(dists)), []).append(pts)
    return {k: np.vstack(v) for k, v in grupos.items()}


# Menor distância entre superfícies de perfis diferentes e entre perfil e parede
def _min_gap(grupos, ymin, ymax):
    pts = []
    for k in grupos:
        p = grupos[k]
        if len(p) > 600:
            p = p[np.linspace(0, len(p) - 1, 600).astype(int)]
        pts.append(p)
    gap = math.inf
    tol = 1e-6 * (ymax - ymin)
    for i, pa in enumerate(pts):
        for folga_parede in (float(pa[:, 1].min() - ymin), float(ymax - pa[:, 1].max())):
            if folga_parede > tol:  # tubos cortados pela parede (célula simétrica) não contam
                gap = min(gap, folga_parede)
        for pb in pts[i + 1:]:
            d = np.sqrt(((pa[:, None, :] - pb[None, :, :]) ** 2).sum(axis=-1))
            gap = min(gap, float(d.min()))
    return gap


# Altura da 1a célula para o y+ alvo (placa plana turbulenta, Cf = 0.058 Re^-0.2)
def estimate_first_layer_height(cfg):
    Re = cfg.U_inlet * cfg.chord / cfg.nu_air
    Cf = 0.058 * Re ** (-0.2)
    u_tau = math.sqrt(0.5 * Cf * cfg.U_inlet ** 2)
    return cfg.y_plus_target * cfg.nu_air / u_tau


# Número de camadas e espessura total da camada-limite, limitada a uma fração do menor vão
def _boundary_layer_layout(cfg, y1, min_gap):
    r = cfg.bl_ratio
    t_max = cfg.bl_max_gap_fraction * min_gap
    n = int(math.floor(math.log(1.0 + t_max * (r - 1.0) / y1) / math.log(r)))
    n = max(3, min(n, cfg.bl_max_layers))
    if cfg.bl_ultima_camada_superficie:
        # Última camada ~ tamanho na superfície (transição suave para os triângulos)
        n = max(3, min(n, int(math.floor(math.log(cfg.size_airfoil * cfg.chord / y1) / math.log(r))) + 1))
    return n, y1 * (r ** n - 1.0) / (r - 1.0)


# Métricas de qualidade do Gmsh na face frontal:
#   SICN  -> "minSICN" e Gamma -> "gamma" nos triângulos (núcleo isotrópico)
#   Rho   -> "minSJ" (Scaled Jacobian) em todos os elementos, inclusive os quads da camada-limite
def _mesh_quality_2d(cfg, surface_tag):
    import gmsh

    tri, quad = [], []
    for etype, etags in zip(*gmsh.model.mesh.getElements(2, surface_tag)[:2]):
        name = gmsh.model.mesh.getElementProperties(etype)[0]
        (tri if name.startswith("Triangle") else quad).extend(etags)

    def q(tags, name):
        return np.array(gmsh.model.mesh.getElementQualities(tags, name)) if tags else np.array([1.0])

    arrays = {"SICN": q(tri, "minSICN"), "Gamma": q(tri, "gamma"), "Rho": q(tri + quad, "minSJ")}
    stats = {"n_tri": len(tri), "n_quad": len(quad)}
    for key, meta in (("SICN", cfg.min_sicn), ("Gamma", cfg.min_gamma), ("Rho", cfg.min_rho)):
        values = arrays[key]
        stats[f"{key}_min"] = float(values.min())
        stats[f"{key}_avg"] = float(values.mean())
        stats[f"{key}_bad"] = float((values < meta).mean())
    stats["_arrays"] = arrays
    return stats


def _metric_ok(cfg, s, key):
    return s[f"{key}_min"] >= cfg.quality_hard_floor and s[f"{key}_bad"] <= cfg.max_bad_fraction


def _quality_ok(cfg, s):
    return all(_metric_ok(cfg, s, k) for k in ("SICN", "Gamma", "Rho"))


# (Re)aplica os valores atuais da config aos campos do Gmsh (em metros)
def _set_size_fields(cfg, fields, y1, min_gap):
    import gmsh

    c = cfg.chord
    f = gmsh.model.mesh.field
    f.setNumber(fields["threshold"], "SizeMin", cfg.size_airfoil * c)
    f.setNumber(fields["threshold"], "SizeMax", cfg.size_bank * c)
    f.setNumber(fields["threshold"], "DistMin", cfg.refine_dist_min * c)
    f.setNumber(fields["threshold"], "DistMax", cfg.refine_dist_max * c)
    if "te_threshold" in fields:
        f.setNumber(fields["te_threshold"], "SizeMin", cfg.size_te * c)
        f.setNumber(fields["te_threshold"], "SizeMax", cfg.size_airfoil * c)
        f.setNumber(fields["te_threshold"], "DistMin", 2.0 * cfg.size_te * c)
        f.setNumber(fields["te_threshold"], "DistMax", cfg.te_refine_dist * c)
    f.setNumber(fields["wake_threshold"], "SizeMin", cfg.size_wake_line * c)
    f.setNumber(fields["wake_threshold"], "SizeMax", cfg.size_bank * c)
    f.setNumber(fields["wake_threshold"], "DistMin", cfg.wake_line_dist_min * c)
    f.setNumber(fields["wake_threshold"], "DistMax", cfg.wake_line_dist_max * c)
    f.setNumber(fields["box_bank"], "VIn", cfg.size_bank * c)
    f.setNumber(fields["box_bank"], "VOut", cfg.size_far * c)
    f.setNumber(fields["box_wake"], "VIn", cfg.size_wake * c)
    f.setNumber(fields["box_wake"], "VOut", cfg.size_far * c)
    gmsh.option.setNumber("Mesh.MeshSizeMax", cfg.size_far * c)

    if "bl" in fields:
        n_layers, bl_thickness = _boundary_layer_layout(cfg, y1, min_gap)
        f.setNumber(fields["bl"], "Size", y1)
        f.setNumber(fields["bl"], "Ratio", cfg.bl_ratio)
        f.setNumber(fields["bl"], "Thickness", bl_thickness)


# Domínio fluido (caixa - banco de perfis), campos de refino (Distance/Threshold nos perfis + Box no banco e na esteira),
# camada-limite, (extrusão 2D para OpenFOAM), Physical Groups, controle automático de qualidade e exportação.
# Mesma metodologia do código de CFD validado (build_and_mesh_domain)
def gerar_malha_cfd(cfg, step_file, contornos_m, file_base, openfoam=False, progresso=None):
    try:
        import gmsh
    except Exception as e:
        raise RuntimeError(f"Gmsh could not be loaded: {e}. On Linux (e.g. Streamlit Cloud) it needs the system libraries "
                           "listed in packages.txt (libglu1-mesa, libxcursor1, libxinerama1, libxft2).")

    c = cfg.chord
    avisos = []
    gmsh.initialize(interruptible=False)  # interruptible=False: o Streamlit roda o script fora da thread principal

    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("hx_bank")
        occ = gmsh.model.occ

        # --- importa o banco (STEP em mm -> convertido para m) ---
        gmsh.option.setString("Geometry.OCCTargetUnit", "M")
        airfoil_dimtags = [(d, t) for d, t in occ.importShapes(step_file) if d == 2]
        occ.synchronize()
        if not airfoil_dimtags:
            raise RuntimeError("The 2D model has no faces to mesh.")
        if len(airfoil_dimtags) != cfg.n_airfoils:
            avisos.append(f"The STEP file has {len(airfoil_dimtags)} faces, {cfg.n_airfoils} expected.")

        # --- extensão do banco a partir das faces importadas ---
        boxes = [gmsh.model.getBoundingBox(*dt) for dt in airfoil_dimtags]
        bank_xmin, bank_xmax = min(b[0] for b in boxes), max(b[3] for b in boxes)
        bank_ymin, bank_ymax = min(b[1] for b in boxes), max(b[4] for b in boxes)
        centers_y = [(b[1] + b[4]) / 2 for b in boxes]

        # --- domínio: X em cordas antes/depois do banco; Y apenas margem em pitch_v*scale acima/abaixo ---
        margin_y = cfg.domain_y_margin_pitches * cfg.pitch_v_m
        if cfg.domain_y_reference == "centro":
            ref_ymin, ref_ymax = min(centers_y), max(centers_y)
        else:
            ref_ymin, ref_ymax = bank_ymin, bank_ymax
        xmin, xmax = bank_xmin - cfg.domain_ahead * c, bank_xmax + cfg.domain_behind * c
        ymin, ymax = ref_ymin - margin_y, ref_ymax + margin_y

        ## Célula simétrica: fileira do meio inteira e metade das fileiras de cima e de baixo.
        ## As paredes passam pela linha de corda dos tubos vizinhos (planos de simetria do banco alternado)
        if cfg.simetria_y0 is not None:
            ymin, ymax = cfg.simetria_y0 - cfg.pitch_v_m, cfg.simetria_y0 + cfg.pitch_v_m
            bank_ymin, bank_ymax = max(bank_ymin, ymin), min(bank_ymax, ymax)

        ## Segurança: as paredes do domínio não podem cortar (nem encostar em) nenhum perfil
        folga_parede = 0.05 * c
        if cfg.simetria_y0 is None and (ymin > bank_ymin - folga_parede or ymax < bank_ymax + folga_parede):
            ymin = min(ymin, bank_ymin - max(margin_y, 0.5 * c))
            ymax = max(ymax, bank_ymax + max(margin_y, 0.5 * c))
            avisos.append("The vertical margin would cut through the tubes, so the domain walls were moved to "
                          f"{max(margin_y, 0.5 * c) * 1e3:.2f} mm from the bank surface.")

        box_tag = occ.addRectangle(xmin, ymin, 0.0, xmax - xmin, ymax - ymin)
        cut_result, _ = occ.cut([(2, box_tag)], airfoil_dimtags, removeObject=True, removeTool=True)
        occ.synchronize()
        cut_result = [dt for dt in cut_result if dt[0] == 2]
        if len(cut_result) != 1:
            raise RuntimeError(f"The fluid domain was split into {len(cut_result)} regions (tubes touching each other "
                               "or the domain walls). Increase the pitch or the vertical margin.")
        front_face = cut_result[0][1]

        # --- curvas dos perfis = todas as arestas internas resultantes do recorte ---
        tol = 1e-6 * max(xmax - xmin, ymax - ymin)
        borda = [(1, ctag) for _, ctag in gmsh.model.getBoundary([(2, front_face)], oriented=False)]
        groups_2d = _classify_boundary(borda, xmin, xmax, ymin, ymax, tol)
        airfoil_curves = groups_2d["Airfoil"]
        if not airfoil_curves:
            raise RuntimeError("No tube contour was found inside the fluid domain.")

        # --- camada-limite: 1a célula p/ y+ alvo, espessura limitada pelo menor vão ---
        n_amostras = int(np.clip(4800 / len(airfoil_curves), 10, 400))
        samples = _sample_curves(airfoil_curves, n_amostras)
        grupos = _agrupar_por_perfil(samples, contornos_m)
        min_gap = _min_gap(grupos, ymin, ymax)
        if min_gap <= 0:
            raise RuntimeError("Tubes are touching each other (zero gap). Increase the pitch.")
        y1 = estimate_first_layer_height(cfg)

        # --- extrusão em z (OpenFOAM): fatia de 1 célula ---
        depth = cfg.extrusion_depth * c
        if openfoam:
            extrude_result = occ.extrude([(2, front_face)], 0, 0, depth, numElements=[1], recombine=True)
            occ.synchronize()
            back_face = extrude_result[0][1]
            volume_tag = next(t for d, t in extrude_result if d == 3)
            side_surfaces = [(d, t) for d, t in extrude_result if d == 2 and t != back_face]
            groups = _classify_boundary(side_surfaces, xmin, xmax, ymin, ymax, tol)

            gmsh.model.addPhysicalGroup(2, groups["Inlet"], name="Inlet")
            gmsh.model.addPhysicalGroup(2, groups["Outlet"], name="Outlet")
            gmsh.model.addPhysicalGroup(2, groups["Walls"], name="Walls")
            gmsh.model.addPhysicalGroup(2, groups["Airfoil"], name="Airfoil")
            gmsh.model.addPhysicalGroup(2, [front_face, back_face], name="frontAndBack")
            gmsh.model.addPhysicalGroup(3, [volume_tag], name="fluid")
            if len(groups["Airfoil"]) < len(airfoil_curves):
                raise RuntimeError("Not all tube edges were assigned to the 'Airfoil' group.")
        else:
            gmsh.model.addPhysicalGroup(1, groups_2d["Inlet"], name="Inlet")
            gmsh.model.addPhysicalGroup(1, groups_2d["Outlet"], name="Outlet")
            gmsh.model.addPhysicalGroup(1, groups_2d["Walls"], name="Walls")
            gmsh.model.addPhysicalGroup(1, groups_2d["Airfoil"], name="Airfoil")
            gmsh.model.addPhysicalGroup(2, [front_face], name="fluid")

        # --- campos de tamanho ---
        f = gmsh.model.mesh.field
        fields = {}

        fields["distance"] = f.add("Distance")
        f.setNumbers(fields["distance"], "CurvesList", airfoil_curves)
        f.setNumber(fields["distance"], "Sampling", n_amostras)

        fields["threshold"] = f.add("Threshold")
        f.setNumber(fields["threshold"], "InField", fields["distance"])
        f.setNumber(fields["threshold"], "StopAtDistMax", 1)  # fora de DistMax quem manda são as caixas

        lista_campos = [fields["threshold"]]

        # refino local nos bordos de fuga (ponto de x máximo de cada perfil) - só aerofólio
        if cfg.refino_te:
            te_points = []
            for pts in grupos.values():
                x_te, y_te = pts[pts[:, 0].argmax()]
                te_points.append(occ.addPoint(float(x_te), float(y_te), 0.0))
            occ.synchronize()
            fields["te_distance"] = f.add("Distance")
            f.setNumbers(fields["te_distance"], "PointsList", te_points)
            fields["te_threshold"] = f.add("Threshold")
            f.setNumber(fields["te_threshold"], "InField", fields["te_distance"])
            f.setNumber(fields["te_threshold"], "StopAtDistMax", 1)
            lista_campos.append(fields["te_threshold"])

        # esteira de cada perfil: linha do bordo de fuga até wake_line_length cordas a jusante
        wake_lines = []
        for pts in grupos.values():
            x_te, y_te = pts[pts[:, 0].argmax()]
            x_end = min(float(x_te) + cfg.wake_line_length * c, xmax - 0.01 * c)
            p0 = occ.addPoint(float(x_te) + 0.002 * c, float(y_te), 0.0)
            p1 = occ.addPoint(x_end, float(y_te), 0.0)
            wake_lines.append(occ.addLine(p0, p1))
        occ.synchronize()
        fields["wake_distance"] = f.add("Distance")
        f.setNumbers(fields["wake_distance"], "CurvesList", wake_lines)
        f.setNumber(fields["wake_distance"], "Sampling", 300)
        fields["wake_threshold"] = f.add("Threshold")
        f.setNumber(fields["wake_threshold"], "InField", fields["wake_distance"])
        f.setNumber(fields["wake_threshold"], "StopAtDistMax", 1)
        lista_campos.append(fields["wake_threshold"])

        # caixa "verde": banco + esteira imediata
        fields["box_bank"] = f.add("Box")
        for k, v in {
            "XMin": bank_xmin - cfg.bank_box_upstream * c, "XMax": bank_xmax + cfg.bank_box_downstream * c,
            "YMin": ymin, "YMax": ymax, "ZMin": -1.0, "ZMax": 1.0, "Thickness": cfg.bank_box_transition * c,
        }.items():
            f.setNumber(fields["box_bank"], k, v)

        # caixa "bege": transição da esteira para o far-field ("vermelho")
        fields["box_wake"] = f.add("Box")
        for k, v in {
            "XMin": bank_xmin - cfg.wake_box_upstream * c, "XMax": bank_xmax + cfg.wake_box_downstream * c,
            "YMin": ymin, "YMax": ymax, "ZMin": -1.0, "ZMax": 1.0, "Thickness": cfg.wake_box_transition * c,
        }.items():
            f.setNumber(fields["box_wake"], k, v)
        lista_campos += [fields["box_bank"], fields["box_wake"]]

        fields["min"] = f.add("Min")
        f.setNumbers(fields["min"], "FieldsList", lista_campos)
        f.setAsBackgroundMesh(fields["min"])

        if cfg.boundary_layer:
            fields["bl"] = f.add("BoundaryLayer")
            f.setNumbers(fields["bl"], "CurvesList", airfoil_curves)
            f.setNumber(fields["bl"], "Quads", 1)
            # Célula simétrica: a camada-limite termina nos pontos onde os tubos cortados encontram os planos de simetria
            pontos_fim_bl = []
            for ctag in airfoil_curves:
                for _, ptag in gmsh.model.getBoundary([(1, ctag)], oriented=False):
                    _, py, _ = gmsh.model.getValue(0, ptag, [])
                    if (abs(py - ymin) < tol or abs(py - ymax) < tol) and ptag not in pontos_fim_bl:
                        pontos_fim_bl.append(ptag)
            if pontos_fim_bl:
                f.setNumbers(fields["bl"], "PointsList", pontos_fim_bl)
            # Leque nos cantos do bordo de fuga reto (pontos dos perfis em x máximo, fora dos planos de simetria)
            if cfg.leque_te:
                pontos_leque = []
                for pts in grupos.values():
                    x_te = pts[:, 0].max()
                    for ctag in airfoil_curves:
                        for _, ptag in gmsh.model.getBoundary([(1, ctag)], oriented=False):
                            px, _, _ = gmsh.model.getValue(0, ptag, [])
                            if abs(px - x_te) < 1e-3 * c and ptag not in pontos_leque and ptag not in pontos_fim_bl:
                                pontos_leque.append(ptag)
                if pontos_leque:
                    f.setNumbers(fields["bl"], "FanPointsList", pontos_leque)
                    gmsh.option.setNumber("Mesh.BoundaryLayerFanElements", cfg.bl_elementos_leque)
            f.setAsBoundaryLayer(fields["bl"])

        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.Algorithm", 6)  # Frontal-Delaunay
        gmsh.option.setNumber("Mesh.Smoothing", cfg.mesh_smoothing_steps)

        # --- geração com controle automático de qualidade ---
        stats, erro_malha = None, None
        for it in range(1, cfg.mesh_max_iterations + 1):
            if progresso is not None:
                progresso.progress(int(100 * (it - 1) / cfg.mesh_max_iterations), text=f'Mesh iteration {it}')
            _set_size_fields(cfg, fields, y1, min_gap)
            try:
                gmsh.model.mesh.clear()
                gmsh.model.mesh.generate(3 if openfoam else 2)
                stats = _mesh_quality_2d(cfg, front_face)
                erro_malha = None
            except Exception as e:  # ex.: camada-limite não fecha -> afina a BL e tenta de novo
                stats, erro_malha = None, e
            if stats is not None and _quality_ok(cfg, stats):
                break
            if it < cfg.mesh_max_iterations:
                relax_mesh_params(cfg)
        else:
            if stats is not None:
                avisos.append(f"Gmsh quality targets were not reached after {cfg.mesh_max_iterations} iterations; "
                              "the last mesh is shown (check the quality table).")

        if stats is None:
            raise RuntimeError(f"Gmsh could not generate the mesh ({erro_malha}). Try a coarser density, disable the "
                               "boundary layer, enable smoothing or increase the pitch.")

        # --- nós, quads (camada-limite) e triângulos da face frontal (a malha extrudada é a extrusão dela) ---
        tags, coords, _ = gmsh.model.mesh.getNodes()
        nos_todos = coords.reshape(-1, 3)[:, :2]
        indice = np.zeros(int(tags.max()) + 1, dtype=np.int64)
        indice[tags.astype(np.int64)] = np.arange(len(tags))
        tris, quads = np.zeros((0, 3), dtype=np.int64), np.zeros((0, 4), dtype=np.int64)
        for etype, _, enodes in zip(*gmsh.model.mesh.getElements(2, front_face)):
            name, _, _, n_nodes = gmsh.model.mesh.getElementProperties(etype)[:4]
            conn = indice[np.asarray(enodes, dtype=np.int64)].reshape(-1, n_nodes)
            if name.startswith("Triangle"):
                tris = np.vstack([tris, conn[:, :3]])
            else:
                quads = np.vstack([quads, conn[:, :4]])

        # Arestas de contorno por grupo
        contornos = {}
        for nome, curvas in groups_2d.items():
            segs = [np.zeros((0, 2), dtype=np.int64)]
            for ent in curvas:
                for etype, _, enodes in zip(*gmsh.model.mesh.getElements(1, ent)):
                    segs.append(indice[np.asarray(enodes, dtype=np.int64)].reshape(-1, 2))
            contornos[nome] = np.vstack(segs)

        # Mantém só os nós usados (os pontos/linhas auxiliares de refino também têm nós)
        usados = np.unique(np.concatenate([tris.ravel(), quads.ravel()]))
        novo = np.full(len(nos_todos), -1, dtype=np.int64)
        novo[usados] = np.arange(len(usados))
        nos = nos_todos[usados]
        tris, quads = novo[tris], novo[quads]
        contornos = {k: novo[v] for k, v in contornos.items()}

        arrays = stats.pop("_arrays")
        n_layers, bl_thickness = _boundary_layer_layout(cfg, y1, min_gap)
        n_cells = sum(len(t) for t in gmsh.model.mesh.getElements(3)[1]) if openfoam else len(tris) + len(quads)

        # --- exportação (em metros) ---
        arquivos = {}
        if openfoam:
            gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)  # gmshToFoam
            formatos = ["msh"]
        else:
            gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
            formatos = ["msh", "su2", "unv", "cgns"]
        for ext in formatos:
            try:
                gmsh.write(file_base + "." + ext)
                with open(file_base + "." + ext, "rb") as arq:
                    arquivos[ext] = arq.read()
            except Exception as e:
                avisos.append(f"Export to .{ext} failed: {e}")

    finally:
        gmsh.finalize()

    return {"nos": nos, "quads": quads, "tris": tris, "rho": arrays["Rho"], "sicn": arrays["SICN"],
            "gamma": arrays["Gamma"], "stats": stats, "contornos": contornos, "arquivos": arquivos, "avisos": avisos,
            "iteracoes": it, "celulas": n_cells, "h1": y1, "n_bl": n_layers if cfg.boundary_layer else 0,
            "delta": bl_thickness if cfg.boundary_layer else 0.0, "r_bl": cfg.bl_ratio, "min_gap": min_gap,
            "depth": depth, "dominio": (xmin, xmax, ymin, ymax), "banco": (bank_xmin, bank_xmax, bank_ymin, bank_ymax),
            "tamanhos": {k: getattr(cfg, k) * c for k in ("size_airfoil", "size_te", "size_wake_line", "size_bank",
                                                          "size_wake", "size_far")},
            "DistMax": cfg.refine_dist_max * c, "openfoam": openfoam}


# Métricas de qualidade da malha 2D (quads da camada-limite + triângulos), mesmas do rocket nozzle sizing
def meshQuality(nos, quads, tris=None):
    with np.errstate(divide='ignore', invalid='ignore'):  # células degeneradas (área ~0) geram inf/nan, sem travar
        return _meshQuality(nos, quads, tris)


def _meshQuality(nos, quads, tris=None):
    # Células na ordem: quads, depois triângulos (ângulo ideal: 90° nos quads, 60° nos triângulos)
    grupos = [(c, ang_eq) for c, ang_eq in [(quads, 90.0), (tris, 60.0)] if c is not None and len(c)]
    areas, centros, sinais, skews, aspects = [], [], [], [], []
    for cel, ang_eq in grupos:
        P = nos[cel]                                     # (M, n, 2)
        P0 = P[:, :1, :]                                 # coordenadas locais (evita perda de precisão em células muito finas)
        x, y = (P - P0)[..., 0], (P - P0)[..., 1]
        xs, ys = np.roll(x, -1, axis=1), np.roll(y, -1, axis=1)
        area_s = 0.5 * np.sum(x * ys - xs * y, axis=1)   # área com sinal (orientação)
        cx = np.sum((x + xs) * (x * ys - xs * y), axis=1) / (6 * area_s)
        cy = np.sum((y + ys) * (x * ys - xs * y), axis=1) / (6 * area_s)
        centros.append(np.stack([cx, cy], axis=1) + P0[:, 0, :])   # centroide de área
        areas.append(np.abs(area_s))
        sinais.append(np.sign(area_s))

        # Ângulos internos -> skewness equiângulo e razão de aspecto
        e = np.roll(P, -1, axis=1) - P                   # arestas i -> i+1
        L = np.linalg.norm(e, axis=2)
        a = -np.roll(e, 1, axis=1)
        cosang = np.sum(a * e, axis=2) / (np.linalg.norm(a, axis=2) * L)
        ang = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        skews.append(np.maximum((ang.max(axis=1) - ang_eq) / (180 - ang_eq), (ang_eq - ang.min(axis=1)) / ang_eq))
        aspects.append(L.max(axis=1) / L.min(axis=1))

    area, C, sinal = np.concatenate(areas), np.vstack(centros), np.concatenate(sinais)
    skew_eq, aspect = np.concatenate(skews), np.concatenate(aspects)

    # Faces (arestas) -> dono / vizinho
    M = len(area)
    a_n = np.concatenate([cel.reshape(-1) for cel, _ in grupos])
    b_n = np.concatenate([np.roll(cel, -1, axis=1).reshape(-1) for cel, _ in grupos])
    celula = np.repeat(np.arange(M), np.concatenate([np.full(len(cel), cel.shape[1]) for cel, _ in grupos]))
    chave = np.sort(np.stack([a_n, b_n], axis=1), axis=1)
    ordem = np.lexsort((chave[:, 1], chave[:, 0]))
    ch = chave[ordem]
    igual = np.all(ch[1:] == ch[:-1], axis=1)
    i1 = ordem[:-1][igual]                           # meia-face do dono
    i2 = ordem[1:][igual]                            # meia-face do vizinho
    o, v = celula[i1], celula[i2]

    pa, pb = nos[a_n[i1]], nos[b_n[i1]]
    fc = 0.5 * (pa + pb)                             # centro da face
    t = pb - pa
    n = np.stack([t[:, 1], -t[:, 0]], axis=1) * sinal[o][:, None]   # normal para fora do dono
    d = C[v] - C[o]
    nmag, dmag = np.linalg.norm(n, axis=1), np.linalg.norm(d, axis=1)
    cos_nd = np.sum(n * d, axis=1) / (nmag * dmag)
    nao_ort = np.degrees(np.arccos(np.clip(cos_nd, -1, 1)))         # não-ortogonalidade (OpenFOAM)

    # Skewness (OpenFOAM): distância do centro da face à interseção da linha entre centros, / |d|
    nh = n / nmag[:, None]
    d_own = np.abs(np.sum((fc - C[o]) * nh, axis=1))
    d_nei = np.abs(np.sum((C[v] - fc) * nh, axis=1))
    ip = C[o] + d * (d_own / (d_own + d_nei))[:, None]
    skew_of = np.linalg.norm(fc - ip, axis=1) / dmag

    # Orthogonal quality (ANSYS): mínimo por célula de cos(A, f) e cos(A, c)
    oq = np.ones(M)
    f_o = fc - C[o]
    cos_fo = np.sum(n * f_o, axis=1) / (nmag * np.linalg.norm(f_o, axis=1))
    f_v = fc - C[v]
    cos_fv = np.sum(-n * f_v, axis=1) / (nmag * np.linalg.norm(f_v, axis=1))
    np.minimum.at(oq, o, np.minimum(cos_fo, cos_nd))
    np.minimum.at(oq, v, np.minimum(cos_fv, cos_nd))
    unicas = np.ones(len(ch), dtype=bool)            # faces de contorno: só cos(A, f)
    unicas[1:][igual] = False
    unicas[:-1][igual] = False
    ib = ordem[unicas]
    cb = celula[ib]
    pa_b, pb_b = nos[a_n[ib]], nos[b_n[ib]]
    tb = pb_b - pa_b
    nb = np.stack([tb[:, 1], -tb[:, 0]], axis=1) * sinal[cb][:, None]
    fb = 0.5 * (pa_b + pb_b) - C[cb]
    cos_b = np.sum(nb * fb, axis=1) / (np.linalg.norm(nb, axis=1) * np.linalg.norm(fb, axis=1))
    np.minimum.at(oq, cb, cos_b)

    # Growth rate (maior razão de área com um vizinho)
    gr_face = np.maximum(area[o] / area[v], area[v] / area[o])
    gr = np.ones(M)
    np.maximum.at(gr, o, gr_face)
    np.maximum.at(gr, v, gr_face)

    # Arestas únicas (para o visualizador)
    arestas = np.vstack([chave[ordem][np.r_[True, ~igual]]])

    return {"area": area, "centro": C, "skew_eq": skew_eq, "aspect": aspect, "ortho": oq,
            "nao_ort": nao_ort, "skew_of": skew_of, "growth": gr, "tamanho": np.sqrt(area),
            "arestas": arestas, "positivas": bool(np.all(sinal == sinal[0]) and np.all(area > 0))}


# Visualizador da malha (Plotly): arestas + contornos por condição de contorno (+ cor por métrica, opcional)
def plotMesh(malha_res, qualidade, metrica=None):
    nos = malha_res["nos"] * 1000                    # mm
    fig = go.Figure()

    ## Arestas das células (um único traço, com quebras)
    ar = qualidade["arestas"]
    xs = np.full(len(ar) * 3, np.nan)
    ys = np.full(len(ar) * 3, np.nan)
    xs[0::3], xs[1::3] = nos[ar[:, 0], 0], nos[ar[:, 1], 0]
    ys[0::3], ys[1::3] = nos[ar[:, 0], 1], nos[ar[:, 1], 1]
    fig.add_trace(go.Scattergl(x=xs, y=ys, mode='lines', name='Mesh', hoverinfo='skip',
                               line=dict(color='rgba(173, 216, 230, 0.55)', width=0.7)))

    ## Cor por métrica (centroides das células)
    if metrica is not None:
        nome, valores = metrica
        C = qualidade["centro"] * 1000
        fig.add_trace(go.Scattergl(x=C[:, 0], y=C[:, 1], mode='markers', name=nome, showlegend=False,
                                   marker=dict(size=4, color=valores, colorscale='Turbo', showscale=True,
                                               colorbar=dict(title=dict(text=nome), thickness=12)),
                                   hovertemplate=nome + ': %{marker.color:.3g}<br>x: %{x:.3f} mm<br>y: %{y:.3f} mm<extra></extra>'))

    ## Contornos (condições de contorno)
    cores = {"Inlet": "#d62728", "Outlet": "#2ca02c", "Airfoil": "#ffffff", "Walls": "#ff7f0e"}
    for nome, segs in malha_res["contornos"].items():
        if not len(segs):
            continue
        bx = np.full(len(segs) * 3, np.nan)
        by = np.full(len(segs) * 3, np.nan)
        bx[0::3], bx[1::3] = nos[segs[:, 0], 0], nos[segs[:, 1], 0]
        by[0::3], by[1::3] = nos[segs[:, 0], 1], nos[segs[:, 1], 1]
        fig.add_trace(go.Scattergl(x=bx, y=by, mode='lines', name=nome, hoverinfo='name',
                                   line=dict(color=cores.get(nome, '#cccccc'), width=3)))

    fig.update_layout(
        xaxis=dict(title=dict(text='x (mm)'), showgrid=False, zeroline=False),
        yaxis=dict(title=dict(text='y (mm)'), showgrid=False, zeroline=False, scaleanchor='x', scaleratio=1),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=0, r=0, t=60, b=0),
        height=620,
        dragmode='zoom',
    )

    ## Zoom inicial no banco (o domínio inteiro tem dezenas de cordas)
    bx0, bx1, by0, by1 = [v * 1000 for v in malha_res["banco"]]
    xmin, xmax, ymin, ymax = [v * 1000 for v in malha_res["dominio"]]
    folga_x = 0.6 * (bx1 - bx0)
    fig.update_xaxes(range=[bx0 - folga_x, bx1 + folga_x])
    fig.update_yaxes(range=[ymin, ymax])
    return fig


# Histogramas das métricas de qualidade (escala log), com os limites dos critérios
def plotMeshQuality(malha_res, qualidade):
    dados = [
        ('Rho = Scaled Jacobian', malha_res["rho"], 0.3),
        ('Equiangle skewness (ANSYS)', qualidade["skew_eq"], 0.95),
        ('Orthogonal quality (ANSYS)', qualidade["ortho"], 0.1),
        ('Non-orthogonality (deg)', qualidade["nao_ort"], 70),
        ('Skewness (OpenFOAM)', qualidade["skew_of"], 4),
        ('Growth rate (area ratio)', qualidade["growth"], 2),
        ('Aspect ratio', qualidade["aspect"], None),
        ('Cell size (mm)', qualidade["tamanho"] * 1000, None),
    ]
    if len(malha_res["tris"]):                         # qualidade dos triângulos
        dados += [('SICN (triangles)', malha_res["sicn"], 0.3), ('Gamma (triangles)', malha_res["gamma"], 0.3)]
    linhas = int(math.ceil(len(dados) / 4))
    fig = make_subplots(rows=linhas, cols=4, subplot_titles=[d[0] for d in dados], horizontal_spacing=0.06,
                        vertical_spacing=0.16 if linhas == 2 else 0.1)
    for k, (nome, valores, limite) in enumerate(dados):
        lin, col = k // 4 + 1, k % 4 + 1
        valores = np.asarray(valores, dtype=float)
        valores = valores[np.isfinite(valores)]  # células degeneradas (área ~0) não quebram o histograma
        fig.add_trace(go.Histogram(x=valores, nbinsx=50, marker_color='#1f77b4', showlegend=False,
                                   hovertemplate='%{x}<br>cells: %{y}<extra></extra>'), row=lin, col=col)
        if limite is not None:
            fig.add_vline(x=limite, line_dash='dash', line_color='#d62728', line_width=1.5, row=lin, col=col)
        fig.update_yaxes(type='log', row=lin, col=col)
    fig.update_layout(height=260 * linhas, margin=dict(l=0, r=0, t=40, b=0),
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', bargap=0.05)
    fig.update_annotations(font_size=12)
    return fig


# Trava para o gmsh (estado global: uma malha por vez, mesmo com vários usuários)
@st.cache_resource
def gmshLock():
    return threading.Lock()


############################################################


## Pasta temporária (por sessão) para os arquivos CAD e de malha, a mesma da página 1
if 'cad_dir' not in st.session_state:
    st.session_state['cad_dir'] = tempfile.mkdtemp()
cad_dir = st.session_state['cad_dir']

## Ao voltar para a página 1 o modelo 3D não é regerado automaticamente (clicar em 'Generate Model' de novo)
st.session_state.extrude_button = False

## Valores padrão do setup da malha
padroes_malha = {
    'mesh_ahead': 10., 'mesh_behind': 20., 'mesh_margin': 1., 'mesh_domain': 'Symmetric cell', 'mesh_density_hx': 'Medium',
    'mesh_type_hx': '2D (Fluent, SU2, CGNS, UNV)', 'mesh_bl': True, 'mesh_U': 5.5, 'mesh_nu': 1.75e-5, 'mesh_yplus': 0.5,
    'mesh_color_hx': 'None',
}
for k, v in padroes_malha.items():
    st.session_state.setdefault(k, v)

st.subheader("2D Model", anchor=False, divider='gray')

## Setup do banco de tubos salvo pela página 1 (Home)
setup_2d = st.session_state.get('setup_2d')

if setup_2d is None:
    st.info("Set up the tube bank on the Home page first.")
    st.page_link("1_Home.py", label="Go to the Home page")
    st.stop()

forma_tubo = setup_2d['forma_tubo']
spline_mode = setup_2d['spline_mode']
contours = setup_2d['contours']
chord = setup_2d['chord']
h_c = setup_2d['h_c']
pitch_v = setup_2d['pitch_v']
num_rows = setup_2d['num_rows']
num_cols = setup_2d['num_cols']
set_scale = setup_2d['set_scale']
scale = setup_2d['scale']
simetria_x = setup_2d['simetria_x']
staggered = setup_2d['staggered']
interferencia = setup_2d['interferencia']

st.caption("Cross-section of the tube bank set up on the Home page (outer profiles only: wall thickness, "
           "connections, fittings and fins are not included). "
           + ("Profile: smoothed polygon (smoothing on)." if (forma_tubo == 'Aerofólio' and not spline_mode) else
              "Profile: spline through all the contour points (smoothing off)." if forma_tubo == 'Aerofólio' else
              "Profile: exact circle / ellipse.")
           + (" Staggered arrangement." if staggered else " Aligned arrangement."))

if contours is None:
    st.info("Upload a coordinates file on the Home page.")
    st.stop()

if interferencia:
    st.error("The tubes interfere with each other with the current pitch (see the Home page). "
             "Increase the pitch to generate the 2D model and the mesh.")
    st.stop()

scale_ = scale if set_scale else 1
assinatura_2d = hashlib.md5(np.array(contours, dtype=float).tobytes() + str((forma_tubo, spline_mode, scale_)).encode()).hexdigest()
step_2d = cad_dir + '/sketch_hxairfoils_sim.step'

if st.button("Generate 2D model", width="stretch"):
    try:
        with st.spinner('Generating 2D model...'):
            gerar_step_2d(contours, forma_tubo, spline_mode, set_scale, scale, step_2d)
            st.session_state['modelo_2d'] = {'step': open(step_2d, "rb").read(), 'assinatura': assinatura_2d}
    except Exception as e:
        st.error(f"Error generating the 2D model: {e}. "
                 + ("Try enabling smoothing (polygon profile) or check that the coordinates are ordered and do not cross each other."
                    if forma_tubo == 'Aerofólio' else "Check the tube radii."))

if 'modelo_2d' in st.session_state:
    if st.session_state['modelo_2d']['assinatura'] != assinatura_2d:
        st.warning("This 2D model was generated for a previous setup. Click on 'Generate 2D model' to update it.")
    fig_2d = go.Figure()
    for contour in contours:
        x_c, y_c = zip(*contour)
        fig_2d.add_trace(go.Scatter(x=[x * scale_ for x in x_c + x_c[:1]], y=[y * scale_ for y in y_c + y_c[:1]],
                                    mode='lines', fill='toself', line=dict(color='lightblue'), hoverinfo='skip'))
    fig_2d.update_layout(xaxis_title='x (mm)', yaxis_title='y (mm)', showlegend=False, height=380,
                         margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    fig_2d.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(fig_2d, width="stretch")
    st.download_button("2D Model (Surfaces) .step", st.session_state['modelo_2d']['step'],
                       file_name='sketch_hxairfoils_sim.step', mime="application/step", width="stretch",
                       on_click="ignore")

## Malha
st.subheader("CFD Mesh (Gmsh)", anchor=False, divider='gray')

if not gmsh_ok:
    st.error(f"Gmsh could not be loaded: {gmsh_erro}\n\nInstall it with `pip install gmsh==4.15.2`. "
             "On Linux (e.g. Streamlit Cloud) it also needs the system libraries listed in packages.txt (libglu1-mesa, libxcursor1, libxinerama1, libxft2).")
    st.stop()

## Setup da malha
col_ms1, col_ms2, col_ms3, col_ms4 = st.columns(4)
domain_ahead = col_ms1.number_input('Upstream length', min_value=1., step=1., format="%.1f", key='mesh_ahead',
                                    help="Domain length upstream of the leading edge of the first tube, in chords.")
domain_behind = col_ms1.number_input('Downstream length', min_value=1., step=1., format="%.1f", key='mesh_behind',
                                     help="Domain length downstream of the trailing edge of the last tube, in chords.")
dominio = col_ms2.radio('Domain', ['Full bank', 'Symmetric cell'], key='mesh_domain',
                        help="Full bank: all the tubes. Symmetric cell: all the columns and the middle row, between two "
                             "symmetry planes (much smaller mesh). Staggered: the planes pass through the chord lines of the "
                             "rows above and below, which appear cut in half. Aligned: the planes lie midway between the rows. "
                             "Needs a profile symmetric about its chord and at least 3 tube rows.")
n_linhas = 2 * num_rows if (num_cols > 1 and staggered) else num_rows  # linhas de tubos na vertical (no alternado as colunas ímpares ficam deslocadas)
simetria_cel = dominio == 'Symmetric cell'
if simetria_cel and not simetria_x:
    col_ms2.warning("The profile is not symmetric about its chord: the symmetry planes would not be valid. Using the full bank.")
    simetria_cel = False
elif simetria_cel and n_linhas < 3:
    col_ms2.warning("At least 3 tube rows are needed for the symmetric cell. Using the full bank.")
    simetria_cel = False
if not simetria_cel:
    margem_y = col_ms2.number_input('Vertical margin', min_value=0.1, step=0.1, format="%.2f", key='mesh_margin',
                                    help="Distance from the centre (chord line) of the top / bottom tubes to the upper / lower "
                                         "walls, in vertical pitches (pitch_v x scale). With 1 pitch the walls lie where the "
                                         "centre of the next staggered tube would be: symmetry planes of an infinite bank, "
                                         "as in the validated CFD case.")
else:
    margem_y = st.session_state['mesh_margin']
densidade = col_ms3.radio('Mesh density', ['Coarse', 'Medium', 'Fine'], key='mesh_density_hx', horizontal=True,
                          help="Cell sizes relative to the chord: x 1.5 (Coarse), x 1 (Medium = validated CFD case) or x 0.7 (Fine).")
tipo_malha = col_ms3.radio('Mesh type', ['2D (Fluent, SU2, CGNS, UNV)', 'OpenFOAM (2D, 1 cell extruded, .msh 2.2)'],
                           key='mesh_type_hx',
                           help="OpenFOAM: the 2D mesh is extruded with one cell in z (frontAndBack = empty) for gmshToFoam.")
camada_limite = col_ms4.toggle('Boundary layer (quads)', key='mesh_bl',
                               help="Structured boundary layer on the tube walls, with the first cell height set for the target y+.")
if camada_limite:
    U_inlet = col_ms4.number_input('Inlet velocity (m/s)', min_value=0.01, step=0.5, format="%.2f", key='mesh_U')
    nu_air = col_ms4.number_input('Kinematic viscosity (m2/s)', min_value=1e-8, step=1e-6, format="%.3e", key='mesh_nu')
    y_plus = col_ms4.number_input('Target y+', min_value=0.05, step=0.1, format="%.2f", key='mesh_yplus',
                                  help="k-omega SST resolved to the wall: average y+ 0.5 -> y+ max < 1 (validated CFD case).")
else:
    U_inlet, nu_air, y_plus = st.session_state['mesh_U'], st.session_state['mesh_nu'], st.session_state['mesh_yplus']

gerar = st.button('Generate mesh', type='primary', width="stretch")

fator = {'Coarse': 1.5, 'Medium': 1.0, 'Fine': 0.7}[densidade]
openfoam = tipo_malha.startswith('OpenFOAM')
assinatura = hashlib.md5((assinatura_2d + str((domain_ahead, domain_behind, margem_y, simetria_cel, densidade, tipo_malha,
                                               camada_limite, U_inlet, nu_air, y_plus))).encode()).hexdigest()

if gerar:
    # Configuração da malha (mesmos valores do código de CFD validado), tamanhos relativos à corda real
    aerofolio = forma_tubo == 'Aerofólio'
    cfg = SimpleNamespace(
        chord=chord * scale_ / 1000.0, pitch_v_m=pitch_v * scale_ / 1000.0, n_airfoils=len(contours),
        domain_ahead=domain_ahead, domain_behind=domain_behind, domain_y_margin_pitches=margem_y,
        domain_y_reference="centro",  # margem medida a partir do centro dos tubos (caso de CFD validado)
        # Aerofólio: valores do caso de CFD validado. Circular / elíptico (corpo rombudo, vãos grandes entre os tubos em
        # cordas): banco e esteira com células 2x maiores, esteira refinada até 10 cordas e transição mais suave a
        # partir da parede (~1/3 das células, mesmos critérios de qualidade)
        size_airfoil=0.010 * fator, size_te=0.002 * fator, te_refine_dist=0.05,
        size_bank=(0.025 if aerofolio else 0.05) * fator,
        size_wake=(0.060 if aerofolio else 0.10) * fator, size_far=0.40 * fator, refine_dist_min=0.02,
        refine_dist_max=0.25 if aerofolio else 0.5,
        bank_box_upstream=0.5, bank_box_downstream=1.5, bank_box_transition=1.0,
        wake_box_upstream=2.0, wake_box_downstream=min(20.0 if aerofolio else 10.0, domain_behind), wake_box_transition=3.0,
        # Faixa refinada da esteira: no aerofólio, estreita (bordo de fuga fino, valores do caso validado); no
        # tubo circular/elíptico (a esteira nasce com a altura do tubo), meia-largura = raio y e tamanho do banco
        wake_line_length=2.5, size_wake_line=(0.012 if aerofolio else 0.05) * fator,
        wake_line_dist_min=0.03 if aerofolio else 0.5 * h_c,
        wake_line_dist_max=0.25 if aerofolio else 0.5 * h_c + 0.25,
        # Última camada da camada-limite ~ tamanho na superfície (senão a transição para os triângulos passa do growth
        # rate 2). No aerofólio isso também reduz o leque de células em volta do bordo de fuga
        bl_ultima_camada_superficie=True,
        extrusion_depth=0.025, y_plus_target=y_plus, bl_ratio=1.2, bl_max_layers=14, bl_min_layers=6,
        bl_max_gap_fraction=0.25, mesh_smoothing_steps=5, min_sicn=0.30, min_gamma=0.30, min_rho=0.30,
        quality_hard_floor=0.10, max_bad_fraction=0.001, mesh_max_iterations=3, refine_shrink_factor=0.85,
        refine_grow_factor=1.30, U_inlet=U_inlet, nu_air=nu_air, boundary_layer=camada_limite,
        refino_te=(forma_tubo == 'Aerofólio'),
        # Polígono (suavizado): bordo de fuga reto (rombudo) -> leque de células nos dois cantos do bordo de fuga
        # (sem o leque, a camada-limite que sai da reta do bordo de fuga gera uma faixa de células muito finas ao lado
        # de células grandes: growth rate ~60). A spline já tem o bordo de fuga suave
        leque_te=(forma_tubo == 'Aerofólio' and not spline_mode), bl_elementos_leque=8,
        # Célula simétrica: centro (y) da fileira do meio (colunas pares), em metros
        simetria_y0=(2 * (num_rows // 2) * pitch_v * scale_ / 1000.0) if simetria_cel else None,
    )
    contornos_m = [np.array(cn, dtype=float) * scale_ / 1000.0 for cn in contours]

    barra = st.progress(0, text='2D model')
    try:
        with st.spinner('Generating mesh...'):
            gerar_step_2d(contours, forma_tubo, spline_mode, set_scale, scale, step_2d)
            st.session_state['modelo_2d'] = {'step': open(step_2d, "rb").read(), 'assinatura': assinatura_2d}
            with gmshLock():
                malha_res = gerar_malha_cfd(cfg, step_2d, contornos_m, cad_dir + '/hx_mesh', openfoam, barra)
            barra.progress(95, text='Mesh quality')
            qualidade = meshQuality(malha_res["nos"], malha_res["quads"], malha_res["tris"])
            st.session_state['malha_cfd'] = {'malha': malha_res, 'qualidade': qualidade, 'assinatura': assinatura}
    except Exception as e:
        st.error(f"Error generating the mesh: {e}")
    barra.empty()

if 'malha_cfd' not in st.session_state:
    st.info("Set up the mesh above and click on 'Generate mesh'.")
    st.stop()

res = st.session_state['malha_cfd']
malha_res, qualidade = res['malha'], res['qualidade']

if res['assinatura'] != assinatura:
    st.warning("This mesh was generated for a previous geometry or setup. Click on 'Generate mesh' to update it.")
for aviso in malha_res['avisos']:
    st.warning(aviso)

n_quads, n_tris = len(malha_res['quads']), len(malha_res['tris'])
n_cel, n_nos = n_quads + n_tris, len(malha_res['nos'])

## Visualizador
opcoes_cor = {'None': None,
              'Orthogonal quality': qualidade['ortho'],
              'Equiangle skewness': qualidade['skew_eq'],
              'Aspect ratio': qualidade['aspect'],
              'Growth rate': qualidade['growth'],
              'Cell size (mm)': qualidade['tamanho'] * 1000}
cor_por = st.selectbox('Color cells by', list(opcoes_cor.keys()), key='mesh_color_hx')
fig_malha = plotMesh(malha_res, qualidade, None if opcoes_cor[cor_por] is None else (cor_por, opcoes_cor[cor_por]))

## HTML do visualizador (gerado uma vez por malha e por cor: em malhas grandes o to_html é pesado)
if res.get('html_cor') != cor_por:
    res['html'] = fig_malha.to_html(include_plotlyjs='cdn', config={'scrollZoom': True})
    res['html_cor'] = cor_por
st.plotly_chart(fig_malha, config={'scrollZoom': True})
st.caption("Scroll to zoom and drag to pan (double-click shows the whole domain). Boundaries: :red[Inlet], "
           ":green[Outlet], :orange[Walls] (upper/lower) and Airfoil (white, tube walls).")

## Downloads
if malha_res['openfoam']:
    col_md1, col_md2 = st.columns(2)
    if 'msh' in malha_res['arquivos']:
        col_md1.download_button("Mesh (.msh 2.2, gmshToFoam)", malha_res['arquivos']['msh'], file_name="hx_mesh.msh", width="stretch", on_click="ignore")
    col_md2.download_button("Mesh viewer (.html)", res['html'],
                            file_name="mesh_viewer.html", width="stretch", on_click="ignore")
else:
    col_md1, col_md2, col_md3, col_md4, col_md5 = st.columns(5)
    for col_md, ext, rotulo in [(col_md1, 'msh', "Mesh (.msh, Gmsh)"), (col_md2, 'cgns', "Mesh (.cgns)"),
                                (col_md3, 'unv', "Mesh (.unv)"), (col_md4, 'su2', "Mesh (.su2)")]:
        if ext in malha_res['arquivos']:
            col_md.download_button(rotulo, malha_res['arquivos'][ext], file_name="hx_mesh." + ext, width="stretch", on_click="ignore")
    col_md5.download_button("Mesh viewer (.html)", res['html'],
                            file_name="mesh_viewer.html", width="stretch", on_click="ignore")

## Verificações de qualidade (inicialmente minimizado)
with st.expander("Mesh quality checks"):
    s = malha_res['stats']
    rho_min, rho_bad = s['Rho_min'], 100 * s['Rho_bad']
    sk_max, sk_med = float(qualidade['skew_eq'].max()), float(qualidade['skew_eq'].mean())
    oq_min, oq_med = float(qualidade['ortho'].min()), float(qualidade['ortho'].mean())
    no_max, no_med = float(qualidade['nao_ort'].max()), float(qualidade['nao_ort'].mean())
    so_max = float(qualidade['skew_of'].max())
    gr_pct, gr_max = 100 * float(np.mean(qualidade['growth'] > 2)), float(qualidade['growth'].max())
    ar_max, ar_p99 = float(qualidade['aspect'].max()), float(np.percentile(qualidade['aspect'], 99))

    if n_tris:
        sicn_res = f"min {s['SICN_min']:.3f} ({100 * s['SICN_bad']:.2f}% < 0.3)"
        sicn_ok = "OK" if (s['SICN_min'] >= 0.1 and s['SICN_bad'] <= 0.001) else "FAIL"
        gamma_res = f"min {s['Gamma_min']:.3f} ({100 * s['Gamma_bad']:.2f}% < 0.3)"
        gamma_ok = "OK" if (s['Gamma_min'] >= 0.1 and s['Gamma_bad'] <= 0.001) else "FAIL"
    else:
        sicn_res, sicn_ok, gamma_res, gamma_ok = "all-quad mesh", "n/a", "all-quad mesh", "n/a"

    verificacoes = [
        ["Rho = Scaled Jacobian (Gmsh)", "min >= 0.1, <= 0.1% below 0.3", f"min {rho_min:.3f} ({rho_bad:.2f}% < 0.3)",
         "OK" if (rho_min >= 0.1 and rho_bad <= 0.1) else "FAIL"],
        ["SICN (Gmsh, triangles)", "min >= 0.1, <= 0.1% below 0.3", sicn_res, sicn_ok],
        ["Gamma (Gmsh, triangles)", "min >= 0.1, <= 0.1% below 0.3", gamma_res, gamma_ok],
        ["Equiangle skewness (ANSYS)", "max < 0.95 (good < 0.8)", f"max {sk_max:.3f}, mean {sk_med:.3f}", "OK" if sk_max < 0.95 else "FAIL"],
        ["Orthogonal quality (ANSYS)", "min > 0.1 (good > 0.2)", f"min {oq_min:.3f}, mean {oq_med:.3f}", "OK" if oq_min > 0.1 else "FAIL"],
        ["Non-orthogonality (OpenFOAM)", "max <= 70 deg (ideal <= 40 deg)", f"max {no_max:.1f} deg, mean {no_med:.1f} deg", "OK" if no_max <= 70 else "FAIL"],
        ["Skewness (OpenFOAM)", "max < 4", f"max {so_max:.3f}", "OK" if so_max < 4 else "FAIL"],
        ["Growth rate (area ratio)", "<= 1% of cells with GR > 2", f"{gr_pct:.2f}% (max {gr_max:.2f})", "OK" if gr_pct <= 1 else "FAIL"],
        ["Positive cell areas (checkMesh)", "no inverted cells", "all cells valid" if qualidade['positivas'] else "inverted cells", "OK" if qualidade['positivas'] else "FAIL"],
        ["Aspect ratio", "informative", f"max {ar_max:.0f}, p99 {ar_p99:.0f}", "info"],
    ]
    cor_status = {"OK": "#2ca02c", "FAIL": "#d62728", "n/a": "gray", "info": "gray"}

    # Tabela nativa do Streamlit (a go.Table do plotly dentro do expander podia aparecer vazia ou cortar o texto)
    df_checks = pd.DataFrame(verificacoes, columns=['Check', 'Criterion', 'Result', 'Status'])
    st.dataframe(df_checks.style.map(lambda v: f"color: {cor_status[v]}; font-weight: bold", subset=['Status']),
                 hide_index=True, width="stretch")

    st.caption("Criteria from the validated tube bank CFD case (Gmsh SICN / Gamma / Rho with automatic quality control: "
               "the mesh is regenerated with a smaller bank size, smoother transition and thinner boundary layer if it fails) "
               "and the usual ANSYS / OpenFOAM checkMesh limits. SICN and Gamma measure isotropy, so they are evaluated "
               "on the triangles only; Rho is evaluated on all cells, including the boundary layer quads.")

    ## Distribuição das métricas
    st.plotly_chart(plotMeshQuality(malha_res, qualidade))

## Detalhes da malha (inicialmente minimizado)
with st.expander("Mesh details"):
    col_mm1, col_mm2, col_mm3, col_mm4, col_mm5 = st.columns(5)
    col_mm1.metric('Cells (2D section)', f"{n_cel:,}")
    col_mm2.metric('Nodes', f"{n_nos:,}")
    col_mm3.metric('First cell height (mm)', f"{malha_res['h1'] * 1000:.3g}" if malha_res['n_bl'] else "-")
    col_mm4.metric('Boundary layer cells', f"{malha_res['n_bl']}")
    col_mm5.metric('Smallest gap (mm)', f"{malha_res['min_gap'] * 1000:.3g}")

    xmin, xmax, ymin, ymax = malha_res['dominio']
    tam = malha_res['tamanhos']
    detalhes = pd.DataFrame({
        'Parameter': ['Quads / triangles', 'Cells of the exported mesh', 'Gmsh quality iterations',
                      'First cell height (mm) / target y+', 'Boundary layer thickness (mm)',
                      'Boundary layer cells / growth ratio', 'Cell size: tube surface / trailing edge (mm)',
                      'Cell size: bank / wake line (mm)', 'Cell size: wake / far field (mm)', 'DistMax (mm)',
                      'Domain x range (mm)', 'Domain y range (mm)', 'Extrusion depth (mm)', 'Units of the exported files'],
        'Value': [f"{n_quads:,} / {n_tris:,}", f"{malha_res['celulas']:,}", f"{malha_res['iteracoes']}",
                  (f"{malha_res['h1'] * 1000:.4g} / {y_plus:.2f}" if malha_res['n_bl'] else "no boundary layer"),
                  f"{malha_res['delta'] * 1000:.3f}", f"{malha_res['n_bl']} / {malha_res['r_bl']:.2f}",
                  f"{tam['size_airfoil'] * 1000:.3f} / " + (f"{tam['size_te'] * 1000:.3f}" if forma_tubo == 'Aerofólio' else "-"),
                  f"{tam['size_bank'] * 1000:.3f} / {tam['size_wake_line'] * 1000:.3f}",
                  f"{tam['size_wake'] * 1000:.3f} / {tam['size_far'] * 1000:.2f}", f"{malha_res['DistMax'] * 1000:.2f}",
                  f"{xmin * 1000:.2f} to {xmax * 1000:.2f}", f"{ymin * 1000:.2f} to {ymax * 1000:.2f}",
                  f"{malha_res['depth'] * 1000:.3f}" if malha_res['openfoam'] else "-", "meters"]})
    st.table(detalhes)
    st.markdown("Boundary conditions (physical groups): **Inlet**: velocity inlet, **Outlet**: pressure outlet, "
                "**Airfoil**: wall (tube surfaces), **Walls**: symmetry (upper/lower planes of the bank)"
                + (", **frontAndBack**: empty (OpenFOAM 2D)." if malha_res['openfoam'] else "."))
