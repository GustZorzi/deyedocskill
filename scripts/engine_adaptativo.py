#!/usr/bin/env python3
"""
Motor de Engenharia Tipográfica e Diagramação Semântica para Datasheets Deye.
Arquitetura Conceitual Adaptativa:
1. Detecta dinamicamente a geometria do documento (centros de colunas de modelos e pilares).
2. Centraliza cada valor numérico no centro exato da sua respectiva coluna (potência ativa, correntes nominais e máximas).
3. Centraliza blocos mesclados (tensões, frequências, proteções, dados gerais) no centro da grade da tabela.
4. Centraliza os 4 pilares da Página 1 diretamente nos centros dos ícones.
5. Mantém os rótulos da Coluna 1 alinhados à esquerda e perfeitamente centrados verticalmente entre as linhas divisórias.
6. Preserva integralmente todas as normas de Regulamentação de Rede (Grid Regulation) e Normas de Segurança/EMC.
7. Padronização tipográfica estrita (fontes uniformes em 7.2 pt para itens/dados, 6.8 pt para bloco denso de proteções e 9.5 pt para tópicos da P1).
"""
import sys
import os
import json
import subprocess
import shutil
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.platform_utils import (
    get_fast_temp_dir,
    get_temp_file,
    run_pdfcraft,
    is_windows
)

class SemanticDeyeEngine:
    def __init__(self, pdf_source):
        self.pdf_source = pdf_source

    def _run_pdfcraft(self, steps):
        temp_dir = str(get_fast_temp_dir())
        script_file = get_temp_file(prefix="engine_steps_", suffix=".json")
        with open(script_file, "w", encoding="utf-8") as f:
            json.dump(steps, f)
        res = run_pdfcraft(["run", "--script", str(script_file), "--report"])
        try:
            os.remove(script_file)
        except OSError:
            pass
        try:
            return json.loads(res.stdout)
        except Exception as e:
            print("Erro no pdfcraft:", e, res.stderr if res else "")
            return None

    def get_lines(self, pdf_file, page):
        steps = [
            {"tool": "doc_open", "args": {"path": pdf_file}},
            {"tool": "text_lines", "args": {"doc": 1, "page": page}}
        ]
        rep = self._run_pdfcraft(steps)
        if rep and rep.get("ok"):
            return rep["steps"][1]["result"][0]["lines"]
        return []

    def get_paragraphs(self, pdf_file, page):
        steps = [
            {"tool": "doc_open", "args": {"path": pdf_file}},
            {"tool": "text_paragraphs", "args": {"doc": 1, "page": page}}
        ]
        rep = self._run_pdfcraft(steps)
        if rep and rep.get("ok"):
            return rep["steps"][1]["result"][0]["paragraphs"]
        return []

    def detect_table_columns(self, pdf_file, page=2):
        """Detecta dinamicamente as coordenadas X centrais de cada modelo e o centro de span da tabela."""
        lines = self.get_lines(pdf_file, page)
        from collections import defaultdict
        rows = defaultdict(list)
        for l in lines:
            if l["rect"][0] > 180: # Área de dados da tabela
                y_key = round(l["rect"][1], 0)
                rows[y_key].append(l)

        if not rows:
            return [229.9, 301.1, 372.9, 443.9, 516.1], 373.0

        best_y, best_items = max(rows.items(), key=lambda item: len(item[1]))
        best_items.sort(key=lambda l: l["rect"][0])
        col_centers = [(l["rect"][0] + l["rect"][2]) / 2 for l in best_items]
        span_center = (col_centers[0] + col_centers[-1]) / 2
        return col_centers, span_center

    def find_current_rows_y(self, pdf_file):
        """Detecta dinamicamente a altura Y das linhas de corrente nominal e corrente máxima."""
        lines = self.get_lines(pdf_file, 2)
        y_rated = 309.9
        y_max = 321.7
        for l in lines:
            if l["rect"][0] < 180:
                txt = l["text"].lower()
                if "rated ac" in txt and "current" in txt:
                    y_rated = (l["rect"][1] + l["rect"][3]) / 2
                elif "max. ac" in txt and "current" in txt:
                    y_max = (l["rect"][1] + l["rect"][3]) / 2
        return y_rated, y_max

    def detect_p1_pillar_centers(self, pdf_file):
        """Detecta os eixos verticais dos ícones dos pilares na Página 1."""
        paras = self.get_paragraphs(pdf_file, 1)
        col1 = []
        col2 = []
        for p in paras:
            txt = p["text"].strip()
            if txt in ["Safe", "Flexible", "Intelligent", "Reliable", "Seguro", "Flexível", "Inteligente", "Confiável"]:
                cx = (p["rect"][0] + p["rect"][2]) / 2
                if cx < 300:
                    col1.append(cx)
                else:
                    col2.append(cx)
        c1 = sum(col1) / len(col1) if col1 else 162.68
        c2 = sum(col2) / len(col2) if col2 else 433.64
        return c1, c2

    def replace_line_by_match(self, curr_pdf, page, match_fn, new_text, out_pdf, extra_args=None):
        lines = self.get_lines(curr_pdf, page)
        target = None
        for l in lines:
            if match_fn(l):
                target = l
                break

        if not target:
            if curr_pdf != out_pdf:
                shutil.copy2(curr_pdf, out_pdf)
            return False

        args = {"doc": 1, "page": page, "line": target["line"], "text": new_text}
        if extra_args:
            args.update(extra_args)

        steps = [
            {"tool": "doc_open", "args": {"path": curr_pdf}},
            {"tool": "text_edit", "args": args},
            {"tool": "doc_save", "args": {"doc": 1, "path": out_pdf}}
        ]
        rep = self._run_pdfcraft(steps)
        return rep and rep.get("ok", False)

    def replace_centered_paragraph(self, curr_pdf, page, match_fn, new_text, out_pdf, target_cx=None, box_width=None, extra_args=None):
        paras = self.get_paragraphs(curr_pdf, page)
        target = None
        for p in paras:
            if match_fn(p):
                target = p
                break

        if not target:
            if curr_pdf != out_pdf:
                shutil.copy2(curr_pdf, out_pdf)
            return False

        rect = target["rect"]
        orig_w = rect[2] - rect[0]
        cx = target_cx if target_cx is not None else ((rect[0] + rect[2]) / 2)
        w = box_width if box_width is not None else max(orig_w, 60.0)
        dx = (cx - w / 2) - rect[0]

        args = {
            "doc": 1,
            "page": page,
            "paragraph": target["paragraph"],
            "text": new_text,
            "align": "center",
            "width": round(w, 2),
            "dx": round(dx, 2)
        }
        if extra_args:
            args.update(extra_args)

        steps = [
            {"tool": "doc_open", "args": {"path": curr_pdf}},
            {"tool": "text_edit", "args": args},
            {"tool": "doc_save", "args": {"doc": 1, "path": out_pdf}}
        ]
        rep = self._run_pdfcraft(steps)
        return rep and rep.get("ok", False)

    def process_all(self, target_pdf):
        curr = str(get_temp_file(prefix="deye_work_0_", suffix=".pdf"))
        shutil.copy2(self.pdf_source, curr)
        step_idx = 1

        # Detecção geométrica das colunas
        p1_c1, p1_c2 = self.detect_p1_pillar_centers(self.pdf_source)
        col_centers, span_center = self.detect_table_columns(self.pdf_source, 2)
        y_rated, y_max = self.find_current_rows_y(self.pdf_source)
        
        print(f"--> Geometria detectada:")
        print(f"    Página 1 Pilares: Coluna 1 = {p1_c1:.2f} pt | Coluna 2 = {p1_c2:.2f} pt")
        print(f"    Página 2 Colunas de Modelos: {col_centers}")
        print(f"    Página 2 Centro de Span da Tabela: {span_center:.2f} pt")
        print(f"    Página 2 Linhas de Corrente: Nominal = {y_rated:.2f} pt | Máxima = {y_max:.2f} pt")

        # =====================================================================
        # ETAPA 1: PÁGINA 1 - TÍTULOS, PILARES E TÓPICOS (FONTE PADRONIZADA 9.5 pt)
        # =====================================================================
        print("--> Processando Página 1...")
        next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
        if self.replace_line_by_match(curr, 1, lambda l: "Three Phase Hybrid Inverter" in l["text"], "Inversor Híbrido Trifásico", next_pdf, {"size": 30.0}):
            curr = next_pdf
            step_idx += 1

        # 4 Pilares: Centralizados milimetricamente sobre os ícones
        pillars = [
            ("Safe", "Seguro", p1_c1),
            ("Flexible", "Flexível", p1_c1),
            ("Intelligent", "Inteligente", p1_c2),
            ("Reliable", "Confiável", p1_c2)
        ]
        for src_word, tgt_word, cx in pillars:
            next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
            ok = self.replace_centered_paragraph(curr, 1, lambda p, w=src_word: p["text"].strip() == w, tgt_word, next_pdf, target_cx=cx, box_width=120.0, extra_args={"size": 13.0})
            if ok:
                curr = next_pdf
                step_idx += 1

        # Tópicos: Fonte uniforme em 9.5 pt
        p1_bullets = [
            (lambda l: "48V low voltage" in l["text"], "• Bateria de baixa tensão 48V,", {"size": 9.5}),
            (lambda l: "isolation design" in l["text"], "  projeto com isolação galvânica", {"size": 9.5}),
            (lambda l: "6 time periods" in l["text"], "• 6 períodos programáveis para carga/descarga", {"size": 9.5}),
            (lambda l: "AC couple to retrofit" in l["text"], "• Acoplamento CA para retrofit fotovoltaico", {"size": 9.5}),
            (lambda l: "Max. 10 pcs parallel" in l["text"], "• Paralelismo de até 10 unidades on/off-grid;", {"size": 9.5}),
            (lambda l: "multiple batteries parallel" in l["text"], "  suporte a múltiplos bancos de baterias", {"size": 9.5}),
            (lambda l: "current of 350A" in l["text"], "• Corrente máx. de carga/descarga de até 350A", {"size": 9.5}),
            (lambda l: "diesel generator" in l["text"], "• Armazenamento a partir de gerador a diesel", {"size": 9.5}),
            (lambda l: "100% unbalanced output" in l["text"], "• Saída desbalanceada de até 50% por fase;", {"size": 9.5}),
            (lambda l: "rated power for each phase" in l["text"], "  100% de saída desbalanceada", {"size": 9.5}),
        ]
        for match_fn, text, args in p1_bullets:
            next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
            if self.replace_line_by_match(curr, 1, match_fn, text, next_pdf, args):
                curr = next_pdf
                step_idx += 1

        # =====================================================================
        # ETAPA 2: PÁGINA 2 - CABEÇALHOS E RÓTULOS DA COLUNA 1 (PADRONIZADOS EM 7.2 pt)
        # =====================================================================
        print("--> Processando Cabeçalhos e Rótulos da Coluna 1 (Padronizados em 7.2 pt)...")
        p2_col1_labels = [
            (lambda l: l["text"].strip() == "Technical Data", "Dados Técnicos", {"size": 14.0}),
            (lambda l: l["text"].strip() == "Model", "Modelo", {"size": 9.0}),
            (lambda l: "Battery Input Data" in l["text"], "Dados de Entrada da Bateria", {"size": 7.6}),
            (lambda l: "PV String Input Data" in l["text"], "Dados de Entrada FV", {"size": 7.6}),
            (lambda l: "AC Input/Output Data" in l["text"], "Dados de Saída / Entrada CA", {"size": 7.6}),
            (lambda l: "Eﬃciency" in l["text"], "Eficiência", {"size": 7.6}),
            (lambda l: "Equipment Protection" in l["text"], "Proteção do Equipamento", {"size": 7.6}),
            (lambda l: "Interface" in l["text"] and l["rect"][0] < 150, "Interface", {"size": 7.6}),
            (lambda l: "General Data" in l["text"], "Dados Gerais", {"size": 7.6}),

            (lambda l: "Battery Type" in l["text"], "Tipo de Bateria", {"size": 7.2}),
            (lambda l: "Battery Voltage Range" in l["text"], "Faixa de Tensão da Bateria (V)", {"size": 7.2}),
            (lambda l: "Max. Charging/Discharging Current" in l["text"], "Corrente Máx. Carga/Descarga (A)", {"size": 7.2}),
            (lambda l: "Charging Strategy for Li-ion" in l["text"], "Estratégia de Carga p/ Bateria Lítio", {"size": 7.2}),
            (lambda l: "Number of Battery Input" in l["text"], "Número de Entradas de Bateria", {"size": 7.2}),

            (lambda l: "access power" in l["text"].lower(), "Potência Máx. Acesso FV (W)", {"size": 7.2}),
            (lambda l: "input power" in l["text"].lower() and "pv" in l["text"].lower(), "Potência Máx. de Entrada FV (W)", {"size": 7.2}),
            (lambda l: "max. pv input voltage" in l["text"].lower(), "Tensão Máxima de Entrada FV (V)", {"size": 7.2}),
            (lambda l: "start-up voltage" in l["text"].lower(), "Tensão de Partida (V)", {"size": 7.2}),
            (lambda l: "mppt  voltage range" in l["text"].lower() or "mppt voltage range" in l["text"].lower(), "Faixa de Tensão MPPT (V)", {"size": 7.2}),
            (lambda l: "rated pv input voltage" in l["text"].lower(), "Tensão Nominal de Entrada FV (V)", {"size": 7.2}),
            (lambda l: "max. operating pv input current" in l["text"].lower(), "Corrente Máx. de Operação FV (A)", {"size": 7.2}),
            (lambda l: "max. input short-circuit current" in l["text"].lower(), "Corrente Máx. Curto-Circuito (A)", {"size": 7.2}),
            (lambda l: "no. of mpp trackers" in l["text"].lower(), "Nº de Rastreadores MPP /", {"size": 7.2}),
            (lambda l: "no. of strings mpp tracker" in l["text"].lower(), "Nº de Strings por MPPT", {"size": 7.2}),

            (lambda l: "rated ac input/output active power" in l["text"].lower(), "Potência Ativa Nominal CA (W)", {"size": 7.2}),
            (lambda l: "max. ac input/output apparent power" in l["text"].lower(), "Potência Aparente Máx. CA (VA)", {"size": 7.2}),
            (lambda l: "rated ac input/output current" in l["text"].lower(), "Corrente Nominal CA (A)", {"size": 7.2}),
            (lambda l: "max. ac input/output current" in l["text"].lower(), "Corrente Máxima CA (A)", {"size": 7.2}),
            (lambda l: "continuous ac passthrough" in l["text"].lower(), "Corrente Máx. Passagem CA (A)", {"size": 7.2}),
            (lambda l: "peak power (off-grid)" in l["text"].lower(), "Potência de Pico (Off-Grid) (W)", {"size": 7.2}),
            (lambda l: "power factor adjustment range" in l["text"].lower(), "Faixa de Ajuste do Fator de Potência", {"size": 7.2}),
            (lambda l: "rated input/output voltage" in l["text"].lower(), "Tensão Nominal de Entrada / Saída", {"size": 7.2}),
            (lambda l: "grid frequency" in l["text"].lower(), "Frequência Nominal da Rede / Faixa (Hz)", {"size": 7.2}),
            (lambda l: "total current harmonic" in l["text"].lower(), "Distorção Harmônica Total THDi", {"size": 7.2}),
            (lambda l: "dc injection current" in l["text"].lower(), "Corrente de Injeção CC", {"size": 7.2}),

            (lambda l: "max. efficiency" in l["text"].lower(), "Eficiência Máxima", {"size": 7.2}),
            (lambda l: "euro efficiency" in l["text"].lower(), "Eficiência Euro", {"size": 7.2}),
            (lambda l: "mppt efficiency" in l["text"].lower(), "Eficiência MPPT", {"size": 7.2}),

            (lambda l: l["text"].strip() == "Integrated", "Integrado", {"size": 7.2}),
            (lambda l: "surge protection level" in l["text"].lower(), "Nível de Prot. contra Surtos", {"size": 7.2}),

            (lambda l: "lcd/led display" in l["text"].lower(), "Display LCD/LED", {"size": 7.2}),
            (lambda l: "communication interface" in l["text"].lower(), "Interface de Comunicação", {"size": 7.2}),

            (lambda l: l["text"].strip() == "℃" and l["rect"][0] < 200, "", {}),
            (lambda l: l["text"].strip() == ")" and l["rect"][0] < 200, "", {}),
            (lambda l: "derating" in l["text"].lower(), "", {}),
            (lambda l: ", >45" in l["text"] and l["rect"][0] > 300, "", {}),
            (lambda l: l["text"].strip() in ["℃", "℃ "] and l["rect"][0] > 300 and 560 < l["rect"][1] < 575, "", {}),

            (lambda l: "operating temperature range" in l["text"].lower(), "Faixa de Temp. Operacional (℃)", {"size": 7.2}),
            (lambda l: "permissible ambient humidity" in l["text"].lower(), "Umidade Ambiente Permitida", {"size": 7.2}),
            (lambda l: "permissible altitude" in l["text"].lower(), "Altitude Permitida", {"size": 7.2}),
            (lambda l: "noise (db)" in l["text"].lower(), "Ruído (dB)", {"size": 7.2}),
            (lambda l: "ingress protection" in l["text"].lower(), "Grau de Proteção (IP)", {"size": 7.2}),
            (lambda l: "inverter topology" in l["text"].lower(), "Topologia do Inversor", {"size": 7.2}),
            (lambda l: "over voltage category" in l["text"].lower(), "Categoria de Sobretensão", {"size": 7.2}),
            (lambda l: "cabinet size" in l["text"].lower(), "Dimensões (L x A x P mm)", {"size": 7.2}),
            (lambda l: l["text"].strip() == "Weight (kg)", "Peso (kg)", {"size": 7.2}),
            (lambda l: "cooling style" in l["text"].lower() or "type of cooling" in l["text"].lower(), "Tipo de Refrigeração", {"size": 7.2}),
            (lambda l: l["text"].strip() == "Warranty", "Garantia", {"size": 7.2}),
            (lambda l: "grid regulation" in l["text"].lower(), "Regulamentação de Rede", {"size": 7.2}),
            (lambda l: "safety / emc standard" in l["text"].lower(), "Normas de Segurança / EMC", {"size": 7.2}),

            # Tradução pontual do rodapé de garantia sem apagar as normas da linha abaixo
            (lambda l: "warranty period varies" in l["text"].lower(),
             "*Período de garantia varia conforme o país. Consulte a política de garantia.", {"size": 7.0}),

            # Rodapé Oficial Deye Brasil
            (lambda l: "ningbo deye" in l["text"].lower(), "Deye Support Center Brasil", {"size": 8.0}),
            (lambda l: "address: no. 26" in l["text"].lower(),
             "End: Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP | Tel: (11) 2500-0681 | E-mail: suporte@deyebrasil.com.br", {"size": 7.5})
        ]
        for match_fn, text, args in p2_col1_labels:
            next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
            if self.replace_line_by_match(curr, 2, match_fn, text, next_pdf, extra_args=args):
                curr = next_pdf
                step_idx += 1

        # =====================================================================
        # ETAPA 3: PÁGINA 2 - VALORES DAS COLUNAS (PADRONIZADOS EM 7.2 pt)
        # =====================================================================
        print("--> Processando Valores das Células com Alinhamento Central aos Modelos (7.2 pt)...")
        current_mappings = [
            (0, True, "21.3"),
            (0, False, "23.4"),
            (1, True, "22.8"),
            (1, False, "25.0"),
            (2, True, "24.3"),
            (2, False, "26.7"),
            (3, True, "27.3"),
            (3, False, "30.0"),
            (4, True, "30.4"),
            (4, False, "33.4"),
        ]
        for col_idx, is_rated, val in current_mappings:
            cx = col_centers[col_idx]
            target_y = y_rated if is_rated else y_max
            def match_cur(p, c=cx, ty=target_y):
                if p["rect"][0] < 180:
                    return False
                pcx = (p["rect"][0] + p["rect"][2]) / 2
                pcy = (p["rect"][1] + p["rect"][3]) / 2
                in_col = abs(pcx - c) < 25.0
                in_row = abs(pcy - ty) < 6.0
                return in_col and in_row

            next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
            ok = self.replace_centered_paragraph(curr, 2, match_cur, val, next_pdf, target_cx=cx, box_width=50.0, extra_args={"size": 7.2})
            if ok:
                curr = next_pdf
                step_idx += 1

        # =====================================================================
        # ETAPA 4: BLOCO DE PROTEÇÕES INTEGRADAS (PADRONIZADO EM 6.8 pt)
        # =====================================================================
        print("--> Processando Bloco de Proteções Integradas (6.8 pt)...")
        prot_text = (
            "Proteção contra Polaridade Reversa CC, Sobrecorrente CA, Térmica,\n"
            "Sobretensão CA, Curto-Circuito CA, Monitoramento CC, Falha de Arco AFCI (opcional),\n"
            "Anti-ilhamento, Seccionadora CC, Detecção de Impedância de Isolamento, Corrente Residual"
        )
        next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
        ok = self.replace_centered_paragraph(
            curr, 2,
            lambda p: "Reverse Polarity" in p["text"] or "Polaridade Reversa" in p["text"],
            prot_text, next_pdf,
            target_cx=span_center,
            box_width=326.0,
            extra_args={"size": 6.8, "line_spacing": 1.35}
        )
        if ok:
            curr = next_pdf
            step_idx += 1

        # =====================================================================
        # ETAPA 5: CÉLULAS MESCLADAS / SPANNING (PADRONIZADAS EM 7.2 pt)
        # =====================================================================
        print("--> Processando Linhas Mescladas (Padronizadas em 7.2 pt)...")
        spanning_edits = [
            (lambda p: "Lead-acid" in p["text"], "Chumbo-Ácido / Íon de Lítio", span_center, 120.0, {"size": 7.2}),
            (lambda p: "Self-adaption" in p["text"], "Autoadaptação ao BMS", span_center, 120.0, {"size": 7.2}),
            (lambda p: "2 times of rated power" in p["text"],
             "70\n2x potência nominal, 10s\n0,8 indutivo a 0,8 capacitivo", span_center, 160.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "220/380V" in p["text"],
             "220/380V  0.85Un-1.1Un  3L+N+PE\n60 / 55-65", span_center, 180.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "<3% (of nominal power)" in p["text"],
             "<3% (da potência nominal)\n<0,5% In", span_center, 140.0, {"size": 7.2, "line_spacing": 1.55}),
            (lambda p: "TYPE II" in p["text"], "TIPO II (CC) / TIPO II (CA)", span_center, 140.0, {"size": 7.2}),
            (lambda p: "-40 to +60" in p["text"], "-40 a +60℃, Redução >45℃", span_center, 150.0, {"size": 7.2}),
            (lambda p: "Non-Isolated" in p["text"], "Não isolado (solar), Isolado (bateria)", span_center, 160.0, {"size": 7.2}),
            (lambda p: "OVC II" in p["text"], "OVC II (CC), OVC III (CA)", span_center, 140.0, {"size": 7.2}),
            (lambda p: "456×750×268.5" in p["text"], "456×750×268.5 (Sem conectores e suportes)", span_center, 200.0, {"size": 7.2}),
            (lambda p: "Intelligent Air Cooling" in p["text"], "Ventilação Inteligente", span_center, 140.0, {"size": 7.2}),
            (lambda p: "5 Years / Up to 10 Years" in p["text"], "5 Anos / Até 10 Anos*", span_center, 140.0, {"size": 7.2})
        ]
        for match_fn, text, cx, w, extra in spanning_edits:
            next_pdf = str(get_temp_file(prefix=f"deye_work_{step_idx}_", suffix=".pdf"))
            ok = self.replace_centered_paragraph(curr, 2, match_fn, text, next_pdf, target_cx=cx, box_width=w, extra_args=extra)
            if ok:
                curr = next_pdf
                step_idx += 1

        os.makedirs(os.path.dirname(os.path.abspath(target_pdf)), exist_ok=True)
        shutil.copy2(curr, target_pdf)
        print(f"✅ Concluído com sucesso! Documento gerado em: {target_pdf}")
        return True

if __name__ == "__main__":
    default_src = Path.home() / "Documentos" / "SG05LP3-EU-SM2.pdf"
    default_dst = Path.home() / "Documentos" / "SG05LP3-EU-SM2" / "Datasheet_SUN-14-20K-SG05LP3-EU-SM2_PT-BR.pdf"
    src = sys.argv[1] if len(sys.argv) > 1 else str(default_src)
    dst = sys.argv[2] if len(sys.argv) > 2 else str(default_dst)
        
    engine = SemanticDeyeEngine(src)
    engine.process_all(dst)
