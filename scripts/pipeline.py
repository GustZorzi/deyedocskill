#!/usr/bin/env python3
"""
Pipeline Mestre da Skill: deye-datasheet-docs.
Orquestra os 3 módulos de homologação com fluxo interativo guiado e amigável:
1. Objetivo:
   - [1] Todos os Modelos (Traduzir e adequar todos os modelos do datasheet)
   - [2] Modelo Único (Filtrar e focar em 1 modelo específico)
   - [3] Derivar / Criar Novos Modelos (a partir de PT-BR ou EN com perguntas guiadas)
2. Entregáveis Selecionáveis:
   - [1] Pacote Completo (Datasheet PDF, PET DOCX/PDF, DCONF PDF)
   - [2] Apenas Datasheet (PDF)
   - [3] Apenas Planilhas Técnicas PET INMETRO (DOCX + PDF)
   - [4] Apenas Declaração de Conformidade DCONF (PDF)
3. Modos de Execução:
   - Modo Automático / Direto (Geração rápida com valores padrão)
   - Modo Manual de Engenharia (Exibição da Tabela Resumo para conferência antes de gerar)
4. Padrões Corporativos e Regulatórios Deye Brasil:
   - Garantia de 10 Anos para todos os modelos (Híbridos, On-Grid e Microinversores)
   - E-mail unificado: suporte@deyeinversores.com.br
   - Telefones: (11) 2500-3700 (Híbridos) | (11) 2500-0681 (Demais)
   - Portaria INMETRO 140/2022 | Validade DCONF: 1 ano
"""
import sys
import os
import re
import time
import argparse
import datetime
from pathlib import Path

# Configuração de caminhos
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common.deye_grammar import (
    parse_model,
    derive_family_name,
    format_family_folder_name,
    get_transfer_time,
    get_voltage_ratings,
    get_dconf_family_desc,
    suggest_model_name,
    get_contact_info,
    WARRANTY_DEFAULT
)
from common.electrical_calc import calculate_ac_currents
from common.platform_utils import is_windows
from modules.mod_datasheet import SemanticDeyeEngine
from modules.mod_pet import DeyePetGenerator
from modules.mod_dconf import DeyeDconfGenerator

def prompt_choice(prompt_text, options, default=1):
    print(f"\n{prompt_text}")
    for idx, opt in enumerate(options, 1):
        star = " (Padrão)" if idx == default else ""
        print(f"  [{idx}] {opt}{star}")
    while True:
        try:
            val = input(f"Escolha [1-{len(options)}] (Enter={default}): ").strip()
            if not val:
                return default
            choice = int(val)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"Por favor, digite um número entre 1 e {len(options)}.")

def prompt_text(prompt_msg, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt_msg}{suffix}: ").strip()
    return val if val else default

def print_engineering_summary_table(models_data):
    """
    Exibe a tabela resumo de engenharia (Modo Manual) para validação do usuário.
    """
    print("\n" + "="*95)
    print("                    TABELA RESUMO DE ENGENHARIA - DEYE BRASIL")
    print("="*95)
    print(f"{'Modelo':<28} | {'Potência':<9} | {'Tensão CA':<11} | {'Fases':<11} | {'I_nom':<8} | {'I_max':<8} | {'EPS':<7}")
    print("-"*95)
    for m in models_data:
        m_name = m["model"]
        pw = f"{m['power_kw']} kW"
        v_nom = m.get("nominal_voltage_str") or str(m.get("nominal_voltage_ac", "220")) + "V"
        fases = m.get("phase_type", "Trifásico")
        i_nom = f"{m['ac_nom_current']} A"
        i_max = f"{m['ac_max_current']} A"
        eps = m.get("transfer_time", "< 10 ms")
        print(f"{m_name:<28} | {pw:<9} | {v_nom:<11} | {fases:<11} | {i_nom:<8} | {i_max:<8} | {eps:<7}")
    print("="*95)
    print(f"ℹ Garantia Padrão Deye: {WARRANTY_DEFAULT} | Portaria INMETRO: 140/2022")
    print("="*95 + "\n")

def build_model_specs(model_name, ref_specs, voltage_override=None, is_interactive=False):
    """
    Calcula automaticamente os parâmetros elétricos para um modelo e permite ajuste manual se solicitado.
    """
    grammar_info = parse_model(model_name, nominal_voltage_override=voltage_override)
    pw_w = grammar_info["power_w"]
    pw_kw = pw_w / 1000.0 if pw_w else 10.0
    v_nom = grammar_info["nominal_voltage_ac"]
    phase = grammar_info["phase_type"]
    transfer_t = grammar_info["transfer_time"]
    nom_v_str, out_v_str = get_voltage_ratings(model_name, nominal_voltage=v_nom)

    # Cálculo automático de corrente AC para o Brasil
    i_nom, i_max = calculate_ac_currents(pw_w, v_nom, phase)

    # Proporções de potência fotovoltaica a partir do modelo de referência
    ref_pw = ref_specs.get("power_w", 14000) or 14000
    ref_pv_acc = ref_specs.get("max_pv_access_w", 28000) or 28000
    ref_pv_in = ref_specs.get("max_pv_input_w", 22400) or 22400
    ratio_acc = ref_pv_acc / ref_pw if ref_pw else 2.0
    ratio_in = ref_pv_in / ref_pw if ref_pw else 1.6

    pv_access_w = int(round(pw_w * ratio_acc))
    pv_input_w = int(round(pw_w * ratio_in))
    bat_current = int(ref_specs.get("bat_current", 240))

    if is_interactive:
        print(f"\n⚡ [Cálculo Automático de Engenharia - {model_name}]")
        print(f"   • Potência Nominal   : {pw_w:,} W ({pw_kw} kW)".replace(",", "."))
        print(f"   • Número de Fases    : {phase}")
        print(f"   • Tensão CA Brasil   : {nom_v_str} (Outras tensões: {out_v_str})")
        print(f"   • Corrente Nominal CA: {i_nom} A")
        print(f"   • Corrente Máxima CA : {i_max} A")
        print(f"   • Tempo Transf. EPS  : {transfer_t}")
        print(f"   • Pot. Acesso FV     : {pv_access_w:,} W | Entrada FV: {pv_input_w:,} W".replace(",", "."))

        conf = input("   Pressione [Enter] para aceitar ou 'C' para corrigir valores: ").strip().lower()
        if conf == 'c':
            pw_in = prompt_text("   Digite a Potência Nominal (W)", str(pw_w))
            pw_w = int(pw_in)
            pw_kw = pw_w / 1000.0
            phase = prompt_text("   Digite o Número de Fases", phase)
            nom_v_str = prompt_text("   Digite a Tensão Nominal", nom_v_str)
            i_nom_in = prompt_text("   Digite a Corrente Nominal (A)", str(i_nom))
            i_nom = float(i_nom_in)
            i_max_in = prompt_text("   Digite a Corrente Máxima (A)", str(i_max))
            i_max = float(i_max_in)

    return {
        "model": model_name,
        "power_w": pw_w,
        "power_kw": int(pw_kw) if float(pw_kw).is_integer() else pw_kw,
        "nominal_voltage_ac": v_nom,
        "nominal_voltage_str": nom_v_str,
        "voltage_other_str": out_v_str,
        "phase_type": phase,
        "transfer_time": transfer_t,
        "ac_nom_current": i_nom,
        "ac_max_current": i_max,
        "bat_current": bat_current,
        "max_pv_access_w": pv_access_w,
        "max_pv_input_w": pv_input_w,
        "mppt_count": ref_specs.get("mppt_count"),
        "num_strings": ref_specs.get("num_strings", "2"),
        "strings_per_mppt": ref_specs.get("strings_per_mppt", "2 / 1+1")
    }

def main():
    parser = argparse.ArgumentParser(description="Esteira de Documentação Técnica Deye Brasil (Datasheet, PET e DCONF)")
    parser.add_argument("pdf_input", help="Caminho do datasheet base em PDF")
    parser.add_argument("--out-dir", default=None, help="Diretório base de saída (padrão: mesmo diretório do arquivo de entrada)")
    parser.add_argument("--mode", choices=["all", "single", "new", "translate"], default=None,
                        help="Modo: 'all' (Todos os modelos), 'single' (Modelo único) ou 'new' (Novo modelo)")
    parser.add_argument("--single-model", default=None, help="Nome do modelo específico para o modo 'single'")
    parser.add_argument("--reference", default=None, help="Índice ou nome do modelo de referência (para novo modelo)")
    parser.add_argument("--models", default=None, help="Lista de novos modelos separados por vírgula")
    parser.add_argument("--powers", default=None, help="Lista de potências em kW para novos modelos (ex: '12,14')")
    parser.add_argument("--voltage", default=None, help="Tensão desejada para novos modelos (ex: 380, 220, 127)")
    parser.add_argument("--module", choices=["all", "datasheet", "pet", "dconf"], default=None,
                        help="Documentos a gerar: all (todos), datasheet, pet ou dconf")
    parser.add_argument("--auto", "--yes", dest="auto_mode", action="store_true",
                        help="Modo automático: executa direto sem telas de confirmação")
    parser.add_argument("--fast", "--quick", dest="fast_mode", action="store_true",
                        help="Modo rápido: gera Datasheet PDF + DCONF PDF + PETs em ~1.5s com conversão em background")
    parser.add_argument("--sync", dest="sync_mode", action="store_true",
                        help="Compatibilidade legada")
    parser.add_argument("--manual", dest="manual_mode", action="store_true",
                        help="Modo manual: exibe a tabela resumo de engenharia antes de executar")
    args = parser.parse_args()

    input_path = Path(args.pdf_input).resolve()
    if not input_path.exists():
        print(f"❌ Erro: Arquivo de entrada não encontrado: {input_path}")
        sys.exit(1)

    print("==========================================================================")
    print("      ESTEIRA DE HOMOLOGAÇÃO DEYE BRASIL - ENGENHARIA & DOCUMENTAÇÃO     ")
    print("==========================================================================")
    print(f"📄 Datasheet Base : {input_path.name}")

    # Inicializa motores e analisa o documento de entrada
    engine = SemanticDeyeEngine(str(input_path))
    doc_lang = engine.detect_language()
    print(f"🌐 Idioma do PDF  : {'Português (Brasil)' if doc_lang == 'PT-BR' else 'Inglês (Internacional)'}")

    pet_gen = DeyePetGenerator()
    base_models_data, common_specs = pet_gen.extract_models_data_from_pdf(str(input_path))
    base_model_names = [m["model"] for m in base_models_data]

    print(f"🔍 Modelos Base   : {', '.join(base_model_names)}")

    fast_mode = args.fast_mode
    use_async_pdf = not args.sync_mode

    # -------------------------------------------------------------
    # 0. MODO RÁPIDO (APRESENTADO ANTES DE QUALQUER PERGUNTA INICIAL)
    # -------------------------------------------------------------
    if not fast_mode and not args.mode and not args.auto_mode:
        speed_choice = prompt_choice(
            "⚡ ESCOLHA A VELOCIDADE DE EXECUÇÃO:",
            [
                "Modo Rápido Ultra-Fast (Gera Datasheet PDF + DCONF PDF + PETs DOCX/PDF nativos via pdfcraft-cli em ~2s)",
                "Modo Personalizado Guiado (Configurar modelos, tensões, potências ou entregáveis)"
            ],
            default=1
        )
        if speed_choice == 1:
            fast_mode = True

    final_models_data = []

    # Se estiver no modo rápido, pula todas as perguntas e executa direto
    if fast_mode:
        print("\n🚀 [MODO RÁPIDO ATIVADO: Execução Ultra-Fast em ~1.5s]")
        final_models_data = base_models_data
        active_modules = args.module or "all"
        is_manual = False
        goal_choice = 1
    else:
        # -------------------------------------------------------------
        # 1. OBJETIVO DO PROCESSAMENTO
        # -------------------------------------------------------------
        if args.mode:
            if args.mode in ["all", "translate"]:
                goal_choice = 1
            elif args.mode == "single":
                goal_choice = 2
            elif args.mode == "new":
                goal_choice = 3
        else:
            goal_choice = prompt_choice(
                "Qual é o objetivo para este datasheet?",
                [
                    "Traduzir / Adequar todos os modelos do datasheet base",
                    "Traduzir / Focar em um único modelo específico",
                    "Derivar / Criar novos modelos (a partir do base PT-BR ou EN)"
                ],
                default=1
            )

    # CASO 1: TODOS OS MODELOS
    if goal_choice == 1:
        print("\n--- [MODO: TODOS OS MODELOS - ADEQUAÇÃO COMPLETA] ---")
        if doc_lang == "PT-BR":
            print("ℹ Documento identificado em Português. Executando auditoria técnica de termos e contatos...")
        else:
            print("ℹ Documento em Inglês. Executando tradução técnica completa e padronização visual...")
        final_models_data = base_models_data

    # CASO 2: MODELO ÚNICO
    elif goal_choice == 2:
        print("\n--- [MODO: MODELO ÚNICO] ---")
        if args.single_model:
            matched = [m for m in base_models_data if args.single_model.upper() in m["model"].upper()]
            if matched:
                selected_model = matched[0]
            else:
                selected_model = build_model_specs(args.single_model, base_models_data[0])
        else:
            options = [f"{m['model']} ({m['power_kw']} kW - {m.get('phase_type', '')})" for m in base_models_data]
            sel_idx = prompt_choice("Selecione o modelo a ser focado no documento:", options, default=1)
            selected_model = base_models_data[sel_idx - 1]

        print(f"✔ Modelo selecionado: {selected_model['model']}")
        final_models_data = [selected_model]

    # CASO 3: DERIVAR / CRIAR NOVOS MODELOS
    elif goal_choice == 3:
        print("\n--- [MODO: DERIVAR / CRIAR NOVOS MODELOS] ---")
        
        # Seleção do modelo de referência
        ref_model_data = base_models_data[0]
        if len(base_models_data) > 1:
            if args.reference:
                try:
                    ref_idx = int(args.reference) - 1
                    ref_model_data = base_models_data[ref_idx]
                except (ValueError, IndexError):
                    matched = [m for m in base_models_data if args.reference.upper() in m["model"].upper()]
                    if matched:
                        ref_model_data = matched[0]
            else:
                ref_choices = [f"{m['model']} (Potência: {m['power_kw']} kW, Bateria: {m.get('bat_current', '')}A)" for m in base_models_data]
                ref_idx = prompt_choice("▶ 3. Modelo de Referência (obrigatório para herdar características):", ref_choices, default=1)
                ref_model_data = base_models_data[ref_idx - 1]

        print(f"✔ Modelo de referência: {ref_model_data['model']}")
        ref_phase = ref_model_data.get("phase_type", "Trifásico")
        is_three_phase = "Trifásico" in ref_phase or "P3" in ref_model_data["model"].upper()

        new_models_list = []

        if args.models:
            model_names = [n.strip() for n in args.models.split(",") if n.strip()]
            for nm in model_names:
                v_override = int(args.voltage) if args.voltage else None
                m_data = build_model_specs(nm, ref_model_data, voltage_override=v_override)
                new_models_list.append(m_data)
        elif args.powers:
            pws = [float(p.strip()) for p in args.powers.split(",") if p.strip()]
            v_choice = args.voltage or ("380V" if is_three_phase else "220V")
            for pw in pws:
                sug = suggest_model_name(ref_model_data["model"], pw, voltage_choice=v_choice)
                v_override = 220 if "220" in v_choice and is_three_phase else (127 if "127" in v_choice else (380 if is_three_phase else 220))
                m_data = build_model_specs(sug, ref_model_data, voltage_override=v_override)
                new_models_list.append(m_data)
        else:
            # Fluxo guiado passo a passo
            adding = True
            model_count = 1
            while adding:
                print(f"\n[Configuração Obrigatória do Novo Modelo #{model_count}]")
                while True:
                    pw_input = prompt_text("▶ 1. Digite a potência nominal em kW (obrigatório, ex: 7.5, 8, 10, 14)")
                    try:
                        pw_val = float(pw_input.replace(",", "."))
                        if pw_val > 0:
                            break
                    except ValueError:
                        pass
                    print("❌ A potência nominal é obrigatória e deve ser um valor numérico positivo.")

                v_choice_str = "380V"
                v_override_val = 380
                if is_three_phase:
                    v_opt = prompt_choice(
                        "▶ 2. Qual a tensão de rede para o Brasil?",
                        [
                            "220/380V (Padrão Trifásico 380V)",
                            "127/220V (Trifásico Baixa Tensão -LV)"
                        ],
                        default=1
                    )
                    v_choice_str = "380V" if v_opt == 1 else "220V"
                    v_override_val = 380 if v_opt == 1 else 220
                else:
                    v_opt = prompt_choice(
                        "▶ 2. Qual a tensão de rede para o Brasil?",
                        [
                            "220V (Padrão Monofásico / Split-Phase)",
                            "127V (Monofásico 127V)"
                        ],
                        default=1
                    )
                    v_choice_str = "220V" if v_opt == 1 else "127V"
                    v_override_val = 220 if v_opt == 1 else 127

                sug_name = suggest_model_name(ref_model_data["model"], pw_val, voltage_choice=v_choice_str)
                final_name = prompt_text(f"▶ 4. Nomenclatura / Part Number do modelo #{model_count} (Enter para aceitar sugestão)", sug_name)

                m_data = build_model_specs(final_name, ref_model_data, voltage_override=v_override_val)
                new_models_list.append(m_data)

                more = input("\nDeseja adicionar mais um novo modelo? [s/N]: ").strip().lower()
                if more in ["s", "sim", "y", "yes"]:
                    model_count += 1
                else:
                    adding = False

        final_models_data = new_models_list

    # -------------------------------------------------------------
    # 2. SELEÇÃO DE ENTREGÁVEIS E MODO (SE NÃO ESTIVER NO MODO RÁPIDO)
    # -------------------------------------------------------------
    if not fast_mode:
        if args.module:
            active_modules = args.module
        else:
            doc_choice = prompt_choice(
                "Quais documentos você deseja gerar?",
                [
                    "Pacote Completo (Datasheet PDF + PET DOCX/PDF + DCONF PDF)",
                    "Apenas Datasheet Traduzido (PDF)",
                    "Apenas Planilhas Técnicas PET INMETRO (DOCX + PDF)",
                    "Apenas Declaração de Conformidade DCONF (PDF)"
                ],
                default=1
            )
            mod_map = {1: "all", 2: "datasheet", 3: "pet", 4: "dconf"}
            active_modules = mod_map[doc_choice]

        # 3. MODO DE OPERAÇÃO (MANUAL VS AUTOMÁTICO)
        is_auto = args.auto_mode
        is_manual = args.manual_mode

        if not is_auto and not is_manual:
            op_mode = prompt_choice(
                "Modo de operação:",
                [
                    "Modo Automático / Direto (Gera imediatamente com os padrões)",
                    "Modo Manual de Engenharia (Exibe Tabela Resumo para conferência antes de gerar)"
                ],
                default=1
            )
            if op_mode == 2:
                is_manual = True

        if is_manual:
            print_engineering_summary_table(final_models_data)
            conf = input("Confirma os parâmetros acima para gerar a documentação? [Enter=Sim, C=Corrigir]: ").strip().lower()
            if conf == "c":
                for idx, m in enumerate(final_models_data):
                    print(f"\n--- Ajuste do Modelo: {m['model']} ---")
                    pw_in = prompt_text("Potência (kW)", str(m["power_kw"]))
                    m["power_kw"] = float(pw_in)
                    m["power_w"] = int(m["power_kw"] * 1000)
                    nom_in = prompt_text("Corrente Nominal CA (A)", str(m["ac_nom_current"]))
                    m["ac_nom_current"] = float(nom_in)
                    max_in = prompt_text("Corrente Máxima CA (A)", str(m["ac_max_current"]))
                    m["ac_max_current"] = float(max_in)

    # -------------------------------------------------------------
    # 4. CRIAÇÃO DE PASTAS E DESTINOS
    # -------------------------------------------------------------
    model_names_list = [m["model"] for m in final_models_data]
    if len(model_names_list) == 1:
        folder_name = model_names_list[0]
    else:
        folder_name = format_family_folder_name(model_names_list)

    base_out = Path(args.out_dir) if args.out_dir else input_path.parent
    target_folder = base_out / folder_name
    target_folder.mkdir(parents=True, exist_ok=True)

    print(f"\n📁 Diretório Oficial de Saída: {target_folder}")
    print(f"   Nome Oficial da Família   : {folder_name}")

    # =============================================================
    # 5. EXECUÇÃO PARALELA DOS MÓDULOS COM TELEMETRIA ATIVA
    # =============================================================
    t_start = time.perf_counter()
    timings = {}

    def _task_datasheet():
        t0 = time.perf_counter()
        out_datasheet = target_folder / f"Datasheet - {folder_name}.pdf"
        engine.process_all(str(out_datasheet), custom_models_data=final_models_data)
        elapsed = time.perf_counter() - t0
        return "Datasheet PDF", elapsed, f"✔ Datasheet PDF gerado: {out_datasheet.name}"

    def _task_pet():
        t0 = time.perf_counter()
        pet_gen.generate_all(
            str(input_path), str(target_folder),
            custom_models_data=final_models_data,
            custom_common_specs=common_specs,
            convert_pdf=True
        )
        elapsed = time.perf_counter() - t0
        return "PET INMETRO (.docx + .pdf)", elapsed, f"✔ Planilhas PET (.docx e .pdf nativos via pdfcraft-cli) geradas"

    def _task_dconf():
        t0 = time.perf_counter()
        dconf_gen = DeyeDconfGenerator()
        dconf_gen.generate(str(input_path), str(target_folder), custom_model_names=model_names_list, folder_name=folder_name)
        elapsed = time.perf_counter() - t0
        return "DCONF PDF", elapsed, f"✔ DCONF PDF gerada: DCONF - {folder_name}.pdf"

    tasks = []
    if active_modules in ["all", "datasheet"]:
        tasks.append(_task_datasheet)
    if active_modules in ["all", "pet"]:
        tasks.append(_task_pet)
    if active_modules in ["all", "dconf"]:
        tasks.append(_task_dconf)

    print(f"\n▶ Executando geração em paralelo com telemetria ativa ({len(tasks)} módulos simultâneos)...")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
        futures = {pool.submit(fn): fn for fn in tasks}
        for fut in as_completed(futures):
            name, elapsed, msg = fut.result()
            timings[name] = elapsed
            print(f"   [{elapsed:.2f}s] {msg}")

    t_total = time.perf_counter() - t_start
    print("\n==========================================================================")
    print("                      PROCESSAMENTO CONCLUÍDO COM SUCESSO!               ")
    print("==========================================================================")
    print(f"  📂 Arquivos salvos em: {target_folder}")
    print(f"  ⚡ 100% dos documentos gerados em {t_total:.2f}s (Livre de LibreOffice, motor nativo pdfcraft-cli)!")
    print("  ⏱️ Resumo de Execução:")
    for k, v in timings.items():
        print(f"     • {k:<30}: {v:.2f}s")
    print(f"     • {'TEMPO TOTAL':<30}: {t_total:.2f}s")
    print("==========================================================================\n")

if __name__ == "__main__":
    main()
