# -*- coding: utf-8 -*-
# app.py — Calculadora Tributária (MVP limpo)
# -------------------------------------------------------------
# Compara Lucro Presumido x Lucro Real.
# Impostos: PIS, COFINS, IRPJ, CSLL, INSS (26,8%) e ICMS (simplificado).
# Créditos no Lucro Real: Energia Elétrica e Aluguel (PIS/COFINS não-cumulativos).
# Períodos: Mensal, Trimestral, Anual e Personalizado (N meses). Exporta Excel e PDF.
# Campos monetários: iniciam vazios, formatam BRL ao confirmar, e limpam/selecionam ao focar.
# -------------------------------------------------------------

import io
import re
import json
from dataclasses import dataclass
from typing import Literal, Tuple

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

import json
from functools import lru_cache
import altair as alt
import numpy as np


# ============================
# Constantes e configurações
# ============================
PIS_PRESUMIDO = 0.0065
COFINS_PRESUMIDO = 0.03
PIS_REAL = 0.0165
COFINS_REAL = 0.076
CSLL_ALIQ = 0.09
IRPJ_ALIQ = 0.15
IRPJ_ADICIONAL_ALIQ = 0.10
ISS_ALIQ = 0.05  # 5%
INSS_PATRONAL_ALIQ_DEFAULT = 0.268  # fixo por ora

SIDEBAR_WIDTH_PX = 320
PERIODO_TIPO = Literal["Mensal", "Trimestral", "Anual", "Personalizado"]

# ============================
# Utilidades
# ============================

import numpy as np

def normalize_df_for_streamlit(df: pd.DataFrame) -> pd.DataFrame:
    """
    - Em colunas numéricas: mantém NaN (ok p/ Arrow e formatação).
    - Em colunas textuais (object/string): troca None/NaN por "".
    """
    df2 = df.copy()
    for col in df2.columns:
        if pd.api.types.is_numeric_dtype(df2[col]):
            # deixa NaN como está (não vira None)
            continue
        # textual: limpa None/NaN
        df2[col] = df2[col].astype("object").where(df2[col].notna(), "")
        df2[col] = df2[col].replace({None: ""})
    return df2




def parse_percent_to_frac(x) -> float:
    """
    Converte entradas como '18', '18,5', '18%', '18,5%' em fração (0.185).
    Aceita também float/int já em percent (ex.: 18 -> 0.18) ou fração (0.18).
    """
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        # heurística: se veio como 0–1, já é fração; se >1 supomos "percent"
        return float(x) if 0.0 <= float(x) <= 1.0 else float(x) / 100.0
    s = str(x).strip().replace("%", "").replace(" ", "")
    s = s.replace(".", "").replace(",", ".")  # 18,5 -> 18.5 ; 1.234,56 -> 1234.56
    try:
        val = float(s)
    except Exception:
        return 0.0
    return val / 100.0

def parse_brl_or_number(x) -> float:
    """
    Converte '1.234,56' ou '1234,56' ou '1234.56' em float.
    Se vier número, apenas float(x).
    """
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    return brl_to_float(str(x))


def format_brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

def format_pct_br(frac: float, casas: int = 2) -> str:
    try:
        s = f"{frac*100:,.{casas}f}%"
        return s.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return "0,00%"

def fmt_percent_styler(val):
    if pd.isna(val):
        return ""
    try:
        return format_pct_br(float(val))
    except Exception:
        return val

def brl_to_float(txt: str) -> float:
    if txt is None:
        return 0.0
    s = str(txt)
    s = s.replace("R$", "").replace(" ", "").strip()
    s = re.sub(r"[^0-9.,-]", "", s)
    if s.count(',') > 1:
        partes = s.split(',')
        s = ''.join(partes[:-1]).replace('.', '') + ',' + partes[-1]
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0

HEADER_CENTER = [
    {"selector": "th.col_heading", "props": [("text-align", "center")]},
    {"selector": "th.col_heading.level0", "props": [("text-align", "center")]},
]

def _sanitize_arrow(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    if "Simples Nacional" in df.columns:
        s = df["Simples Nacional"]
        # garante que não haja bytes/strings
        s = s.map(lambda v: None if isinstance(v, (bytes, bytearray)) else v)
        # converte para float (NaN se não for numérico)
        s = pd.to_numeric(s, errors="coerce")
        df["Simples Nacional"] = s

    for col in ("Lucro Presumido", "Lucro Real"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def style_df_center_headers(df: pd.DataFrame, money_cols=None, perc_cols=None, percent_row_label: str = "Carga sobre Receita"):
    money_cols = money_cols or [
        "Base","Crédito","Valor","PIS","COFINS","IRPJ","CSLL","INSS","ISS","ICMS","Total",
        "Lucro Presumido","Lucro Real","Simples Nacional"
    ]
    perc_cols  = perc_cols  or ["Alíquota","Carga sobre Receita"]

    sty = df.style.set_table_styles(HEADER_CENTER).hide(axis="index")
    first_col = df.columns[0]
    ser_first = df[first_col].astype(str)
    has_percent_row = percent_row_label in ser_first.values
    row_mask = ser_first == percent_row_label

    def _money_br(x):
        try:
            return format_brl(float(x))
        except Exception:
            return x

    money_in_df = [c for c in money_cols if c in df.columns and c != first_col]
    perc_in_df  = [c for c in perc_cols  if c in df.columns and c != first_col]

    # depois (usa seu formatter tolerante)
    for c in perc_in_df:
        sty = sty.format(fmt_percent_styler, subset=[c])


    if has_percent_row:
        num_cols = [c for c in df.columns if c != first_col]
        for c in num_cols:
            sty = sty.format(fmt_percent_styler, subset=pd.IndexSlice[row_mask, c])

    if money_in_df:
        if has_percent_row:
            for c in money_in_df:
                sty = sty.format(_money_br, subset=pd.IndexSlice[~row_mask, c])
        else:
            for c in money_in_df:
                sty = sty.format(_money_br, subset=[c])

    return sty

def moeda_input(label: str, key: str, value: float = 0.0,
                clear_on_focus_when_zero: bool = True,
                select_all_else: bool = True) -> float:
    if key not in st.session_state:
        st.session_state[key] = ""
    def _format_callback(_key=key):
        raw = st.session_state[_key]
        if str(raw).strip() == "":
            st.session_state[_key] = ""
            return
        val = brl_to_float(raw)
        st.session_state[_key] = format_brl(val)
    st.text_input(label, key=key, on_change=_format_callback)
    labels = st.session_state.get("_currency_labels", [])
    if label not in labels:
        labels.append(label)
        st.session_state["_currency_labels"] = labels
    st.session_state["_currency_clear_on_zero"] = clear_on_focus_when_zero
    st.session_state["_currency_select_all_else"] = select_all_else
    return brl_to_float(st.session_state[key])

def inject_currency_focus_script():
    labels = st.session_state.get("_currency_labels", [])
    clear_on_zero = st.session_state.get("_currency_clear_on_zero", True)
    select_all_else = st.session_state.get("_currency_select_all_else", True)
    js_labels = json.dumps(labels)
    js_clear  = "true" if clear_on_zero else "false"
    js_select = "true" if select_all_else else "false"
    script = """
    <script>
    (function(){
      const labels = %s;
      const clearOnZero = %s;
      const selectElse = %s;
      const root = window.parent && window.parent.document ? window.parent.document : document;
      const norm = s => (s || '').toLowerCase().replace(/\s+/g,' ').trim();
      const wanted = new Set(labels.map(norm));
      function isTarget(el){
        if(!el || el.tagName !== 'INPUT') return false;
        const al = el.getAttribute('aria-label');
        return wanted.has(norm(al));
      }
      function handle(el){
        const raw = (el.value || '').toLowerCase().replace(/\s+/g,'');
        const isZero = raw === 'r$0,00' || raw === '0,00' || raw === '0' || raw === '';
        if (clearOnZero && isZero) {
          el.value='';
          el.dispatchEvent(new Event('input', {bubbles:true}));
        } else if (selectElse) {
          el.select();
        }
      }
      root.addEventListener('focusin', function(ev){
        const el = ev.target;
        if (isTarget(el)) handle(el);
      }, true);
    })();
    </script>
    """ % (js_labels, js_clear, js_select)
    st_html(script, height=0)

def set_sidebar_style(width_px: int = SIDEBAR_WIDTH_PX, compact_gap_px: int = 6):
    st.markdown("""
<style>
  /* título e separadores */
  h1, h2, h3 { letter-spacing: .2px; }
  hr { border-top: 1px solid #e7eef5; }

  /* botões */
  button[kind="primary"] { border-radius: 12px; }
  .stDownloadButton button { border-radius: 12px; }

  /* métricas: bordas suaves */
  div[data-testid="stMetric"] {
    padding: 8px 20px; align-items: center;border: 2px solid #eef2f7; border-radius: 12px;
  }

  /* tabelas */
  .stDataFrame td, .stDataFrame th { font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)

def limite_irpj(periodo: PERIODO_TIPO, meses_personalizado: int) -> float:
    if periodo == "Mensal": return 20000.0
    if periodo == "Trimestral": return 60000.0
    if periodo == "Anual": return 240000.0
    meses = max(1, int(meses_personalizado or 1))
    return 20000.0 * meses

def adicional_irpj(base_calculo: float, periodo: PERIODO_TIPO, meses_personalizado: int = 0) -> float:
    lim = limite_irpj(periodo, meses_personalizado)
    excedente = max(base_calculo - lim, 0.0)
    return excedente * IRPJ_ADICIONAL_ALIQ

# ============ CNAE — Carregamento e decisão de anexo ============
@lru_cache(maxsize=1)
def load_cnae_map(path: str = "cnae_map_2025.json") -> dict:
    """Carrega o dicionário {codigo: {descricao, anexo}} do JSON gerado."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def normalize_cnae_mask(s: str) -> str:
    # 9999-9/99 quando possível
    import re
    if not s:
        return ""
    s = str(s).strip()
    digits = re.sub(r"[^0-9]", "", s)
    if len(digits) >= 7:
        d = digits[:8] if len(digits) >= 8 else digits[:7]
        return f"{d[0:4]}-{d[4]}/{d[5:7]}"
    return s

def anexo_por_cnae_mapa(cnae: str, rbt12: float, folha_12m: float, sujeito_fator_r: bool, mapa: dict) -> str | None:
    """Aplica regra do mapa; trata III/V com Fator R."""
    code = normalize_cnae_mask(cnae)
    info = mapa.get(code)
    if not info:
        return None
    base = (info.get("anexo") or "").upper().strip()

    if base in {"I","II","IV"}:
        return base
    if base in {"III","V"}:
        # já veio fechado no arquivo
        return base
    if base == "III/V":
        if sujeito_fator_r:
            fator_r = (folha_12m or 0.0) / (rbt12 or 1.0) if rbt12 else 0.0
            return "III" if fator_r >= 0.28 else "V"
        return "V"  # conservador se não marcar Fator R
    # AUTO ou não identificado no arquivo
    return None



# ============================
# Modelos de dados
# ============================

@dataclass
class Entradas:
    periodo: PERIODO_TIPO
    meses_personalizado: int
    receita_bruta: float
    atividade: str
    presumido_irpj_base: float
    presumido_csll_base: float
    folha_inss_base: float
    despesas_totais: float
    energia_eletrica: float
    aluguel: float
    servicos_sem_icms: bool
    receita_icms: float
    icms_aliquota: float
    icms_creditos: float
    icms_percentual_st: float
    zerar_pis_cofins_icms: bool = False
    
    
    inss_aliquota: float = INSS_PATRONAL_ALIQ_DEFAULT

def empresa_de_servicos(e: Entradas) -> bool:
    """Considera serviço quando a atividade é 'Serviços...' ou quando marcou 'só serviços (sem ICMS)'."""
    return str(e.atividade or "").startswith("Serviços") or bool(e.servicos_sem_icms)

@dataclass
class ResultadoRegime:
    regime: str
    base_pis: float
    credito_pis: float
    pis: float
    base_cofins: float
    credito_cofins: float
    cofins: float
    base_irpj: float
    irpj_15: float
    irpj_adicional: float
    irpj_total: float
    base_csll: float
    csll: float
    inss: float
    iss: float
    icms_debito: float
    icms_credito: float
    icms_devido: float
    total_impostos: float
    carga_efetiva_sobre_receita: float

# ============================
# Cálculos
# ============================



def _icms_destacado_saida(e: Entradas) -> float:
    if e.servicos_sem_icms or e.zerar_pis_cofins_icms:
        return 0.0
    return float(e.receita_icms or 0.0) * float(e.icms_aliquota or 0.0) * (1.0 - float(e.icms_percentual_st or 0.0))

def _icms_simplificado(e: Entradas) -> Tuple[float, float, float]:
    """
    Cálculo simplificado do ICMS:
      Débito  = receita_icms × alíquota × (1 - %ST)
      Crédito = icms_creditos × alíquota
      Devido  = Débito - Crédito
    """
    if e.servicos_sem_icms or e.zerar_pis_cofins_icms:
        return 0.0, 0.0, 0.0
    debito  = float(e.receita_icms or 0.0) * float(e.icms_aliquota or 0.0) * (1.0 - float(e.icms_percentual_st or 0.0))
    credito = float(e.icms_creditos or 0.0) * float(e.icms_aliquota or 0.0)
    devido  = debito - credito
    return debito, credito, devido







def calcular_lucro_presumido(e: Entradas) -> ResultadoRegime:
    icms_debito, icms_credito, icms_devido = _icms_simplificado(e)
    icms_destacado_saida = _icms_destacado_saida(e)

    # Bases “normais”
    base_pis = max(e.receita_bruta - icms_destacado_saida, 0.0)
    base_cofins = base_pis
    credito_pis = 0.0
    credito_cofins = 0.0
    pis = base_pis * PIS_PRESUMIDO
    cofins = base_cofins * COFINS_PRESUMIDO

    # NOVO — isenção setorial (ex.: livros)
    if e.zerar_pis_cofins_icms:
        base_pis = base_cofins = 0.0
        pis = cofins = 0.0
        # (ICMS já veio 0 pelo _icms_simplificado/_icms_destacado_saida)

    base_irpj = e.receita_bruta * e.presumido_irpj_base
    irpj_15 = base_irpj * IRPJ_ALIQ
    irpj_adic = adicional_irpj(base_irpj, e.periodo, e.meses_personalizado)
    irpj_total = irpj_15 + irpj_adic
    base_csll = e.receita_bruta * e.presumido_csll_base
    csll = base_csll * CSLL_ALIQ
    inss = e.folha_inss_base * e.inss_aliquota
    iss = (e.receita_bruta * ISS_ALIQ) if empresa_de_servicos(e) else 0.0

    total = pis + cofins + irpj_total + csll + inss + iss + icms_devido
    carga = total / e.receita_bruta if e.receita_bruta > 0 else 0.0

    return ResultadoRegime("Lucro Presumido", base_pis, credito_pis, pis,
                           base_cofins, credito_cofins, cofins,
                           base_irpj, irpj_15, irpj_adic, irpj_total,
                           base_csll, csll, inss,
                           iss,
                           icms_debito, icms_credito, icms_devido,
                           total, carga)


def calcular_lucro_real(e: Entradas) -> ResultadoRegime:
    icms_debito, icms_credito, icms_devido = _icms_simplificado(e)

    receita = float(e.receita_bruta or 0.0)
    compras = float(e.icms_creditos or 0.0)
    aliq_icms = float(e.icms_aliquota or 0.0)

    base_ajustada = receita * aliq_icms - compras * 0.20
    if base_ajustada < 0: base_ajustada = 0.0

    base_pis = base_ajustada
    base_cofins = base_ajustada
    credito_pis = 0.0
    credito_cofins = 0.0
    pis = base_pis * PIS_REAL
    cofins = base_cofins * COFINS_REAL

    # NOVO — isenção setorial (ex.: livros)
    if e.zerar_pis_cofins_icms:
        base_pis = base_cofins = 0.0
        pis = cofins = 0.0
        # (ICMS já veio 0 pelo _icms_simplificado)

    lucro_liquido = receita - float(e.despesas_totais or 0.0)
    base_irpj = max(lucro_liquido, 0.0)
    base_csll = max(lucro_liquido, 0.0)
    irpj_15 = base_irpj * IRPJ_ALIQ
    irpj_adic = adicional_irpj(base_irpj, e.periodo, e.meses_personalizado)
    irpj_total = irpj_15 + irpj_adic
    csll = base_csll * CSLL_ALIQ
    inss = float(e.folha_inss_base or 0.0) * float(e.inss_aliquota or 0.0)
    iss = (receita * ISS_ALIQ) if empresa_de_servicos(e) else 0.0

    total = pis + cofins + irpj_total + csll + inss + iss + icms_devido
    carga = total / receita if receita > 0 else 0.0

    return ResultadoRegime(
        "Lucro Real",
        base_pis, credito_pis, pis,
        base_cofins, credito_cofins, cofins,
        base_irpj, irpj_15, irpj_adic, irpj_total,
        base_csll, csll, inss,
        iss,
        icms_debito, icms_credito, icms_devido,
        total, carga
    )







# ============================
# Simples Nacional
# ============================

TABELAS_SIMPLES = {
    "I":   [(180000.00,0.040,0.00),(360000.00,0.073,5940.00),(720000.00,0.095,13860.00),(1800000.00,0.107,22500.00),(3600000.00,0.143,87300.00),(4800000.00,0.190,378000.00)],
    "II":  [(180000.00,0.045,0.00),(360000.00,0.078,5940.00),(720000.00,0.100,13860.00),(1800000.00,0.112,22500.00),(3600000.00,0.147,85500.00),(4800000.00,0.300,720000.00)],
    "III": [(180000.00,0.060,0.00),(360000.00,0.112,9360.00),(720000.00,0.135,17640.00),(1800000.00,0.160,35640.00),(3600000.00,0.210,125640.00),(4800000.00,0.330,648000.00)],
    "IV":  [(180000.00,0.045,0.00),(360000.00,0.090,8100.00),(720000.00,0.102,12420.00),(1800000.00,0.140,39780.00),(3600000.00,0.220,183780.00),(4800000.00,0.330,828000.00)],
    "V":   [(180000.00,0.155,0.00),(360000.00,0.180,4500.00),(720000.00,0.195,9900.00),(1800000.00,0.205,17100.00),(3600000.00,0.230,62100.00),(4800000.00,0.305,540000.00)],
}

AnexoTipo = Literal["I","II","III","IV","V","Auto"]

@dataclass
class SimplesInput:
    rbt12: float
    receita_mes: float
    anexo: AnexoTipo
    folha_12m: float = 0.0
    atividade_sujeita_fator_r: bool = False
    considerar_sublimite: bool = False
    icms_iss_foras: bool = False

def _escolher_anexo(inp: SimplesInput) -> str:
    if inp.anexo != "Auto":
        return inp.anexo
    if not inp.atividade_sujeita_fator_r:
        return "III"
    if inp.rbt12 <= 0:
        return "V"
    fator_r = (inp.folha_12m or 0.0) / inp.rbt12
    return "III" if fator_r >= 0.28 else "V"

def _faixa(anexo: str, rbt12: float) -> Tuple[float, float]:
    faixas = TABELAS_SIMPLES[anexo]
    for limite, aliq, pd in faixas:
        if rbt12 <= limite:
            return aliq, pd
    aliq, pd = faixas[-1][1], faixas[-1][2]
    return aliq, pd

def aliquota_efetiva(aliq_nom: float, pd: float, rbt12: float) -> float:
    if rbt12 <= 0:
        return 0.0
    return max((rbt12 * aliq_nom - pd) / rbt12, 0.0)

def calcular_simples(inp: SimplesInput):
    anexo = _escolher_anexo(inp)
    aliq_nom, pd = _faixa(anexo, inp.rbt12)
    aliq_eff = aliquota_efetiva(aliq_nom, pd, inp.rbt12)
    das = aliq_eff * max(inp.receita_mes, 0.0)
    base_difal = float(getattr(inp, "base_difal", 0.0) or 0.0)
    aliq_inter = float(getattr(inp, "aliq_inter", 0.0) or 0.0)
    aliq_interna_dest = float(getattr(inp, "aliq_interna_dest", 0.0) or 0.0)
    fcp_perc = float(getattr(inp, "fcp_perc", 0.0) or 0.0)
    difal_parte = max((aliq_interna_dest - aliq_inter), 0.0) * base_difal
    fcp_valor  = fcp_perc * base_difal
    difal_total = difal_parte + fcp_valor
    return {
        "anexo": anexo,
        "aliquota_nominal": aliq_nom,
        "parcela_deduzir": pd,
        "aliquota_efetiva": aliq_eff,
        "das_mes": das,
        "difal_base": base_difal,
        "difal_parte": difal_parte,
        "fcp_valor": fcp_valor,
        "difal_total": difal_total,
        "das_total_com_difal": das + difal_total,
        "aliq_inter": aliq_inter,
        "aliq_interna_dest": aliq_interna_dest,
        "fcp_perc": fcp_perc,
    }

# ============================
# Exportadores
# ============================

import numpy as np

def _df_detalhamento(e: Entradas, r: ResultadoRegime, periodo: PERIODO_TIPO, regime_nome: str) -> pd.DataFrame:
    if regime_nome == "Lucro Presumido":
        aliq_pis = PIS_PRESUMIDO
        aliq_cofins = COFINS_PRESUMIDO
    else:
        aliq_pis = PIS_REAL
        aliq_cofins = COFINS_REAL

    lim = limite_irpj(periodo, e.meses_personalizado)
    base_exced = max(r.base_irpj - lim, 0.0)

    linhas = [
        {"Tributo": "PIS", "Base": r.base_pis, "Crédito": r.credito_pis, "Alíquota": aliq_pis, "Valor": r.pis},
        {"Tributo": "COFINS", "Base": r.base_cofins, "Crédito": r.credito_cofins, "Alíquota": aliq_cofins, "Valor": r.cofins},
        {"Tributo": "IRPJ (15%)", "Base": r.base_irpj, "Crédito": 0.0, "Alíquota": IRPJ_ALIQ, "Valor": r.irpj_15},
        {"Tributo": "IRPJ Adicional", "Base": base_exced, "Crédito": 0.0, "Alíquota": IRPJ_ADICIONAL_ALIQ, "Valor": r.irpj_adicional},
        {"Tributo": "CSLL", "Base": r.base_csll, "Crédito": 0.0, "Alíquota": CSLL_ALIQ, "Valor": r.csll},
        {"Tributo": "INSS", "Base": e.folha_inss_base, "Crédito": 0.0, "Alíquota": e.inss_aliquota, "Valor": r.inss},
    ]

    if empresa_de_servicos(e):
        linhas.append({"Tributo": "ISS", "Base": e.receita_bruta, "Crédito": 0.0, "Alíquota": ISS_ALIQ, "Valor": r.iss})

    linhas.append({
        "Tributo": "ICMS",
        "Base": (0.0 if e.servicos_sem_icms else e.receita_icms * (1.0 - e.icms_percentual_st)),
        "Crédito": r.icms_credito,
        "Alíquota": e.icms_aliquota,
        "Valor": r.icms_devido
    })

    # 🔧 aqui troquei None por np.nan (para não aparecer None na tela)
    linhas.append({"Tributo": "TOTAL", "Base": np.nan, "Crédito": np.nan, "Alíquota": np.nan, "Valor": r.total_impostos})    
    df = pd.DataFrame(linhas)
    df = normalize_df_for_streamlit(df)
    return df

    


def gerar_excel(rp: ResultadoRegime, rr: ResultadoRegime, e: Entradas, periodo: PERIODO_TIPO, sn: dict | None = None) -> bytes:
    cols = {
    "Imposto": ["PIS", "COFINS", "IRPJ", "CSLL", "INSS", "ISS", "ICMS", "Total", "Carga sobre Receita"],
    "Lucro Presumido": [rp.pis, rp.cofins, rp.irpj_total, rp.csll, rp.inss, rp.iss, rp.icms_devido, rp.total_impostos, rp.carga_efetiva_sobre_receita],
    "Lucro Real": [rr.pis, rr.cofins, rr.irpj_total, rr.csll, rr.inss, rr.iss, rr.icms_devido, rr.total_impostos, rr.carga_efetiva_sobre_receita],
    }
    
    if sn is not np.nan:
        cols["Simples Nacional"] = [
            "", "", "", "", "", "", "",
            sn.get("das_total_com_difal", sn["das_mes"]),
            (sn.get("das_total_com_difal", sn["das_mes"]) / sn["receita_mes"]) if sn.get("receita_mes", 0) > 0 else 0.0
        ]
    df_comp = pd.DataFrame(cols)

    dflp = _df_detalhamento(e, rp, periodo, "Lucro Presumido")
    dflr = _df_detalhamento(e, rr, periodo, "Lucro Real")
    
    

    periodo_desc = e.periodo if e.periodo != "Personalizado" else f"Personalizado ({e.meses_personalizado} meses)"
    if str(e.atividade).startswith("Personalizado"):
        atividade_desc = f"Personalizado (IRPJ {e.presumido_irpj_base*100:.2f}%, CSLL {e.presumido_csll_base*100:.2f}%)"
    else:
        atividade_desc = e.atividade

    params_rows = [
        ("Período de apuração", periodo_desc, "text"),
        ("Atividade (Presumido)", atividade_desc, "text"),
        ("Receita Bruta", e.receita_bruta, "money"),
        ("Folha (Base INSS)", e.folha_inss_base, "money"),
        ("Despesas Totais", e.despesas_totais, "money"),
        ("Energia Elétrica", e.energia_eletrica, "money"),
        ("Aluguel", e.aluguel, "money"),
        ("Receita Mercadorias (ICMS)", e.receita_icms, "money"),
        ("ICMS Alíquota", e.icms_aliquota, "percent"),
        ("ICMS Créditos", e.icms_creditos, "money"),
        ("% vendas ICMS-ST", e.icms_percentual_st, "percent"),
        ("INSS Alíquota", e.inss_aliquota, "percent"),
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        pd.DataFrame().to_excel(writer, sheet_name="Relatório", index=False)
        ws = writer.sheets["Relatório"]
        wb = writer.book

        money_fmt = wb.add_format({"num_format": "R$ #,##0.00"})
        perc_fmt = wb.add_format({"num_format": "0.00%"})
        header_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True,"align": "center","valign": "vcenter"})
        title_fmt = wb.add_format({"bold": True, "font_size": 14})
        total_text_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True})
        total_money_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True, "num_format": "R$ #,##0.00"})
        total_perc_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True, "num_format": "0.00%"})

        def write_block(title, start_row, start_col, df, total_row_name: str | None):
            ws.merge_range(start_row, start_col, start_row, start_col + max(df.shape[1]-1, 0), title, title_fmt)
            r = start_row + 1
            for j, col in enumerate(df.columns):
                ws.write(r, start_col + j, col, header_fmt)
            r += 1
            for i in range(len(df)):
                row_is_total = False
                first_col_name = df.columns[0] if len(df.columns) else ""
                if total_row_name and first_col_name in ("Tributo", "Imposto"):
                    row_is_total = str(df.iloc[i, 0]).strip().upper() in {total_row_name.upper()}
                row_label =  str(df.iloc[i, 0]) if first_col_name == "Item" else ""
                for j, col in enumerate(df.columns):
                    val = df.iloc[i, j]
                    fmt = None
                    if col in {"Base", "Crédito", "Valor"}: fmt = money_fmt
                    elif col in {"Alíquota"}: fmt = perc_fmt
                    if first_col_name == "Imposto" and j > 0:
                        fmt = perc_fmt if df.iloc[i, 0] == "Carga sobre Receita" else money_fmt
                    if first_col_name == "Item" and col == "Valor":
                        if "alíquota" in row_label.lower(): fmt = perc_fmt
                        elif row_label in ("RBT12","Receita do mês","Folha 12m","Parcela a Deduzir (PD)","DAS do mês"): fmt = money_fmt
                    if row_is_total:
                        if j == 0: fmt = total_text_fmt
                        elif fmt is perc_fmt: fmt = total_perc_fmt
                        else: fmt = total_money_fmt
                    cell_row = r + i; cell_col = start_col + j
                    if pd.isna(val): ws.write_blank(cell_row, cell_col, np.nan, fmt)
                    elif isinstance(val, (int, float)) and fmt is not np.nan: ws.write_number(cell_row, cell_col, float(val), fmt)
                    else: ws.write(cell_row, cell_col, val, fmt)
            for j, col in enumerate(df.columns):
                width = 24 if col in ("Regime","Tributo","Imposto","Item") else 18
                ws.set_column(start_col + j, start_col + j, width)
            return r + len(df) + 3

        def write_params_block(start_row, start_col, rows):
            ws.merge_range(start_row, start_col, start_row, start_col + 1, "Entradas (Parâmetros)", title_fmt)
            r = start_row + 1
            ws.write(r, start_col + 0, "Parâmetro", header_fmt)
            ws.write(r, start_col + 1, "Informação", header_fmt)
            r += 1
            for i, (name, value, kind) in enumerate(rows):
                ws.write(r + i, start_col + 0, name)
                fmt = money_fmt if kind == "money" else perc_fmt if kind == "percent" else np.nan
                if isinstance(value, (int, float)) and fmt is not np.nan: ws.write_number(r + i, start_col + 1, float(value), fmt)
                else: ws.write(r + i, start_col + 1, value)
            ws.set_column(start_col + 0, start_col + 0, 28)
            ws.set_column(start_col + 1, start_col + 1, 26)
            return r + len(rows) + 3

        row = 0
        row = write_block("Resumo (Comparativo)", row, 0, df_comp, total_row_name="Total")

        # --- GRÁFICO (Excel): Total por Regime a partir de H16 ---
        from xlsxwriter.utility import xl_rowcol_to_cell, xl_range

        start_row = 15   # linha 16 (0-based)
        start_col = 7    # coluna H (0-based)

        regimes = ["Lucro Presumido", "Lucro Real"]
        totais  = [float(rp.total_impostos), float(rr.total_impostos)]
        if sn is not None:
            regimes.append("Simples Nacional")
            totais.append(float(sn.get("das_total_com_difal", sn["das_mes"])))

        # header
        ws.write(start_row,   start_col,     "Regime", header_fmt)
        ws.write(start_row,   start_col + 1, "Total (R$)", header_fmt)

        # dados
        for i, (reg, val) in enumerate(zip(regimes, totais), start=1):
            ws.write(start_row + i, start_col,     reg)
            ws.write_number(start_row + i, start_col + 1, val, money_fmt)

        # referências
        first = start_row + 1
        last  = start_row + len(regimes)

        categorias_ref = f"=Relatório!{xl_range(first, start_col, last, start_col)}"
        valores_ref    = f"=Relatório!{xl_range(first, start_col + 1, last, start_col + 1)}"

        chart = wb.add_chart({"type": "column"})
        chart.add_series({
            "name":       "Total por Regime",
            "categories": categorias_ref,
            "values":     valores_ref,
            "data_labels": {"value": True, "num_format": "R$ #,##0.00"},
        })
        chart.set_title({"name": "Total de tributos por regime"})
        chart.set_legend({"none": True})
        chart.set_y_axis({"num_format": "R$ #,##0"})
        chart.set_size({"width": 520, "height": 300})

        # ancora o gráfico algumas colunas à direita do range (H16 → insere em K16)
        ws.insert_chart(start_row, start_col + 3, chart)




        row = write_block("Detalhamento — Lucro Presumido", row, 0, dflp, total_row_name="TOTAL")
        row = write_block("Detalhamento — Lucro Real", row, 0, dflr, total_row_name="TOTAL")

        if sn is not None:
            df_sn = pd.DataFrame([
                {"Item": "RBT12", "": sn.get("rbt12", 0.0)},
                {"Item": "Receita do mês", "": sn.get("receita_mes", 0.0)},
                {"Item": "Folha 12m", "": sn.get("folha_12m", 0.0)},

                {"Item": "CNAE", "": "", "Info": sn.get("cnae", "")},
                {"Item": "Anexo", "": "", "Info": sn.get("anexo", "")},

                {"Item": "Alíquota Nominal", "": sn["aliquota_nominal"]},
                {"Item": "Parcela a Deduzir (PD)", "": sn["parcela_deduzir"]},
                {"Item": "Alíquota Efetiva", "": sn["aliquota_efetiva"]},
                {"Item": "DAS do mês", "": sn["das_mes"]},

                {"Item": "DIFAL Vendas — Base (soma)", "": sn.get("difal_base_v", 0.0)},
                {"Item": "DIFAL Vendas — Alíquotas / UFs", "": "", "Info": "múltiplas linhas"},
                {"Item": "DIFAL Vendas — Parcela (Δ aliq × base)", "": sn.get("difal_parte_v", 0.0)},
                {"Item": "DIFAL Vendas — FCP (R$)", "": sn.get("fcp_valor_v", 0.0)},
                {"Item": "DIFAL Vendas — Total", "": sn.get("difal_total_v", 0.0)},

                {"Item": "DIFAL Compras — Base (soma)", "": sn.get("difal_base_c", 0.0)},
                {"Item": "DIFAL Compras — Alíquotas / UFs", "": "", "Info": "múltiplas linhas"},
                {"Item": "DIFAL Compras — Parcela (Δ aliq × base)", "": sn.get("difal_parte_c", 0.0)},
                {"Item": "DIFAL Compras — FCP (R$)", "": sn.get("fcp_valor_c", 0.0)},
                {"Item": "DIFAL Compras — Total", "": sn.get("difal_total_c", 0.0)},

                {"Item": f"Total Simples ({sn.get('criterio_soma_difal','Nenhum')})",
                "Valor": sn.get("das_total_com_difal", sn["das_mes"])},
            ])

            df_sn["Valor"] = pd.to_numeric(df_sn["Valor"], errors="coerce")
            if "Info" in df_sn.columns:
                df_sn["Info"] = df_sn["Info"].fillna("").replace({None: ""})


        _ = write_params_block(0, 7, params_rows)

    return output.getvalue()

def gerar_pdf(rp: ResultadoRegime, rr: ResultadoRegime, e: Entradas, sn: dict | None = None) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm
    except Exception:
        buff = io.BytesIO()
        buff.write("Instale reportlab para PDF formatado.\n".encode("utf-8"))
        for r in (rp, rr):
            buff.write(f"{r.regime}: Total {format_brl(r.total_impostos)}\n".encode("utf-8"))
        if sn is not None:
            buff.write(f"Simples Nacional: DAS {format_brl(sn['das_mes'])}\n".encode("utf-8"))
        return buff.getvalue()

    buff = io.BytesIO()
    c = canvas.Canvas(buff, pagesize=A4)
    w, h = A4
    y = h - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, "Relatório - Comparativo Tributário")
    y -= 0.8*cm

    periodo_desc = e.periodo if e.periodo != "Personalizado" else f"Personalizado ({e.meses_personalizado} meses)"
    atividade_desc = (f"Personalizado (IRPJ {e.presumido_irpj_base*100:.2f}%, CSLL {e.presumido_csll_base*100:.2f}%)"
                      if str(e.atividade).startswith("Personalizado") else e.atividade)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Parâmetros informados")
    y -= 0.5*cm
    c.setFont("Helvetica", 10)
    linhas_param = [
        ("Período de apuração", periodo_desc),
        ("Atividade (Presumido)", atividade_desc),
        ("Receita Bruta", format_brl(e.receita_bruta)),
        ("Folha (Base INSS)", format_brl(e.folha_inss_base)),
        ("Despesas Totais", format_brl(e.despesas_totais)),
        ("Energia Elétrica", format_brl(e.energia_eletrica)),
        ("Aluguel", format_brl(e.aluguel)),
        ("Receita Mercadorias (ICMS)", format_brl(e.receita_icms)),
        ("ICMS Alíquota", f"{e.icms_aliquota*100:.2f}%"),
        ("ICMS Créditos", format_brl(e.icms_creditos)),
        ("% vendas ICMS-ST", f"{e.icms_percentual_st*100:.2f}%"),
        ("INSS Alíquota", f"{e.inss_aliquota*100:.2f}%"),
    ]
    for nome, val in linhas_param:
        c.drawString(2.5*cm, y, f"{nome}: {val}"); y -= 0.4*cm
        if y < 3*cm: c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)

    y -= 0.2*cm


    def draw_column_chart(cvs, x0, y0, w, h, pares):
        """
        Desenha colunas verticais (Regime, Total) centradas em (x0,y0) com largura w, altura h.
        pares: list[tuple[str, float]]
        """
        if not pares:
            return
        max_v = max(v for _, v in pares) or 1.0
        n = len(pares)
        # cálculo de largura de barra e gaps para ficar compacto e centralizado
        gap = 18
        bar_w = min(58, max(28, (w - gap*(n+1)) / max(n,1)))
        total_w = bar_w*n + gap*(n+1)
        start_x = x0 + (w - total_w)/2.0

        # eixo Y (0 -> max)
        cvs.setLineWidth(0.6)
        cvs.line(x0, y0, x0, y0 + h)  # eixo Y
        cvs.line(x0, y0, x0 + w, y0)  # eixo X

        cvs.setFont("Helvetica", 8)
        # marcas Y (0%, 50%, 100%)
        for frac in (0.0, 0.5, 1.0):
            yy = y0 + h*frac
            cvs.setDash(1,2) if frac not in (0.0,) else cvs.setDash()  # linha pontilhada
            cvs.line(x0, yy, x0 + w, yy)
            cvs.setDash()
            label = format_brl(max_v*frac)
            cvs.drawRightString(x0 - 6, yy - 3, label)

        # barras
        cvs.setFillGray(0.2)  # cinza neutro
        for i, (nome, val) in enumerate(pares):
            x = start_x + gap*(i+1) + bar_w*i
            altura = 0 if max_v <= 0 else (val / max_v) * (h - 8)
            cvs.rect(x, y0, bar_w, altura, fill=1, stroke=0)
            # rótulo do valor
            cvs.setFillGray(0)
            cvs.drawCentredString(x + bar_w/2, y0 + altura + 8, format_brl(val))
            # rótulo do regime
            cvs.drawCentredString(x + bar_w/2, y0 - 12, nome)


    def bloco(titulo, r, y):
        c.setFont("Helvetica-Bold", 12); c.drawString(2*cm, y, titulo); y -= 0.45*cm
        c.setFont("Helvetica", 10)
        linhas = [
            ("PIS", r.pis), ("COFINS", r.cofins), ("IRPJ (15%)", r.irpj_15),
            ("IRPJ Adicional", r.irpj_adicional), ("IRPJ Total", r.irpj_total),
            ("CSLL", r.csll), ("INSS", r.inss)
        ]
        if empresa_de_servicos(e):
            linhas.append(("ISS", r.iss))
        linhas += [
            ("ICMS Devido", r.icms_devido),
            ("Total", r.total_impostos),
            ("Carga sobre Receita", f"{r.carga_efetiva_sobre_receita*100:.2f}%"),
        ]

        for nome, val in linhas:
            txt = f"{nome}: {format_brl(float(val))}" if isinstance(val, (int, float)) else f"{nome}: {val}"
            c.drawString(2.5*cm, y, txt); y -= 0.38*cm
            if y < 3*cm: c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)
        return y

    y = bloco("Lucro Presumido", rp, y); y -= 0.4*cm
    y = bloco("Lucro Real", rr, y)

    y -= 0.4*cm


    if sn is not None:
        c.setFont("Helvetica-Bold", 12); c.drawString(2*cm, y, "Simples Nacional"); y -= 0.45*cm
        c.setFont("Helvetica", 10)
        linhas_sn = [
            ("RBT12", format_brl(sn.get("rbt12", 0.0))),
            ("Receita do mês", format_brl(sn.get("receita_mes", 0.0))),
            ("Folha 12m", format_brl(sn.get("folha_12m", 0.0))),
            ("Anexo", str(sn["anexo"])),
            ("Alíquota Nominal", f"{sn['aliquota_nominal']*100:.2f}%"),
            ("Parcela a Deduzir (PD)", format_brl(sn["parcela_deduzir"])),
            ("Alíquota Efetiva", f"{sn['aliquota_efetiva']*100:.2f}%"),
            ("DAS do mês", format_brl(sn["das_mes"])),

            # Vendas (agregado)
            ("DIFAL Vendas — Base (soma)", format_brl(sn.get("difal_base_v", 0.0))),
            ("DIFAL Vendas — Alíquotas / UFs", "múltiplas linhas"),
            ("DIFAL Vendas — Parcela (aliq × base)", format_brl(sn.get("difal_parte_v", 0.0))),
            ("DIFAL Vendas — FCP (R$)", format_brl(sn.get("fcp_valor_v", 0.0))),
            ("DIFAL Vendas — Total", format_brl(sn.get("difal_total_v", 0.0))),

            # Compras (agregado)
            ("DIFAL Compras — Base (soma)", format_brl(sn.get("difal_base_c", 0.0))),
            ("DIFAL Compras — Alíquotas / UFs", "múltiplas linhas"),
            ("DIFAL Compras — Parcela (aliq × base)", format_brl(sn.get("difal_parte_c", 0.0))),
            ("DIFAL Compras — FCP (R$)", format_brl(sn.get("fcp_valor_c", 0.0))),
            ("DIFAL Compras — Total", format_brl(sn.get("difal_total_c", 0.0))),

            (f"Total Simples ({sn.get('criterio_soma_difal','Nenhum')})", format_brl(sn.get("das_total_com_difal", sn["das_mes"]))),
        ]
        for nome, val in linhas_sn:
            c.drawString(2.5*cm, y, f"{nome}: {val}"); y -= 0.38*cm
            if y < 3*cm: c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)

    # === GRÁFICO FINAL: Total de tributos por regime ===
    pares = [("Lucro Pres.", float(rp.total_impostos)),
             ("Lucro Real", float(rr.total_impostos))]
    if sn is not None:
        pares.append(("Simples", float(sn.get("das_total_com_difal", sn["das_mes"]))))

    # se faltar espaço na página corrente, abre nova página
    if y < 9*cm:
        c.showPage(); y = h - 2*cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Total de tributos por regime")
    y -= 0.6*cm

    # largura menor (≈ 12 cm) e centralizado
    gw = 12*cm
    gh = 6.2*cm
    gx = (w - gw) / 2.0
    gy = y - gh
    draw_column_chart(c, gx, gy, gw, gh, pares)
    y = gy - 0.8*cm
    

    c.showPage(); c.save()
    return buff.getvalue()

# ============================
# UI (Streamlit)
# ============================

UFs = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PE","PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"]
_SSE = {"SP","RJ","MG","PR","SC","RS"}  # Sul/Sudeste sem ES
_NNECO_ES = {"ES","DF","GO","MT","MS","TO","BA","SE","AL","PE","PB","RN","CE","PI","MA","PA","AP","AM","RR","RO","AC"}

def icms_interestadual(orig_uf: str, dest_uf: str, mercadoria_importada: bool=False) -> float:
    orig = (orig_uf or "").upper(); dest = (dest_uf or "").upper()
    if mercadoria_importada: return 0.04
    if orig in _SSE and dest in _NNECO_ES: return 0.07
    return 0.12

def ui() -> None:
    st.set_page_config(page_title="Calculadora Tributária", layout="wide")
    st.title("📊 Calculadora Tributária")
    st.caption("Ferramenta de planejamento — Regras simplificadas. Verifique a legislação específica antes de decidir.")
    set_sidebar_style(SIDEBAR_WIDTH_PX, compact_gap_px=6)

    with st.sidebar:
        st.header("Parâmetros Gerais")
        periodo = st.selectbox("Período de apuração", ["Mensal", "Trimestral", "Anual", "Personalizado"], index=0)
        meses_personalizado = 0
        if periodo == "Personalizado":
            meses_personalizado = int(st.number_input("Quantidade de meses", min_value=1, max_value=60, value=5, step=1))
        receita = moeda_input("Receita Bruta do período (R$)", key="receita_bruta", value=0.0)

        st.divider()
        st.header("Simples Nacional")

        # Carrega mapa de CNAE (JSON ao lado do app.py)
        mapa_cnae = load_cnae_map("cnae_map_2025.json")

        # ===========================
        # 1) CNAE no topo (autocomplete único)
        # ===========================
        cnae_informado = ""
        anexo_sugerido = None

        # Opções "CÓDIGO — DESCRIÇÃO — [ANEXO]"
        opcoes = []
        for cod, info in mapa_cnae.items():
            desc = (info.get("descricao") or "").strip()
            anx  = (info.get("anexo") or "AUTO").strip()
            label = f"{cod} — {desc} — [{anx}]"
            opcoes.append((label, cod))
        opcoes.sort(key=lambda x: x[0].lower())

        try:
            label_escolhido = st.selectbox(
                "Buscar CNAE por código/descrição",
                options=[o[0] for o in opcoes],
                index=None,
                placeholder="Digite o CNAE (ex.: 6201-5/01) ou parte da descrição…",
            )
        except TypeError:
            labels = ["— selecione —"] + [o[0] for o in opcoes]
            label_escolhido = st.selectbox("Buscar CNAE por código/descrição", options=labels, index=0)
            if label_escolhido == "— selecione —":
                label_escolhido = None

        if label_escolhido:
            idx = [o[0] for o in opcoes].index(label_escolhido)
            cnae_informado = opcoes[idx][1]
            st.session_state["sn_cnae"] = cnae_informado

        # ===========================
        # 2) Demais parâmetros do Simples
        # ===========================
        rbt12 = moeda_input("RBT12 (R$) — últimos 12 meses", key="sn_rbt12", value=0.0)
        receita_mes_sn = moeda_input("Receita do mês (R$)", key="sn_receita_mes", value=0.0)
        folha_12m_sn = moeda_input("Folha 12m (R$) — p/ Fator R", key="sn_folha12m", value=0.0)

        atividade_fator_r = st.checkbox(
            "Atividade sujeita ao Fator R (III/V)?",
            value=True,
            help="Para CNAEs que alternam entre Anexo III ou V, aplica a regra do Fator R (28%)."
        )

        # ===========================
        # 3) Sugerir Anexo automaticamente pelo CNAE (sempre)
        # ===========================
        if cnae_informado:
            anexo_sugerido = anexo_por_cnae_mapa(
                cnae=cnae_informado,
                rbt12=rbt12,
                folha_12m=folha_12m_sn,
                sujeito_fator_r=atividade_fator_r,
                mapa=mapa_cnae
            )
            if anexo_sugerido:
                st.success(f"Anexo sugerido pelo CNAE **{normalize_cnae_mask(cnae_informado)}** → **{anexo_sugerido}**")
            else:
                st.info("CNAE sem anexo definido no arquivo. Usando 'Auto (Fator R)' como padrão, mas você pode escolher abaixo.")

        # ===========================
        # 4) Override manual do Anexo (sempre visível)
        # ===========================
        opcoes_anexo = ["Auto (Fator R)", "I", "II", "III", "IV", "V"]
        default_label = anexo_sugerido if anexo_sugerido in {"I","II","III","IV","V"} else "Auto (Fator R)"
        idx_default = opcoes_anexo.index(default_label)
        anexo_opt = st.selectbox(
            "Anexo (pode sobrescrever o sugerido)",
            opcoes_anexo,
            index=idx_default,
            help="Se escolher manualmente, esse valor prevalece sobre o sugerido pelo CNAE."
        )

        # --------- (seu bloco de DIFAL vem depois, igual estava) ---------




        # --------- ICMS DIFAL — VENDAS (Múltiplas linhas) ---------
        st.header("ICMS DIFAL — Simples Nacional")
        difal_v_base_soma = 0.0
        difal_v_parte_soma = 0.0
        difal_v_fcp_soma   = 0.0
        difal_v_total      = 0.0   # <- inicializa aqui

        with st.expander("DIFAL nas VENDAS (consumidor final, múltiplos estados)"):
            st.caption("Adicione uma linha por UF destino. A alíquota interestadual é automática (4%% importada; 7%% S/SE(exc.ES) → N/NE/CO/ES; 12%% demais).")
            df_def_v = pd.DataFrame([
                {"UF origem": "SP", "UF destino": "BA", "Importada?": False, "Base (R$)": "0,00", "Aliq. interna destino (%)": 18.0, "FCP (%)": 0.0},
                {"UF origem": "PR", "UF destino": "CE", "Importada?": False, "Base (R$)": "0,00", "Aliq. interna destino (%)": 18.0, "FCP (%)": 0.0},
            ])
            vendas_cols = st.data_editor(
                df_def_v,
                key="vendas_difal_rows",
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "UF origem": st.column_config.SelectboxColumn(options=UFs),
                    "UF destino": st.column_config.SelectboxColumn(options=UFs),
                    "Importada?": st.column_config.CheckboxColumn(help="Se a mercadoria for importada → 4% interestadual"),
                    # Aceita "1.234,56" e "1234.56"; formatação visual fica a cargo do usuário
                    "Base (R$)": st.column_config.TextColumn(help="Aceita 1.234,56 ou 1234.56"),
                    "Aliq. interna destino (%)": st.column_config.NumberColumn(format="%.2f", step=0.5, min_value=0.0, max_value=100.0),
                    "FCP (%)": st.column_config.NumberColumn(format="%.2f", step=0.5, min_value=0.0, max_value=100.0),
                },
            )
            
            if isinstance(vendas_cols, pd.DataFrame) and not vendas_cols.empty:
                for _, row in vendas_cols.iterrows():
                    # Lê UFs e flag importada
                    uf_o = str(row.get("UF origem", "") or "")
                    uf_d = str(row.get("UF destino", "") or "")
                    imp  = bool(row.get("Importada?", False))

                    # Base em texto: aceita "1.234,56" / "1234.56"
                    base = brl_to_float(row.get("Base (R$)", 0))

                    # Alíquotas (%): aceita 18 / 18,5 / "18%" / etc.
                    aliq_int_dest = parse_percent_to_frac(row.get("Aliq. interna destino (%)", 0))
                    fcp           = parse_percent_to_frac(row.get("FCP (%)", 0))

                    # Garante que não propague NaN
                    if pd.isna(base): base = 0.0
                    if pd.isna(aliq_int_dest): aliq_int_dest = 0.0
                    if pd.isna(fcp): fcp = 0.0

                    if not uf_o or not uf_d:
                        continue

                    aliq_inter_row   = icms_interestadual(uf_o, uf_d, imp)
                    difal_parte_row  = max(aliq_int_dest - aliq_inter_row, 0.0) * base
                    fcp_row          = fcp * base

                    difal_v_base_soma  += base
                    difal_v_parte_soma += difal_parte_row
                    difal_v_fcp_soma   += fcp_row

            difal_v_total = difal_v_parte_soma + difal_v_fcp_soma

        # --------- ICMS DIFAL — COMPRAS (Múltiplas linhas) ---------
        difal_c_base_soma = 0.0
        difal_c_parte_soma = 0.0
        difal_c_fcp_soma   = 0.0
        difal_c_total      = 0.0   # <- inicializa aqui

        with st.expander("DIFAL nas COMPRAS (uso/consumo/ativo, múltiplos estados)"):
            st.caption("Adicione uma linha para cada UF de origem/destino. A alíquota interestadual é calculada automaticamente (4% importada; 7% S/SE(exc.ES) → N/NE/CO/ES; 12% demais).")
            df_def = pd.DataFrame([
                {"UF origem": "SP", "UF destino": "BA", "Importada?": False, "Base (R$)": "0,00", "Aliq. interna destino (%)": 18.0, "FCP (%)": 0.0},
                {"UF origem": "MG", "UF destino": "GO", "Importada?": False, "Base (R$)": "0,00", "Aliq. interna destino (%)": 17.0, "FCP (%)": 0.0},
            ])
            compras_cols = st.data_editor(
                df_def,
                key="compras_difal_rows",
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "UF origem": st.column_config.SelectboxColumn(options=UFs),
                    "UF destino": st.column_config.SelectboxColumn(options=UFs),
                    "Importada?": st.column_config.CheckboxColumn(help="Se a mercadoria for importada → 4% interestadual"),
                    "Base (R$)": st.column_config.TextColumn(help="Aceita 1.234,56 ou 1234.56"),
                    "Aliq. interna destino (%)": st.column_config.NumberColumn(format="%.2f", step=0.5, min_value=0.0, max_value=100.0),
                    "FCP (%)": st.column_config.NumberColumn(format="%.2f", step=0.5, min_value=0.0, max_value=100.0),
                },
            )
            
            if isinstance(compras_cols, pd.DataFrame) and not compras_cols.empty:
                for _, row in compras_cols.iterrows():
                    uf_o = str(row.get("UF origem", "") or "")
                    uf_d = str(row.get("UF destino", "") or "")
                    imp  = bool(row.get("Importada?", False))

                    base = brl_to_float(row.get("Base (R$)", 0))
                    aliq_int_dest = parse_percent_to_frac(row.get("Aliq. interna destino (%)", 0))
                    fcp           = parse_percent_to_frac(row.get("FCP (%)", 0))

                    if pd.isna(base): base = 0.0
                    if pd.isna(aliq_int_dest): aliq_int_dest = 0.0
                    if pd.isna(fcp): fcp = 0.0

                    if not uf_o or not uf_d:
                        continue

                    aliq_inter_row   = icms_interestadual(uf_o, uf_d, imp)
                    difal_parte_row  = max(aliq_int_dest - aliq_inter_row, 0.0) * base
                    fcp_row          = fcp * base

                    difal_c_base_soma  += base
                    difal_c_parte_soma += difal_parte_row
                    difal_c_fcp_soma   += fcp_row

            difal_c_total = difal_c_parte_soma + difal_c_fcp_soma

        st.caption("Qual DIFAL deve somar ao DAS no total do Simples?")
        modo_soma_difal = st.radio(
            "Aplicação do DIFAL no total",
            ["Nenhum", "Somar Vendas", "Somar Compras", "Somar Ambos"],
            index=0,
            horizontal=True
        )

        st.divider()
        st.header("Lucro Presumido — Bases Presumidas")
        atividade = st.selectbox("Atividade (define base presumida)", [
            "Comércio/Indústria (IRPJ 8% | CSLL 12%)",
            "Serviços (IRPJ 32% | CSLL 32%)",
            "Personalizado",
        ])
        if atividade.startswith("Comércio"):
            presumido_irpj_base = 0.08; presumido_csll_base = 0.12
        elif atividade.startswith("Serviços"):
            presumido_irpj_base = 0.32; presumido_csll_base = 0.32
        else:
            presumido_irpj_base = st.number_input("Base Presumida IRPJ (%)", 0.0, 100.0, 8.0, 0.5) / 100.0
            presumido_csll_base = st.number_input("Base Presumida CSLL (%)", 0.0, 100.0, 12.0, 0.5) / 100.0

        # NOVO — modo isenção setorial (ex.: livros)
        st.header("Isenção PIS/COFINS/ICMS")
        zerar_pis_cofins_icms = st.toggle(
            "Zerar PIS, COFINS e ICMS (casos específicos, ex.: livros)",
            value=False,
            help="Quando ligado, zera PIS, COFINS e ICMS nos cálculos do Lucro Presumido e Lucro Real. "
                "Use para atividades com imunidade/isenção setorial. Não afeta Simples Nacional."
    )

        st.header("Folha / INSS")
        folha_inss = moeda_input("Base da Folha (R$)", key="folha_inss", value=0.0)
        inss_aliquota = st.number_input("Alíquota INSS (%)", 0.0, 100.0, 26.8, 0.1) / 100.0

        st.divider()
        st.header("Lucro Real — Despesas e Créditos")
        despesas_totais = moeda_input("Despesas Totais do período (R$)", key="despesas_totais", value=0.0)
        energia = moeda_input("Energia Elétrica (R$) — crédito PIS/COFINS", key="energia", value=0.0)
        aluguel = moeda_input("Aluguel (R$) — crédito PIS/COFINS", key="aluguel", value=0.0)

        # ============================
        # ICMS — Simplificado (modo único: Base × Alíquota)
        # ============================

        st.header("ICMS — Simplificado")
        servicos_sem_icms = st.checkbox(
            "Empresa só de serviços (sem ICMS)",
            value=False,
            help="Zera e oculta campos de ICMS."
        )

        # Modo único (Base × Alíquota)
        modo_icms = "Base × Alíquota (padrão)"

        if not servicos_sem_icms:
            # Base de vendas (saídas) sobre as quais incide ICMS
            receita_icms = moeda_input("Receita de Mercadorias (base ICMS) (R$)", key="receita_icms", value=0.0)

            # Alíquota do ICMS (usada no débito e também no crédito)
            icms_aliquota = st.number_input("Alíquota ICMS (%)", 0.0, 100.0, 20.0, 0.5) / 100.0

            # Base de compras (entradas) com direito a crédito de ICMS
            icms_creditos = moeda_input("Compras do período (base ICMS) (R$)", key="icms_creditos", value=0.0)

            # Percentual das vendas sujeitas a ICMS-ST (não gera débito próprio)
            icms_percentual_st = st.number_input("% das vendas com ICMS-ST (0-100)", 0.0, 100.0, 0.0, 1.0) / 100.0
        else:
            # Mantém compatibilidade com o restante do código
            receita_icms = 0.0
            icms_aliquota = 0.0
            icms_creditos = 0.0
            icms_percentual_st = 0.0



        inject_currency_focus_script()

    entradas = Entradas(
    periodo=periodo, meses_personalizado=meses_personalizado,
    receita_bruta=receita, atividade=atividade,
    presumido_irpj_base=presumido_irpj_base, presumido_csll_base=presumido_csll_base,
    folha_inss_base=folha_inss, inss_aliquota=inss_aliquota,
    despesas_totais=despesas_totais, energia_eletrica=energia, aluguel=aluguel,
    servicos_sem_icms=servicos_sem_icms, receita_icms=receita_icms,
    icms_aliquota=icms_aliquota, icms_creditos=icms_creditos, icms_percentual_st=icms_percentual_st,
    zerar_pis_cofins_icms=zerar_pis_cofins_icms,
)


    if st.button("Calcular", type="primary"):
        st.session_state["res_presumido"] = calcular_lucro_presumido(entradas)
        st.session_state["res_real"] = calcular_lucro_real(entradas)

    an = "Auto" if anexo_opt.startswith("Auto") else anexo_opt
    sn = calcular_simples(SimplesInput(
        rbt12=rbt12,
        receita_mes=receita_mes_sn,
        anexo=an,
        folha_12m=folha_12m_sn,
        atividade_sujeita_fator_r=atividade_fator_r,
    ))
    if 'sn_cnae' in st.session_state:
        sn["cnae"] = normalize_cnae_mask(st.session_state['sn_cnae'])



    # ---------- DIFAL agregados ----------
    # VENDAS
    base_difal_v   = difal_v_base_soma
    difal_parte_v  = difal_v_parte_soma
    fcp_valor_v    = difal_v_fcp_soma
    difal_total_v  = difal_v_total

    # COMPRAS
    base_difal_c   = difal_c_base_soma
    difal_parte_c  = difal_c_parte_soma
    fcp_valor_c    = difal_c_fcp_soma
    difal_total_c  = difal_c_total

    soma_v = (modo_soma_difal in ["Somar Vendas", "Somar Ambos"])
    soma_c = (modo_soma_difal in ["Somar Compras", "Somar Ambos"])
    das_total = sn["das_mes"] + (difal_total_v if soma_v else 0.0) + (difal_total_c if soma_c else 0.0)

    sn.update({
        "rbt12": rbt12, "receita_mes": receita_mes_sn, "folha_12m": folha_12m_sn,
        # vendas (agregado)
        "difal_base_v": base_difal_v,
        "difal_parte_v": difal_parte_v,
        "fcp_valor_v": fcp_valor_v,
        "difal_total_v": difal_total_v,
        # compras (agregado)
        "difal_base_c": base_difal_c,
        "difal_parte_c": difal_parte_c,
        "fcp_valor_c": fcp_valor_c,
        "difal_total_c": difal_total_c,
        # totais
        "criterio_soma_difal": modo_soma_difal,
        "das_total_com_difal": das_total,
    })
    st.session_state["res_simples"] = sn

    if "res_presumido" in st.session_state and "res_real" in st.session_state:
        rp: ResultadoRegime = st.session_state["res_presumido"]
        rr: ResultadoRegime = st.session_state["res_real"]
        tem_sn = "res_simples" in st.session_state
        sn = st.session_state.get("res_simples", None)

        kpi_cols = st.columns(3 if tem_sn else 2)
        with kpi_cols[0]:
            st.subheader("Lucro Presumido")
            st.metric("Total de Impostos", format_brl(rp.total_impostos))
            st.metric("Carga Efetiva", format_pct_br(rp.carga_efetiva_sobre_receita))
        with kpi_cols[1]:
            st.subheader("Lucro Real")
            st.metric("Total de Impostos", format_brl(rr.total_impostos))
            st.metric("Carga Efetiva", format_pct_br(rr.carga_efetiva_sobre_receita))
        if tem_sn:
            with kpi_cols[2]:
                st.subheader("Simples Nacional")
                st.metric("DAS do mês", format_brl(sn["das_mes"]))
                st.metric("DIFAL Vendas", format_brl(sn.get("difal_total_v", 0.0)))
                st.metric("DIFAL Compras", format_brl(sn.get("difal_total_c", 0.0)))
                st.metric(f"Total ({sn.get('criterio_soma_difal','Nenhum')})", format_brl(sn.get("das_total_com_difal", sn["das_mes"])))
                st.metric("Alíquota Efetiva", format_pct_br(sn['aliquota_efetiva']))
                def _txt(x): 
                    return "" if x is None else str(x)
                # exemplo
                st.metric("Anexo", _txt(sn.get("anexo")))
                

        st.divider()
        if tem_sn:
            tab_lp, tab_lr, tab_sn = st.tabs(["Detalhamento — Presumido", "Detalhamento — Real", "Detalhamento — Simples"])
        else:
            tab_lp, tab_lr = st.tabs(["Detalhamento — Presumido", "Detalhamento — Real"])
            tab_sn = None

        with tab_lp:
            dflp = _df_detalhamento(entradas, rp, periodo, "Lucro Presumido")
            st.dataframe(style_df_center_headers(dflp), use_container_width=True)

        with tab_lr:
            dflr = _df_detalhamento(entradas, rr, periodo, "Lucro Real")
            st.dataframe(style_df_center_headers(dflr), use_container_width=True)


        if tab_sn is not None:
            df_sn = pd.DataFrame([
                {"Item": "RBT12", "Valor": sn.get("rbt12", 0.0)},
                {"Item": "Receita do mês", "Valor": sn.get("receita_mes", 0.0)},
                {"Item": "Folha 12m", "Valor": sn.get("folha_12m", 0.0)},

                {"Item": "CNAE",  "Valor": "", "Info": sn.get("cnae", "")},
                {"Item": "Anexo", "Valor": "", "Info": sn.get("anexo", "")},

                {"Item": "Alíquota Nominal",       "Valor": sn["aliquota_nominal"]},
                {"Item": "Parcela a Deduzir (PD)", "Valor": sn["parcela_deduzir"]},
                {"Item": "Alíquota Efetiva",       "Valor": sn["aliquota_efetiva"]},
                {"Item": "DAS do mês",             "Valor": sn["das_mes"]},

                {"Item": "DIFAL Vendas — Base (soma)", "Valor": sn.get("difal_base_v", 0.0)},
                {"Item": "DIFAL Vendas — Alíquotas / UFs", "Valor": "", "Info": "múltiplas linhas"},
                {"Item": "DIFAL Vendas — Parcela (Δ aliq × base)", "Valor": sn.get("difal_parte_v", 0.0)},
                {"Item": "DIFAL Vendas — FCP (R$)", "Valor": sn.get("fcp_valor_v", 0.0)},
                {"Item": "DIFAL Vendas — Total", "Valor": sn.get("difal_total_v", 0.0)},

                {"Item": "DIFAL Compras — Base (soma)", "Valor": sn.get("difal_base_c", 0.0)},
                {"Item": "DIFAL Compras — Alíquotas / UFs", "Valor": "", "Info": "múltiplas linhas"},
                {"Item": "DIFAL Compras — Parcela (Δ aliq × base)", "Valor": sn.get("difal_parte_c", 0.0)},
                {"Item": "DIFAL Compras — FCP (R$)", "Valor": sn.get("fcp_valor_c", 0.0)},
                {"Item": "DIFAL Compras — Total", "Valor": sn.get("difal_total_c", 0.0)},

                {"Item": f"Total Simples ({sn.get('criterio_soma_difal','Nenhum')})",
                "Valor": sn.get("das_total_com_difal", sn["das_mes"])},
            ])

            # Numérico coerente
            df_sn["Valor"] = pd.to_numeric(df_sn["Valor"], errors="coerce")
            if "Info" in df_sn.columns:
                df_sn["Info"] = df_sn["Info"].fillna("").replace({None: ""})

            # aplica o normalizador para colunas textuais
            df_sn = normalize_df_for_streamlit(df_sn)

            def _fmt_sn(df: pd.DataFrame):
                sty = df.style.set_table_styles(HEADER_CENTER).hide(axis="index")
                s = df["Item"].astype(str)

                money_items = ["RBT12","Receita do mês","Folha 12m","Parcela a Deduzir (PD)","DAS do mês"]
                difal_suffixes = ["Base (soma)", "Parcela (Δ aliq × base)", "FCP (R$)", "Total"]
                difal_items = [f"DIFAL {tipo} — {suf}" for tipo in ("Vendas","Compras") for suf in difal_suffixes]

                money_mask = s.isin(money_items + difal_items) | s.str.startswith("Total Simples")
                perc_mask  = s.isin(["Alíquota Nominal","Alíquota Efetiva"])

                def _fmt_brl(v):
                    try: return format_brl(float(v))
                    except Exception: return v

                sty = sty.format(formatter="{:.2%}", subset=pd.IndexSlice[perc_mask, "Valor"])
                sty = sty.format(formatter=_fmt_brl, subset=pd.IndexSlice[money_mask, "Valor"])
                return sty

            with tab_sn:
                st.dataframe(_fmt_sn(df_sn), use_container_width=True)

            st.divider()
            st.subheader("Resumo (Comparativo)")

            comp_dict = {
                "Imposto": ["PIS","COFINS","IRPJ","CSLL","INSS","ISS","ICMS","Total","Carga sobre Receita"],
                "Lucro Presumido": [
                    float(rp.pis), float(rp.cofins), float(rp.irpj_total), float(rp.csll),
                    float(rp.inss), float(rp.iss), float(rp.icms_devido),
                    float(rp.total_impostos), float(rp.carga_efetiva_sobre_receita)
                ],
                "Lucro Real": [
                    float(rr.pis), float(rr.cofins), float(rr.irpj_total), float(rr.csll),
                    float(rr.inss), float(rr.iss), float(rr.icms_devido),
                    float(rr.total_impostos), float(rr.carga_efetiva_sobre_receita)
                ],
            }

            if sn is not None:
                total_simples = float(sn.get("das_total_com_difal", sn["das_mes"]))
                carga_simples = (total_simples / sn["receita_mes"]) if sn.get("receita_mes", 0) > 0 else 0.0
                # 7 NaN (linhas não monetárias do Simples), depois Total e Carga
                import numpy as np
                comp_dict["Simples Nacional"] = [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan,
                                                total_simples, float(carga_simples)]

            # >>> cria SEMPRE o df, fora do if
            df_comp = pd.DataFrame(comp_dict)
            df_comp = _sanitize_arrow(df_comp)             # numéricas → float/NaN
            df_comp = normalize_df_for_streamlit(df_comp)  # textuais → "" (sem None)

            st.dataframe(
                style_df_center_headers(
                    df_comp,
                    perc_cols=["Carga sobre Receita"],
                    money_cols=["Lucro Presumido","Lucro Real"] + (["Simples Nacional"] if "Simples Nacional" in df_comp.columns else []),
                ),
                use_container_width=True,
            )





            # === NOVO: Gráficos de comparação ===
            st.markdown("### 📈 Visualizações — Comparativo entre regimes")

            # Base de dados: Regime / Total / (Carga opcional – não vamos mostrar aqui)
            dados = [
                {"Regime": "Lucro Presumido", "Total": float(rp.total_impostos)},
                {"Regime": "Lucro Real",      "Total": float(rr.total_impostos)},
            ]
            if isinstance(sn, dict) and sn:
                total_sn = float(sn.get("das_total_com_difal", sn["das_mes"]))
                dados.append({"Regime": "Simples Nacional", "Total": total_sn})

            df_regimes = pd.DataFrame(dados)

            # --- Gráfico ÚNICO: Total em COLUNAS, compacto e centralizado ---
            # largura proporcional ao nº de barras, mas com limites para não "esticar"
            n = len(df_regimes)
            largura = min(520, max(300, 140 * n))   # mantém compacto
            altura  = 260

            graf_colunas = (
                alt.Chart(df_regimes)
                .mark_bar(size=48, cornerRadiusTopLeft=8, cornerRadiusTopRight=8)
                .encode(
                    x=alt.X("Regime:N", title="", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Total:Q", title="Total (R$)", axis=alt.Axis(format=",.0f")),
                    tooltip=[
                        alt.Tooltip("Regime:N", title="Regime"),
                        alt.Tooltip("Total:Q",  title="Total (R$)", format=",.2f"),
                    ],
                )
                .properties(width=largura, height=altura)
            )
            labels_total = graf_colunas.mark_text(
                dy=-8  # acima das barras
            ).encode(text=alt.Text("Total:Q", format=",.2f"))

            # Centraliza o gráfico usando colunas fantasma (1–auto–1)
            c1, c2, c3 = st.columns([1, 3, 1])
            with c1:
                st.markdown("&nbsp;")  # evita container vazio
            with c2:
                st.caption("Total de tributos por regime")
                st.altair_chart(graf_colunas + labels_total, use_container_width=True)
            with c3:
                st.markdown("&nbsp;")  # evita container vazio


            # — (Mantido) Detalhe por tributo — ajuda a explicar o "porquê" do total
            with st.expander("Quebra por tributo (LP x LR)"):
                breakdown = []
                def add_breakdown(regime, r):
                    breakdown.extend([
                        {"Regime": regime, "Tributo": "PIS",   "Valor": float(r.pis)},
                        {"Regime": regime, "Tributo": "COFINS","Valor": float(r.cofins)},
                        {"Regime": regime, "Tributo": "IRPJ",  "Valor": float(r.irpj_total)},
                        {"Regime": regime, "Tributo": "CSLL",  "Valor": float(r.csll)},
                        {"Regime": regime, "Tributo": "INSS",  "Valor": float(r.inss)},
                    ])
                    if empresa_de_servicos(entradas):
                        breakdown.append({"Regime": regime, "Tributo": "ISS", "Valor": float(r.iss)})
                    breakdown.append({"Regime": regime, "Tributo": "ICMS", "Valor": float(r.icms_devido)})
                add_breakdown("Lucro Presumido", rp)
                add_breakdown("Lucro Real", rr)

                df_bk = pd.DataFrame(breakdown)
                chart_stack = (
                    alt.Chart(df_bk)
                    .mark_bar()
                    .encode(
                        x=alt.X("Valor:Q", title="Total (R$)", axis=alt.Axis(format=",.0f")),
                        y=alt.Y("Regime:N", title=""),
                        color=alt.Color("Tributo:N", title="Tributo"),
                        tooltip=[
                            alt.Tooltip("Regime:N",  title="Regime"),
                            alt.Tooltip("Tributo:N", title="Tributo"),
                            alt.Tooltip("Valor:Q",   title="Valor (R$)", format=",.2f"),
                        ],
                    )
                    .properties(height=max(140, 64 * 2))
                )
                st.altair_chart(chart_stack, use_container_width=True)
          


        st.divider()
        st.subheader("Exportar Relatório")

        excel_bytes = gerar_excel(rp, rr, entradas, periodo, sn=st.session_state.get("res_simples", None))
        pdf_bytes   = gerar_pdf(rp, rr, entradas, sn=st.session_state.get("res_simples", None))

        left, right = st.columns(2)
        with left:
            st.download_button("⬇️ Baixar Excel", data=excel_bytes,
                            file_name="relatorio_calculo_tributario.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True)
        with right:
            st.download_button("⬇️ Baixar PDF", data=pdf_bytes,
                            file_name="relatorio_calculo_tributario.pdf",
                            mime="application/pdf",
                            use_container_width=True)


    with st.expander("Notas e Premissas"):
        st.markdown("""
        - **INSS**: Alíquota patronal personalizável aplicada sobre a base de folha informada.
        - **PIS/COFINS**: base = Receita Bruta − **ICMS destacado** nas saídas (estimado, não-ST). No Presumido: PIS **0,65%** e COFINS **3%**. No Real: PIS **1,65%** e COFINS **7,6%** com créditos de **energia** e **aluguel**.
        - **IRPJ/CSLL**: Presumido conforme bases; Real sobre lucro líquido simplificado. **Adicional IRPJ 10%** acima do limite do período.
        - **ICMS simplificado**: débito sobre vendas não-ST menos créditos informados.
        - **DIFAL** (Vendas e Compras) com grade dinâmica: soma de **Base**, **Δ-alíquota** e **FCP**; alíquotas interestaduais = 4% importada; 7% S/SE(exc.ES) → N/NE/CO/ES; 12% demais.
        - Ferramenta para **simulação**. Valide regras estaduais/particulares.
        """)

# ============================
# Self-tests
# ============================

def _run_self_tests():
    assert format_brl(1234.5) == "R$ 1.234,50"
    assert brl_to_float("R$ 1.234,50") == 1234.5
    assert brl_to_float("1.234,50") == 1234.5
    assert brl_to_float("R$0,00") == 0.0
    assert limite_irpj("Mensal", 0) == 20000
    assert limite_irpj("Trimestral", 0) == 60000
    assert limite_irpj("Anual", 0) == 240000
    assert limite_irpj("Personalizado", 7) == 140000
    assert adicional_irpj(50000, "Mensal", 0) == (50000-20000) * IRPJ_ADICIONAL_ALIQ
    assert adicional_irpj(100000, "Personalizado", 5) == max(100000-100000, 0) * IRPJ_ADICIONAL_ALIQ
    e = Entradas("Mensal",0,100000,"Comércio/Indústria (IRPJ 8% | CSLL 12%)",0.08,0.12,0,0,0,0,False,50000,0.18,1000,0.0)
    deb, cred, dev = _icms_simplificado(e)
    assert round(deb, 2) == 9000.00 and round(cred, 2) == 1000.00 and round(dev, 2) == 8000.00
    print("Self-tests OK")

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _run_self_tests()
    else:
        _ = ui()   # evita que o Streamlit escreva "None" na tela

