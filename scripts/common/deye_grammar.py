#!/usr/bin/env python3
"""
Módulo Comum: Decodificador Gramatical de Nomenclatura Deye (Part Numbers).
Identifica topologia, nível de tensão da bateria, tipo de faseamento e família.
Define também as constantes corporativas e de engenharia padronizadas para o Brasil.
"""
import re

# =============================================================================
# CONSTANTES CORPORATIVAS E TÉCNICAS PADRÃO - DEYE BRASIL
# =============================================================================
EMAIL_SUPPORT = "suporte@deyeinversores.com.br"
PHONE_HYBRID = "(11) 2500-3700"
PHONE_DEFAULT = "(11) 2500-0681"
ADDRESS_DEFAULT = "Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP"
COMPANY_MANUFACTURER = "Ningbo Deye Inverter Technology Co., Ltd."
COMPANY_SUPPLIER = "DEYE BRASIL SUPPORT CENTER COMERCIO DE INVERSORES FOTOVOLTAICOS LTDA"
CNPJ_SUPPLIER = "32.574.888/0001-62"
WARRANTY_DEFAULT = "10 Anos"
PORTARIA_INMETRO_DEFAULT = "140/2022"
GRID_FREQUENCY_DEFAULT = "60 Hz"
GRID_FREQUENCY_RANGE = "57,5 ~ 62 Hz"
POWER_FACTOR_DEFAULT = "0,8 adiantado a 0,8 atrasado"

def get_contact_info(model_str):
    """
    Retorna os dados cadastrais e de suporte Deye Brasil específicos da topologia:
    - Inversores Híbridos: Telefone (11) 2500-3700
    - Demais inversores (On-Grid / Microinversores): Telefone (11) 2500-0681
    - E-mail unificado: suporte@deyeinversores.com.br
    - Garantia unificada: 10 Anos
    """
    m = str(model_str).upper()
    is_hybrid = "SG" in m
    phone = PHONE_HYBRID if is_hybrid else PHONE_DEFAULT
    return {
        "email": EMAIL_SUPPORT,
        "phone": phone,
        "address": ADDRESS_DEFAULT,
        "manufacturer": COMPANY_MANUFACTURER,
        "supplier": COMPANY_SUPPLIER,
        "cnpj": CNPJ_SUPPLIER,
        "warranty": WARRANTY_DEFAULT,
        "portaria": PORTARIA_INMETRO_DEFAULT,
        "frequency": GRID_FREQUENCY_DEFAULT,
        "frequency_range": GRID_FREQUENCY_RANGE,
        "power_factor": POWER_FACTOR_DEFAULT,
        "is_hybrid": is_hybrid
    }

def parse_model(model_str, nominal_voltage_override=None):
    """
    Analisa um modelo individual (ex: SUN-14K-SG05LP3-EU-SM2)
    e retorna seus atributos elétricos e de engenharia.
    """
    model_str = str(model_str).strip()
    data = {
        "model": model_str,
        "brand": "DEYE",
        "power_w": 0,
        "battery_type": "Desconhecido",
        "phase_type": "Desconhecido",
        "nominal_voltage_ac": 380,
        "grid_connection": "3L+N+PE",
        "family": ""
    }

    # Extração de Potência Nominal (W)
    pw_match = re.search(r"SUN-(\d+(?:\.\d+)?)\s*K", model_str, re.IGNORECASE)
    if pw_match:
        data["power_w"] = int(float(pw_match.group(1)) * 1000)
    else:
        pw_match2 = re.search(r"SUN-(\d+)", model_str, re.IGNORECASE)
        if pw_match2:
            val = int(pw_match2.group(1))
            data["power_w"] = val * 1000 if val < 100 else val

    # Topologia da Bateria: LP vs HP
    if "LP" in model_str.upper():
        data["battery_type"] = "Baixa Tensão (40-60V)"
        data["battery_class"] = "LP"
    elif "HP" in model_str.upper():
        data["battery_type"] = "Alta Tensão (160-800V)"
        data["battery_class"] = "HP"
    else:
        data["battery_type"] = "Sem Bateria / String"
        data["battery_class"] = "NONE"

    # Faseamento e Tensão AC para o Brasil
    is_p3 = "P3" in model_str.upper() or ("-LV" in model_str.upper() and "P1" not in model_str.upper() and "P2" not in model_str.upper())
    if not is_p3 and ("G04" in model_str.upper() or "G05" in model_str.upper() or "G06" in model_str.upper()):
        is_p3 = True

    if is_p3:
        data["phase_type"] = "Trifásico"
        if nominal_voltage_override == 220 or "-LV" in model_str.upper():
            data["nominal_voltage_ac"] = 220 # 127/220V
            data["grid_connection"] = "3L+N+PE"
        else:
            data["nominal_voltage_ac"] = 380 # 220/380V
            data["grid_connection"] = "3L+N+PE"
    elif "P2" in model_str.upper():
        if "EU" in model_str.upper():
            data["phase_type"] = "Monofásico"
            data["nominal_voltage_ac"] = 127 if nominal_voltage_override == 127 else 220
            data["grid_connection"] = "L+N+PE"
        else:
            data["phase_type"] = "Bifásico / Split-Phase"
            data["nominal_voltage_ac"] = 220
            data["grid_connection"] = "2L+PE"
    elif "P1" in model_str.upper():
        if "US" in model_str.upper():
            data["phase_type"] = "Bifásico / Split-Phase"
            data["nominal_voltage_ac"] = 220
            data["grid_connection"] = "2L+PE"
        else:
            data["phase_type"] = "Monofásico"
            data["nominal_voltage_ac"] = 127 if (nominal_voltage_override == 127 or "127" in model_str) else 220
            data["grid_connection"] = "L+N+PE"
    else:
        # Padrão caso não haja P1/P2/P3 explícito
        if "-LV" in model_str.upper():
            data["phase_type"] = "Trifásico"
            data["nominal_voltage_ac"] = 220
            data["grid_connection"] = "3L+N+PE"
        elif nominal_voltage_override:
            data["nominal_voltage_ac"] = nominal_voltage_override
            data["phase_type"] = "Trifásico" if nominal_voltage_override in [220, 380] and "MONO" not in model_str.upper() else "Monofásico"
        else:
            data["phase_type"] = "Trifásico"
            data["nominal_voltage_ac"] = 380
            data["grid_connection"] = "3L+N+PE"

    # Tempo de transferência (modo backup / EPS)
    data["transfer_time"] = get_transfer_time(model_str)

    # Tensões de operação (Nominal e Outras Tensões)
    nom_v, out_v = get_voltage_ratings(model_str, nominal_voltage=data["nominal_voltage_ac"])
    data["voltage_nominal_str"] = nom_v
    data["voltage_other_str"] = out_v

    return data

def get_transfer_time(model_str):
    """
    Retorna o tempo de transferência para modo backup/EPS:
    - Se o aparelho for P1, P2 ou LP3: '< 10 ms'
    - Se for HP3: '< 20 ms'
    """
    m = model_str.upper()
    if "HP3" in m or ("HP" in m and "P3" in m):
        return "< 20 ms"
    return "< 10 ms"

def get_voltage_ratings(model_str, nominal_voltage=None):
    """
    Retorna a tensão nominal e a outra tensão de operação conforme a topologia e nível de tensão:
    - Monofásico 127V                : Nominal = '127V'     | Outra Tensão = '120V'
    - Monofásico / Bifásico 220V     : Nominal = '220V'     | Outra Tensão = '230V'
    - Trifásico 127/220V (com -LV)   : Nominal = '127/220V' | Outra Tensão = '120/208V'
    - Trifásico 220/380V (P3 padrão) : Nominal = '220/380V' | Outra Tensão = '230/400V'
    """
    m = str(model_str).upper()
    if nominal_voltage == 127 or ("127" in m and "-LV" not in m and "P3" not in m):
        return "127V", "120V"

    is_three_phase = "P3" in m or ("-LV" in m and "P1" not in m and "P2" not in m) or nominal_voltage in [380] or (nominal_voltage == 220 and "-LV" in m)
    if is_three_phase:
        if "-LV" in m or nominal_voltage == 220:
            return "127/220V", "120/208V"
        else:
            return "220/380V", "230/400V"
    else:
        # P1, P2 ou monofásico genérico
        if nominal_voltage == 127:
            return "127V", "120V"
        return "220V", "230V"

def suggest_model_name(base_model, power_kw, voltage_choice=None):
    """
    Gera uma sugestão consistente de part number a partir do modelo base.
    Substitui a potência numérica pelo novo valor (formatado como inteiro se for exato).
    Se trifásico e voltage_choice == '220V' ou '220', assegura sufixo -LV.
    Se trifásico e voltage_choice == '380V' ou '380', remove sufixo -LV se houver.
    Se monofásico e voltage_choice == '127V', adiciona indicação ou sufixo se necessário.
    """
    kw_str = str(int(power_kw)) if float(power_kw).is_integer() else str(power_kw)
    name = re.sub(r"(SUN-)(\d+(?:\.\d+)?)(K)", rf"\g<1>{kw_str}\g<3>", base_model, flags=re.I)
    if name == base_model:
        name = re.sub(r"(SUN-)(\d+(?:\.\d+)?)", rf"\g<1>{kw_str}K", base_model, flags=re.I)

    if voltage_choice:
        v_str = str(voltage_choice).upper()
        if "220" in v_str and ("P3" in name.upper() or "380" in base_model):
            if "-LV" not in name.upper():
                if "-EU" in name.upper():
                    name = re.sub(r"-EU", "-LV", name, flags=re.I)
                elif re.search(r"-[A-Z0-9]+$", name):
                    name = re.sub(r"(-[A-Z0-9]+)$", r"-LV\1", name)
                else:
                    name += "-LV"
        elif "380" in v_str and "-LV" in name.upper():
            name = re.sub(r"-LV", "", name, flags=re.I)
        elif "127" in v_str:
            if "127" not in name.upper():
                if "-220" in name.upper():
                    name = re.sub(r"-220", "-127", name, flags=re.I)
                else:
                    name += "-127"

    # Limpeza de hífens duplicados
    name = re.sub(r"-+", "-", name)
    return name

def extract_max_pv_current(val):
    """
    Se a corrente máxima fotovoltaica se apresentar com somas (ex: 36+36, 36+36+36+36, 26+40),
    extrai e utiliza estritamente o maior número individual.
    """
    if not val:
        return ""
    numbers = re.findall(r"\d+(?:\.\d+)?", str(val))
    if not numbers:
        return str(val)
    max_num = max(float(n) for n in numbers)
    return str(int(max_num) if max_num.is_integer() else max_num)

def derive_family_name(models):
    """
    A partir de uma lista de modelos (ex: SUN-14K-SG05LP3-EU-SM2, ..., SUN-20K-SG05LP3-EU-SM2),
    gera o nome de família oficial (ex: SUN-(14-20)K-SG05LP3-EU-SM2).
    """
    if not models:
        return ""
    if len(models) == 1:
        return models[0]

    # Extrai potências numéricas em kW
    kws = []
    prefix = ""
    suffix = ""
    for m in models:
        match = re.search(r"^(SUN-)(\d+(?:\.\d+)?)(K-.*)$", m, re.IGNORECASE)
        if match:
            prefix = match.group(1)
            kws.append(float(match.group(2)))
            suffix = match.group(3)

    if kws and prefix and suffix:
        min_kw = int(min(kws)) if min(kws).is_integer() else min(kws)
        max_kw = int(max(kws)) if max(kws).is_integer() else max(kws)
        return f"{prefix}({min_kw}-{max_kw}){suffix}"

    return models[0]

def get_dconf_family_desc(model_str):
    """
    Retorna a descrição oficial da família para o campo 'Familia:' na DCONF:
    Topologias Híbridas (SG):
    - Inversor Híbrido Monofásico com Baterias de Baixa Tensão
    - Inversor Híbrido SplitPhase com Baterias de Baixa Tensão
    - Inversor Híbrido Trifásico com Baterias de Baixa Tensão
    - Inversor Híbrido Trifásico com Baterias de Alta Tensão
    Topologias On-Grid / Conexão à Rede (G):
    - Inversor de Conexão à Rede Monofásico
    - Inversor de Conexão à Rede Split-Phase
    - Inversor de Conexão à Rede Trifásico
    - Inversor de Conexão à Rede Trifásico Baixa Tensão (127V/220V)
    """
    m = model_str.upper()
    is_storage = "SG" in m
    is_hp = "HP" in m
    is_lv = "-LV" in m or "LV" in m

    if is_storage:
        if "P3" in m:
            if is_hp:
                return "Inversor Híbrido Trifásico com Baterias de Alta Tensão"
            else:
                return "Inversor Híbrido Trifásico com Baterias de Baixa Tensão"
        elif "P2" in m:
            if "EU" in m:
                return "Inversor Híbrido Monofásico com Baterias de Baixa Tensão"
            else:
                return "Inversor Híbrido SplitPhase com Baterias de Baixa Tensão"
        elif "P1" in m:
            if "US" in m:
                return "Inversor Híbrido SplitPhase com Baterias de Baixa Tensão"
            else:
                return "Inversor Híbrido Monofásico com Baterias de Baixa Tensão"
        else:
            if is_hp:
                return "Inversor Híbrido Trifásico com Baterias de Alta Tensão"
            else:
                return "Inversor Híbrido Trifásico com Baterias de Baixa Tensão"
    else:
        # Linha On-Grid / String (G01 a G06)
        if "P3" in m:
            if is_lv:
                return "Inversor de Conexão à Rede Trifásico Baixa Tensão (127V/220V)"
            return "Inversor de Conexão à Rede Trifásico"
        elif "P2" in m:
            if "US" in m:
                return "Inversor de Conexão à Rede Split-Phase"
            return "Inversor de Conexão à Rede Monofásico"
        elif "P1" in m:
            if "US" in m:
                return "Inversor de Conexão à Rede Split-Phase"
            return "Inversor de Conexão à Rede Monofásico"
        else:
            if is_lv:
                return "Inversor de Conexão à Rede Trifásico Baixa Tensão (127V/220V)"
            return "Inversor de Conexão à Rede Trifásico"

def format_family_folder_name(models):
    """
    Formata o nome da pasta seguindo a regra:
    Menor potencia ~ Maior potencia -- nome comum do equipamento -- [diferenciacao de sufixos]
    Exemplo: SUN-5~12K-SG02LP2-US-[AM2-AM3] ou SUN-14~20K-SG05LP3-EU-SM2
    """
    if not models:
        return "INVERSOR_DEYE"
    if len(models) == 1:
        return models[0]

    parsed = []
    for m in models:
        match = re.search(r"^(SUN-)?(\d+(?:\.\d+)?)(K-)?(.*?)(?:-([A-Z0-9]+))?$", m.strip(), re.IGNORECASE)
        if match:
            prefix = match.group(1) or "SUN-"
            kw = float(match.group(2))
            body = match.group(4) or ""
            suffix = match.group(5) or ""
            parsed.append({"prefix": prefix, "kw": kw, "body": body, "suffix": suffix, "orig": m})
        else:
            parsed.append(None)

    if any(p is None for p in parsed):
        return f"{models[0]}_a_{models[-1]}"

    kws = [p["kw"] for p in parsed]
    min_kw = int(min(kws)) if min(kws).is_integer() else min(kws)
    max_kw = int(max(kws)) if max(kws).is_integer() else max(kws)

    # Identifica o corpo comum (sem potências)
    bodies = list(dict.fromkeys(p["body"] for p in parsed if p["body"]))
    body_str = bodies[0] if len(bodies) == 1 else "-".join(bodies)

    suffixes = list(dict.fromkeys(p["suffix"] for p in parsed if p["suffix"]))
    if len(suffixes) > 1:
        suffix_str = f"-[{'-'.join(suffixes)}]"
    elif len(suffixes) == 1:
        suffix_str = f"-{suffixes[0]}"
    else:
        suffix_str = ""

    return f"SUN-{min_kw}~{max_kw}K-{body_str}{suffix_str}"
