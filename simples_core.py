# simples_core.py
from dataclasses import dataclass
from typing import Literal, Tuple
from simples_tabelas import TABELAS

Anexo = Literal["I","II","III","IV","V","Auto"]

@dataclass
class SimplesInput:
    rbt12: float            # Receita bruta acumulada 12m
    receita_mes: float      # Receita do mês (competência)
    anexo: Anexo            # I-V ou Auto (usa Fator R)
    folha_12m: float = 0.0  # para Fator R
    atividade_sujeita_fator_r: bool = False
    considerar_sublimite: bool = False  # MVP2
    icms_iss_foras: bool = False        # MVP2

def _escolher_anexo(inp: SimplesInput) -> str:
    if inp.anexo != "Auto":
        return inp.anexo
    if not inp.atividade_sujeita_fator_r:
        return "III"  # fallback seguro p/ serviços elegíveis ao III
    # Fator R: folha_12m / rbt12 >= 0.28 => Anexo III; senão, V
    if inp.rbt12 <= 0:
        return "V"  # se não houver RBT12, use o mais gravoso
    fator_r = (inp.folha_12m or 0.0) / inp.rbt12
    return "III" if fator_r >= 0.28 else "V"

def _faixa(anexo: str, rbt12: float) -> Tuple[float, float]:
    for limite, aliq, pd in TABELAS[anexo]:
        if rbt12 <= limite:
            return aliq, pd
    # se passar dos 4,8M, use última faixa (mas trate sublimite no MVP2)
    aliq, pd = TABELAS[anexo][-1][1], TABELAS[anexo][-1][2]
    return aliq, pd

def aliquota_efetiva(aliq_nom: float, pd: float, rbt12: float) -> float:
    if rbt12 <= 0:
        return 0.0
    return max((rbt12 * aliq_nom - pd) / rbt12, 0.0)

def calcular_simples(inp: SimplesInput):
    anexo = _escolher_anexo(inp)
    aliq_nom, pd = _faixa(anexo, inp.rbt12)
    aliq_eff = aliquota_efetiva(aliq_nom, pd, inp.rbt12)  # (RBT12*ALIQ - PD)/RBT12
    das = aliq_eff * max(inp.receita_mes, 0.0)

    return {
        "anexo": anexo,
        "aliquota_nominal": aliq_nom,
        "parcela_deduzir": pd,
        "aliquota_efetiva": aliq_eff,
        "das_mes": das,
        # "breakdown": repartir_por_tributo(...),  # MVP2
    }
