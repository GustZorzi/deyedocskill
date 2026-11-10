#!/usr/bin/env python3
"""
Motor de Engenharia Tipográfica e Diagramação Semântica para Datasheets Deye (Batch Mode).
Arquitetura Conceitual Adaptativa Otimizada:
1. Executa leitura estrutural completa (P1 e P2) em uma única chamada ao pdfcraft-cli (~0.03s).
2. Aplica todas as edições de linha (P1 e P2) e inserções centralizadas em um único lote em memória (~0.25s),
   ordenando por índice decrescente para imunidade total a deslocamento de índices.
3. Aplica todas as edições de parágrafos centralizados (pilares P1, células mescladas P2 e Dados Gerais)
   em um único lote final (~0.15s).
4. Suporta integralmente linhas Híbridas (SG) e On-Grid (G), incluindo geração de Modelo Único
   sem jamais apagar as linhas divisórias horizontais da tabela.
"""
import sys
import os
import re
import json
import zlib
import subprocess
import shutil
import tempfile
from pathlib import Path
from collections import defaultdict

# Garante importação dos módulos comuns
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.platform_utils import (
    get_fast_temp_dir,
    get_temp_file,
    run_pdfcraft,
    extract_text_from_pdf,
    is_windows
)


class SemanticDeyeEngine:
    def __init__(self, pdf_source):
        self.pdf_source = str(pdf_source)

    def detect_language(self):
        """Detecta se o datasheet está em português ou inglês de forma multiplataforma."""
        text = extract_text_from_pdf(self.pdf_source).lower()
        pt_words = ['eficiência', 'potência', 'tensão', 'corrente', 'bateria', 'inversor', 'trifásico', 'dados técnicos']
        en_words = ['efficiency', 'power', 'voltage', 'current', 'battery', 'inverter', 'three phase', 'technical data']
        pt_count = sum(text.count(w) for w in pt_words)
        en_count = sum(text.count(w) for w in en_words)
        return "PT-BR" if pt_count > en_count else "EN"

    def _run_pdfcraft(self, steps):
        temp_dir = str(get_fast_temp_dir())
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=temp_dir, delete=False, encoding="utf-8") as f:
            json.dump(steps, f)
            script_path = f.name
        res = run_pdfcraft(["run", "--script", script_path, "--report"])
        try:
            os.remove(script_path)
        except OSError:
            pass
        try:
            return json.loads(res.stdout)
        except Exception as e:
            print("Erro no pdfcraft:", e, res.stderr if res else "")
            return None

    def read_all_structure(self, pdf_file):
        """Lê todas as linhas e parágrafos das páginas 1 e 2 em uma única chamada ao pdfcraft-cli."""
        steps = [
            {"tool": "doc_open", "args": {"path": str(pdf_file)}},
            {"tool": "text_lines", "args": {"doc": 1, "page": 1}},
            {"tool": "text_paragraphs", "args": {"doc": 1, "page": 1}},
            {"tool": "text_lines", "args": {"doc": 1, "page": 2}},
            {"tool": "text_paragraphs", "args": {"doc": 1, "page": 2}},
        ]
        rep = self._run_pdfcraft(steps)
        if not rep or not rep.get("ok"):
            return [], [], [], []
        p1_lines = rep["steps"][1]["result"][0]["lines"]
        p1_paras = rep["steps"][2]["result"][0]["paragraphs"]
        p2_lines = rep["steps"][3]["result"][0]["lines"]
        p2_paras = rep["steps"][4]["result"][0]["paragraphs"]
        return p1_lines, p1_paras, p2_lines, p2_paras

    def _detect_columns_from_lines(self, p2_lines):
        rows = defaultdict(list)
        for l in p2_lines:
            if l["rect"][0] > 180 and l["rect"][1] < 400:
                y_key = round(l["rect"][1] / 3.0) * 3
                rows[y_key].append(l)

        if not rows:
            return [229.9, 301.1, 372.9, 443.9, 516.1], 373.0

        best_y, best_items = max(rows.items(), key=lambda item: len(item[1]))
        best_items.sort(key=lambda l: l["rect"][0])
        col_centers = [(l["rect"][0] + l["rect"][2]) / 2 for l in best_items]
        span_center = (col_centers[0] + col_centers[-1]) / 2
        return col_centers, span_center

    def _detect_general_data_center_from_lines(self, p2_lines, default_cx=374.88):
        gen_lines = [l for l in p2_lines if l["text"].strip() in ["0-100%", "IP 65", "IP65", "3000m", "4000m", "<60", "<45", "OVC II(DC), OVC III(AC)"]]
        if gen_lines:
            return round(sum((l["rect"][0] + l["rect"][2]) / 2 for l in gen_lines) / len(gen_lines), 2)
        return default_cx

    def _find_current_rows_y_from_lines(self, p2_lines):
        y_rated = 309.9
        y_max = 321.7
        for l in p2_lines:
            if l["rect"][0] < 180:
                txt = l["text"].lower()
                if "rated ac" in txt and "current" in txt:
                    y_rated = (l["rect"][1] + l["rect"][3]) / 2
                elif "max. ac" in txt and "current" in txt:
                    y_max = (l["rect"][1] + l["rect"][3]) / 2
        return y_rated, y_max

    def _detect_p1_pillar_centers_from_paras(self, p1_paras):
        col1, col2 = [], []
        for p in p1_paras:
            txt = p["text"].strip()
            if txt in ["Safe", "Eﬃcient", "Efficient", "Adaptive", "Flexible", "Intelligent", "Reliable", "Seguro", "Eficiente", "Adaptável", "Flexível", "Inteligente", "Confiável"]:
                cx = (p["rect"][0] + p["rect"][2]) / 2
                if cx < 300:
                    col1.append(cx)
                else:
                    col2.append(cx)
        c1 = sum(col1) / len(col1) if col1 else 162.68
        c2 = sum(col2) / len(col2) if col2 else 433.64
        return c1, c2

    def _queue_line_edit(self, edits_dict, lines, page, match_fn, new_text, extra_args=None):
        """Encontra a primeira linha correspondente que ainda não foi editada e registra no dicionário de lote."""
        for l in lines:
            line_no = l["line"]
            if line_no in edits_dict:
                continue
            if match_fn(l):
                final_text = new_text(l) if callable(new_text) else new_text
                args = {"doc": 1, "page": page, "line": line_no, "text": final_text}
                if extra_args:
                    args.update(extra_args)
                edits_dict[line_no] = args
                return True
        return False

    def _queue_paragraph_edit(self, edits_dict, paras, page, match_fn, new_text, target_cx=None, box_width=None, extra_args=None):
        """Encontra o primeiro parágrafo correspondente e registra a edição centralizada no dicionário de lote."""
        for p in paras:
            para_no = p["paragraph"]
            if para_no in edits_dict:
                continue
            if match_fn(p):
                rect = p["rect"]
                orig_w = rect[2] - rect[0]
                cx = target_cx if target_cx is not None else ((rect[0] + rect[2]) / 2)
                w = box_width if box_width is not None else max(orig_w, 60.0)
                dx = (cx - w / 2) - rect[0]
                final_text = new_text(p) if callable(new_text) else new_text
                args = {
                    "doc": 1,
                    "page": page,
                    "paragraph": para_no,
                    "text": final_text,
                    "align": "center",
                    "width": round(w, 2),
                    "dx": round(dx, 2)
                }
                if extra_args:
                    args.update(extra_args)
                edits_dict[para_no] = args
                return True
        return False

    def process_all(self, target_pdf, custom_models_data=None):
        # =====================================================================
        # LOTE 1: LEITURA ESTRUTURAL UNIFICADA (P1 e P2)
        # =====================================================================
        p1_lines, p1_paras, p2_lines, p2_paras = self.read_all_structure(self.pdf_source)
        if not p2_lines:
            shutil.copy2(self.pdf_source, target_pdf)
            return False

        p1_c1, p1_c2 = self._detect_p1_pillar_centers_from_paras(p1_paras)
        orig_col_centers, orig_span_center = self._detect_columns_from_lines(p2_lines)
        col_centers, span_center = list(orig_col_centers), orig_span_center
        y_rated, y_max = self._find_current_rows_y_from_lines(p2_lines)
        c_gerais = self._detect_general_data_center_from_lines(p2_lines, default_cx=span_center)

        if custom_models_data and len(custom_models_data) != len(col_centers):
            x_left = col_centers[0] - 35.0
            x_right = col_centers[-1] + 35.0
            w_total = x_right - x_left
            n_cols = len(custom_models_data)
            w_col = w_total / n_cols
            col_centers = [round(x_left + (k + 0.5) * w_col, 2) for k in range(n_cols)]
            span_center = round((col_centers[0] + col_centers[-1]) / 2, 2)
            print(f"    [Ajuste de Colunas]: Recalculadas {n_cols} coluna(s): {col_centers}")

        print(f"--> Geometria detectada (Leitura em Lote Único):")
        print(f"    Página 1 Pilares: Coluna 1 = {p1_c1:.2f} pt | Coluna 2 = {p1_c2:.2f} pt")
        print(f"    Página 2 Colunas Originais: {[round(c, 1) for c in orig_col_centers]} | Span = {orig_span_center:.2f} pt")
        print(f"    Página 2 Centro de Dados Gerais: {c_gerais:.2f} pt")

        p1_line_edits = {}
        p2_line_edits = {}
        p2_add_texts = []
        has_battery = any("Battery" in l["text"] or "Bateria" in l["text"] for l in p2_lines)

        # =====================================================================
        # PREPARAÇÃO PÁGINA 1 (TÍTULOS E TÓPICOS)
        # =====================================================================
        p1_is_lv = any("lv" in l["text"].lower() for l in p1_lines)
        orig_title_lines = [
            l for l in p1_lines
            if any(k in l["text"].lower() for k in ["hybrid inverter", "string inverter"])
        ]
        orig_title_is_centered = bool(orig_title_lines and orig_title_lines[0]["rect"][0] > 80.0)
        orig_title_cx = (
            (orig_title_lines[0]["rect"][0] + orig_title_lines[0]["rect"][2]) / 2.0
            if orig_title_lines else 297.64
        )

        self._queue_line_edit(p1_line_edits, p1_lines, 1, lambda l: "Three Phase Hybrid Inverter" in l["text"], "Inversor Híbrido Trifásico", {})
        self._queue_line_edit(p1_line_edits, p1_lines, 1, lambda l: "Single Phase Hybrid Inverter" in l["text"], "Inversor Híbrido Monofásico", {})
        self._queue_line_edit(
            p1_line_edits, p1_lines, 1,
            lambda l: "three phase string inverter" in l["text"].lower(),
            "Inversor On-Grid - Trifásico 220V" if p1_is_lv else "Inversor On-Grid - Trifásico 380V",
            {}
        )
        self._queue_line_edit(p1_line_edits, p1_lines, 1, lambda l: "single phase string inverter" in l["text"].lower(), "Inversor On-Grid Monofásico", {})

        if custom_models_data and len(custom_models_data) == 1:
            m_single = custom_models_data[0]["model"]
            self._queue_line_edit(p1_line_edits, p1_lines, 1, lambda l: re.search(r"SUN-[\d/\.]+K-", l["text"], re.I) is not None, m_single, {})
            self._queue_line_edit(p1_line_edits, p1_lines, 1, lambda l: re.match(r"^(?:P[123]-EU|-EU-|-LV$)", l["text"].strip(), re.I) is not None, "", {})

        def _fmt_mpp_line(l):
            bul = "• " if "•" in l["text"] else ""
            m = re.search(r"(?:max\.\s*)?(\d+)\s*mpp\s*trackers.*?([\d\.]+%)", l["text"], re.I)
            if m:
                return f"{bul}{m.group(1)} Rastreadores MPP, Eficiência de até {m.group(2).replace('.', ',')}"
            return f"{bul}2 Rastreadores MPP, Eficiência de até 98,3%"

        p1_bullets = [
            # Híbridos (SG)
            (lambda l: "48V low voltage" in l["text"], "• Bateria de baixa tensão 48V,", {"size": 9.5}),
            (lambda l: "isolation design" in l["text"], "  projeto com isolação galvânica", {"size": 9.5}),
            (lambda l: "high voltage battery" in l["text"].lower(), "• Bateria de alta tensão, maior eficiência", {"size": 9.5}),
            (lambda l: "6 time periods" in l["text"], "• 6 períodos programáveis para carga/descarga", {"size": 9.5}),
            (lambda l: "ac couple to retrofit" in l["text"].lower(), "• Acoplamento CA para retrofit fotovoltaico", {"size": 9.5}),
            (lambda l: "max. 10 pcs parallel" in l["text"].lower(), "• Paralelismo de até 10 unidades on/off-grid;", {"size": 9.5}),
            (lambda l: "multiple batteries parallel" in l["text"], "  suporte a múltiplos bancos de baterias", {"size": 9.5}),
            (lambda l: re.search(r"current of (\d+)A", l["text"], re.I) is not None,
             lambda l: f"• Corrente máx. de carga/descarga de {re.search(r'current of (\d+)A', l['text'], re.I).group(1)}A", {"size": 9.5}),
            (lambda l: "diesel generator" in l["text"], "• Armazenamento a partir de gerador a diesel", {"size": 9.5}),
            (lambda l: l["text"].strip() == "100% unbalanced output", "• 100% de saída desbalanceada", {"size": 9.5}),
            (lambda l: "100% unbalanced output" in l["text"] and l["text"].strip() != "100% unbalanced output", "• Saída desbalanceada de até 50% por fase;", {"size": 9.5}),
            (lambda l: "rated power for each phase" in l["text"], "  100% de saída desbalanceada", {"size": 9.5}),
            (lambda l: "low voltage" in l["text"].lower() and "compatíveis" in l["text"].lower(), "Compatíveis com baterias de baixa tensão de 48V", {}),
            # On-Grid (G)
            (lambda l: l["text"].strip() == "LV", "BT", {}),
            (lambda l: "127v/220v" in l["text"].lower(), "127V/220V, 133V/230V e", {}),
            (lambda l: "three phase system" in l["text"].lower(), "50/60Hz, Sistema Trifásico", {}),
            (lambda l: "mpp trackers" in l["text"].lower(), _fmt_mpp_line, {}),
            (lambda l: "zero export" in l["text"].lower(), lambda l: ("• " if "•" in l["text"] else "") + "Aplicação Zero Export, aplicação VSG", {}),
            (lambda l: "string intelligent" in l["text"].lower(), lambda l: ("• " if "•" in l["text"] else "") + "Monitoramento inteligente de strings (opcional)", {}),
            (lambda l: "wide output" in l["text"].lower(), lambda l: ("• " if "•" in l["text"] else "") + "Ampla faixa de tensão de saída", {}),
            (lambda l: "anti-pid" in l["text"].lower(), lambda l: ("• " if "•" in l["text"] else "") + "Função Anti-PID (Opcional)", {}),
            (lambda l: re.search(r"low start-up voltage of (\d+)v", l["text"], re.I) is not None,
             lambda l: ("• " if "•" in l["text"] else "") + f"Baixa tensão de partida de {re.search(r'low start-up voltage of (\d+)v', l['text'], re.I).group(1)}V", {}),
        ]
        for match_fn, text, args in p1_bullets:
            self._queue_line_edit(p1_line_edits, p1_lines, 1, match_fn, text, args)

        # =====================================================================
        # PREPARAÇÃO PÁGINA 2 - MODELO ÚNICO (PRESERVANDO 100% DAS LINHAS DA TABELA)
        # =====================================================================
        if custom_models_data and len(custom_models_data) == 1 and len(orig_col_centers) > 1:
            m_info = custom_models_data[0]
            m_name = m_info["model"]
            pw_w = m_info["power_w"]
            pw_kw = m_info["power_kw"]

            # Identifica a coordenada X da coluna de referência (se o modelo já existir no cabeçalho, usa a coluna dele; caso contrário, a última)
            ref_cx = orig_col_centers[-1]
            exact_model_in_table = False
            for l in p2_lines:
                if l["rect"][0] > 180 and l["rect"][1] < 80 and "SUN-" in l["text"].upper():
                    if m_name.upper().startswith(l["text"].replace(" ", "").strip().upper()):
                        ref_cx = (l["rect"][0] + l["rect"][2]) / 2
                        if m_name.upper() == l["text"].replace(" ", "").strip().upper():
                            exact_model_in_table = True
                        break

            # Agrupa linhas da área de dados da tabela por coordenada Y (altura da linha)
            data_rows = defaultdict(list)
            for l in p2_lines:
                if l["rect"][0] > 180 and 42.0 <= l["rect"][1] <= 680.0:
                    t_low = l["text"].lower()
                    t_str = l["text"].strip()
                    if "deyeinverter" in t_low:
                        continue
                    # Ignora linha de temperatura operacional (que possui '℃', ', >45' e 'Derating' / 'Desaceleração' na mesma linha)
                    if t_str in ["℃", "°C"] or any(k in t_low for k in ["-25 to", "-40 to", "derating", "desaceleração", ">45"]):
                        continue
                    # Remove caractere espúrio isolado na borda direita (ex.: '3' em AC Output Side)
                    if t_str == "3" and l["rect"][0] > 500 and 190 < l["rect"][1] < 205:
                        p2_line_edits[l["line"]] = {"doc": 1, "page": 2, "line": l["line"], "text": ""}
                        continue
                    y_bucket = round(l["rect"][1] / 5.0) * 5
                    data_rows[y_bucket].append(l)

            model_header_placed = False
            for y_bucket, row_items in sorted(data_rows.items()):
                row_items.sort(key=lambda x: x["rect"][0])
                # Verifica se a linha possui múltiplos valores por coluna ou está deslocada do centro
                is_multi_col = len(row_items) > 1 or abs(((row_items[0]["rect"][0] + row_items[0]["rect"][2]) / 2) - orig_span_center) > 25.0
                if not is_multi_col:
                    continue

                ref_item = min(row_items, key=lambda x: abs(((x["rect"][0] + x["rect"][2]) / 2) - ref_cx))
                # Limpa apenas as outras colunas dessa linha, preservando ref_item com sua fonte e cor nativas!
                for item in row_items:
                    if item["line"] != ref_item["line"]:
                        p2_line_edits[item["line"]] = {"doc": 1, "page": 2, "line": item["line"], "text": ""}

                row_y = ref_item["rect"][1]
                row_cy = (ref_item["rect"][1] + ref_item["rect"][3]) / 2
                ref_text = ref_item["text"].strip()

                # Identifica o rótulo da Coluna 1 na mesma faixa vertical Y
                col1_matches = [
                    l for l in p2_lines
                    if l["rect"][0] < 180 and abs(((l["rect"][1] + l["rect"][3]) / 2) - row_cy) < 9.0
                ]
                label_text = " ".join(l["text"].lower() for l in col1_matches)

                new_val = ref_text

                if row_y < 72.0 or "model" in label_text or "modelo" in label_text:
                    if model_header_placed or ref_text.startswith("-") or (not "SUN-" in ref_text.upper() and any("SUN-" in l["text"].upper() for l in row_items)):
                        # Linha de sufixo quebrado de modelo (ex: -EU-EM6, LP3-EU-BM2) ou linha subsequente do cabeçalho -> limpa
                        p2_line_edits[ref_item["line"]] = {"doc": 1, "page": 2, "line": ref_item["line"], "text": ""}
                        continue
                    new_val = m_name
                    model_header_placed = True
                elif exact_model_in_table:
                    # Quando o modelo escolhido já existe na tabela original, preserva seus valores exatos de fábrica
                    new_val = ref_text
                elif "access power" in label_text or "acesso fotovoltaico" in label_text or "acesso fv" in label_text:
                    val_w = m_info.get("max_pv_access_w", int(pw_w * 2.0))
                    new_val = str(round(val_w / 1000.0, 1)) if "(kw)" in label_text else str(val_w)
                elif ("input power" in label_text and "pv" in label_text) or "potência máxima cc" in label_text or ("entrada fv" in label_text and "potência" in label_text):
                    val_w = m_info.get("max_pv_input_w", int(pw_w * (1.5 if ("G0" in m_name or "SG04" in m_name) else 1.6)))
                    new_val = str(round(val_w / 1000.0, 1)) if "(kw)" in label_text else str(val_w)
                elif "active power" in label_text or "potência ativa" in label_text:
                    new_val = str(pw_kw) if "(kw)" in label_text else str(pw_w)
                elif "apparent power" in label_text or "potência aparente" in label_text:
                    new_val = str(round(pw_kw * 1.1, 1)) if "(kva)" in label_text else str(int(pw_w * 1.1))
                elif ("rated ac" in label_text and "current" in label_text) or "corrente nominal de saída" in label_text or "corrente nominal ca" in label_text:
                    if "/" in ref_text and "-LV" in m_name.upper() and not has_battery:
                        i1 = round(pw_w / (1.73205 * 220.0), 1)
                        i2 = round(pw_w / (1.73205 * 230.0), 1)
                        new_val = f"{i1}/{i2}"
                    else:
                        new_val = str(m_info["ac_nom_current"])
                elif ("max. ac" in label_text and "current" in label_text) or "corrente nominal de entrada/saída" in label_text or "corrente máxima ca" in label_text:
                    if "/" in ref_text and "-LV" in m_name.upper() and ref_text == "21/20.1" and not has_battery:
                        i1 = round(pw_w / (1.73205 * 220.0), 1)
                        i2 = round(pw_w / (1.73205 * 230.0), 1)
                        new_val = f"{i1}/{i2}"
                    else:
                        new_val = str(m_info["ac_max_current"])
                elif "weight" in label_text or "peso" in label_text:
                    new_val = f"{float(ref_text):.1f}" if ref_text.isdigit() and not has_battery else ref_text

                if new_val != ref_text:
                    p2_line_edits[ref_item["line"]] = {"doc": 1, "page": 2, "line": ref_item["line"], "text": new_val}

        # =====================================================================
        # PREPARAÇÃO PÁGINA 2 - CABEÇALHOS, RÓTULOS E VALORES EM LINHA
        # =====================================================================
        p2_col1_labels = [
            (lambda l: l["text"].strip().lower() in ["technical data", "dados técnicos"], "Dados Técnicos", {"size": 14.0}),
            (lambda l: l["text"].strip().lower() in ["model", "modelo"], "Modelo", {"size": 9.0}),
            (lambda l: "Battery Input Data" in l["text"] or "dados de entrada da bateria" in l["text"].lower(), "Dados de Entrada da Bateria", {"size": 7.6}),
            (lambda l: "pv string input data" in l["text"].lower() or l["text"].strip().lower() == "dados de entrada", "Dados de Entrada FV", {"size": 7.6}),
            (lambda l: "AC Input/Output Data" in l["text"] or "dados de saída ac" in l["text"].lower(), "Dados de Saída / Entrada CA", {"size": 7.6}),
            (lambda l: "ac output side" in l["text"].lower(), "Dados de Saída CA", {"size": 7.6}),
            (lambda l: "eﬃciency" in l["text"].lower() or l["text"].strip() == "Efficiency" or "eﬁciência" in l["text"].lower(), "Eficiência", {"size": 7.6}),
            (lambda l: "equipment protection" in l["text"].lower() or l["text"].strip().lower() == "protecção", "Proteção do Equipamento", {"size": 7.6}),
            (lambda l: l["text"].strip() == "Interface" and l["rect"][0] < 150, "Interface", {"size": 7.6}),
            (lambda l: "general data" in l["text"].lower() or "dados gerais" in l["text"].lower(), "Dados Gerais", {"size": 7.6}),

            (lambda l: "Battery Type" in l["text"], "Tipo de Bateria", {"size": 7.2}),
            (lambda l: "Battery Voltage Range" in l["text"], "Faixa de Tensão da Bateria (V)", {"size": 7.2}),
            (lambda l: l["text"].strip() == "Lithium-ion", "Íon de Lítio", {"size": 7.2}),
            (lambda l: "charging/discharging current" in l["text"].lower(), "Corrente Máx. de Carga/Descarga (A)", {"size": 7.2}),
            (lambda l: ("charging current" in l["text"].lower() and "discharging" not in l["text"].lower()) or "corrente de carga máxima" in l["text"].lower(), "Corrente Máx. de Carga (A)", {"size": 7.2}),
            (lambda l: "discharging current" in l["text"].lower() or "corrente máxima de descarga" in l["text"].lower(), "Corrente Máx. de Descarga (A)", {"size": 7.2}),
            (lambda l: "Charging Strategy for Li-ion" in l["text"] or "estratégia de carga para bateria" in l["text"].lower(), "Estratégia de Carga p/ Bateria Lítio", {"size": 7.2}),
            (lambda l: "Number of Battery Input" in l["text"] or "número de entrada de bateria" in l["text"].lower(), "Número de Entradas de Bateria", {"size": 7.2}),

            (lambda l: "access power" in l["text"].lower() or "acesso fotovoltaico" in l["text"].lower(),
             lambda l: "Potência Máx. Acesso FV (kW)" if "(kw)" in l["text"].lower() else "Potência Máx. Acesso FV (W)", {"size": 7.2}),
            (lambda l: ("input power" in l["text"].lower() and "pv" in l["text"].lower()) or "potência máxima cc" in l["text"].lower(),
             lambda l: "Potência Máx. de Entrada FV (kW)" if "(kw)" in l["text"].lower() else "Potência Máx. de Entrada FV (W)", {"size": 7.2}),
            (lambda l: "max. pv input voltage" in l["text"].lower() or "tensão nominal cc" in l["text"].lower(), "Tensão Máxima de Entrada FV (V)", {"size": 7.2}),
            (lambda l: "start-up voltage" in l["text"].lower(), "Tensão de Partida (V)", {"size": 7.2}),
            (lambda l: "mppt  voltage range" in l["text"].lower() or "mppt voltage range" in l["text"].lower() or "faixa de tensão da mppt" in l["text"].lower(), "Faixa de Tensão MPPT (V)", {"size": 7.2}),
            (lambda l: "rated pv input voltage" in l["text"].lower() or "tensão de entrada dc nominal" in l["text"].lower(), "Tensão Nominal de Entrada FV (V)", {"size": 7.2}),
            (lambda l: "max. operating pv input current" in l["text"].lower() or "corrente máxima de operação da entrada" in l["text"].lower(), "Corrente Máx. de Operação FV (A)", {"size": 7.2}),
            (lambda l: "max. input short-circuit current" in l["text"].lower() or "max. input short circuit current" in l["text"].lower() or "corrente máxima de curto circuito" in l["text"].lower(), "Corrente Máx. Curto-Circuito (A)", {"size": 7.2}),
            (lambda l: "no. of mpp trackers" in l["text"].lower() or "número de rastreadores mpp" in l["text"].lower(), "Nº de Rastreadores MPP /", {"size": 7.2}),
            (lambda l: ("no. of strings" in l["text"].lower() or "número de strings" in l["text"].lower()) and "tracker" in l["text"].lower(), "Nº de Strings por MPPT", {"size": 7.2}),

            (lambda l: ("rated ac" in l["text"].lower() and "active power" in l["text"].lower()) or "potência ativa de entrada/saída" in l["text"].lower(),
             lambda l: "Potência Ativa Nominal CA (kW)" if "(kw)" in l["text"].lower() else "Potência Ativa Nominal CA (W)", {"size": 7.2}),
            (lambda l: ("max. ac" in l["text"].lower() and "apparent power" in l["text"].lower()) or "potência aparente máxima de entrada/saída" in l["text"].lower(),
             lambda l: "Potência Aparente Máx. CA (kVA)" if "(kva)" in l["text"].lower() else "Potência Aparente Máx. CA (VA)", {"size": 7.2}),
            (lambda l: ("rated ac" in l["text"].lower() and "current" in l["text"].lower()) or "corrente nominal de saída ca" in l["text"].lower(), "Corrente Nominal CA (A)", {"size": 7.2}),
            (lambda l: ("max. ac" in l["text"].lower() and "current" in l["text"].lower()) or "corrente nominal de entrada/saída" in l["text"].lower(), "Corrente Máxima CA (A)", {"size": 7.2}),
            (lambda l: "continuous ac passthrough" in l["text"].lower() or "corrente máxima de passagem" in l["text"].lower(), "Corrente Máx. Passagem CA (A)", {"size": 7.2}),
            (lambda l: "peak power (off-grid)" in l["text"].lower() or "potência de pico (off grid)" in l["text"].lower(), "Potência de Pico (Off-Grid) (W)", {"size": 7.2}),
            (lambda l: "power factor adjustment range" in l["text"].lower() or "faixa do fator de potência" in l["text"].lower(), "Faixa de Ajuste do Fator de Potência", {"size": 7.2}),
            (lambda l: "rated input/output voltage" in l["text"].lower() or "frequência e tensão de saída" in l["text"].lower(), "Tensão Nominal de Entrada / Saída", {"size": 7.2}),
            (lambda l: "rated output voltage" in l["text"].lower(), "Tensão Nominal de Saída (V)", {"size": 7.2}),
            (lambda l: "grid frequency" in l["text"].lower() or "faixa de frequência de trabalho" in l["text"].lower(), "Frequência Nominal da Rede / Faixa (Hz)", {"size": 7.2}),
            (lambda l: "grid connection form" in l["text"].lower() or "tipo de conexão à rede" in l["text"].lower(), "Forma de Conexão à Rede", {"size": 7.2}),
            (lambda l: "total current harmonic" in l["text"].lower() or "distorção harmônica (thd)" in l["text"].lower(), "Distorção Harmônica Total THDi", {"size": 7.2}),
            (lambda l: "dc injection current" in l["text"].lower() or "corrente de injeção dc" in l["text"].lower(), "Corrente de Injeção CC", {"size": 7.2}),

            (lambda l: "max. efficiency" in l["text"].lower() or "eficiência máxima" in l["text"].lower(), "Eficiência Máxima", {"size": 7.2}),
            (lambda l: "euro efficiency" in l["text"].lower() or "eficiência euro" in l["text"].lower(), "Eficiência Euro", {"size": 7.2}),
            (lambda l: "mppt efficiency" in l["text"].lower() or "eficiência mppt" in l["text"].lower(), "Eficiência MPPT", {"size": 7.2}),

            # Proteções (Rótulos individuais On-Grid e Integrado Híbrido)
            (lambda l: l["text"].strip() == "Integrated", "Integrado", {"size": 7.2}),
            (lambda l: "dc reverse polarity" in l["text"].lower() and l["rect"][0] < 180, "Proteção contra Polaridade Reversa CC", {"size": 7.2}),
            (lambda l: "ac output overcurrent" in l["text"].lower() and l["rect"][0] < 180, "Proteção de Sobrecorrente CA", {"size": 7.2}),
            (lambda l: "ac output overvoltage" in l["text"].lower() and l["rect"][0] < 180, "Proteção de Sobretensão CA", {"size": 7.2}),
            (lambda l: "ac output short circuit" in l["text"].lower() and l["rect"][0] < 180, "Proteção de Curto-Circuito CA", {"size": 7.2}),
            (lambda l: "thermal protection" in l["text"].lower() and l["rect"][0] < 180, "Proteção Térmica", {"size": 7.2}),
            (lambda l: "insulation impedance" in l["text"].lower() and l["rect"][0] < 180, "Detecção de Impedância de Isolamento", {"size": 7.2}),
            (lambda l: "dc component monitoring" in l["text"].lower() and l["rect"][0] < 180, "Monitoramento de Injeção CC", {"size": 7.2}),
            (lambda l: "arc fault" in l["text"].lower() and l["rect"][0] < 180, "Interruptor de Falha de Arco (AFCI)", {"size": 7.2}),
            (lambda l: "anti-islanding" in l["text"].lower() and l["rect"][0] < 180, "Proteção Anti-ilhamento", {"size": 7.2}),
            (lambda l: "residual current" in l["text"].lower() and l["rect"][0] < 180, "Detecção de Corrente Residual", {"size": 7.2}),
            (lambda l: ("dc switch" in l["text"].lower() or "dc  switch" in l["text"].lower()) and l["rect"][0] < 180, "Seccionadora CC", {"size": 7.2}),
            (lambda l: ("surge protection level" in l["text"].lower() or "proteção contra sobretensão" in l["text"].lower()) and l["rect"][0] < 180, "Nível de Prot. contra Surtos", {"size": 7.2}),
            (lambda l: l["text"].strip() == "Optional", "Opcional", {"size": 7.2}),

            (lambda l: "lcd/led display" in l["text"].lower(), "Display LCD/LED", {"size": 7.2}),
            (lambda l: "communication interface" in l["text"].lower(), "Interface de Comunicação", {"size": 7.2}),
            (lambda l: "monitor mode" in l["text"].lower() or "modo de monitor" in l["text"].lower(), "Modo de Monitoramento", {"size": 7.2}),
            (lambda l: "rs485/rs232" in l["text"].lower() and "can" not in l["text"].lower(), "RS485, RS232, Wi-Fi, LAN", {"size": 7.2}),
            (lambda l: ("(optional)" in l["text"].lower() or "(facultativo)" in l["text"].lower()) and l["rect"][0] > 200,
             lambda l: re.sub(r"\((?:optional|facultativo)\)", " (opcional)", l["text"], flags=re.I), {"size": 7.2}),

            (lambda l: l["text"].strip() == ")" and l["rect"][0] < 200, "", {}),
            (lambda l: "derating" in l["text"].lower() or "desaceleração" in l["text"].lower(), "", {}),
            (lambda l: ", >45" in l["text"] and l["rect"][0] > 300, "", {}),

            (lambda l: "operating temperature range" in l["text"].lower() or "faixa de temperatura operacional" in l["text"].lower(), "Faixa de Temp. Operacional (°C)", {"size": 7.6}),
            (lambda l: "permissible ambient humidity" in l["text"].lower() or "umidade ambiente permitida" in l["text"].lower(), "Umidade Ambiente Permitida", {"size": 7.2}),
            (lambda l: "permissible altitude" in l["text"].lower() or "altitude permitida" in l["text"].lower(), "Altitude Permitida", {"size": 7.2}),
            (lambda l: "noise" in l["text"].lower() and l["rect"][0] < 180, "Ruído (dB)", {"size": 7.2}),
            (lambda l: "ingress protection" in l["text"].lower() or "grau de proteção" in l["text"].lower(), "Grau de Proteção (IP)", {"size": 7.2}),
            (lambda l: "inverter topology" in l["text"].lower() or "topologia do inversor" in l["text"].lower(), "Topologia do Inversor", {"size": 7.2}),
            (lambda l: "over voltage category" in l["text"].lower() or "categoria de sobretensão" in l["text"].lower(), "Categoria de Sobretensão", {"size": 7.2}),
            (lambda l: "cabinet size" in l["text"].lower() or "dimensões" in l["text"].lower(), "Dimensões (L x A x P mm)", {"size": 7.2}),
            (lambda l: "(excluding connectors" in l["text"].lower() and l["rect"][0] < 180, "(Sem conectores e suportes)", {"size": 7.0}),
            (lambda l: ("(excluding connectors" in l["text"].lower() or "(excluindo conectores" in l["text"].lower()) and l["rect"][0] > 180,
             lambda l: re.sub(r"\((?:excluding|excluindo)\s+(?:connectors|conectores).*?\)", "(Sem conectores e suportes)", l["text"], flags=re.I), {"size": 7.2}),
            (lambda l: ("weight" in l["text"].lower() or "peso" in l["text"].lower()) and l["rect"][0] < 180, "Peso (kg)", {"size": 7.2}),
            (lambda l: "cooling style" in l["text"].lower() or "type of cooling" in l["text"].lower() or "modo de resfriamento" in l["text"].lower(), "Tipo de Refrigeração", {"size": 7.2}),
            (lambda l: l["text"].strip() == "Warranty", "Garantia", {"size": 7.2}),
            (lambda l: "grid regulation" in l["text"].lower() or "regulamentação da rede" in l["text"].lower(), "Regulamentação de Rede", {"size": 7.2}),
            (lambda l: "safety / emc standard" in l["text"].lower() or "safety emc" in l["text"].lower() or "regulamentação de emc" in l["text"].lower(), "Normas de Segurança / EMC", {"size": 7.2}),

            # Valores em linha (On-Grid / Híbrido)
            (lambda l: "-25 to +60" in l["text"] or "-25 to -60" in l["text"], "-25 a +60°C, Redução >45°C", {"size": 7.2}),
            (lambda l: l["text"].strip() == "-40 to +60", "-40 a +60°C, Redução >45°C", {"size": 7.2}),
            (lambda l: "natural cooling" in l["text"].lower(), "Convecção Natural", {"size": 7.2}),
            (lambda l: "intelligent air cooling" in l["text"].lower() and not has_battery, "Ventilação Inteligente", {"size": 7.2}),
            (lambda l: "refrigeração de ar inteligente" in l["text"].lower(), "Ventilação Inteligente", {"size": 7.2}),
            (lambda l: l["text"].strip() in ["5 Years", "5 Anos", "5/10 Anos", "5 anos/10 anos", "5 Years / 10 Years", "10 Years"] or ("years" in l["text"].lower() and l["rect"][0] > 180 and "warranty" not in l["text"].lower()), "10 Anos", {"size": 7.2}),
            (lambda l: "<0.5%ln" in l["text"].lower() or "<0.5% in" in l["text"].lower(), "<0,5% In", {"size": 7.2}),
            (lambda l: "0.8 leading to 0.8 lagging" in l["text"].lower() and not has_battery, "0,8 indutivo a 0,8 capacitivo", {"size": 7.2}),
            (lambda l: "type ii(dc)" in l["text"].lower() and not has_battery, "TIPO II (CC) / TIPO II (CA)", {"size": 7.2}),
            (lambda l: "non-isolated" in l["text"].lower() and not has_battery, "Não Isolado", {"size": 7.2}),
            (lambda l: "ovc ii(dc)" in l["text"].lower(), "OVC II (CC), OVC III (CA)", {"size": 7.2}),

            # Rodapé de garantia e endereço Deye Brasil padronizados
            (lambda l: "warranty period varies" in l["text"].lower() or "warranty period depends" in l["text"].lower() or "política de garantia" in l["text"].lower(),
             "*Período de garantia de 10 anos conforme a política de garantia da Deye Brasil.", {"size": 7.0}),
            (lambda l: "5 anos/10 anos" in l["text"].lower() or "5 anos / 10 anos" in l["text"].lower(), "10 Anos*", {"size": 7.2}),
            (lambda l: "depende do local de instalação final" in l["text"].lower(),
             "*O período de garantia de 10 anos varia conforme termos contratuais e política de garantia oficial.", {"size": 7.0}),
            (lambda l: "ningbo deye" in l["text"].lower(), "Deye Support Center Brasil", {"size": 8.0}),
            (lambda l: "address: no. 26" in l["text"].lower() or "add: no. 26" in l["text"].lower() or "south yongjiang" in l["text"].lower() or "suporte@deyebrasil" in l["text"].lower() or "deyeinversores.com.br" in l["text"].lower() and "tel:" in l["text"].lower(),
             f"End: Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP | Tel: {'(11) 2500-3700' if has_battery else '(11) 2500-0681'} | E-mail: suporte@deyeinversores.com.br", {"size": 7.2}),
            (lambda l: "deye support center" in l["text"].lower() and l["rect"][1] > 745, "Deye Support Center Brasil", {"size": 8.0}),
            (lambda l: "vila mogi" in l["text"].lower() and l["rect"][1] > 745, "End: Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP", {"size": 7.2}),
            (lambda l: "deyeeunversores" in l["text"].lower() and l["rect"][1] > 745, "www.deyeinversores.com.br", {"size": 7.2})
        ]
        for match_fn, text, args in p2_col1_labels:
            self._queue_line_edit(p2_line_edits, p2_lines, 2, match_fn, text, args)

        # Traduz todas as ocorrências de 'Yes' na coluna de Proteções para 'Sim' e limpa todos os fragmentos de '℃' / '>45 Derating'
        for l in p2_lines:
            if l["line"] in p2_line_edits:
                continue
            t_strip = l["text"].strip()
            if t_strip == "Yes" and l["rect"][0] > 250:
                p2_line_edits[l["line"]] = {"doc": 1, "page": 2, "line": l["line"], "text": "Sim", "size": 7.2}
            elif t_strip == "℃" or (t_strip == ")" and l["rect"][0] < 200):
                p2_line_edits[l["line"]] = {"doc": 1, "page": 2, "line": l["line"], "text": ""}
            elif ("derating" in t_strip.lower() or "desaceleração" in t_strip.lower() or ">45" in t_strip) and l["rect"][0] > 300 and "-25" not in t_strip and "-40" not in t_strip:
                p2_line_edits[l["line"]] = {"doc": 1, "page": 2, "line": l["line"], "text": ""}

        # =====================================================================
        # LOTE 2: EXECUÇÃO DE TODAS AS EDIÇÕES DE LINHA + INSERÇÕES + RELEITURA DE PARÁGRAFOS
        # =====================================================================
        tmp_batch1 = str(get_temp_file(prefix="deye_batch1_", suffix=".pdf"))
        batch2_steps = [{"tool": "doc_open", "args": {"path": self.pdf_source}}]

        for line_no in sorted(p1_line_edits.keys(), reverse=True):
            batch2_steps.append({"tool": "text_edit", "args": p1_line_edits[line_no]})

        for line_no in sorted(p2_line_edits.keys(), reverse=True):
            batch2_steps.append({"tool": "text_edit", "args": p2_line_edits[line_no]})

        batch2_steps.extend(p2_add_texts)
        batch2_steps.append({"tool": "text_paragraphs", "args": {"doc": 1, "page": 1}})
        batch2_steps.append({"tool": "text_paragraphs", "args": {"doc": 1, "page": 2}})
        batch2_steps.append({"tool": "doc_save", "args": {"doc": 1, "path": tmp_batch1}})

        rep2 = self._run_pdfcraft(batch2_steps)
        if not rep2 or not rep2.get("ok"):
            print("⚠️ Falha no Lote 2, copiando arquivo base...")
            shutil.copy2(self.pdf_source, target_pdf)
            return False

        # Extrai os parágrafos atualizados retornados pelos passos finais do Lote 2
        upd_p1_paras = rep2["steps"][-3]["result"][0]["paragraphs"]
        upd_p2_paras = rep2["steps"][-2]["result"][0]["paragraphs"]

        # =====================================================================
        # LOTE 3: EDIÇÃO EM LOTE ÚNICO DE TODOS OS PARÁGRAFOS CENTRALIZADOS (P1 e P2)
        # =====================================================================
        p1_para_edits = {}
        p2_para_edits = {}

        # Formatação do Título Principal e Subtítulo da Página 1 (centralizado ou alinhado à esquerda dentro da página)
        for p in upd_p1_paras:
            if p["rect"][1] < 180.0:
                t_p = p["text"].strip()
                if "Inversor" in t_p:
                    if orig_title_is_centered:
                        self._queue_paragraph_edit(
                            p1_para_edits, upd_p1_paras, 1,
                            lambda x, pid=p["paragraph"]: x["paragraph"] == pid,
                            p["editor_text"].strip(), target_cx=orig_title_cx, box_width=500.0,
                            extra_args={"size": 28.0}
                        )
                    else:
                        p1_para_edits[p["paragraph"]] = {
                            "doc": 1,
                            "page": 1,
                            "paragraph": p["paragraph"],
                            "text": p["editor_text"].strip(),
                            "align": "left",
                            "width": 520.0,
                            "dx": 0.0,
                            "size": 28.0
                        }
                elif orig_title_is_centered and "SUN-" in t_p.upper():
                    self._queue_paragraph_edit(
                        p1_para_edits, upd_p1_paras, 1,
                        lambda x, pid=p["paragraph"]: x["paragraph"] == pid,
                        p["editor_text"].strip(), target_cx=orig_title_cx, box_width=380.0,
                        extra_args={}
                    )

        # Pilares da Página 1
        pillars = [
            ("Safe", "Seguro", p1_c1),
            ("Eﬃcient", "Eficiente", p1_c1),
            ("Efficient", "Eficiente", p1_c1),
            ("Adaptive", "Adaptável", p1_c1),
            ("Flexible", "Flexível", p1_c1),
            ("Intelligent", "Inteligente", p1_c2),
            ("Reliable", "Confiável", p1_c2)
        ]
        for src_word, tgt_word, cx in pillars:
            self._queue_paragraph_edit(
                p1_para_edits, upd_p1_paras, 1,
                lambda p, w=src_word: p["text"].strip() == w,
                tgt_word, target_cx=cx, box_width=120.0, extra_args={"size": 13.0}
            )

        # Correntes por coluna quando há múltiplos modelos customizados
        if custom_models_data and len(custom_models_data) > 1:
            for col_idx, m_info in enumerate(custom_models_data):
                if col_idx < len(col_centers):
                    cx = col_centers[col_idx]
                    for is_rated, val in [(True, str(m_info["ac_nom_current"])), (False, str(m_info["ac_max_current"]))]:
                        ty = y_rated if is_rated else y_max
                        def match_cur(p, c=cx, target_y=ty):
                            if p["rect"][0] < 180:
                                return False
                            pcx = (p["rect"][0] + p["rect"][2]) / 2
                            pcy = (p["rect"][1] + p["rect"][3]) / 2
                            return abs(pcx - c) < 25.0 and abs(pcy - target_y) < 6.0
                        self._queue_paragraph_edit(
                            p2_para_edits, upd_p2_paras, 2, match_cur, val,
                            target_cx=cx, box_width=50.0, extra_args={"size": 7.2}
                        )

        # Bloco de Proteções Integradas (Híbridos)
        prot_text = (
            "Proteção contra Polaridade Reversa CC, Sobrecorrente CA, Térmica,\n"
            "Sobretensão CA, Curto-Circuito CA, Monitoramento CC, Falha de Arco AFCI (opcional),\n"
            "Anti-ilhamento, Seccionadora CC, Detecção de Impedância de Isolamento, Corrente Residual"
        )
        self._queue_paragraph_edit(
            p2_para_edits, upd_p2_paras, 2,
            lambda p: p["rect"][0] > 180 and ("reverse polarity" in p["text"].lower() or "polaridade reversa" in p["text"].lower()),
            prot_text, target_cx=span_center, box_width=326.0, extra_args={"size": 6.8, "line_spacing": 1.35}
        )

        # Células mescladas / Spanning
        p_bms_list = [p for p in upd_p2_paras if "self-adaption" in p["text"].lower() or "auto-adaptação" in p["text"].lower()]
        bat_inp_num = "2"
        if p_bms_list:
            bms_lines = [l.strip() for l in p_bms_list[0].get("editor_text", "").splitlines() if l.strip()]
            if len(bms_lines) > 1:
                bat_inp_num = bms_lines[1]

        p_weight = [
            p for p in upd_p2_paras
            if p["rect"][0] > 200 and re.match(r"^\d+(\.\d+)?$", p["text"].strip()) and 10 <= float(p["text"].strip()) <= 500
        ]
        weight_val = p_weight[0]["text"].strip() if p_weight else "51.9"

        is_lv = custom_models_data and ("-LV" in custom_models_data[0]["model"].upper() or custom_models_data[0].get("nominal_voltage_ac") == 220)
        has_separate_3lnpe = any(l["text"].strip() == "3L+N+PE" for l in p2_lines)
        if has_separate_3lnpe:
            volt_text = "127/220V,  120/208V   0.85Un-1.1Un\n60 / 55-65" if is_lv else "220/380V  0.85Un-1.1Un\n60 / 55-65"
        else:
            volt_text = "127/220V,  120/208V   0.85Un-1.1Un  3L+N+PE\n60 / 55-65" if is_lv else "220/380V  0.85Un-1.1Un  3L+N+PE\n60 / 55-65"
        is_lv_battery = any("48V" in l["text"] or "LP" in l["text"] for l in p1_lines + p2_lines)
        topo_text = "Não isolado (solar), Isolado (bateria)" if is_lv_battery else "Não Isolado"

        def format_passthrough_peak_pf(p):
            txt = p["text"].strip()
            mult = "1,5x" if "1.5" in txt else "2x"
            if p["rect"][3] - p["rect"][1] >= 16.0:
                m_pass = re.match(r"^(\d+)", txt)
                if m_pass and int(m_pass.group(1)) > 5:
                    pass_num = m_pass.group(1)
                    return f"{pass_num}\n{mult} potência nominal, 10s\n0,8 indutivo a 0,8 capacitivo"
                return f"{mult} da potência nominal, 10s\n0,8 indutivo a 0,8 capacitivo"
            return f"{mult} da potência nominal, 10s"

        spanning_edits = [
            (lambda p: ("lead-acid" in p["text"].lower() or "ácido de chumbo" in p["text"].lower()) and (p["rect"][3] - p["rect"][1] < 16.0), "Chumbo-Ácido / Íon de Lítio", span_center, 140.0, {"size": 7.2}),
            (lambda p: ("lead-acid" in p["text"].lower() or "ácido de chumbo" in p["text"].lower()) and (p["rect"][3] - p["rect"][1] >= 16.0), "Chumbo-Ácido / Íon de Lítio\n40-60", span_center, 140.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "self-adaption" in p["text"].lower() or "auto-adaptação" in p["text"].lower(), f"Autoadaptação ao BMS\n{bat_inp_num}", span_center, 120.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "times of rated power" in p["text"].lower() or "vez da potência nominal" in p["text"].lower(), format_passthrough_peak_pf, span_center, 160.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: ("0.8 leading to 0.8 lagging" in p["text"].lower() or "0,8 adiantado" in p["text"].lower()) and (p["rect"][3] - p["rect"][1] < 16.0), "0,8 indutivo a 0,8 capacitivo", span_center, 140.0, {"size": 7.2}),
            (lambda p: has_battery and ("220/380V" in p["text"] or ("127/220V" in p["text"] and "3L+N+PE" in p["text"])),
             volt_text, span_center, 180.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "<3% (of nominal power)" in p["text"],
             "<3% (da potência nominal)\n<0,5% In", span_center, 140.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "TYPE II" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), "TIPO II (CC) / TIPO II (CA)", span_center, 140.0, {"size": 7.2}),
            (lambda p: "(optional)" in p["text"].lower() and (p["rect"][3] - p["rect"][1] < 16.0),
             lambda p: re.sub(r"\(optional\)", "(opcional)", p["text"], flags=re.I), span_center, 180.0, {"size": 7.2}),
            (lambda p: "-40 to +60" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), "-40 a +60°C, Redução >45°C", c_gerais, 160.0, {"size": 7.2}),
            (lambda p: "Non-Isolated" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), topo_text, c_gerais, 160.0, {"size": 7.2}),
            (lambda p: "OVC II" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), "OVC II (CC), OVC III (CA)", c_gerais, 140.0, {"size": 7.2}),
            (lambda p: re.search(r'\d+×\d+×\d+', p["text"]) is not None and (p["rect"][3] - p["rect"][1] < 16.0) and (not custom_models_data or len(custom_models_data) > 1 or len(orig_col_centers) == 1),
             lambda p: f"{re.search(r'\d+×\d+×\d+', p['text']).group(0)} (Sem conectores e suportes)", c_gerais, 200.0, {"size": 7.2}),
            (lambda p: p["text"].strip() == weight_val and (p["rect"][3] - p["rect"][1] < 16.0) and (not custom_models_data or len(custom_models_data) > 1 or len(orig_col_centers) == 1),
             weight_val, c_gerais, 60.0, {"size": 7.6}),
            (lambda p: "Intelligent Air Cooling" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), "Ventilação Inteligente", c_gerais, 140.0, {"size": 7.2}),
            (lambda p: "5 Years" in p["text"] and "10 Years" in p["text"] and (p["rect"][3] - p["rect"][1] < 16.0), "5 Anos / 10 Anos*", c_gerais, 140.0, {"size": 7.2})
        ]
        for match_fn, text, cx, w, extra in spanning_edits:
            self._queue_paragraph_edit(p2_para_edits, upd_p2_paras, 2, match_fn, text, target_cx=cx, box_width=w, extra_args=extra)

        # No modo de Modelo Único, alinha todos os parágrafos da coluna de valores (x > 180) exatamente no centro c_gerais,
        # preservando 100% da fonte nativa (Lato-Regular / Lato-Bold), tamanho, espaçamento e cor original (#FFFFFF no topo, #4D4D4D nos dados)
        if custom_models_data and len(custom_models_data) == 1:
            for p in upd_p2_paras:
                pid = p["paragraph"]
                if pid in p2_para_edits:
                    continue
                rect = p["rect"]
                if rect[0] > 180.0 and 40.0 <= rect[1] <= 730.0:
                    ed_txt = p.get("editor_text", "").strip()
                    if not ed_txt or "deyeinverter" in ed_txt.lower():
                        continue
                    pcx = (rect[0] + rect[2]) / 2.0
                    # Preserva parágrafos que já estão centralizados em c_gerais
                    if abs(pcx - c_gerais) < 12.0:
                        continue
                    orig_w = rect[2] - rect[0]
                    if "\n" not in ed_txt:
                        # Translação pura para parágrafos de linha única (erro 0.00 pt em fontes CID/subset)
                        dx = c_gerais - pcx
                        extra_dy = {"dy": -4.0} if rect[1] < 55.0 else {}
                        p2_para_edits[pid] = {
                            "doc": 1,
                            "page": 2,
                            "paragraph": pid,
                            "text": ed_txt,
                            "align": "left",
                            "width": round(max(orig_w + 20.0, 120.0), 2),
                            "dx": round(dx, 2),
                            **extra_dy
                        }
                    else:
                        w = max(orig_w + 40.0, 260.0)
                        dx = (c_gerais - w / 2.0) - rect[0]
                        p2_para_edits[pid] = {
                            "doc": 1,
                            "page": 2,
                            "paragraph": pid,
                            "text": ed_txt,
                            "align": "center",
                            "width": round(w, 2),
                            "dx": round(dx, 2)
                        }

        os.makedirs(os.path.dirname(os.path.abspath(target_pdf)), exist_ok=True)

        if p1_para_edits or p2_para_edits:
            batch3_steps = [{"tool": "doc_open", "args": {"path": tmp_batch1}}]
            for para_no in sorted(p1_para_edits.keys(), reverse=True):
                batch3_steps.append({"tool": "text_edit", "args": p1_para_edits[para_no]})
            for para_no in sorted(p2_para_edits.keys(), reverse=True):
                batch3_steps.append({"tool": "text_edit", "args": p2_para_edits[para_no]})
            batch3_steps.append({"tool": "doc_save", "args": {"doc": 1, "path": str(target_pdf)}})
            self._run_pdfcraft(batch3_steps)
        else:
            shutil.copy2(tmp_batch1, target_pdf)

        try:
            os.remove(tmp_batch1)
        except OSError:
            pass

        if custom_models_data and len(custom_models_data) == 1 and len(orig_col_centers) > 1:
            self._remove_vertical_table_dividers(target_pdf)

        print(f"✅ Concluído com sucesso! Documento gerado em: {target_pdf}")
        return True

    def _remove_vertical_table_dividers(self, pdf_path):
        """Remove cirurgicamente apenas os traços verticais internos das colunas na Página 2, mantendo 100% das linhas horizontais."""
        try:
            data = open(pdf_path, "rb").read()
        except OSError:
            return

        def repl_stream(m):
            prefix, raw, suffix = m.group(1), m.group(2), m.group(3)
            try:
                dec = zlib.decompress(raw).decode("latin1")
            except Exception:
                return m.group(0)
            if " cm" not in dec:
                return m.group(0)

            def repl_block(bm):
                x, y, body = float(bm.group(1)), float(bm.group(2)), bm.group(3)
                if 205.0 <= x <= 535.0 and y >= 250.0:
                    ml = re.findall(r"([\d\.\-]+)\s+([\d\.\-]+)\s+[ml]", body)
                    if ml and all(abs(float(px)) < 0.5 for px, py in ml) and any(abs(float(py)) > 3.0 for px, py in ml):
                        return ""
                return bm.group(0)

            new_dec = re.sub(r"q\s+1\s+0\s+0\s+1\s+([\d\.\-]+)\s+([\d\.\-]+)\s+cm\s+(.*?)Q", repl_block, dec, flags=re.S)
            if new_dec != dec:
                comp = zlib.compress(new_dec.encode("latin1"), 9)
                if len(comp) <= len(raw):
                    padded = comp + (b" " * (len(raw) - len(comp)))
                    return prefix + padded + suffix
            return m.group(0)

        new_data = re.sub(rb"(stream\r?\n)(.*?)(\r?\nendstream)", repl_stream, data, flags=re.S)
        if new_data != data:
            open(pdf_path, "wb").write(new_data)


if __name__ == "__main__":
    default_src = Path.home() / "Documentos" / "SG05LP3-EU-SM2.pdf"
    default_dst = Path.home() / "Documentos" / "SG05LP3-EU-SM2" / "Datasheet_SUN-14-20K-SG05LP3-EU-SM2_PT-BR.pdf"
    src = sys.argv[1] if len(sys.argv) > 1 else str(default_src)
    dst = sys.argv[2] if len(sys.argv) > 2 else str(default_dst)

    engine = SemanticDeyeEngine(src)
    engine.process_all(dst)
