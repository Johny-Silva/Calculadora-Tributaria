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
INSS_PATRONAL_ALIQ_DEFAULT = 0.268  # fixo por ora

# Largura padrão da sidebar (edite aqui no código; não aparece na UI)
SIDEBAR_WIDTH_PX = 320

PERIODO_TIPO = Literal["Mensal", "Trimestral", "Anual", "Personalizado"]

# ============================
# Utilidades
# ============================

def format_brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

def format_pct_br(frac: float, casas: int = 2) -> str:
    """Recebe fração (0.1925) e devolve '19,25%'."""
    try:
        s = f"{frac*100:,.{casas}f}%"
        return s.replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return "0,00%"

def fmt_percent_styler(val):
    """Usado por Styler; aceita fração."""
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
    # remove qualquer caractere que não seja dígito, ponto ou vírgula
    s = re.sub(r"[^0-9.,-]", "", s)
    # se houver mais de uma vírgula, mantém a última como decimal
    if s.count(',') > 1:
        partes = s.split(',')
        s = ''.join(partes[:-1]).replace('.', '') + ',' + partes[-1]
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


# Centralizar cabeçalhos dos dataframes
HEADER_CENTER = [
    {"selector": "th.col_heading", "props": [("text-align", "center")]},
    {"selector": "th.col_heading.level0", "props": [("text-align", "center")]},
]

def style_df_center_headers(df: pd.DataFrame, money_cols=None, perc_cols=None, percent_row_label: str = "Carga sobre Receita"):
    money_cols = money_cols or [
        "Base","Crédito","Valor","PIS","COFINS","IRPJ","CSLL","INSS","ICMS","Total",
        "Lucro Presumido","Lucro Real","Simples Nacional"
    ]
    perc_cols  = perc_cols  or ["Alíquota","Carga sobre Receita"]

    sty = df.style.set_table_styles(HEADER_CENTER).hide(axis="index")

    # coluna 0 (rótulos)
    first_col = df.columns[0]

    # máscara da linha "Carga sobre Receita"
    ser_first = df[first_col].astype(str)
    has_percent_row = percent_row_label in ser_first.values
    row_mask = ser_first == percent_row_label

    # formatador de dinheiro pt-BR
    def _money_br(x):
        try:
            return format_brl(float(x))
        except Exception:
            return x

    # -------- Formatos por coluna (base) --------
    money_in_df = [c for c in money_cols if c in df.columns and c != first_col]
    perc_in_df  = [c for c in perc_cols  if c in df.columns and c != first_col]

    # 1) porcentagens "normais" por coluna (se existirem colunas percentuais)
    if perc_in_df:
        sty = sty.format({c: "{:.2%}" for c in perc_in_df})

    # 2) linha especial "Carga sobre Receita": força % pt-BR em TODAS as colunas numéricas
    if has_percent_row:
        num_cols = [c for c in df.columns if c != first_col]
        for c in num_cols:
            sty = sty.format(fmt_percent_styler, subset=pd.IndexSlice[row_mask, c])

    # 3) dinheiro pt-BR somente para as linhas que NÃO são a "Carga sobre Receita"
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
    """Campo monetário BRL com formatação automática ao confirmar.
    - PRIMEIRA VEZ: o campo inicia **em branco** (sem R$ 0,00).
    - Ao focar: se o valor atual for R$ 0,00 (ou vazio), **limpa**; senão, **seleciona tudo**.
    - Usa somente Session State (evita warnings do Streamlit).
    """
    if key not in st.session_state:
        # inicia vazio na primeira renderização
        st.session_state[key] = ""

    def _format_callback(_key=key):
        raw = st.session_state[_key]
        # se ficou vazio, mantém vazio em vez de R$ 0,00
        if str(raw).strip() == "":
            st.session_state[_key] = ""
            return
        val = brl_to_float(raw)
        st.session_state[_key] = format_brl(val)

    # sem `value=` (usa apenas session_state)
    st.text_input(label, key=key, on_change=_format_callback)

    # registra este label para o injetor global (feito 1x após renderizar a sidebar)
    labels = st.session_state.get("_currency_labels", [])
    if label not in labels:
        labels.append(label)
        st.session_state["_currency_labels"] = labels
    st.session_state["_currency_clear_on_zero"] = clear_on_focus_when_zero
    st.session_state["_currency_select_all_else"] = select_all_else

    return brl_to_float(st.session_state[key])


def inject_currency_focus_script():
    """Captura global de focusin para limpar/selecionar JÁ no primeiro clique."""
    labels = st.session_state.get("_currency_labels", [])
    clear_on_zero = st.session_state.get("_currency_clear_on_zero", True)
    select_all_else = st.session_state.get("_currency_select_all_else", True)

    js_labels = json.dumps(labels)
    js_clear  = "true" if clear_on_zero else "false"
    js_select = "true" if select_all_else else "false"

    # String normal (não f-string) — não precisa escapar chaves
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

      // Delegação: dispara no PRIMEIRO foco de qualquer input monitorado
      root.addEventListener('focusin', function(ev){
        const el = ev.target;
        if (isTarget(el)) handle(el);
      }, true);
    })();
    </script>
    """ % (js_labels, js_clear, js_select)

    st_html(script, height=0)


def set_sidebar_style(width_px: int = SIDEBAR_WIDTH_PX, compact_gap_px: int = 6):
    st.markdown(
        f"""
        <style>
          div[data-testid=\"stSidebar\"] {{ width: {width_px}px; }}
          div[data-testid=\"stSidebar\"] > div:first-child {{ width: {width_px}px; }}
          /* compacta espaçamentos verticais entre widgets do mesmo bloco */
          div[data-testid=\"stSidebar\"] .stTextInput,
          div[data-testid=\"stSidebar\"] .stNumberInput,
          div[data-testid=\"stSidebar\"] .stSelectbox,
          div[data-testid=\"stSidebar\"] .stCheckbox {{ margin-bottom: {compact_gap_px}px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def limite_irpj(periodo: PERIODO_TIPO, meses_personalizado: int) -> float:
    if periodo == "Mensal":
        return 20000.0
    if periodo == "Trimestral":
        return 60000.0
    if periodo == "Anual":
        return 240000.0
    meses = max(1, int(meses_personalizado or 1))
    return 20000.0 * meses


def adicional_irpj(base_calculo: float, periodo: PERIODO_TIPO, meses_personalizado: int = 0) -> float:
    lim = limite_irpj(periodo, meses_personalizado)
    excedente = max(base_calculo - lim, 0.0)
    return excedente * IRPJ_ADICIONAL_ALIQ


# ============================
# Modelos de dados
# ============================

@dataclass
class Entradas:
    periodo: PERIODO_TIPO
    meses_personalizado: int
    receita_bruta: float
    atividade: str  # "Comércio/Indústria" | "Serviços" | "Personalizado"
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

    inss_aliquota: float = INSS_PATRONAL_ALIQ_DEFAULT


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

    icms_debito: float
    icms_credito: float
    icms_devido: float

    total_impostos: float
    carga_efetiva_sobre_receita: float


# ============================
# Cálculos
# ============================

def _icms_destacado_saida(e: Entradas) -> float:
    if e.servicos_sem_icms:
        return 0.0
    return e.receita_icms * e.icms_aliquota * (1.0 - e.icms_percentual_st)


def _icms_simplificado(e: Entradas) -> Tuple[float, float, float]:
    if e.servicos_sem_icms:
        return 0.0, 0.0, 0.0
    debito = e.receita_icms * e.icms_aliquota * (1.0 - e.icms_percentual_st)
    credito = e.icms_creditos
    devido = max(debito - credito, 0.0)
    return debito, credito, devido


def calcular_lucro_presumido(e: Entradas) -> ResultadoRegime:
    icms_debito, icms_credito, icms_devido = _icms_simplificado(e)
    icms_destacado_saida = _icms_destacado_saida(e)

    base_pis = max(e.receita_bruta - icms_destacado_saida, 0.0)
    base_cofins = base_pis
    credito_pis = 0.0
    credito_cofins = 0.0
    pis = base_pis * PIS_PRESUMIDO
    cofins = base_cofins * COFINS_PRESUMIDO

    base_irpj = e.receita_bruta * e.presumido_irpj_base
    irpj_15 = base_irpj * IRPJ_ALIQ
    irpj_adic = adicional_irpj(base_irpj, e.periodo, e.meses_personalizado)
    irpj_total = irpj_15 + irpj_adic

    base_csll = e.receita_bruta * e.presumido_csll_base
    csll = base_csll * CSLL_ALIQ

    inss = e.folha_inss_base * e.inss_aliquota

    total = pis + cofins + irpj_total + csll + inss + icms_devido
    carga = total / e.receita_bruta if e.receita_bruta > 0 else 0.0

    return ResultadoRegime(
        regime="Lucro Presumido",
        base_pis=base_pis,
        credito_pis=credito_pis,
        pis=pis,
        base_cofins=base_cofins,
        credito_cofins=credito_cofins,
        cofins=cofins,
        base_irpj=base_irpj,
        irpj_15=irpj_15,
        irpj_adicional=irpj_adic,
        irpj_total=irpj_total,
        base_csll=base_csll,
        csll=csll,
        inss=inss,
        icms_debito=icms_debito,
        icms_credito=icms_credito,
        icms_devido=icms_devido,
        total_impostos=total,
        carga_efetiva_sobre_receita=carga,
    )


def calcular_lucro_real(e: Entradas) -> ResultadoRegime:
    icms_debito, icms_credito, icms_devido = _icms_simplificado(e)
    icms_destacado_saida = _icms_destacado_saida(e)

    creditavel = e.energia_eletrica + e.aluguel
    base_pis = max(e.receita_bruta - icms_destacado_saida, 0.0)
    base_cofins = base_pis
    credito_pis = creditavel * PIS_REAL
    credito_cofins = creditavel * COFINS_REAL
    pis = max(base_pis * PIS_REAL - credito_pis, 0.0)
    cofins = max(base_cofins * COFINS_REAL - credito_cofins, 0.0)

    lucro_liquido = e.receita_bruta - e.despesas_totais
    base_irpj = max(lucro_liquido, 0.0)
    base_csll = max(lucro_liquido, 0.0)

    irpj_15 = base_irpj * IRPJ_ALIQ
    irpj_adic = adicional_irpj(base_irpj, e.periodo, e.meses_personalizado)
    irpj_total = irpj_15 + irpj_adic

    csll = base_csll * CSLL_ALIQ

    inss = e.folha_inss_base * e.inss_aliquota

    total = pis + cofins + irpj_total + csll + inss + icms_devido
    carga = total / e.receita_bruta if e.receita_bruta > 0 else 0.0

    return ResultadoRegime(
        regime="Lucro Real",
        base_pis=base_pis,
        credito_pis=credito_pis,
        pis=pis,
        base_cofins=base_cofins,
        credito_cofins=credito_cofins,
        cofins=cofins,
        base_irpj=base_irpj,
        irpj_15=irpj_15,
        irpj_adicional=irpj_adic,
        irpj_total=irpj_total,
        base_csll=base_csll,
        csll=csll,
        inss=inss,
        icms_debito=icms_debito,
        icms_credito=icms_credito,
        icms_devido=icms_devido,
        total_impostos=total,
        carga_efetiva_sobre_receita=carga,
    )

# ============================
# Simples Nacional (tabelas e core)
# ============================

# Tabelas 2025 (faixas: limite RBT12, alíquota nominal, parcela a deduzir)
# Obs.: alíquotas em decimal (ex.: 0.073 = 7,3%)
TABELAS_SIMPLES = {
    "I": [
        (180_000.00,   0.040,   0.00),
        (360_000.00,   0.073,  5_940.00),
        (720_000.00,   0.095, 13_860.00),
        (1_800_000.00, 0.107, 22_500.00),
        (3_600_000.00, 0.143, 87_300.00),
        (4_800_000.00, 0.190, 378_000.00),
    ],
    "II": [
        (180_000.00,   0.045,    0.00),
        (360_000.00,   0.078,  5_940.00),
        (720_000.00,   0.100, 13_860.00),
        (1_800_000.00, 0.112, 22_500.00),
        (3_600_000.00, 0.147, 85_500.00),
        (4_800_000.00, 0.300, 720_000.00),
    ],
    "III": [
        (180_000.00,   0.060,     0.00),
        (360_000.00,   0.112,  9_360.00),
        (720_000.00,   0.135, 17_640.00),
        (1_800_000.00, 0.160, 35_640.00),
        (3_600_000.00, 0.210,125_640.00),
        (4_800_000.00, 0.330,648_000.00),
    ],
    "IV": [
        (180_000.00,   0.045,     0.00),
        (360_000.00,   0.090,  8_100.00),
        (720_000.00,   0.102, 12_420.00),
        (1_800_000.00, 0.140, 39_780.00),
        (3_600_000.00, 0.220,183_780.00),
        (4_800_000.00, 0.330,828_000.00),
    ],
    "V": [
        (180_000.00,   0.155,     0.00),
        (360_000.00,   0.180,  4_500.00),
        (720_000.00,   0.195,  9_900.00),
        (1_800_000.00, 0.205, 17_100.00),
        (3_600_000.00, 0.230, 62_100.00),
        (4_800_000.00, 0.305,540_000.00),
    ],
}

from dataclasses import dataclass
from typing import Literal, Tuple

AnexoTipo = Literal["I","II","III","IV","V","Auto"]

@dataclass
class SimplesInput:
    rbt12: float              # Receita bruta acumulada 12 meses
    receita_mes: float        # Receita do mês (competência)
    anexo: AnexoTipo          # I–V ou "Auto" (usa Fator R quando aplicável)
    folha_12m: float = 0.0    # p/ Fator R
    atividade_sujeita_fator_r: bool = False  # se a atividade pode alternar III/V
    considerar_sublimite: bool = False       # (MVP2)
    icms_iss_foras: bool = False             # (MVP2)

def _escolher_anexo(inp: SimplesInput) -> str:
    """Se anexo='Auto' e atividade_sujeita_fator_r=True, aplica Fator R (>=28% => III, senão V)."""
    if inp.anexo != "Auto":
        return inp.anexo
    if not inp.atividade_sujeita_fator_r:
        # atividade que não usa Fator R: por padrão considere III (ou ajuste conforme seu catálogo de CNAEs)
        return "III"
    # Fator R = folha_12m / rbt12
    if inp.rbt12 <= 0:
        return "V"  # sem histórico => escolha conservadora
    fator_r = (inp.folha_12m or 0.0) / inp.rbt12
    return "III" if fator_r >= 0.28 else "V"

def _faixa(anexo: str, rbt12: float) -> Tuple[float, float]:
    """Retorna (alíquota_nominal, parcela_deduzir) pela RBT12."""
    faixas = TABELAS_SIMPLES[anexo]
    for limite, aliq, pd in faixas:
        if rbt12 <= limite:
            return aliq, pd
    # extrapolou 4,8MM: segura última faixa (tratar sublimite no MVP2)
    aliq, pd = faixas[-1][1], faixas[-1][2]
    return aliq, pd

def aliquota_efetiva(aliq_nom: float, pd: float, rbt12: float) -> float:
    if rbt12 <= 0:
        return 0.0
    return max((rbt12 * aliq_nom - pd) / rbt12, 0.0)

def calcular_simples(inp: SimplesInput):
    """Calcula alíquota efetiva e DAS do mês (MVP)."""
    anexo = _escolher_anexo(inp)
    aliq_nom, pd = _faixa(anexo, inp.rbt12)
    aliq_eff = aliquota_efetiva(aliq_nom, pd, inp.rbt12)
    das = aliq_eff * max(inp.receita_mes, 0.0)
    return {
        "anexo": anexo,
        "aliquota_nominal": aliq_nom,
        "parcela_deduzir": pd,
        "aliquota_efetiva": aliq_eff,
        "das_mes": das,
    }


# ============================
# Exportadores
# ============================

def _df_detalhamento(e: Entradas, r: ResultadoRegime, periodo: PERIODO_TIPO, regime_nome: str) -> pd.DataFrame:
    if regime_nome == "Lucro Presumido":
        aliq_pis = PIS_PRESUMIDO
        aliq_cofins = COFINS_PRESUMIDO
    else:
        aliq_pis = PIS_REAL
        aliq_cofins = COFINS_REAL

    lim = limite_irpj(periodo, e.meses_personalizado)
    base_exced = max(r.base_irpj - lim, 0.0)

    dados = [
        {"Tributo": "PIS", "Base": r.base_pis, "Crédito": r.credito_pis, "Alíquota": aliq_pis, "Valor": r.pis},
        {"Tributo": "COFINS", "Base": r.base_cofins, "Crédito": r.credito_cofins, "Alíquota": aliq_cofins, "Valor": r.cofins},
        {"Tributo": "IRPJ (15%)", "Base": r.base_irpj, "Crédito": 0.0, "Alíquota": IRPJ_ALIQ, "Valor": r.irpj_15},
        {"Tributo": "IRPJ Adicional", "Base": base_exced, "Crédito": 0.0, "Alíquota": IRPJ_ADICIONAL_ALIQ, "Valor": r.irpj_adicional},
        {"Tributo": "CSLL", "Base": r.base_csll, "Crédito": 0.0, "Alíquota": CSLL_ALIQ, "Valor": r.csll},
        {"Tributo": "INSS", "Base": e.folha_inss_base, "Crédito": 0.0, "Alíquota": e.inss_aliquota, "Valor": r.inss},
        {"Tributo": "ICMS", "Base": (0.0 if e.servicos_sem_icms else e.receita_icms * (1.0 - e.icms_percentual_st)), "Crédito": r.icms_credito, "Alíquota": e.icms_aliquota, "Valor": r.icms_devido},
        {"Tributo": "TOTAL", "Base": None, "Crédito": None, "Alíquota": None, "Valor": r.total_impostos},
    ]
    return pd.DataFrame(dados)


def gerar_excel(
    rp: ResultadoRegime,
    rr: ResultadoRegime,
    e: Entradas,
    periodo: PERIODO_TIPO,
    sn: dict | None = None
) -> bytes:
    """
    Planilha com blocos à esquerda e tabela **Entradas (Parâmetros)** em duas colunas à direita (H1).
    Esquerda:
      1) Resumo (Comparativo) — impostos em coluna (Presumido x Real [x Simples])
      2) Detalhamento — Lucro Presumido
      3) Detalhamento — Lucro Real
      4) (Opcional) Simples Nacional — insumos e resultados
    Direita (H1):
      • Entradas (Parâmetros)
    """

    # --- Resumo (inclui Simples se houver) ---
    cols = {
        "Imposto": ["PIS", "COFINS", "IRPJ", "CSLL", "INSS", "ICMS", "Total", "Carga sobre Receita"],
        "Lucro Presumido": [
            rp.pis, rp.cofins, rp.irpj_total, rp.csll, rp.inss, rp.icms_devido,
            rp.total_impostos, rp.carga_efetiva_sobre_receita
        ],
        "Lucro Real": [
            rr.pis, rr.cofins, rr.irpj_total, rr.csll, rr.inss, rr.icms_devido,
            rr.total_impostos, rr.carga_efetiva_sobre_receita
        ],
    }
    if sn is not None:
        cols["Simples Nacional"] = [
            None, None, None, None, None, None,
            sn["das_mes"],
            (sn["das_mes"] / sn["receita_mes"]) if sn.get("receita_mes", 0) > 0 else 0.0
        ]
    df_comp = pd.DataFrame(cols)

    # --- Detalhamentos LP/LR ---
    dflp = _df_detalhamento(e, rp, periodo, "Lucro Presumido")
    dflr = _df_detalhamento(e, rr, periodo, "Lucro Real")

    # --- Parâmetros (duas colunas) ---
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
        # cria sheet base
        pd.DataFrame().to_excel(writer, sheet_name="Relatório", index=False)
        ws = writer.sheets["Relatório"]
        wb = writer.book

        # ---- formatos ----
        money_fmt = wb.add_format({"num_format": "R$ #,##0.00"})
        perc_fmt = wb.add_format({"num_format": "0.00%"})
        header_fmt = wb.add_format({
            "bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True,
            "align": "center", "valign": "vcenter"
        })
        title_fmt = wb.add_format({"bold": True, "font_size": 14})
        total_text_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True})
        total_money_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True, "num_format": "R$ #,##0.00"})
        total_perc_fmt = wb.add_format({"bg_color": "#38B0DE", "font_color": "#FFFFFF", "bold": True, "num_format": "0.00%"})

        # ---- helpers (DECLARADOS ANTES DE USAR!) ----
        def write_block(title: str, start_row: int, start_col: int, df: pd.DataFrame, total_row_name: str | None) -> int:
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

                    if col in {"Base", "Crédito", "Valor"}:
                        fmt = money_fmt
                    elif col in {"Alíquota"}:
                        fmt = perc_fmt

                    if first_col_name == "Imposto" and j > 0:
                        if df.iloc[i, 0] == "Carga sobre Receita":
                            fmt = perc_fmt
                        else:
                            fmt = money_fmt

                    # NOVO: regras específicas p/ bloco "Item" (Simples)
                    if first_col_name == "Item" and col == "Valor":
                        if "alíquota" in row_label.lower():
                            fmt = perc_fmt                     # aplica percentual em Nominal/Efetiva
                        elif row_label in ("RBT12", "Receita do mês", "Folha 12m", "Parcela a Deduzir (PD)", "DAS do mês"):
                            fmt = money_fmt                    # dinheiro nesses itens

                    if row_is_total:
                        if j == 0:
                            fmt = total_text_fmt
                        elif fmt is perc_fmt:
                            fmt = total_perc_fmt
                        else:
                            fmt = total_money_fmt

                    cell_row = r + i
                    cell_col = start_col + j
                    if pd.isna(val):
                        ws.write_blank(cell_row, cell_col, None, fmt)
                    elif isinstance(val, (int, float)) and fmt is not None:
                        ws.write_number(cell_row, cell_col, float(val), fmt)
                    else:
                        ws.write(cell_row, cell_col, val, fmt)
            for j, col in enumerate(df.columns):
                width = 24 if col in ("Regime", "Tributo", "Imposto", "Item") else 18
                ws.set_column(start_col + j, start_col + j, width)
            return r + len(df) + 3

        def write_params_block(start_row: int, start_col: int, rows: list[tuple[str, object, str]]) -> int:
            ws.merge_range(start_row, start_col, start_row, start_col + 1, "Entradas (Parâmetros)", title_fmt)
            r = start_row + 1
            ws.write(r, start_col + 0, "Parâmetro", header_fmt)
            ws.write(r, start_col + 1, "Informação", header_fmt)
            r += 1
            for i, (name, value, kind) in enumerate(rows):
                ws.write(r + i, start_col + 0, name)
                fmt = money_fmt if kind == "money" else perc_fmt if kind == "percent" else None
                if isinstance(value, (int, float)) and fmt is not None:
                    ws.write_number(r + i, start_col + 1, float(value), fmt)
                else:
                    ws.write(r + i, start_col + 1, value)
            ws.set_column(start_col + 0, start_col + 0, 28)
            ws.set_column(start_col + 1, start_col + 1, 26)
            return r + len(rows) + 3

        # ---- escrita dos blocos (agora que os helpers existem) ----
        row = 0
        row = write_block("Resumo (Comparativo)", row, 0, df_comp, total_row_name="Total")
        row = write_block("Detalhamento — Lucro Presumido", row, 0, dflp, total_row_name="TOTAL")
        row = write_block("Detalhamento — Lucro Real", row, 0, dflr, total_row_name="TOTAL")

        # Bloco Simples Nacional (opcional)
        if sn is not None:
            df_sn = pd.DataFrame([
                {"Item": "RBT12",                  "Valor": sn.get("rbt12", 0.0)},
                {"Item": "Receita do mês",         "Valor": sn.get("receita_mes", 0.0)},
                {"Item": "Folha 12m",              "Valor": sn.get("folha_12m", 0.0)},
                {"Item": "Anexo",                  "Valor": str(sn["anexo"])},
                {"Item": "Alíquota Nominal",       "Valor": sn["aliquota_nominal"]},
                {"Item": "Parcela a Deduzir (PD)", "Valor": sn["parcela_deduzir"]},
                {"Item": "Alíquota Efetiva",       "Valor": sn["aliquota_efetiva"]},
                {"Item": "DAS do mês",             "Valor": sn["das_mes"]},
            ])
            row = write_block("Simples Nacional", row, 0, df_sn, total_row_name=None)

        # Direita (H1): parâmetros
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

    # Parâmetros
    periodo_desc = e.periodo if e.periodo != "Personalizado" else f"Personalizado ({e.meses_personalizado} meses)"
    if str(e.atividade).startswith("Personalizado"):
        atividade_desc = f"Personalizado (IRPJ {e.presumido_irpj_base*100:.2f}%, CSLL {e.presumido_csll_base*100:.2f}%)"
    else:
        atividade_desc = e.atividade

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
        c.drawString(2.5*cm, y, f"{nome}: {val}")
        y -= 0.4*cm
        if y < 3*cm:
            c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)

    y -= 0.2*cm

    def bloco(titulo, r, y):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2*cm, y, titulo)
        y -= 0.45*cm
        c.setFont("Helvetica", 10)
        linhas = [
            ("PIS", r.pis), ("COFINS", r.cofins), ("IRPJ (15%)", r.irpj_15),
            ("IRPJ Adicional", r.irpj_adicional), ("IRPJ Total", r.irpj_total),
            ("CSLL", r.csll), ("INSS", r.inss), ("ICMS Devido", r.icms_devido),
            ("Total", r.total_impostos), ("Carga sobre Receita", f"{r.carga_efetiva_sobre_receita*100:.2f}%"),
        ]
        for nome, val in linhas:
            txt = f"{nome}: {format_brl(float(val))}" if isinstance(val, (int, float)) else f"{nome}: {val}"
            c.drawString(2.5*cm, y, txt)
            y -= 0.38*cm
            if y < 3*cm:
                c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)
        return y

    y = bloco("Lucro Presumido", rp, y); y -= 0.4*cm
    y = bloco("Lucro Real", rr, y)

    if sn is not None:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2*cm, y, "Simples Nacional")
        y -= 0.45*cm
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
        ]
        for nome, val in linhas_sn:
            c.drawString(2.5*cm, y, f"{nome}: {val}")
            y -= 0.38*cm
            if y < 3*cm:
                c.showPage(); y = h - 2*cm; c.setFont("Helvetica", 10)

    c.showPage(); c.save()
    return buff.getvalue()



# ============================
# UI (Streamlit)
# ============================

def ui() -> None:
    st.set_page_config(page_title="Calculadora Tributária", layout="wide")
    st.title("📊 Calculadora Tributária")
    st.caption("Ferramenta de planejamento — Regras simplificadas. Verifique a legislação específica antes de decidir.")

    # Aplica estilo/ largura da sidebar (sem controle na UI)
    set_sidebar_style(SIDEBAR_WIDTH_PX, compact_gap_px=6)

    # ================= Sidebar =================
    with st.sidebar:
        st.header("Parâmetros Gerais")
        periodo = st.selectbox("Período de apuração", ["Mensal", "Trimestral", "Anual", "Personalizado"], index=0)
        meses_personalizado = 0
        if periodo == "Personalizado":
            meses_personalizado = int(st.number_input("Quantidade de meses", min_value=1, max_value=60, value=5, step=1))
        receita = moeda_input("Receita Bruta do período (R$)", key="receita_bruta", value=0.0)

        st.divider()

        st.header("Simples Nacional")
        rbt12 = moeda_input("RBT12 (R$) — últimos 12 meses", key="sn_rbt12", value=0.0)
        receita_mes_sn = moeda_input("Receita do mês (R$)", key="sn_receita_mes", value=0.0)
        folha_12m_sn = moeda_input("Folha 12m (R$) — p/ Fator R", key="sn_folha12m", value=0.0)
        anexo_opt = st.selectbox("Anexo", ["Auto (Fator R)", "I","II","III","IV","V"], index=0)
        atividade_fator_r = st.checkbox("Atividade sujeita ao Fator R (III/V)?", value=True)

        st.divider()

        st.header("Lucro Presumido — Bases Presumidas")
        atividade = st.selectbox("Atividade (define base presumida)", [
            "Comércio/Indústria (IRPJ 8% | CSLL 12%)",
            "Serviços (IRPJ 32% | CSLL 32%)",
            "Personalizado",
        ])
        if atividade.startswith("Comércio"):
            presumido_irpj_base = 0.08
            presumido_csll_base = 0.12
        elif atividade.startswith("Serviços"):
            presumido_irpj_base = 0.32
            presumido_csll_base = 0.32
        else:
            presumido_irpj_base = st.number_input("Base Presumida IRPJ (%)", 0.0, 100.0, 8.0, 0.5) / 100.0
            presumido_csll_base = st.number_input("Base Presumida CSLL (%)", 0.0, 100.0, 12.0, 0.5) / 100.0

        st.divider()
        st.header("Folha / INSS")
        folha_inss = moeda_input("Base da Folha (R$)", key="folha_inss", value=0.0)
        inss_aliquota = st.number_input("Alíquota INSS (%)", min_value=0.0, max_value=100.0, value=26.8, step=0.1) / 100.0

        st.divider()
        st.header("Lucro Real — Despesas e Créditos")
        despesas_totais = moeda_input("Despesas Totais do período (R$)", key="despesas_totais", value=0.0)
        energia = moeda_input("Energia Elétrica (R$) — crédito PIS/COFINS", key="energia", value=0.0)
        aluguel = moeda_input("Aluguel (R$) — crédito PIS/COFINS", key="aluguel", value=0.0)

        st.divider()
        st.header("ICMS — Simplificado")
        servicos_sem_icms = st.checkbox("Empresa só de serviços (sem ICMS)", value=False, help="Zera e oculta campos de ICMS.")
        if not servicos_sem_icms:
            receita_icms = moeda_input("Receita de Mercadorias (base ICMS) (R$)", key="receita_icms", value=0.0)
            icms_aliquota = st.number_input("Alíquota ICMS (%)", min_value=0.0, max_value=100.0, value=18.0, step=0.5) / 100.0
            icms_creditos = moeda_input("Créditos de ICMS no período (R$)", key="icms_creditos", value=0.0)
            icms_percentual_st = st.number_input("% das vendas com ICMS-ST (0-100)", min_value=0.0, max_value=100.0, value=0.0, step=1.0) / 100.0
        else:
            receita_icms = 0.0
            icms_aliquota = 0.0
            icms_creditos = 0.0
            icms_percentual_st = 0.0

        # injeta script de foco global (somente 1x por render)
        inject_currency_focus_script()

    entradas = Entradas(
        periodo=periodo,
        meses_personalizado=meses_personalizado,
        receita_bruta=receita,
        atividade=atividade,
        presumido_irpj_base=presumido_irpj_base,
        presumido_csll_base=presumido_csll_base,
        folha_inss_base=folha_inss,
        inss_aliquota=inss_aliquota,
        despesas_totais=despesas_totais,
        energia_eletrica=energia,
        aluguel=aluguel,
        servicos_sem_icms=servicos_sem_icms,
        receita_icms=receita_icms,
        icms_aliquota=icms_aliquota,
        icms_creditos=icms_creditos,
        icms_percentual_st=icms_percentual_st,
    )

    if st.button("Calcular", type="primary"):
        st.session_state["res_presumido"] = calcular_lucro_presumido(entradas)
        st.session_state["res_real"] = calcular_lucro_real(entradas)

    # Simples
    an = "Auto" if anexo_opt.startswith("Auto") else anexo_opt
    sn = calcular_simples(SimplesInput(
        rbt12=rbt12,
        receita_mes=receita_mes_sn,
        anexo=an, folha_12m=folha_12m_sn,
        atividade_sujeita_fator_r=atividade_fator_r,
    ))
    sn["rbt12"] = rbt12
    sn["receita_mes"] = receita_mes_sn
    sn["folha_12m"] = folha_12m_sn
    st.session_state["res_simples"] = sn

    if "res_presumido" in st.session_state and "res_real" in st.session_state:
        rp: ResultadoRegime = st.session_state["res_presumido"]
        rr: ResultadoRegime = st.session_state["res_real"]

        # ===== Resultados sem abas: blocos lado a lado (dinâmico) =====
        # ===== Resultados =====
        if "res_presumido" in st.session_state and "res_real" in st.session_state:
            rp: ResultadoRegime = st.session_state["res_presumido"]
            rr: ResultadoRegime = st.session_state["res_real"]
            tem_sn = "res_simples" in st.session_state
            sn = st.session_state.get("res_simples", None)

            # ---------- topo: KPIs em colunas ----------
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
                    st.metric("Alíquota Efetiva", format_pct_br(sn['aliquota_efetiva']))
                    st.metric("Anexo", sn["anexo"])

            # ---------- detalhamento: abas para “desafogar” o painel ----------
            st.divider()
            if tem_sn:
                tab_lp, tab_lr, tab_sn = st.tabs(["Detalhamento — Presumido", "Detalhamento — Real", "Detalhamento — Simples"])
            else:
                tab_lp, tab_lr = st.tabs(["Detalhamento — Presumido", "Detalhamento — Real"])
                tab_sn = None

            with tab_lp:
                df_lp = _df_detalhamento(entradas, rp, periodo, "Lucro Presumido")
                st.dataframe(style_df_center_headers(df_lp), use_container_width=True)

            with tab_lr:
                df_lr = _df_detalhamento(entradas, rr, periodo, "Lucro Real")
                st.dataframe(style_df_center_headers(df_lr), use_container_width=True)

            if tab_sn is not None:
                with tab_sn:
                    # quadro compacto e formatado
                    df_sn = pd.DataFrame([
                        {"Item": "RBT12", "Valor": sn.get("rbt12", 0.0)},
                        {"Item": "Receita do mês", "Valor": sn.get("receita_mes", 0.0)},
                        {"Item": "Folha 12m", "Valor": sn.get("folha_12m", 0.0)},
                        {"Item": "Anexo", "Valor": sn["anexo"]},
                        {"Item": "Alíquota Nominal", "Valor": sn["aliquota_nominal"]},
                        {"Item": "Parcela a Deduzir (PD)", "Valor": sn["parcela_deduzir"]},
                        {"Item": "Alíquota Efetiva", "Valor": sn["aliquota_efetiva"]},
                        {"Item": "DAS do mês", "Valor": sn["das_mes"]},
                    ])

                    def _fmt_sn(df: pd.DataFrame):
                        sty = df.style.set_table_styles(HEADER_CENTER).hide(axis="index")
                        for i, row in df.iterrows():
                            if row["Item"] in ("RBT12","Receita do mês","Folha 12m","Parcela a Deduzir (PD)","DAS do mês"):
                                sty = sty.format(subset=pd.IndexSlice[i, "Valor"], formatter=lambda v: format_brl(float(v)))
                            elif row["Item"] in ("Alíquota Nominal","Alíquota Efetiva"):
                                sty = sty.format(subset=pd.IndexSlice[i, "Valor"], formatter="{:.2%}")
                        return sty
                    st.dataframe(_fmt_sn(df_sn), use_container_width=True)

            # ---------- resumo comparativo (agora com Simples) ----------
            st.divider()
            st.subheader("Resumo (Comparativo)")

            # Monte um dicionário separado do nome usado para o layout
            comp_dict = {
                "Imposto": ["PIS","COFINS","IRPJ","CSLL","INSS","ICMS","Total","Carga sobre Receita"],
                "Lucro Presumido": [
                    float(rp.pis), float(rp.cofins), float(rp.irpj_total), float(rp.csll),
                    float(rp.inss), float(rp.icms_devido), float(rp.total_impostos),
                    float(rp.carga_efetiva_sobre_receita),
                ],
                "Lucro Real": [
                    float(rr.pis), float(rr.cofins), float(rr.irpj_total), float(rr.csll),
                    float(rr.inss), float(rr.icms_devido), float(rr.total_impostos),
                    float(rr.carga_efetiva_sobre_receita),
                ],
            }

            # Se tiver Simples, inclua os números (sem strings com %)
            if sn is not None:
                total_simples = float(sn.get("das_total_com_difal", sn["das_mes"]))
                carga_simples = (total_simples / sn["receita_mes"]) if sn.get("receita_mes", 0) > 0 else 0.0
                comp_dict["Simples Nacional"] = [
                    None, None, None, None, None, None,
                    total_simples,
                    float(carga_simples),
                ]

            df_comp = pd.DataFrame(comp_dict)

            st.dataframe(
                style_df_center_headers(
                    df_comp,
                    perc_cols=["Carga sobre Receita"],
                    money_cols=["Lucro Presumido","Lucro Real"] + (["Simples Nacional"] if "Simples Nacional" in df_comp.columns else [])
                ).hide(axis="index"),
                use_container_width=True
)



        # ===== Exportações =====
        st.divider()
        st.subheader("Exportar Relatório")
        excel_bytes = gerar_excel(rp, rr, entradas, periodo, sn=st.session_state.get("res_simples", None))
        pdf_bytes = gerar_pdf(rp, rr, entradas, sn=st.session_state.get("res_simples", None))
        left, right = st.columns(2)
        with left:
            st.download_button(
                label="⬇️ Baixar Excel",
                data=excel_bytes,
                file_name="relatorio_calculo_tributario.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with right:
            st.download_button(
                label="⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name="relatorio_calculo_tributario.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


    with st.expander("Notas e Premissas"):
        st.markdown(
            """
            - **INSS**: Alíquota patronal personalizável aplicada sobre a base de folha informada.
            - **PIS/COFINS**: base = Receita Bruta − **ICMS destacado** nas saídas (estimado, não-ST). No Lucro Presumido (cumulativo): PIS **0,65%** e COFINS **3%** sem créditos. No Lucro Real (não-cumulativo): PIS **1,65%** e COFINS **7,6%** com créditos de **energia** e **aluguel**.
            - **IRPJ/CSLL**: Presumido conforme bases por atividade; Real sobre lucro líquido simplificado (Receita − **Despesas Totais**). **Adicional de 10%** do IRPJ sobre excedente ao limite do período (R$ 20k/mês × meses).
            - **ICMS simplificado**: débito sobre vendas não-ST e créditos informados. Se marcar **empresa só de serviços**, ICMS = 0 e campos são ocultados.
            - **Período Personalizado**: limite do adicional de IRPJ proporcional ao número de meses.
            - Ferramenta para **simulação**. Valide regras estaduais/particulares antes de decisões.
            """
        )


# ============================
# Self-tests
# ============================

def _run_self_tests():
    # format/parse BRL
    assert format_brl(1234.5) == "R$ 1.234,50"
    assert brl_to_float("R$ 1.234,50") == 1234.5
    assert brl_to_float("1.234,50") == 1234.5
    assert brl_to_float("R$0,00") == 0.0

    # limites adicional IRPJ
    assert limite_irpj("Mensal", 0) == 20000
    assert limite_irpj("Trimestral", 0) == 60000
    assert limite_irpj("Anual", 0) == 240000
    assert limite_irpj("Personalizado", 7) == 140000

    # adicional IRPJ
    assert adicional_irpj(50000, "Mensal", 0) == (50000-20000) * IRPJ_ADICIONAL_ALIQ
    assert adicional_irpj(100000, "Personalizado", 5) == max(100000-100000, 0) * IRPJ_ADICIONAL_ALIQ

    # ICMS simplificado
    e = Entradas(
        periodo="Mensal", meses_personalizado=0, receita_bruta=100000, atividade="Comércio/Indústria (IRPJ 8% | CSLL 12%)",
        presumido_irpj_base=0.08, presumido_csll_base=0.12, folha_inss_base=0,
        despesas_totais=0, energia_eletrica=0, aluguel=0,
        servicos_sem_icms=False, receita_icms=50000, icms_aliquota=0.18, icms_creditos=1000, icms_percentual_st=0.0
    )
    deb, cred, dev = _icms_simplificado(e)
    assert round(deb, 2) == 9000.00 and round(cred, 2) == 1000.00 and round(dev, 2) == 8000.00

    print("Self-tests OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _run_self_tests()
    else:
        ui()
