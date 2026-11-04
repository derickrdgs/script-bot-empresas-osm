#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot de extração de empresas (OpenStreetMap / Overpass API) com GUI em tkinter.

Instalação das dependências (somente 'requests' é externo; o resto é biblioteca padrão):
    pip install requests

Execução:
    python bot_empresas_osm.py

Fluxo:
    1. Geocodifica a cidade (Nominatim) -> latitude/longitude
    2. Consulta a Overpass API (raio + categoria), limite estrito de 1.000 empresas
    3. (Opcional) Enriquece com CNPJ / Razão Social / Situação / CNAE
    4. Exporta CSV (utf-8-sig) ao terminar OU ao clicar em "Parar" (dados parciais)
"""

import csv
import difflib
import queue
import random
import re
import threading
import time
import tkinter as tk
import unicodedata
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import requests

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================
# IMPORTANTE: troque o contato abaixo. Nominatim/Overpass exigem User-Agent identificável.
USER_AGENT = "BotEmpresasOSM/1.0 (uso pessoal; contato: seu-email@exemplo.com)"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{}"
# Endpoint público NÃO oficial (busca por nome). Pode mudar/sair do ar a qualquer momento.
BUSCA_NOME_URL = "https://api.casadosdados.com.br/v2/public/cnpj/search"

MAX_EMPRESAS = 1000          # limite estrito por execução
RAIO_PADRAO_KM = 60
PAUSA_MIN, PAUSA_MAX = 1.0, 3.0   # pausa aleatória entre requisições (segundos)

LIMITE_FALHAS_OVERPASS = 3   # falhas consecutivas toleradas por serviço
LIMITE_FALHAS_ENRIQUECIMENTO = 5
LIMITE_VAZIOS_BUSCA = 30     # buscas de CNPJ sem resultado, seguidas, até desistir do enriquecimento
LIMIAR_SIMILARIDADE = 0.85   # nome OSM x nome oficial, para aceitar o vínculo de CNPJ

NAO_INFORMADO = "Não informado"
CSV_DELIMITADOR = ";"        # ';' abre corretamente no Excel/LibreOffice em pt-BR

COLUNAS = [
    "Nome da Empresa", "Razão Social", "CNPJ", "Categoria", "Endereço Completo",
    "Telefone", "Latitude", "Longitude", "Situação CNPJ",
    "CNAE Principal",  # coluna extra (CNAE é pedido na etapa de enriquecimento)
]

# Categoria digitada (sem acento, minúscula) -> filtros OSM (chave, valor)
CATEGORIAS = {
    "restaurante": [("amenity", "restaurant")],
    "lanchonete": [("amenity", "fast_food")],
    "bar": [("amenity", "bar"), ("amenity", "pub")],
    "cafe": [("amenity", "cafe")],
    "padaria": [("shop", "bakery")],
    "farmacia": [("amenity", "pharmacy")],
    "oficina": [("shop", "car_repair")],
    "mecanica": [("shop", "car_repair")],
    "supermercado": [("shop", "supermarket")],
    "mercado": [("shop", "supermarket"), ("shop", "convenience")],
    "banco": [("amenity", "bank")],
    "posto": [("amenity", "fuel")],
    "clinica": [("amenity", "clinic")],
    "dentista": [("amenity", "dentist")],
    "hospital": [("amenity", "hospital")],
    "escola": [("amenity", "school")],
    "salao": [("shop", "hairdresser"), ("shop", "beauty")],
    "cabeleireiro": [("shop", "hairdresser")],
    "academia": [("leisure", "fitness_centre")],
    "hotel": [("tourism", "hotel")],
    "pet": [("shop", "pet")],
    "loja de roupas": [("shop", "clothes")],
    "padarias": [("shop", "bakery")],
}
SUGESTOES_CATEGORIA = ["todos", "restaurante", "farmácia", "oficina", "padaria",
                       "supermercado", "lanchonete", "bar", "academia", "clínica",
                       "salão", "posto", "hotel", "pet"]


# ============================================================================
# FUNÇÕES AUXILIARES (puras, sem dependência de GUI)
# ============================================================================
def normalizar(texto):
    """Minúsculo, sem acentos e sem pontuação (para comparar/slug)."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", texto)).strip()


def so_digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def cnpj_valido(cnpj):
    """Valida os dígitos verificadores de um CNPJ (14 dígitos)."""
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1

    def dv(base, pesos):
        resto = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return "0" if resto < 2 else str(11 - resto)

    d1 = dv(cnpj[:12], p1)
    d2 = dv(cnpj[:12] + d1, p2)
    return cnpj[12:] == d1 + d2


def formatar_cnpj(c):
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def similaridade(a, b):
    """Similaridade 0..1 entre dois nomes de empresa."""
    a, b = normalizar(a), normalizar(b)
    if not a or not b:
        return 0.0
    score = difflib.SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) >= 5 and (a in b or b in a):
        score = max(score, 0.9)
    return score


def filtros_categoria(categoria):
    """Converte a categoria digitada em filtros OSM [(chave, valor|None)]."""
    cat = normalizar(categoria)
    if cat in ("", "todos", "todas", "all"):
        return [(k, None) for k in ("amenity", "shop", "craft", "office")]
    if cat in CATEGORIAS:
        return CATEGORIAS[cat]
    # Categoria desconhecida: tenta o texto cru como valor de tag OSM
    valor = re.sub(r"[^a-z0-9_]", "", cat.replace(" ", "_"))
    return [(k, valor) for k in ("amenity", "shop", "craft", "office")]


def montar_query(lat, lon, raio_m, filtros):
    """Monta a consulta Overpass QL (nós, vias e relações dentro do raio)."""
    linhas = []
    for chave, valor in filtros:
        alvo = f'["{chave}"]' if valor is None else f'["{chave}"="{valor}"]'
        linhas.append(f'  nwr{alvo}["name"](around:{raio_m},{lat},{lon});')
    return (f"[out:json][timeout:120];\n(\n" + "\n".join(linhas) +
            f"\n);\nout center {MAX_EMPRESAS};")


def formatar_endereco(tags):
    rua, num = tags.get("addr:street"), tags.get("addr:housenumber")
    bairro = (tags.get("addr:suburb") or tags.get("addr:neighbourhood")
              or tags.get("addr:district"))
    cidade, uf, cep = tags.get("addr:city"), tags.get("addr:state"), tags.get("addr:postcode")
    logradouro = f"{rua}, {num}" if rua and num else (rua or "")
    cidade_uf = f"{cidade} - {uf}" if cidade and uf else (cidade or uf or "")
    partes = [logradouro, bairro or "", cidade_uf, f"CEP {cep}" if cep else ""]
    partes = [p for p in partes if p]
    return ", ".join(partes) if partes else NAO_INFORMADO


def categoria_osm(tags):
    for chave in ("amenity", "shop", "craft", "office", "tourism", "leisure"):
        if tags.get(chave):
            return f"{tags[chave]} ({chave})"
    return NAO_INFORMADO


def cnpj_das_tags(tags):
    """Algumas empresas trazem o CNPJ mapeado no OSM; aproveita quando existir."""
    for chave in ("cnpj", "ref:cnpj", "ref:BR:CNPJ", "contact:cnpj", "ref:vatin", "vat"):
        c = so_digitos(tags.get(chave))
        if cnpj_valido(c):
            return c
    return None


def exportar_csv(rows, cidade, pasta=None):
    """Salva o CSV (utf-8-sig). Retorna o caminho ou None se não houver dados."""
    if not rows:
        return None
    pasta = pasta or Path(__file__).resolve().parent
    slug = normalizar(cidade).replace(" ", "_") or "empresas"
    caminho = Path(pasta) / f"empresas_{slug}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS, delimiter=CSV_DELIMITADOR,
                           extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({c: (row.get(c) or NAO_INFORMADO) for c in COLUNAS})
    return caminho


# ============================================================================
# BACKEND (roda em thread separada)
# ============================================================================
class LimiteFalhas(Exception):
    """Falhas consecutivas além do tolerado (bloqueio de IP, rate limit etc.)."""


class BotWorker:
    def __init__(self, cidade, raio_km, categoria, enriquecer, log_fn, stop_event):
        self.cidade, self.raio_km, self.categoria = cidade, raio_km, categoria
        self.enriquecer = enriquecer
        self._log_fn = log_fn
        self.stop = stop_event          # flag de parada (threading.Event)
        self.rows = []                  # dados coletados (parciais ficam aqui)
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.falhas = {}                # contador de falhas consecutivas por serviço
        self.vazios_busca = 0
        self.enriquecimento_ativo = enriquecer

    # ---- utilidades -------------------------------------------------------
    def _log(self, msg):
        self._log_fn(msg)

    def _dormir(self, segundos):
        """Sleep que acorda imediatamente se o usuário clicar em Parar."""
        fim = time.time() + segundos
        while time.time() < fim and not self.stop.is_set():
            time.sleep(0.1)

    def _pausa(self):
        self._dormir(random.uniform(PAUSA_MIN, PAUSA_MAX))

    def _requisitar(self, chave, limite, metodo, url, ok_status=(200,), **kw):
        """
        Faz UMA requisição com checagem explícita de status.
        - Sucesso: zera o contador, aplica pausa aleatória e devolve a resposta.
        - Falha (rede, 429, != 200): incrementa contador, espera (backoff) e devolve None.
        - Falhas consecutivas >= limite: levanta LimiteFalhas.
        """
        timeout = kw.pop("timeout", 130 if chave == "overpass" else 30)
        espera, motivo = 0, None
        try:
            r = self.sessao.request(metodo, url, timeout=timeout, **kw)
        except requests.RequestException as e:
            r, motivo = None, f"erro de rede ({e.__class__.__name__})"
        else:
            if r.status_code in ok_status:
                self.falhas[chave] = 0
                self._pausa()
                return r
            if r.status_code == 429:
                motivo = "HTTP 429 (Too Many Requests / rate limit)"
                try:
                    espera = int(r.headers.get("Retry-After", 0))
                except ValueError:
                    espera = 0
            else:
                motivo = f"HTTP {r.status_code}"

        self.falhas[chave] = self.falhas.get(chave, 0) + 1
        self._log(f"[AVISO] {chave}: {motivo} "
                  f"(falha {self.falhas[chave]}/{limite})")
        if self.falhas[chave] >= limite:
            raise LimiteFalhas(f"{chave}: {limite} falhas consecutivas ({motivo}). "
                               f"Possível bloqueio de IP ou serviço indisponível.")
        self._dormir(max(espera, 10 * self.falhas[chave]))   # backoff
        return None

    def _requisitar_ate_ok(self, chave, limite, metodo, url, **kw):
        """Repete até obter resposta, ser parado ou estourar o limite de falhas."""
        while not self.stop.is_set():
            r = self._requisitar(chave, limite, metodo, url, **kw)
            if r is not None:
                return r
        return None

    # ---- etapa 1: geocodificação -----------------------------------------
    def _geocodificar(self):
        self._log(f"Localizando '{self.cidade}'...")
        r = self._requisitar_ate_ok(
            "nominatim", 3, "GET", NOMINATIM_URL,
            params={"q": self.cidade, "format": "jsonv2", "limit": 1,
                    "addressdetails": 1, "countrycodes": "br"})
        if r is None:
            return None
        dados = r.json()
        if not dados:
            self._log("[ERRO] Cidade/região não encontrada (retorno vazio do Nominatim).")
            return None
        d = dados[0]
        end = d.get("address", {})
        municipio = (end.get("city") or end.get("town") or end.get("municipality")
                     or end.get("village") or self.cidade)
        uf = (end.get("ISO3166-2-lvl4") or "").split("-")[-1] or None
        self._log(f"Centro: {d['display_name']} ({d['lat']}, {d['lon']})")
        return float(d["lat"]), float(d["lon"]), uf, municipio

    # ---- etapa 2: extração Overpass --------------------------------------
    def _extrair(self, lat, lon):
        filtros = filtros_categoria(self.categoria)
        query = montar_query(lat, lon, int(self.raio_km * 1000), filtros)
        self._log(f"Consultando Overpass (raio {self.raio_km:g} km, "
                  f"categoria '{self.categoria}')... pode levar até 2 min.")
        r = self._requisitar_ate_ok("overpass", LIMITE_FALHAS_OVERPASS, "POST",
                                    OVERPASS_URL, data={"data": query})
        if r is None:
            return
        try:
            dados = r.json()
        except ValueError:
            self._log("[ERRO] Overpass devolveu resposta que não é JSON.")
            return
        if dados.get("remark"):
            self._log(f"[AVISO] Overpass: {dados['remark']}")
        elementos = dados.get("elements") or []
        if not elementos:
            self._log("[AVISO] Overpass retornou lista vazia: nenhuma empresa encontrada.")
            return

        vistos = set()
        for el in elementos:
            if len(self.rows) >= MAX_EMPRESAS:
                self._log(f"Limite de {MAX_EMPRESAS} empresas atingido.")
                break
            tags = el.get("tags") or {}
            nome = tags.get("name") or tags.get("brand")
            chave = (el.get("type"), el.get("id"))
            if not nome or chave in vistos:
                continue
            vistos.add(chave)
            centro = el.get("center") or {}
            la, lo = el.get("lat", centro.get("lat")), el.get("lon", centro.get("lon"))
            self.rows.append({
                "Nome da Empresa": nome,
                "Razão Social": NAO_INFORMADO,
                "CNPJ": NAO_INFORMADO,
                "Categoria": categoria_osm(tags),
                "Endereço Completo": formatar_endereco(tags),
                "Telefone": (tags.get("phone") or tags.get("contact:phone")
                             or tags.get("contact:mobile") or NAO_INFORMADO),
                "Latitude": la if la is not None else NAO_INFORMADO,
                "Longitude": lo if lo is not None else NAO_INFORMADO,
                "Situação CNPJ": NAO_INFORMADO,
                "CNAE Principal": NAO_INFORMADO,
                "_tags": tags,   # uso interno (ignorado no CSV)
            })
        self._log(f"{len(self.rows)} empresas coletadas do OpenStreetMap.")

    # ---- etapa 3: enriquecimento de CNPJ ---------------------------------
    def _buscar_por_nome(self, nome, uf, municipio):
        """Busca candidata por nome+cidade. Retorna dict do melhor item ou None."""
        corpo = {
            "query": {"termo": [nome], "atividade_principal": [], "natureza_juridica": [],
                      "uf": [uf] if uf else [], "municipio": [normalizar(municipio).upper()],
                      "bairro": [], "situacao_cadastral": "ATIVA", "cep": [], "ddd": []},
            "range_query": {"data_abertura": {"lte": None, "gte": None},
                            "capital_social": {"lte": None, "gte": None}},
            "extras": {"somente_mei": False, "excluir_mei": False, "com_email": False,
                       "incluir_atividade_secundaria": False, "com_contato_telefonico": False,
                       "somente_fixo": False, "somente_celular": False,
                       "somente_matriz": False, "somente_filial": False},
            "page": 1,
        }
        r = self._requisitar("busca-cnpj", LIMITE_FALHAS_ENRIQUECIMENTO, "POST",
                             BUSCA_NOME_URL, json=corpo)
        if r is None:
            return None
        try:
            lista = ((r.json().get("data") or {}).get("cnpj")) or []
        except (ValueError, AttributeError):
            lista = []
        melhor, melhor_score = None, 0.0
        for item in lista:
            for campo in ("nome_fantasia", "razao_social"):
                s = similaridade(nome, item.get(campo))
                if s > melhor_score:
                    melhor, melhor_score = item, s
        return melhor if melhor_score >= LIMIAR_SIMILARIDADE else None

    def _consultar_brasilapi(self, cnpj):
        r = self._requisitar("brasilapi", LIMITE_FALHAS_ENRIQUECIMENTO, "GET",
                             BRASILAPI_URL.format(cnpj), ok_status=(200, 404))
        if r is None or r.status_code == 404:
            return None
        try:
            return r.json() or None
        except ValueError:
            return None

    def _enriquecer_um(self, row, uf, municipio):
        cnpj = cnpj_das_tags(row["_tags"])
        item = None
        if not cnpj:
            item = self._buscar_por_nome(row["Nome da Empresa"], uf, municipio)
            if not item:
                self.vazios_busca += 1
                if self.vazios_busca >= LIMITE_VAZIOS_BUSCA:
                    self._log(f"[AVISO] {LIMITE_VAZIOS_BUSCA} buscas seguidas sem resultado: "
                              "enriquecimento desativado (seguindo para exportação).")
                    self.enriquecimento_ativo = False
                return
            self.vazios_busca = 0
            cnpj = so_digitos(item.get("cnpj"))
            if not cnpj_valido(cnpj):
                return
            # Dados básicos vindos da busca (serão refinados pela BrasilAPI)
            row["CNPJ"] = formatar_cnpj(cnpj)
            row["Razão Social"] = item.get("razao_social") or NAO_INFORMADO
            row["Situação CNPJ"] = str(item.get("situacao_cadastral") or NAO_INFORMADO).title()

        dados = self._consultar_brasilapi(cnpj)
        if dados:
            row["CNPJ"] = formatar_cnpj(cnpj)
            row["Razão Social"] = dados.get("razao_social") or row["Razão Social"]
            row["Situação CNPJ"] = (str(dados.get("descricao_situacao_cadastral") or "")
                                    .title() or row["Situação CNPJ"])
            if dados.get("cnae_fiscal"):
                row["CNAE Principal"] = (f"{dados['cnae_fiscal']} - "
                                         f"{dados.get('cnae_fiscal_descricao', '')}").strip(" -")

    def _enriquecer_todos(self, uf, municipio):
        self._log("Enriquecendo com CNPJ (BrasilAPI)... isso é lento por causa das pausas.")
        total = len(self.rows)
        for i, row in enumerate(self.rows, 1):
            if self.stop.is_set() or not self.enriquecimento_ativo:
                break
            try:
                self._enriquecer_um(row, uf, municipio)
            except LimiteFalhas as e:
                self._log(f"[PROTEÇÃO] {e} Enriquecimento interrompido.")
                break
            if i % 10 == 0 or i == total:
                achados = sum(1 for r in self.rows if r["CNPJ"] != NAO_INFORMADO)
                self._log(f"Enriquecimento: {i}/{total} processadas | CNPJs vinculados: {achados}")

    # ---- orquestração -----------------------------------------------------
    def executar(self):
        """Executa tudo e SEMPRE exporta o que foi coletado (mesmo parcial)."""
        try:
            geo = self._geocodificar()
            if geo and not self.stop.is_set():
                lat, lon, uf, municipio = geo
                self._extrair(lat, lon)
                if self.rows and self.enriquecer and not self.stop.is_set():
                    self._enriquecer_todos(uf, municipio)
        except LimiteFalhas as e:
            self._log(f"[PROTEÇÃO] {e} Exportando dados coletados até aqui.")
        except Exception as e:  # noqa: BLE001 - nunca perder os dados coletados
            self._log(f"[ERRO] Falha inesperada: {e.__class__.__name__}: {e}")
        finally:
            if self.stop.is_set():
                self._log("Execução interrompida pelo usuário. Exportando dados parciais...")
            caminho = exportar_csv(self.rows, self.cidade)
            if caminho:
                self._log(f"CSV salvo ({len(self.rows)} empresas): {caminho}")
            else:
                self._log("Nenhum dado coletado: nada para exportar.")
        return len(self.rows)


# ============================================================================
# INTERFACE GRÁFICA (tkinter)
# ============================================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("Bot de Empresas - OpenStreetMap")
        root.geometry("760x560")
        root.minsize(640, 460)

        self.fila = queue.Queue()          # comunicação thread -> GUI
        self.stop_event = threading.Event()  # flag global de parada
        self.thread = None
        self.fechar_ao_terminar = False

        self._montar_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.root.after(100, self._processar_fila)

    def _montar_widgets(self):
        frm = ttk.LabelFrame(self.root, text="Parâmetros", padding=10)
        frm.pack(fill="x", padx=10, pady=(10, 5))
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Cidade ou região:").grid(row=0, column=0, sticky="w", pady=3)
        self.ent_cidade = ttk.Entry(frm)
        self.ent_cidade.insert(0, "Ribeirão Preto")
        self.ent_cidade.grid(row=0, column=1, sticky="ew", padx=8)

        ttk.Label(frm, text="Raio (km):").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_raio = ttk.Entry(frm, width=10)
        self.ent_raio.insert(0, str(RAIO_PADRAO_KM))
        self.ent_raio.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Categoria:").grid(row=2, column=0, sticky="w", pady=3)
        self.cmb_cat = ttk.Combobox(frm, values=SUGESTOES_CATEGORIA)
        self.cmb_cat.set("todos")
        self.cmb_cat.grid(row=2, column=1, sticky="ew", padx=8)

        self.var_enriquecer = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Enriquecer com CNPJ (mais lento)",
                        variable=self.var_enriquecer).grid(row=3, column=1, sticky="w",
                                                           padx=8, pady=(6, 0))

        botoes = ttk.Frame(self.root, padding=(10, 5))
        botoes.pack(fill="x")
        self.btn_iniciar = ttk.Button(botoes, text="[ INICIAR BOT ]", command=self.iniciar)
        self.btn_iniciar.pack(side="left", padx=(0, 8))
        self.btn_parar = ttk.Button(botoes, text="[ PARAR BOT ]", command=self.parar,
                                    state="disabled")
        self.btn_parar.pack(side="left")
        self.lbl_status = ttk.Label(botoes, text="Parado")
        self.lbl_status.pack(side="right")

        log_frm = ttk.LabelFrame(self.root, text="Log / Status", padding=5)
        log_frm.pack(fill="both", expand=True, padx=10, pady=(5, 10))
        self.txt_log = ScrolledText(log_frm, state="disabled", wrap="word", height=12)
        self.txt_log.pack(fill="both", expand=True)

    # ---- log thread-safe: a thread só enfileira, a GUI desenha -----------
    def _log_thread(self, msg):
        self.fila.put(("log", msg))

    def _escrever_log(self, msg):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"[{datetime.now():%H:%M:%S}] {msg}\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _processar_fila(self):
        try:
            while True:
                tipo, valor = self.fila.get_nowait()
                if tipo == "log":
                    self._escrever_log(valor)
                elif tipo == "fim":
                    self._finalizar(valor)
        except queue.Empty:
            pass
        self.root.after(100, self._processar_fila)

    # ---- ações ------------------------------------------------------------
    def iniciar(self):
        cidade = self.ent_cidade.get().strip()
        if not cidade:
            messagebox.showwarning("Atenção", "Informe a cidade ou região.")
            return
        try:
            raio = float(self.ent_raio.get().replace(",", "."))
            if not 0 < raio <= 300:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Atenção", "Raio inválido. Use um valor entre 1 e 300 km.")
            return

        self.stop_event.clear()
        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self.lbl_status.configure(text="Em execução...")
        self._escrever_log(f"=== Nova execução: {cidade} | {raio:g} km | "
                           f"{self.cmb_cat.get() or 'todos'} ===")

        worker = BotWorker(cidade, raio, self.cmb_cat.get().strip(),
                           self.var_enriquecer.get(), self._log_thread, self.stop_event)

        def alvo():
            total = worker.executar()
            self.fila.put(("fim", total))

        self.thread = threading.Thread(target=alvo, daemon=True)
        self.thread.start()

    def parar(self):
        self.stop_event.set()
        self.btn_parar.configure(state="disabled")
        self.lbl_status.configure(text="Parando...")
        self._escrever_log("Parada solicitada: finalizando com segurança e exportando...")

    def _finalizar(self, total):
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        self.lbl_status.configure(text=f"Concluído ({total} empresas)")
        if self.fechar_ao_terminar:
            self.root.destroy()

    def _ao_fechar(self):
        if self.thread and self.thread.is_alive():
            if messagebox.askokcancel("Sair", "O bot está em execução.\n"
                                      "Parar, exportar dados parciais e sair?"):
                self.fechar_ao_terminar = True
                self.parar()
            return
        self.root.destroy()


if __name__ == "__main__":
    janela = tk.Tk()
    App(janela)
    janela.mainloop()