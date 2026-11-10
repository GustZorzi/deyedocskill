#!/usr/bin/env python3
"""
MÓDULO 2: Gerador de Planilhas de Especificações Técnicas (PET) INMETRO (.docx e .pdf).
Gera 1 arquivo .docx e .pdf individual por modelo homologado a partir dos templates oficiais:
- pet_modelo.docx (Inversores Híbridos - linha SG)
- pet_modelo_ongrid.docx (Inversores On-Grid - linha G)
Otimizado com geração vetorial nativa direta via pdfcraft-cli para máxima velocidade (sub-segundo).
"""
import sys
import os
import re
import json
import zipfile
import datetime
import tempfile
import subprocess
import concurrent.futures
import xml.sax.saxutils as saxutils
from pathlib import Path

# Adiciona o diretório raiz dos scripts ao sys.path para importar módulos comuns
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.deye_grammar import (
    parse_model,
    get_transfer_time,
    get_voltage_ratings,
    extract_max_pv_current
)
from common.electrical_calc import calculate_ac_currents
from common.platform_utils import (
    get_fast_temp_dir,
    get_temp_file,
    run_pdfcraft,
    extract_text_from_pdf,
    resolve_template_path,
    is_windows
)


def calculate_total_strings(mppt_count_str, strings_expr):
    """
    Calcula o número total de strings monitoradas (ex.: '2 / 1+2' -> '3', '2/2+2' -> '4').
    """
    s = str(strings_expr).strip()
    if "/" in s:
        mppt_part, right = s.split("/", 1)
    else:
        mppt_part, right = str(mppt_count_str).strip(), s

    right = right.strip()
    if "+" in right:
        nums = [int(x) for x in re.findall(r"\d+", right)]
        if nums:
            return str(sum(nums))

    nums = [int(x) for x in re.findall(r"\d+", right)]
    if nums:
        # Se for apenas um número inteiro já representando o total ou strings por MPPT
        return str(nums[0])
    return "2"


class DeyePetGenerator:
    def __init__(self, template_path=None):
        skill_root = Path(__file__).resolve().parent.parent.parent
        self.templates_dir = skill_root / "resources" / "templates"
        self.hybrid_template = resolve_template_path("pet_modelo.docx")
        self.ongrid_template = resolve_template_path("pet_modelo_ongrid.docx")

        self.template_path = Path(template_path) if template_path else self.hybrid_template

    def get_template_for_model(self, model_name):
        """Seleciona automaticamente o template Híbrido (SG) ou On-Grid (G)."""
        if "SG" in str(model_name).upper():
            return self.hybrid_template
        if self.ongrid_template.exists():
            return self.ongrid_template
        return self.template_path

    def extract_models_data_from_pdf(self, pdf_path):
        """
        Extrai os modelos e seus parâmetros elétricos dinamicamente a partir do datasheet.
        Usa extração multiplataforma garantida (pdftotext com fallback para pdfcraft-cli text).
        """
        raw_text = extract_text_from_pdf(pdf_path, layout=False)
        layout_text = extract_text_from_pdf(pdf_path, layout=True) or raw_text

        models = []
        # Extração primária de alta precisão via pdfcraft-cli (cabeçalho da Página 2)
        try:
            steps = [
                {"tool": "doc_open", "args": {"path": str(pdf_path)}},
                {"tool": "text_paragraphs", "args": {"doc": 1, "page": 2}}
            ]
            temp_dir = str(get_fast_temp_dir())
            with tempfile.NamedTemporaryFile("w", suffix=".json", dir=temp_dir, delete=False, encoding="utf-8") as fp:
                json.dump(steps, fp)
                sp = fp.name
            r_pdf = run_pdfcraft(["run", "--script", sp, "--report"])
            try:
                os.remove(sp)
            except OSError:
                pass
            rep = json.loads(r_pdf.stdout) if r_pdf and r_pdf.returncode == 0 else None
            if rep and rep.get("ok"):
                paras = rep["steps"][1]["result"][0]["paragraphs"]
                header_paras = [p for p in paras if p["rect"][0] > 170 and 25.0 <= p["rect"][1] <= 82.0]
                bases = [p for p in header_paras if "SUN-" in p["text"].upper() and "/" not in p["text"]]
                bases.sort(key=lambda p: p["rect"][0])
                suffixes = [
                    p for p in header_paras
                    if "SUN-" not in p["text"].upper() and re.match(r"^-?[A-Za-z0-9\-]+$", p["text"].replace(" ", ""))
                    and not re.match(r"^\d+(\.\d+)?$", p["text"].replace(" ", ""))
                    and "deyeinverter" not in p["text"].lower()
                ]
                for b in bases:
                    bcx = (b["rect"][0] + b["rect"][2]) / 2
                    m_txt = b["text"].replace(" ", "").strip()
                    for s in suffixes:
                        scx = (s["rect"][0] + s["rect"][2]) / 2
                        if abs(bcx - scx) < 18.0 and s["rect"][1] >= b["rect"][1]:
                            s_clean = s["text"].replace(" ", "").strip()
                            if not m_txt.endswith(s_clean):
                                m_txt += s_clean if s_clean.startswith("-") or m_txt.endswith("-") else s_clean
                            break
                    for sub_m in re.findall(r"SUN-[\d\.]+K-[A-Za-z0-9\-]+?(?=SUN-|$)", m_txt, re.I):
                        if sub_m not in models:
                            models.append(sub_m)
        except Exception:
            pass

        if not models:
            # Fallback via pdftotext
            cleaned_text = re.sub(r'(SUN-[\d\.]+K-[A-Za-z0-9]+)\s*\n\s*([A-Za-z0-9\-]+)', r'\1\2', raw_text)
            candidates = re.findall(r'SUN-[\d\.]+K-[A-Za-z0-9\-]+', cleaned_text)
            seen = set()
            for m in candidates:
                if "/" in m:
                    continue
                if m not in seen and not any(m != other and m in other for other in candidates):
                    seen.add(m)
                    models.append(m)

        if not models:
            models = [
                "SUN-14K-SG05LP3-EU-SM2", "SUN-15K-SG05LP3-EU-SM2",
                "SUN-16K-SG05LP3-EU-SM2", "SUN-18K-SG05LP3-EU-SM2",
                "SUN-20K-SG05LP3-EU-SM2"
            ]

        # Dimensões (pega a última coluna disponível se houver múltiplas colunas no datasheet)
        dims_all = re.findall(r'(\d+)\s*[×xX]\s*(\d+)\s*[×xX]\s*(\d+(?:\.\d+)?)', layout_text)
        if dims_all:
            dim_l, dim_a, dim_p = dims_all[-1]
        else:
            dim_l, dim_a, dim_p = ('456', '750', '268.5')

        # Peso (pega o último valor da linha Weight (kg) ou Peso (kg))
        peso_line = re.search(r'(?:Weight|Peso)\s*\(kg\)([^\n]+)', layout_text, re.IGNORECASE)
        if peso_line:
            pesos = re.findall(r'\d+(?:\.\d+)?', peso_line.group(1))
            peso = pesos[-1] if pesos else '51.9'
        else:
            peso_m = re.search(r'(?:Weight|Peso)\s*\(kg\)[^\d]*(\d+(?:\.\d+)?)', raw_text, re.IGNORECASE)
            peso = peso_m.group(1) if peso_m else '51.9'

        mppt_v = re.search(r'(?:MPPT\s*Voltage\s*Range|Faixa\s*de\s*tens[ãa]o\s*(?:da\s*)?MPPT)[^\d]*(\d+)\s*-\s*(\d+)', layout_text, re.IGNORECASE)
        mppt_min, mppt_max = (mppt_v.group(1), mppt_v.group(2)) if mppt_v else ('160', '650')

        bat_v = re.search(r'(?:Battery\s*Voltage\s*Range|Faixa\s*de\s*Tens[ãa]o\s*da\s*Bateria)[^\d]*(\d+)\s*-\s*(\d+)', layout_text, re.IGNORECASE)
        bat_min, bat_max = (bat_v.group(1), bat_v.group(2)) if bat_v else ('40', '60')

        def find_val(pattern, default=''):
            m = re.search(pattern, layout_text, re.IGNORECASE)
            if not m:
                m = re.search(pattern, raw_text, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else default

        pv_v_max = find_val(r'(?:Max\.\s*PV\s*Input\s*Voltage|Tens[ãa]o\s*(?:M[áa]xima\s*de\s*Entrada\s*FV|nominal\s*CC))[^\d]*(\d+)', '800')
        pv_start = find_val(r'(?:Start-up\s*Voltage|Tens[ãa]o\s*de\s*Partida)[^\d]*(\d+)', '160')

        # Corrente máxima de operação FV (pega a última coluna se houver várias)
        pv_i_line = re.search(r'(?:Max\.\s*Operating\s*PV\s*Input\s*Current|Corrente\s*m[áa]xima\s*de\s*opera[çc][ãa]o(?:\s*da\s*entrada|\s*FV))[^\n]*', layout_text, re.IGNORECASE)
        if pv_i_line:
            pv_i_matches = re.findall(r'\d+(?:\.\d+)?(?:\+\d+(?:\.\d+)?)+|\d+(?:\.\d+)?', pv_i_line.group(0))
            pv_i_max = pv_i_matches[-1] if pv_i_matches else '36+36'
        else:
            pv_i_max = find_val(r'(?:Max\.\s*Operating\s*PV\s*Input\s*Current|Corrente\s*m[áa]xima\s*de\s*opera[çc][ãa]o)[^\d]*([0-9.+]+)', '36+36')

        pv_isc_line = re.search(r'(?:Max\.\s*Input\s*Short[\s\-]*Circuit\s*Current|Corrente\s*m[áa]xima\s*de\s*curto[\s\-]*circuito)[^\n]*', layout_text, re.IGNORECASE)
        if pv_isc_line:
            pv_isc_matches = re.findall(r'\d+(?:\.\d+)?(?:\+\d+(?:\.\d+)?)+|\d+(?:\.\d+)?', pv_isc_line.group(0))
            pv_isc = pv_isc_matches[-1] if pv_isc_matches else '54+54'
        else:
            pv_isc = find_val(r'(?:Max\.\s*Input\s*Short[\s\-]*Circuit\s*Current|Corrente\s*m[áa]xima\s*de\s*curto[\s\-]*circuito)[^\d]*([0-9.+]+)', '54+54')

        # MPPT Trackers / Strings por MPPT (restrito à seção de entrada FV antes da seção CA)
        pv_section_m = re.search(r'(?:No\.\s*of\s*MPP\s*Trackers|N[úu]mero\s*de\s*rastreadores\s*MPP|N[º°]\s*de\s*Rastreadores\s*MPP).*?(?:AC\s*Input|AC\s*Output|Dados\s*de\s*Sa[íi]da)', layout_text, re.IGNORECASE | re.DOTALL)
        pv_section_txt = pv_section_m.group(0) if pv_section_m else layout_text
        mppt_matches = re.findall(r'(?<![\d\-])(\d+)\s*/\s*([0-9+]+)', pv_section_txt)
        valid_mppt = [(m, s) for m, s in mppt_matches if int(m) <= 16 and ("+" in s or int(s) <= 4)]
        if valid_mppt:
            mppt_count, strings_per_mppt = valid_mppt[-1]
        else:
            mppt_count, strings_per_mppt = ('2', '1+2')

        num_strings = calculate_total_strings(mppt_count, strings_per_mppt)

        eff_list = re.findall(r'(\d{2}\.\d{1,2})\s*%', layout_text)
        efficiency = (eff_list[0] + "%") if eff_list else '97.6%'

        bat_cur_list = []
        bat_c_line = re.search(r'(?:Max\.\s*Charging(?:/Discharging)?\s*Current|Corrente\s*(?:M[áa]x\.\s*de\s*Carga|de\s*carga\s*m[áa]xima))\s*\(A\)([^\n]+)', layout_text, re.IGNORECASE)
        if bat_c_line:
            for tok in re.findall(r'\d+(?:\+\d+)*', bat_c_line.group(1)):
                bat_cur_list.append(sum(int(x) for x in tok.split('+')))

        bat_cur_m = re.search(r'(?:current of|carga e descarga de at[ée])\s*(\d+)\s*A', raw_text, re.IGNORECASE)
        if bat_cur_m:
            bat_cur = int(bat_cur_m.group(1))
        elif bat_cur_list:
            bat_cur = bat_cur_list[-1]
        else:
            bat_cur = 260

        pv_acc_list = []
        pv_acc_line = re.search(r'(?:Max\.\s*PV\s*Access\s*Power|Pot[êe]ncia\s*m[áa]xima\s*de\s*acesso\s*fotovoltaico|Pot[êe]ncia\s*M[áa]x\.\s*Acesso\s*FV)\s*\((k?W)\)([^\n]+)', layout_text, re.IGNORECASE)
        if pv_acc_line:
            mult_acc = 1000.0 if pv_acc_line.group(1).lower() == 'kw' else 1.0
            pv_acc_list = [int(float(x) * mult_acc) for x in re.findall(r'\d+(?:\.\d+)?', pv_acc_line.group(2))]

        pv_in_list = []
        pv_in_line = re.search(r'(?:Max\.\s*PV\s*Input\s*Power|Pot[êe]ncia\s*m[áa]xima\s*CC|Pot[êe]ncia\s*M[áa]x\.\s*de\s*Entrada\s*FV)\s*\((k?W)\)([^\n]+)', layout_text, re.IGNORECASE)
        if pv_in_line:
            mult_in = 1000.0 if pv_in_line.group(1).lower() == 'kw' else 1.0
            pv_in_list = [int(float(x) * mult_in) for x in re.findall(r'\d+(?:\.\d+)?', pv_in_line.group(2))]

        first_model = models[0]
        first_info = parse_model(first_model)
        v_nom_str, _ = get_voltage_ratings(first_model)
        t_transf = get_transfer_time(first_model)

        common_specs = {
            "dim_l": dim_l,
            "dim_a": dim_a,
            "dim_p": dim_p,
            "peso": peso,
            "mppt_count": mppt_count,
            "mppt_v_min": mppt_min,
            "mppt_v_max": mppt_max,
            "pv_v_start": pv_start,
            "pv_v_max": pv_v_max,
            "pv_i_max": pv_i_max,
            "pv_isc_max": pv_isc,
            "strings_per_mppt": f"{mppt_count} / {strings_per_mppt}",
            "num_strings": num_strings,
            "bat_v_min": bat_min,
            "bat_v_max": bat_max,
            "nominal_voltage": v_nom_str,
            "grid_freq": "60 Hz",
            "efficiency": efficiency,
            "transfer_time": t_transf,
            "fases": first_info.get("phase_type", "Trifásico")
        }

        models_data = []
        for idx, m in enumerate(models):
            info = parse_model(m)
            pw_w = info.get("power_w", 10000)
            pw_kw = pw_w / 1000.0 if pw_w else 10.0
            v_nom = info.get("nominal_voltage_ac", 380)
            phase = info.get("phase_type", "Trifásico")
            i_nom, i_max = calculate_ac_currents(pw_w, v_nom, phase)

            # Associa MPPT pelo sufixo M<N> do modelo (ex: GM8 -> 8, GM10 -> 10) ou pela coluna correspondente
            m_suf = re.search(r'[A-Z]M(\d+)\b', m.upper())
            matched_mppt = None
            if m_suf:
                target_m = m_suf.group(1)
                for vm, vs in valid_mppt:
                    if vm == target_m:
                        matched_mppt = (vm, vs)
                        break
            if not matched_mppt:
                matched_mppt = valid_mppt[idx] if idx < len(valid_mppt) else (mppt_count, strings_per_mppt)
            m_mppt, m_str = matched_mppt
            m_num_strings = calculate_total_strings(m_mppt, m_str)

            m_bat_cur = bat_cur_list[idx] if idx < len(bat_cur_list) else bat_cur
            m_pv_acc = pv_acc_list[idx] if idx < len(pv_acc_list) else int(pw_w * 2.0)
            m_pv_in = pv_in_list[idx] if idx < len(pv_in_list) else int(pw_w * (1.5 if ("G0" in m or "SG04" in m) else 1.6))

            models_data.append({
                "model": m,
                "power_kw": int(pw_kw) if float(pw_kw).is_integer() else pw_kw,
                "power_w": pw_w,
                "ac_nom_current": i_nom,
                "ac_max_current": i_max,
                "bat_current": m_bat_cur,
                "max_pv_access_w": m_pv_acc,
                "max_pv_input_w": m_pv_in,
                "mppt_count": m_mppt,
                "num_strings": m_num_strings,
                "strings_per_mppt": f"{m_mppt} / {m_str}"
            })

        return models_data, common_specs

    def build_pet_dictionary(self, model_info, common_specs, custom_date=None):
        """
        Monta o dicionário completo de substituição das tags {{ pet.* }} tanto para Híbrido quanto On-Grid.
        """
        now = custom_date or datetime.datetime.now()
        data_str = now.strftime("%d/%m/%Y")
        ano_str = str(now.year)

        model_name = model_info["model"]
        is_hybrid = "SG" in model_name.upper()

        pw_kw = model_info["power_kw"]
        pw_w = model_info["power_w"]
        pw_w_str = f"{pw_w:,}".replace(",", ".") + " W"
        pw_kw_str = f"{pw_kw} kW"

        t_transf = get_transfer_time(model_name)
        v_nom, v_outras = get_voltage_ratings(model_name)
        fv_corrente_max = extract_max_pv_current(common_specs.get("pv_i_max", ""))

        raw_str_expr = model_info.get("num_strings") or common_specs.get("num_strings") or common_specs.get("strings_per_mppt", "2")
        num_strings = calculate_total_strings(common_specs.get("mppt_count", "2"), raw_str_expr)

        # Formatação específica se for On-Grid vs Híbrido
        eff_val = common_specs.get("efficiency", "98.0%")
        if not is_hybrid:
            eff_clean = eff_val.replace("%", "").replace(".", ",").strip()
            v_nom_clean = v_nom.replace("V", "").strip()
            v_out_clean = "133/230" if "-LV" in model_name.upper() else v_outras.replace("V", "").strip()
            freq_clean = "60"
            peso_clean = str(common_specs["peso"]).replace(".", ",")
            i_nom_clean = str(model_info["ac_nom_current"]).replace(".", ",")
            tensoes_cc_str = f"{common_specs['pv_v_start']}V - {common_specs['mppt_v_max']}V"
        else:
            eff_clean = eff_val
            v_nom_clean = v_nom
            v_out_clean = v_outras
            freq_clean = common_specs.get("grid_freq", "60 Hz")
            peso_clean = str(common_specs["peso"])
            i_nom_clean = str(model_info["ac_nom_current"])
            tensoes_cc_str = f"{common_specs['mppt_v_min']}-{common_specs['mppt_v_max']} V"

        pet = {
            "ano": ano_str,
            "data": data_str,
            "nome_modelo": model_name,
            "modelo": model_name,
            "fases": common_specs.get("fases", "Trifásico"),
            "tensoes_cc": tensoes_cc_str,
            "pot_ca": pw_w_str,
            "pot_w": str(pw_w),
            "faixa_pot_rede": pw_kw_str,
            "comp": common_specs["dim_l"] if not is_hybrid else common_specs["dim_p"],
            "alt": common_specs["dim_a"],
            "larg": common_specs["dim_p"] if not is_hybrid else common_specs["dim_l"],
            "peso": peso_clean,
            "fv_num_spmp": model_info.get("mppt_count") or common_specs["mppt_count"],
            "fv_spmp_tens_min": common_specs["mppt_v_min"],
            "fv_spmp_tens_max": common_specs["mppt_v_max"],
            "fv_oper_tens_min": common_specs["pv_v_start"],
            "fv_oper_tens_max": common_specs["mppt_v_max"] if not is_hybrid else common_specs["pv_v_max"],
            "fv_corrente_max": fv_corrente_max,
            "fv_lim_tens_max": common_specs["pv_v_max"],
            "bat_tens_min": common_specs["bat_v_min"],
            "bat_tens_max": common_specs["bat_v_max"],
            "bat_corrente_carga": str(model_info.get("bat_current", 260)),
            "bat_corrente_descarga": str(model_info.get("bat_current", 260)),
            "tensao_nom": v_nom_clean,
            "outras_tensoes": v_out_clean,
            "freq": freq_clean,
            "carga_pot_nom_kw": str(pw_kw),
            "carga_corrente_max": str(model_info["ac_max_current"]),
            "rede_inj_pot_kw": str(pw_kw),
            "rede_inj_corrente": i_nom_clean,
            "rede_abs_pot_kw": str(pw_kw),
            "rede_abs_corrente": str(model_info["ac_nom_current"]),
            "tempo_transf": t_transf,
            "eficiencia": eff_clean,
            "num_strings": num_strings
        }
        return pet

    def generate_single_pet(self, model_info, common_specs, output_docx_path, convert_pdf=True):
        """
        Gera um arquivo PET individual (.docx) aplicando o dicionário de dados sobre o template.
        Se convert_pdf=True, converte imediatamente para PDF; quando chamado por generate_all,
        a conversão é feita em lote único para todos os modelos de uma só vez.
        """
        pet_dict = self.build_pet_dictionary(model_info, common_specs)
        out_dir = Path(output_docx_path).parent
        out_dir.mkdir(parents=True, exist_ok=True)

        tpl_path = self.get_template_for_model(model_info["model"])

        with zipfile.ZipFile(tpl_path, "r") as zin:
            with zipfile.ZipFile(output_docx_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    content = zin.read(item.filename)
                    if item.filename == "word/document.xml":
                        xml_str = content.decode("utf-8")

                        # 1. Previne quebra de linha nas células da linha de dados
                        pos = xml_str.find("{{ pet.modelo }}")
                        if pos != -1:
                            row_start = xml_str.rfind("<w:tr ", 0, pos)
                            if row_start == -1:
                                row_start = xml_str.rfind("<w:tr>", 0, pos)
                            row_end = xml_str.find("</w:tr>", pos)
                            if row_start != -1 and row_end != -1:
                                row_end += len("</w:tr>")
                                row_xml = xml_str[row_start:row_end]
                                if "<w:noWrap/>" not in row_xml:
                                    mod_row = re.sub(r"(<w:vAlign\b[^>]*>)", r"<w:noWrap/>\1", row_xml)
                                    mod_row = re.sub(r"(<w:tcPr>)(?!.*?<w:noWrap/>)", r"\1<w:noWrap/>", mod_row)
                                else:
                                    mod_row = row_xml
                                mod_row = mod_row.replace('<w:sz w:val="16"/>', '<w:sz w:val="11"/>')
                                mod_row = mod_row.replace('<w:sz w:val="14"/>', '<w:sz w:val="11"/>')
                                xml_str = xml_str[:row_start] + mod_row + xml_str[row_end:]

                        # 2. Garante dados de contato corporativos oficiais Deye Brasil
                        is_hybrid_dev = "SG" in model_info["model"].upper()
                        target_phone = "+55 (11) 2500-3700" if is_hybrid_dev else "+55 (11) 2500-0681"
                        if is_hybrid_dev:
                            xml_str = xml_str.replace("+55 (11) 2500-0681", target_phone)
                            xml_str = xml_str.replace("(11) 2500-0681", "(11) 2500-3700")

                        xml_str = xml_str.replace("xxxxxx@xxxxx.xxx.xx", "suporte@deyeinversores.com.br")
                        xml_str = xml_str.replace("&lt;xxxxxx@xxxxx.xxx.xx&gt;", "suporte@deyeinversores.com.br")
                        xml_str = xml_str.replace("<xxxxxx@xxxxx.xxx.xx>", "suporte@deyeinversores.com.br")
                        xml_str = xml_str.replace("suporte@deyebrasil.com.br", "suporte@deyeinversores.com.br")

                        # 3. Garante a data vigente nos campos de aprovação e última revisão
                        xml_str = re.sub(r'APROVAÇÃO:\s*xx/xx/[^<\s]+', f'APROVAÇÃO: {pet_dict["data"]}', xml_str)
                        xml_str = re.sub(r'ÚLTIMA REVISÃO:\s*xx/xx/[^<\s]+', f'ÚLTIMA REVISÃO: {pet_dict["data"]}', xml_str)

                        # 4. Elimina quebras de seção espúrias e garante orientação landscape
                        xml_str = re.sub(r'(<w:pPr>[^<]*?)<w:sectPr\b[^>]*>.*?</w:sectPr>', r'\1', xml_str)
                        xml_str = re.sub(r'(<w:sectPr[^>]*>.*?<w:pgSz\b[^>]*?)(w:orient="[^"]*")?', r'\1 w:orient="landscape"', xml_str)

                        # 5. Substitui as variáveis Jinja com escape de caracteres XML
                        for k, v in pet_dict.items():
                            escaped_val = saxutils.escape(str(v))
                            pattern = re.compile(r"\{\{\s*pet\." + re.escape(k) + r"\s*\}\}")
                            xml_str = pattern.sub(escaped_val, xml_str)

                        content = xml_str.encode("utf-8")
                    zout.writestr(item, content)

        out_pdf = Path(output_docx_path).with_suffix(".pdf")
        if convert_pdf:
            self.generate_single_pet_pdf(model_info, common_specs, out_pdf)

        return output_docx_path, out_pdf

    def generate_single_pet_pdf(self, model_info, common_specs, output_pdf_path):
        """
        Gera diretamente o arquivo PET oficial em PDF usando pdfcraft-cli sobre o template base.
        Substitui 100% o LibreOffice com velocidade sub-segundo (~0.12s por arquivo).
        """
        pet_dict = self.build_pet_dictionary(model_info, common_specs)
        is_hybrid = "SG" in model_info["model"].upper()
        tpl_pdf = resolve_template_path("BASE_PET.pdf" if is_hybrid else "BASE_PET_ONGRID.pdf")

        if not tpl_pdf.exists():
            print(f"⚠️ Template PDF não encontrado: {tpl_pdf}. Gerando apenas DOCX.")
            return str(output_pdf_path)

        out_pdf = Path(output_pdf_path)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)

        steps = [
            {"tool": "doc_open", "args": {"path": str(tpl_pdf)}},
            {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 4, "text": f"APROVAÇÃO: {pet_dict['data']}"}},
            {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 8, "text": f"ÚLTIMA REVISÃO: {pet_dict['data']}"}},
            {"tool": "text_edit", "args": {"doc": 1, "page": 1, "line": 29, "text": "Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP, 08773-120"}}
        ]

        if is_hybrid:
            steps.append({"tool": "text_edit", "args": {"doc": 1, "page": 1, "line": 31, "text": "+55 (11) 2500-3700"}})
            steps.append({
                "tool": "text_edit",
                "args": {
                    "doc": 1, "page": 1, "paragraph": 13,
                    "text": (
                        f"3 IDENTIFICAÇÃO DO MODELO DE INVERSORES ON-GRID COM BATERIA\n"
                        f"Nome do modelo de inversor on-grid com bateria: {pet_dict['nome_modelo']}\n"
                        f"Unidade fabril: No.26, Southern Yang Jiang Road Beilun, NingBo, China                        Marca: Deye\n"
                        f"Número de fases: {pet_dict['fases']}                                                       Tensões de operação c.c.: {pet_dict['tensoes_cc']}                                  Potência nominal c.a.: {pet_dict['pot_ca']}\n"
                        f"Tipo de isolação galvânica em baixa frequência no lado da rede: Sem Transformador              Número de fases de fornecimento de energia às cargas c.a.: {pet_dict['fases']}\n"
                        f"Modos de operação quanto à conexão à rede:  injeta e absorve energia da rede\n"
                        f"Tipo de sistema de armazenamento de energia: externo ao equipamento                          Tecnologia das baterias: Chumbo-Ácido ou LiFePO4(Lítio)\n"
                        f"Faixa de potência nominal de conexão com a rede (modo injeção de energia): {pet_dict['faixa_pot_rede']}                                               Tecnologia de fornecimento de energia às cargas c.a.: modo interativo com a rede"
                    )
                }
            })
            # Tabela Linha 11 - Híbrido
            steps.extend([
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 59, "text": pet_dict["modelo"], "size": 5.0}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 60, "text": pet_dict["comp"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 61, "text": pet_dict["alt"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 62, "text": pet_dict["larg"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 63, "text": pet_dict["peso"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 64, "text": pet_dict["fv_num_spmp"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 65, "text": pet_dict["fv_spmp_tens_min"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 66, "text": pet_dict["fv_spmp_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 67, "text": pet_dict["fv_oper_tens_min"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 68, "text": pet_dict["fv_oper_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 69, "text": pet_dict["fv_corrente_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 70, "text": pet_dict["fv_lim_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 71, "text": pet_dict["bat_tens_min"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 72, "text": pet_dict["bat_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 73, "text": pet_dict["bat_corrente_carga"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 74, "text": pet_dict["bat_corrente_descarga"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 75, "text": pet_dict["tensao_nom"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 76, "text": pet_dict["freq"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 77, "text": pet_dict["carga_pot_nom_kw"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 78, "text": pet_dict["carga_corrente_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 79, "text": pet_dict["tensao_nom"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 80, "text": pet_dict["outras_tensoes"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 81, "text": pet_dict["freq"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 82, "text": pet_dict["rede_inj_pot_kw"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 83, "text": pet_dict["rede_inj_corrente"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 84, "text": pet_dict["rede_abs_pot_kw"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 85, "text": pet_dict["rede_abs_corrente"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 86, "text": pet_dict["tempo_transf"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 87, "text": pet_dict["eficiencia"], "size": 5.5}}
            ])
            steps.append({
                "tool": "text_edit",
                "args": {
                    "doc": 1, "page": 1, "paragraph": 91,
                    "text": (
                        "Funcionalidade: Detecção e Interrupção de Arco – AFPE\n"
                        f"Número de strings, entradas e canais monitorados: {pet_dict['num_strings']}\n"
                        "Método de reconexão: Manual"
                    )
                }
            })
        else:
            # On-Grid
            steps.append({"tool": "text_edit", "args": {"doc": 1, "page": 1, "line": 31, "text": "+55 (11) 2500-0681"}})
            steps.append({
                "tool": "text_edit",
                "args": {
                    "doc": 1, "page": 1, "paragraph": 13,
                    "text": (
                        f"3 IDENTIFICAÇÃO DO MODELO DE INVERSORES ON-GRID\n"
                        f"Nome do modelo de inversor on-grid: {pet_dict['nome_modelo']}\n"
                        f"Unidade fabril: No.26, Southern Yang Jiang Road Beilun, NingBo, China          Marca: Deye\n"
                        f"Número de fases: {pet_dict['fases']}                                         Tensões de operação c.c. (mín. e máx.): {pet_dict['tensoes_cc']}\n"
                        f"Tipo de isolação galvânica em baixa frequência no lado da rede: Sem Transformador"
                    )
                }
            })
            # Tabela Linha 11 - On-Grid
            steps.extend([
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 40, "text": pet_dict["modelo"], "size": 5.0}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 41, "text": pet_dict["comp"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 42, "text": pet_dict["alt"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 43, "text": pet_dict["larg"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 44, "text": pet_dict["peso"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 45, "text": pet_dict["fv_num_spmp"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 46, "text": pet_dict["fv_spmp_tens_min"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 47, "text": pet_dict["fv_spmp_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 48, "text": pet_dict["fv_oper_tens_min"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 49, "text": pet_dict["fv_oper_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 50, "text": pet_dict["fv_corrente_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 51, "text": pet_dict["fv_lim_tens_max"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 52, "text": pet_dict["tensao_nom"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 53, "text": pet_dict["outras_tensoes"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 54, "text": pet_dict["freq"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 55, "text": pet_dict["pot_w"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 56, "text": pet_dict["rede_inj_corrente"], "size": 5.5}},
                {"tool": "text_edit", "args": {"doc": 1, "page": 1, "paragraph": 57, "text": pet_dict["eficiencia"], "size": 5.5}}
            ])
            steps.append({
                "tool": "text_edit",
                "args": {
                    "doc": 1, "page": 1, "paragraph": 61,
                    "text": (
                        "Funcionalidade: Detecção e Interrupção de Arco – AFPE\n"
                        f"Número de strings, entradas e canais monitorados: {pet_dict['num_strings']}\n"
                        "Método de reconexão: Manual"
                    )
                }
            })

        # Página 2 - Data
        steps.append({"tool": "text_edit", "args": {"doc": 1, "page": 2, "line": 1, "text": pet_dict["data"]}})
        steps.append({"tool": "doc_save", "args": {"doc": 1, "path": str(out_pdf)}})

        temp_dir = str(get_fast_temp_dir())
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=temp_dir, delete=False, encoding="utf-8") as f:
            json.dump(steps, f)
            sp = f.name

        try:
            res = run_pdfcraft(["run", "--script", sp, "--report"])
            if not res or res.returncode != 0:
                print(f"Erro pdfcraft na PET {model_info['model']}: {res.stderr if res else ''}")
        finally:
            try:
                os.remove(sp)
            except OSError:
                pass

        return str(out_pdf)

    def generate_all(self, pdf_path, output_dir, custom_models_data=None, custom_common_specs=None, convert_pdf=True):
        """
        Gera todas as planilhas PET (.docx e .pdf) diretamente via Python e pdfcraft-cli.
        Totalmente livre de LibreOffice, com execução em lote paralela sub-segundo.
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if custom_models_data and custom_common_specs:
            models_data = custom_models_data
            common_specs = custom_common_specs
        else:
            models_data, common_specs = self.extract_models_data_from_pdf(pdf_path)

        generated_files = []
        print(f"--> [MÓDULO 2 - PET] Gerando {len(models_data)} planilhas técnicas INMETRO (.docx e .pdf) via pdfcraft-cli...")

        # 1. Gera todos os DOCX via zipfile em memória (0.05s)
        for m in models_data:
            model_name = m["model"]
            out_docx = out_dir / f"PET - {model_name}.docx"
            out_pdf = out_dir / f"PET - {model_name}.pdf"
            self.generate_single_pet(m, common_specs, out_docx, convert_pdf=False)
            generated_files.extend([str(out_docx), str(out_pdf)])

        # 2. Gera todos os PDFs via pdfcraft-cli em paralelo (sub-segundo)
        if convert_pdf:
            def _gen_pdf(m):
                out_pdf = out_dir / f"PET - {m['model']}.pdf"
                self.generate_single_pet_pdf(m, common_specs, out_pdf)

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(models_data))) as pool:
                list(pool.map(_gen_pdf, models_data))

        print(f"✅ [MÓDULO 2 - PET] Todas as planilhas PET (.docx e .pdf) foram geradas com sucesso em: {out_dir}")
        return generated_files, None


if __name__ == "__main__":
    default_src = Path.home() / "Documentos" / "SG05LP3-EU-SM2.pdf"
    default_out = Path.home() / "Documentos" / "SG05LP3-EU-SM2"
    src_pdf = sys.argv[1] if len(sys.argv) > 1 else str(default_src)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else str(default_out)

    gen = DeyePetGenerator()
    gen.generate_all(src_pdf, out_dir)
