#!/usr/bin/env python3
"""
MÓDULO 3: Gerador da Declaração de Conformidade do Fornecedor (DCONF) (.pdf).
Gera o documento oficial DCONF preenchido para a família de inversores a partir do BASE_DCONF.pdf.
Utiliza pdfcraft-cli em modo Batch (leitura única + gravação única em memória) para execução sub-segundo.
"""
import sys
import os
import re
import json
import shutil
import datetime
import subprocess
import tempfile
from pathlib import Path

# Adiciona o diretório raiz dos scripts ao sys.path para importar módulos comuns
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.deye_grammar import derive_family_name, get_dconf_family_desc, format_family_folder_name
from modules.mod_pet import DeyePetGenerator

MESES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
]

class DeyeDconfGenerator:
    def __init__(self, template_path=None):
        if template_path is None:
            skill_root = Path(__file__).resolve().parent.parent.parent
            template_path = skill_root / "resources" / "templates" / "BASE_DCONF.pdf"
            if not template_path.exists():
                template_path = Path("/home/oem/Documentos/BASE_DCONF.pdf")
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template DCONF não encontrado em: {self.template_path}")

    def _run_pdfcraft(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(script, f)
            script_path = f.name

        cmd = ["pdfcraft-cli", "run", "--script", script_path, "--report"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        try:
            os.remove(script_path)
        except OSError:
            pass

        if p.returncode != 0:
            print(f"[pdfcraft ERRO]: {p.stderr}")
            return None
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            return None

    def get_lines(self, pdf_file, page=1):
        steps = [
            {"tool": "doc_open", "args": {"path": str(pdf_file)}},
            {"tool": "text_lines", "args": {"doc": 1, "page": page}}
        ]
        rep = self._run_pdfcraft(steps)
        if rep and rep.get("ok"):
            return rep["steps"][1]["result"][0]["lines"]
        return []

    def get_paragraphs(self, pdf_file, page=1):
        steps = [
            {"tool": "doc_open", "args": {"path": str(pdf_file)}},
            {"tool": "text_paragraphs", "args": {"doc": 1, "page": page}}
        ]
        rep = self._run_pdfcraft(steps)
        if rep and rep.get("ok"):
            return rep["steps"][1]["result"][0]["paragraphs"]
        return []

    def generate(self, pdf_input, output_dir, custom_date=None, custom_model_names=None, folder_name=None):
        """
        Gera a DCONF oficial para a família de modelos encontrada no datasheet em lote único (Batch Mode).
        Nome do arquivo no padrão: DCONF - <Nome da pasta>.pdf
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        now = custom_date or datetime.datetime.now()
        dia_str = f"{now.day:02d}"
        mes_nome = MESES_PT[now.month - 1]
        ano_str = str(now.year)
        validade_dt = now + datetime.timedelta(days=365)
        validade_str = validade_dt.strftime("%d/%m/%Y")

        # 1. Extração de Modelos e Família
        if custom_model_names:
            model_names = custom_model_names
        else:
            pet_gen = DeyePetGenerator()
            models_data, _ = pet_gen.extract_models_data_from_pdf(pdf_input)
            model_names = [m["model"] for m in models_data]

        family_name = derive_family_name(model_names)
        family_desc = get_dconf_family_desc(family_name)
        folder_display_name = folder_name or format_family_folder_name(model_names)

        models_joined = ", ".join(model_names)
        models_text = f"Modelo: {models_joined}"

        print(f"--> [MÓDULO 3 - DCONF] Gerando Declaração de Conformidade (Batch Mode)...")
        print(f"    Família Identificada: {family_name} ({family_desc})")
        print(f"    Modelos Abrangidos  : {len(model_names)} modelos")
        print(f"    Data de Emissão     : {dia_str} de {mes_nome} de {ano_str}")
        print(f"    Data de Validade    : {validade_str}")

        out_pdf = out_dir / f"DCONF - {folder_display_name}.pdf"

        # Chamada 1: Leitura unificada de linhas e parágrafos
        read_steps = [
            {"tool": "doc_open", "args": {"path": str(self.template_path)}},
            {"tool": "text_lines", "args": {"doc": 1, "page": 1}},
            {"tool": "text_paragraphs", "args": {"doc": 1, "page": 1}},
        ]
        rep = self._run_pdfcraft(read_steps)
        if not rep or not rep.get("ok"):
            shutil.copy2(self.template_path, out_pdf)
            return str(out_pdf)

        lines = rep["steps"][1]["result"][0]["lines"]
        paras = rep["steps"][2]["result"][0]["paragraphs"]

        # Coleta todas as edições de linha e ordena de forma decrescente por índice de linha
        line_edits = []
        for l in lines:
            txt = l["text"]
            if "Familia:" in txt:
                line_edits.append((l["line"], {
                    "doc": 1, "page": 1, "line": l["line"],
                    "text": f" Familia: {family_desc}", "size": 9.0
                }))
            elif "Cruzes" in txt and l["rect"][1] > 640:
                line_edits.append((l["line"], {
                    "doc": 1, "page": 1, "line": l["line"],
                    "text": f"GOMES Mogi das Cruzes, {dia_str} de {mes_nome} "
                }))
            elif re.search(r"de 202\d", txt) and l["rect"][1] > 660:
                line_edits.append((l["line"], {
                    "doc": 1, "page": 1, "line": l["line"],
                    "text": f"de {ano_str}"
                }))
            elif re.search(r"\d{2}/\d{2}/\d{4}", txt) and l["rect"][1] < 750:
                line_edits.append((l["line"], {
                    "doc": 1, "page": 1, "line": l["line"],
                    "text": validade_str, "size": 10.0
                }))

        line_edits.sort(key=lambda x: x[0], reverse=True)

        # Chamada 2: Aplicação em lote único de todas as edições + salvamento direto
        write_steps = [{"tool": "doc_open", "args": {"path": str(self.template_path)}}]
        for _, args in line_edits:
            write_steps.append({"tool": "text_edit", "args": args})

        for p in paras:
            if "Modelo:" in p["text"] and "Escopo" not in p["text"]:
                write_steps.append({
                    "tool": "text_edit",
                    "args": {
                        "doc": 1, "page": 1, "paragraph": p["paragraph"],
                        "text": models_text, "width": 480.0, "size": 8.5
                    }
                })
                break

        write_steps.append({"tool": "doc_save", "args": {"doc": 1, "path": str(out_pdf)}})
        self._run_pdfcraft(write_steps)

        print(f"✔ [MÓDULO 3 - DCONF] Documento gerado com sucesso: {out_pdf.name}")
        return str(out_pdf)

if __name__ == "__main__":
    src_pdf = "/home/oem/Documentos/SG05LP3-EU-SM2.pdf"
    out_dir = "/home/oem/Documentos/SG05LP3-EU-SM2"
    if len(sys.argv) > 1:
        src_pdf = sys.argv[1]
    if len(sys.argv) > 2:
        out_dir = sys.argv[2]

    gen = DeyeDconfGenerator()
    gen.generate(src_pdf, out_dir)
