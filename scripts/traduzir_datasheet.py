#!/usr/bin/env python3
"""
Script de tradução de datasheets Deye para o padrão brasileiro via pdfcraft-cli.
"""
import sys
import os
import json
import subprocess

def traduzir_datasheet(pdf_in, out_dir, models_custom=None):
    os.makedirs(out_dir, exist_ok=True)
    base_name = os.path.basename(pdf_in).replace(".pdf", "")
    pdf_out = os.path.join(out_dir, f"Datasheet_{base_name}_PT-BR.pdf")

    # Mapeamento da Página 1
    page1_edits = {
        1: "Inversor Híbrido Trifásico\nSUN-14/15/16/18/20K-SG05LP3-EU-SM2",
        4: "Seguro",
        2: "• Bateria de baixa tensão 48V, isolação galvânica",
        9: "Inteligente",
        3: "• 6 períodos programáveis para carga/descarga",
        7: "Flexível",
        5: "• Acoplamento CA para retrofit em sistemas solares existentes",
        6: "• Até 10 unidades em paralelo (on-grid e off-grid); suporta múltiplos bancos de baterias",
        8: "Confiável",
        10: "• Corrente máx. de carga/descarga de 350A",
        11: "• Suporta armazenamento de gerador a diesel",
        12: "• Saída 100% desbalanceada, até 50% da potência nominal por fase"
    }

    # Mapeamento da Página 2
    page2_edits = {
        2: "Dados Técnicos",
        3: "Deye Support Center Brasil\nEnd: Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP | Tel: (11) 2500-0681 | E-mail: suporte@deyebrasil.com.br",
        4: "Dados de Entrada da Bateria",
        5: "Modelo",
        13: "Tipo de Bateria\nFaixa de Tensão da Bateria (V)\nCorrente Máx. de Carga/Descarga (A)\nEstratégia de Carga p/ Bateria de Lítio\nNúmero de Entradas de Bateria",
        44: "Chumbo-ácido ou Íons de lítio\n40-60",
        14: "Autoadaptação ao BMS\n2",
        39: "Dados de Entrada FV\nPotência Máx. de Acesso FV (W)\nPotência Máxima CC (W)",
        42: "Tensão Máxima CC (V)",
        40: "Tensão de Partida (V)",
        43: "Faixa de Tensão MPPT (V)\nTensão Nominal CC (V)\nCorrente Máx. de Operação FV (A)\nCorrente Máx. de Curto-Circuito (A)",
        41: "Nº de Rastreadores MPP /\nNº de Strings por MPPT",
        16: "Dados de Saída / Entrada CA",
        15: "Potência Ativa Nominal CA (W)\nPotência Aparente Máx. CA (VA)\nCorrente Nominal CA (A)\nCorrente Máx. CA (A)",
        49: "21.3",
        48: "23.4",
        55: "22.8",
        54: "25.0",
        67: "24.3",
        66: "26.7",
        73: "27.3",
        72: "30.0",
        61: "30.4",
        60: "33.4",
        17: "Corrente Máx. Contínua de Passagem (Rede para Carga) (A)\nPotência de Pico (Off-Grid) (W)\nFaixa de Ajuste do Fator de Potência",
        7: "70\n2 vezes a potência nominal, 10s\n0,8 indutivo a 0,8 capacitivo",
        19: "Tensão Nominal de Entrada/Saída\nFrequência Nominal da Rede / Faixa (Hz)",
        9: "220/380V 0.85Un-1.1Un 3L+N+PE\n60/55-65",
        18: "Distorção Harmônica Total THDi\nCorrente de Injeção CC",
        8: "<3% (da potência nominal)\n<0,5% In",
        20: "Eficiência\nEficiência Máxima\nEficiência Euro\nEficiência MPPT",
        10: "Proteção de Equipamento\nIntegrado\nNível de Proteção contra Surtos",
        11: "TIPO II (CC), TIPO II (CA)",
        12: "Proteção contra Polaridade Reversa CC, Proteção contra Sobrecorrente CA, Proteção Térmica,\nProteção contra Sobretensão CA, Proteção contra Curto-Circuito CA, Monitoramento de Componente CC,\nInterruptor de Falha de Arco (AFCI)(opcional), Proteção Anti-ilhamento, Seccionadora CC,\nDetecção de Impedância de Isolamento, Detecção de Corrente Residual",
        23: "Interface",
        77: "Display LCD/LED",
        76: "Interface de Comunicação",
        35: "Dados Gerais",
        24: "Faixa de Temperatura Operacional (℃)\nUmidade Ambiente Permitida\nAltitude Permitida\nRuído (dB)\nGrau de Proteção (IP)\nTopologia do Inversor\nCategoria de Sobretensão",
        25: "-40 a +60℃, Redução >45℃\n0-100%\n3000m\n<60",
        26: "Não isolado (solar), Isolado (bateria)",
        27: "Refrigeração de Ar Inteligente",
        29: "IP 65",
        37: "Dimensões (L x A x P mm)\nPeso (kg)\nTipo de Refrigeração\nGarantia",
        30: "456×750×268.5 (Excluindo conectores e suportes)",
        32: "5 Anos / Até 10 Anos*",
        33: "*Consulte a política de garantia da Deye Brasil para maiores detalhes.\nIEC 61727, IEC 62116, CEI 0-21, EN 50549, NRS 097, RD 140, UNE 217002,\nOVE-Richtlinie R25, G99, VDE-AR-N 4105",
        36: "Normas de Conexão à Rede",
        38: "Normas de Segurança / EMC"
    }

    def apply_edits(current_pdf, page, edits, target_pdf):
        steps = [{"tool": "doc_open", "args": {"path": current_pdf}}]
        for pid in sorted(edits.keys(), reverse=True):
            steps.append({
                "tool": "text_edit",
                "args": {
                    "doc": 1,
                    "page": page,
                    "paragraph": pid,
                    "text": edits[pid]
                }
            })
        steps.append({"tool": "doc_save", "args": {"doc": 1, "path": target_pdf}})
        
        script_path = f"/tmp/steps_p{page}.json"
        with open(script_path, "w") as f:
            json.dump(steps, f)
            
        res = subprocess.run(["pdfcraft-cli", "run", "--script", script_path, "--report"], capture_output=True, text=True)
        report = json.loads(res.stdout)
        return report.get("ok", False)

    temp_p1 = "/tmp/datasheet_p1.pdf"
    if apply_edits(pdf_in, 1, page1_edits, temp_p1):
        if apply_edits(temp_p1, 2, page2_edits, pdf_out):
            return pdf_out
    return None

if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else "/home/oem/Documentos/SG05LP3-EU-SM2.pdf"
    out = sys.argv[2] if len(sys.argv) > 2 else "/home/oem/Documentos/SG05LP3-EU-SM2"
    res = traduzir_datasheet(pdf, out)
    if res:
        print(f"Sucesso: {res}")
    else:
        print("Falha ao gerar o PDF traduzido.")
        sys.exit(1)
