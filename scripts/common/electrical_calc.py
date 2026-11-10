#!/usr/bin/env python3
"""
Módulo Comum: Cálculos de Engenharia Elétrica para o Padrão Brasileiro.
Unifica grandezas elétricas para tensões nominais brasileiras (380V trifásico e 220V monofásico/bifásico).
"""
import math

def calculate_ac_currents(power_w, phase_type="Trifásico", nominal_voltage=380, max_apparent_power_va=None):
    """
    Calcula corrente nominal e máxima CA segundo o padrão brasileiro.
    Suporta inversão flexível de argumentos (power_w, nominal_voltage, phase_type).
    """
    if isinstance(phase_type, (int, float)) and isinstance(nominal_voltage, str):
        nominal_voltage, phase_type = phase_type, nominal_voltage
    nominal_voltage = float(nominal_voltage)

    if max_apparent_power_va is None:
        max_apparent_power_va = power_w * 1.10

    if phase_type == "Trifásico":
        # Trifásico: I = P / (sqrt(3) * V)
        i_nom = power_w / (math.sqrt(3) * nominal_voltage)
        i_max = max_apparent_power_va / (math.sqrt(3) * nominal_voltage)
    else:
        # Monofásico ou Bifásico: I = P / V
        i_nom = power_w / nominal_voltage
        i_max = max_apparent_power_va / nominal_voltage

    return round(i_nom, 1), round(i_max, 1)

def calculate_oversizing(power_w, dc_ratio=1.60, access_ratio=2.00):
    """
    Calcula potência de entrada FV máxima (CC) e potência de acesso máxima (W)
    a partir das razões de sobredimensionamento do inversor.
    """
    max_pv_w = round(power_w * dc_ratio, -2) # Arredonda para centenas
    max_access_w = round(power_w * access_ratio, -2)
    return int(max_pv_w), int(max_access_w)
